"""Paper research orchestration, risk gates and auditable virtual settlements.
PAPER stays virtual; LIVE_MT5 submits durable, risk-sized intents and reconciles broker deals.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import math
import uuid
import hashlib
import time
import copy
import pandas as pd
from backend.config import TradingConfig, strategy_requires_ml
from backend.diagnostics import DiagnosticRecorder, RecoveryState, analysis_event, digest, preflight_code
from backend.journal import PaperJournal
from backend.market_data import validate_candles
from backend.mt5_execution import MT5Executor, MT5ExecutionError, ACTIVE_LIVE
from backend.portfolio_risk import position_limit, active_trades, reserved_risk, global_budget, local_block, context
from backend.telegram_bot import TelegramBot
from backend.trade_plan import price_plan, goal_limited_budget, TradePlanError
from backend.trade_diagnostics import observe_exit_quote

from backend.strategies import StrategyEngine, SignalResult
from backend.mt5_connector import MT5Connector
from backend.safirion_connector import SafirionConnector


class BotManager:
    def __init__(self):
        self.mt5 = MT5Connector()
        self.safirion = SafirionConnector()

        self.live = MT5Executor(self.mt5)

        self.is_running = False
        self.journal = PaperJournal()
        self.audit = DiagnosticRecorder(self.journal)
        self.recovery = RecoveryState()
        self._pending_trade_saves = {}
        self._intent_uncertain = False
        self._notification_failure = False
        self._pending_notifications = {}
        self.config = TradingConfig.model_validate(self.journal.load_config() or {}).model_dump()
        self.mt5.configure_time(self.config)
        self.mt5.connect(use_simulation_if_unavailable=self.config['execution_mode']=='PAPER')
        self._session_day = datetime.now(timezone.utc).date().isoformat()
        self._order_lock = asyncio.Lock()
        self._mt5_busy=False
        self._last_snapshot=None
        self._multi = None
        self._last_signal_bar = None
        self._settlement_tasks = set()

        self.stats: Dict[str, Any] = {
            "daily_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "total_trades": 0,
            "win_rate": 0.0,
            "current_gale_step": 0,
            "win_streak": 0,
            "loss_streak": 0,
            "cycle_loss_accumulated": 0.0,
            "last_win_profit": 0.0,
            "next_suggested_amount": 5.0,
            "sizing_reason": "1x Base Analítica (R$ 5.00)",
            "open_trade_remaining_sec": 0,
            "status_reason": "Robô em espera (Pausado). Configure seu ativo e clique em LIGAR ROBÔ.",
        }

        self.trade_history: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, str]] = []
        self.last_signal: Optional[Dict[str, Any]] = None
        self._last_trade_timestamp: float = 0.0
        self._last_loss_timestamp: float = 0.0
        self._last_loss_action: Optional[str] = None
        self._has_open_trade: bool = False

        # Restarts retain session risk and never turn pending paper trades into wins.
        self.trade_history = self._session_trades()
        for trade in self.trade_history:
            if trade['status'] == 'OPEN' and trade.get('mode','PAPER') == 'PAPER' and not (trade.get('exit_policy')=='SL_TP' and trade.get('source')=='MT5_LIVE'):
                trade.update(status='INTERRUPTED', pnl=-float(trade['amount']), remaining_sec=0,
                             settlement_note='Simulação interrompida: reserva de risco integral debitada; não é resultado de mercado.')
                self.journal.save(trade)
        self._has_open_trade = any((t.get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE) or (t.get('mode','PAPER')=='PAPER' and t['status']=='OPEN') for t in self.trade_history)
        self._refresh_stats()
        self.notifier = TelegramBot(self.journal, self.telegram_status, self.pause_from_telegram)
        self._last_signal_bar = self.trade_history[0].get('bar_time') if self.trade_history else None
        self._last_trade_timestamp = max((t.get('settled_at', t.get('created_at', 0)) for t in self.trade_history), default=0)
        self.add_log("INFO", f"Modo {self.config['execution_mode']}; robô inicia pausado. Posições MT5 pendentes continuam sendo monitoradas.")

    def _session_trades(self):
        today = datetime.now(timezone.utc).date().isoformat()
        mode = self.config['execution_mode']
        result=[]
        for t in self.journal.all_trades():
            tmode=t.get('mode','PAPER')
            if tmode == 'LIVE_MT5' and t.get('deals') and t.get('deal_times_version') != 1:
                same_account = (t.get('account_login'), t.get('account_server')) == (self.config['mt5_account_login'], self.config['mt5_account_server'])
                try:
                    normalized = MT5Executor.normalize_trade_times(t, self.config.get('mt5_deal_time_offset_minutes') if same_account else None)
                except MT5ExecutionError as exc:
                    # Preserve an unprovable legacy timestamp, never guess or
                    # overwrite its financial result. New sync requires proof.
                    t['deal_time_warning'] = str(exc)
                else:
                    t.update(normalized)
                    t.pop('deal_time_warning', None)
                    try:
                        self.journal.save(t)
                    except Exception:
                        self._pending_trade_saves[t['id']] = t
            pending=(tmode=='LIVE_MT5' and t['status'] in ACTIVE_LIVE) or (tmode=='PAPER' and t['status']=='OPEN' and t.get('exit_policy')=='SL_TP')
            if tmode!=mode and not pending:
                continue
            if mode=='LIVE_MT5' and not pending and (t.get('account_login')!=self.config['mt5_account_login'] or t.get('account_server')!=self.config['mt5_account_server']):
                continue
            has_today_deal=any(datetime.fromtimestamp(d['time'],timezone.utc).date().isoformat()==today for d in t.get('deals',[]))
            settled_today=t.get('settled_at') is not None and datetime.fromtimestamp(t['settled_at'],timezone.utc).date().isoformat()==today
            if pending or t.get('day')==today or has_today_deal or settled_today:
                result.append(t)
        return result

    def _refresh_stats(self):
        mode=self.config['execution_mode']
        relevant=[t for t in self.trade_history if t.get('mode','PAPER')==mode]
        settled = [t for t in relevant if t['status'] in ('WIN','LOSS','DRAW','INTERRUPTED')]
        today=datetime.now(timezone.utc).date().isoformat()
        if mode=='LIVE_MT5':
            self.stats['daily_pnl']=round(sum(d['pnl'] for t in relevant for d in t.get('deals',[]) if datetime.fromtimestamp(d['time'],timezone.utc).date().isoformat()==today),8)
        else:
            self.stats['daily_pnl'] = round(sum(t['pnl'] for t in settled if (datetime.fromtimestamp(t['settled_at'],timezone.utc).date().isoformat() if t.get('settled_at') else t.get('day'))==today), 2)
        self.stats['wins'] = sum(t['status'] == 'WIN' for t in settled)
        self.stats['losses'] = sum(t['status'] in ('LOSS', 'INTERRUPTED') for t in settled)
        self.stats['total_trades'] = len(settled)
        self.stats['win_rate'] = round(100 * self.stats['wins'] / len(settled), 1) if settled else 0
        streak = 0
        for t in settled:
            if t['status'] not in ('LOSS', 'INTERRUPTED'):
                break
            streak += 1
        self.stats['loss_streak'] = streak
        self.stats['floating_pnl']=sum(t.get('floating_pnl',0) for t in relevant if t['status'] in ACTIVE_LIVE)

    def telegram_status(self):
        c=self.config
        account=f"{c['mt5_account_login']} / {c['mt5_account_server']}" if c['execution_mode']=='LIVE_MT5' else 'banca virtual'
        currency=self.mt5.account_info.get('currency','') if c['execution_mode']=='LIVE_MT5' else 'BRL virtual'
        monitored = ', '.join(p['symbol'] for p in c.get('forex_pairs', []) if p['enabled']) if c.get('forex_multi_enabled') else c['symbol']
        operational = self.operational_status()
        monitoring_label={'PAUSED':'PAUSADO','MONITORING':'MONITORANDO','RECOVERING':'EM RECUPERAÇÃO'}[operational['state']]
        active = ' • '.join(f"{key} [{fault['code']}]: {fault['message']}" for key, fault in operational['faults'].items())
        pending_revalidation = ' • '.join(f"{key} [{fault['code']}]" for key, fault in operational['pending_revalidation'].items())
        operational_reason = operational['blocked_reason'] or active or 'Sem falha operacional ativa registrada.'
        details = f"\nCausa operacional: {operational_reason}"
        if pending_revalidation:
            details += f"\nRejeições anteriores aguardando revalidação: {pending_revalidation}. Nenhuma entrada autorizada por expiração da espera."
        if c.get('forex_multi_enabled'):
            rows = self._multi.rows if self._multi else {}
            portfolio = self._multi.portfolio if self._multi else None
            details += f"\nLimite simultâneo: {position_limit(c)} posições em símbolos diferentes."
            if portfolio:
                details += f"\nRisco reservado/global: {portfolio['reserved_risk']:.2f}/{portfolio['risk_limit']:.2f} {currency}"
                details += f"\nRisco USD comprado/vendido até SL: {portfolio['usd_risk']['LONG']:.2f}/{portfolio['usd_risk']['SHORT']:.2f} {currency} (sem compensação)."
            for item in c.get('forex_pairs', []):
                if item['enabled']:
                    updated = rows.get(item['pair'], {}).get('updated_at')
                    last_analysis = datetime.fromtimestamp(updated, timezone.utc).isoformat(timespec='seconds') if updated else 'aguardando primeira análise'
                    details += f"\nÚltima atualização {item['symbol']}: {last_analysis}"
        pending=sum(t.get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE for t in self.trade_history)
        return (f"ProspeTraderBot\nModo: {c['execution_mode']} | {'ATIVO' if self.is_running else 'PAUSADO'}\n"
                f"Conta: {account}\nAtivo(s): {monitored} / {c['timeframe']}\n"
                f"PnL realizado hoje (UTC): {self.stats['daily_pnl']:.2f} {currency}\n"
                f"PnL aberto: {self.stats.get('floating_pnl',0):.2f} {currency}\nPosições/intenção MT5 pendentes: {pending}\n"
                f"Monitoramento: {monitoring_label}{details}\nResultado das análises: {self.stats['status_reason']}\n"
                f"Status gerado em UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")

    def pause_from_telegram(self):
        self.is_running=False
        self.stats['status_reason']='Novas entradas automáticas pausadas pelo Telegram; posições abertas continuam monitoradas.'
        self.add_log('STOP',self.stats['status_reason'])

    def add_log(self, level: str, message: str):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.insert(0, entry)
        self.logs = self.logs[:80]

    def _notify(self, event_id, message):
        try:
            if self.notifier.enqueue(event_id,message) is False:
                raise RuntimeError('Notification queue unavailable')
            self._pending_notifications.pop(event_id,None)
            self._notification_failure=False
        except Exception:
            if len(self._pending_notifications)<100 or event_id in self._pending_notifications:
                self._pending_notifications[event_id]=message
            self.notifier.last_error='Notificação indisponível; monitoramento continua.'
            if not self._notification_failure:
                self.add_log('ALERT',self.notifier.last_error)
                self._notification_failure=True

    def _flush_notifications(self):
        for event_id,message in list(self._pending_notifications.items())[:10]:
            self._notify(event_id,message)
            if event_id in self._pending_notifications:
                break

    def _audit_state(self, healthy):
        if not healthy:
            self._fault('audit','DIAGNOSTIC_UNAVAILABLE',self.audit.error or 'Auditoria indisponível; novas entradas bloqueadas.')
        else:
            self._recover('audit')

    def _diagnostic_event(self, kind, code, payload, event_key=None, bar_time=None, context=None):
        context = context or self.config
        event=dict(event_key=event_key or uuid.uuid4().hex,kind=kind,code=code,
                   symbol=context['symbol'],timeframe=context['timeframe'],bar_time=bar_time,
                   payload=dict(mode=context['execution_mode'],account_login=context.get('mt5_account_login'),
                                account_server=context.get('mt5_account_server'),**payload))
        healthy=self.audit.record(event)
        if code!='DIAGNOSTIC_UNAVAILABLE':
            self._audit_state(healthy)
        return healthy

    def _fault(self, key, code, message, context=None):
        context = context or self.config
        fault,notify=self.recovery.fail(key,code,message)
        if notify:
            self.add_log('ERROR',message+' • monitoramento continua; entradas sujeitas aos bloqueios.')
            self._notify('fault:'+uuid.uuid4().hex,
                f"ALERTA {context['symbol']} / {context['timeframe']} [{code}]\n{message}\nMonitoramento continua. Novas entradas dependem de revalidação segura. Próxima tentativa em até 60s.")
        # One persistent event per error code/incident; repeated failures aggregate.
        self._diagnostic_event('ERROR',code,dict(component=key,**fault),
            digest([key,code,fault['first_seen'],context['symbol']]), context=context)
        return fault

    def _recover(self, key):
        old=self.recovery.clear(key)
        if old:
            preflight = key == 'preflight' or key.startswith('pair-preflight:')
            outcome = 'Rejeição anterior encerrada após revalidação.' if preflight else f'Componente {key} voltou a responder.'
            self.add_log('INFO',f"Recuperação: {key}. {outcome} Limites permanecem ativos.")
            self._diagnostic_event('RECOVERY',old['code'],dict(component=key,previous_fault=old))
            notification_key=('recovery',key,old['code'],self.config['symbol'])
            if time.time()-self.recovery.alerted.get(notification_key,0)>=300:
                self.recovery.alerted[notification_key]=time.time()
                self._notify('recovery:'+uuid.uuid4().hex,
                    f"RECUPERAÇÃO {self.config['symbol']} [{old['code']}]\n{outcome} Automação {'solicitada' if self.is_running else 'pausada pelo usuário'}; todos os filtros serão reavaliados.")

    def _runtime_block(self):
        if not self.audit.healthy:
            return self.audit.error
        if self._pending_trade_saves or self._intent_uncertain:
            return 'Persistência de operações pendente; novas entradas bloqueadas até recuperação.'
        if any(t.get('mode') == 'LIVE_MT5' and t.get('deal_time_warning') for t in self.trade_history):
            return 'Horário de negócios antigos não confirmado; confira o ajuste dos negócios antes de novas entradas.'
        if 'analysis' in self.recovery.faults:
            return 'Análise em recuperação; aguarde uma avaliação válida.'
        return self.recovery.retry_block()

    def operational_status(self):
        block=self._runtime_block()
        active, pending = self.recovery.presentation()
        # Include local incidents for visibility only. The coordinator retains
        # its existing per-pair backoff and global execution safety decisions.
        if self.config.get('forex_multi_enabled') and self._multi:
            for view in self._multi.views.values():
                local_active, local_pending = view.recovery.presentation()
                prefix = 'pair:'+view.config['symbol']+':'
                active.update({prefix+key: fault for key, fault in local_active.items()})
                pending.update({prefix+key: fault for key, fault in local_pending.items()})
        return dict(automation_requested=self.is_running,
                    state='PAUSED' if not self.is_running else ('RECOVERING' if block or active else 'MONITORING'),
                    blocked_reason=block,faults=active,pending_revalidation=pending,audit=self.audit.status(),
                    pending_trade_saves=len(self._pending_trade_saves),pending_notifications=len(self._pending_notifications))

    def _save_trade(self, trade):
        try:
            self.journal.save(trade)
        except Exception:
            self._pending_trade_saves[trade['id']]=trade
            self._fault('persistence','TRADE_PERSISTENCE','Não foi possível persistir a operação; estado reservado e novas entradas bloqueadas.')
            return False
        self._pending_trade_saves.pop(trade['id'],None)
        return True

    def _flush_trade_saves(self):
        for trade in list(self._pending_trade_saves.values()):
            if not self._save_trade(trade):
                return
        if self._intent_uncertain:
            self.trade_history=self._session_trades()
            self._has_open_trade=any((t.get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE) or t['status']=='OPEN' for t in self.trade_history)
            self._intent_uncertain=False
        self._recover('persistence')

    def update_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        clock_only = set(new_cfg) == {'mt5_deal_time_offset_minutes'}
        if self.is_running or self._mt5_busy or (self._has_open_trade and not clock_only):
            raise ValueError("Pause o robô e aguarde a operação encerrar antes de alterar parâmetros.")
        payload = dict(new_cfg)
        # Legacy dashboard balance is virtual only, never a broker balance override.
        if 'custom_balance' in payload:
            payload['paper_balance'] = payload.pop('custom_balance')
        merged = {**self.config, **payload}
        if 'strategy_params' in payload:
            merged['strategy_params'] = {**self.config['strategy_params'], **payload['strategy_params']}
        if merged.get('execution_mode')=='LIVE_MT5':
            if merged.get('market_type')=='SAFIRION_OB':
                raise ValueError('Selecione MT5 B3 ou Forex antes de ativar envio ao terminal.')
            account=self.live.account(self.config['mt5_account_login'],self.config['mt5_account_server'],trading=True) if self.config['execution_mode']=='LIVE_MT5' else self.live.account(trading=True)
            merged['mt5_account_login']=account['login']
            merged['mt5_account_server']=account['server']
        validated = TradingConfig.model_validate(merged).model_dump()
        if self.trade_history and validated['paper_balance'] != self.config['paper_balance']:
            raise ValueError("Banca inicial não pode mudar após a primeira operação da sessão.")
        changed_stream = any(validated[k] != self.config[k] for k in ('symbol', 'timeframe', 'market_type'))
        previous_mode=self.config['execution_mode']
        self.journal.save_config(validated)
        self.config = validated
        if not strategy_requires_ml(validated['strategy_id'],validated['strategy_params']):
            self._recover('model')
        self._multi = None
        self._last_snapshot = None
        self.mt5.configure_time(self.config)
        if previous_mode != validated['execution_mode'] or 'mt5_deal_time_offset_minutes' in payload:
            self.trade_history=self._session_trades()
            self._refresh_stats()
            self._last_signal_bar=None
        if changed_stream:
            for component in ('analysis','market_data','preflight'):
                self.recovery.clear(component)
            self._last_snapshot=None
            self.safirion.reset_candles()
            self._last_signal_bar = None
        self._diagnostic_event('CONFIG','CONFIG_UPDATED',dict(config=self.config,config_version=digest(self.config)))
        self.add_log("CONFIG", f"Parâmetros validados: {self.config['symbol']} / {self.config['timeframe']} / {self.config['execution_mode']}")
        return self.config

    def _calculate_smart_entry_amount(self, signal=None):
        live=self.config['execution_mode']=='LIVE_MT5'
        if live:
            try:
                account=self.live.account(self.config['mt5_account_login'],self.config['mt5_account_server'])
                balance=max(0.,account['equity'])
            except MT5ExecutionError as exc:
                return 0.,str(exc)
        else:
            balance = max(0.0, self.config['paper_balance'] + self.stats['daily_pnl'])
        cap = min(balance * self.config['risk_per_trade_pct'] / 100,
                  max(0.0, self.config['stop_loss'] + self.stats['daily_pnl']))
        amount = balance * self.config['risk_per_trade_pct'] / 100 if self.config['bankroll_mode'] == 'PERCENT' else self.config['entry_amount']
        if live and position_limit(self.config) > 1:
            cap = global_budget(self.config, balance, max(0., self.config['stop_loss']+self.stats['daily_pnl']))
            try:
                used = sum(reserved_risk(t) for t in active_trades(self.trade_history))
            except ValueError as exc:
                return 0., str(exc)
            amount = min(cap/position_limit(self.config), max(0., cap-used))
        amount = math.floor(min(amount, cap) * 100) / 100
        minimum = 5 if self.config['market_type'] == 'SAFIRION_OB' else .01
        if amount < minimum:
            return 0.0, "Bloqueado: mínimo excede o orçamento de risco."
        currency=self.mt5.account_info.get('currency','') if live else 'BRL virtual'
        if live and position_limit(self.config)>1:
            return amount, f"RISCO ATÉ SL {amount:.2f} {currency} • carteira até {cap:.2f} {currency} • orçamento compartilhado"
        return amount, f"{'RISCO ATÉ SL' if live else 'PAPER'} {amount:.2f} {currency} • limite {self.config['risk_per_trade_pct']}% • sem progressão"

    def _risk_block(self, signal=None, manual=False):
        if self._mt5_busy:
            return 'MT5 processando consulta/envio; aguarde a confirmação.'
        if position_limit(self.config) > 1:
            block=local_block(self.config, self.trade_history, self.config['symbol'])
            if block: return block
        elif self._has_open_trade:
            return "Já existe uma operação em andamento."
        runtime_block=self._runtime_block()
        if runtime_block:
            return runtime_block
        if self.config['execution_mode']=='LIVE_MT5':
            try:
                self.live.account(self.config['mt5_account_login'],self.config['mt5_account_server'],trading=True)
            except MT5ExecutionError as exc:
                return str(exc)
        if self.stats['daily_pnl'] <= -self.config['stop_loss']:
            return "Stop loss da sessão atingido."
        goal=self.goal_status()
        if not goal['ready']:
            return goal['error']
        if goal['reached']:
            return 'Meta diária atingida; novas entradas pausadas.'
        if self.stats['loss_streak'] >= self.config['max_consecutive_losses']:
            return "Limite de perdas consecutivas atingido."
        if self._entry_count() >= self.config['max_daily_trades']:
            return "Limite de operações da sessão atingido."
        if datetime.now().timestamp()-self._last_trade_timestamp < self.config['cooldown_seconds']:
            return "Intervalo mínimo entre operações ainda não cumprido."
        amount, reason = self._calculate_smart_entry_amount(signal)
        if amount <= 0:
            return reason
        if not manual and signal:
            if signal['action'] not in ('BUY', 'SELL'):
                return signal.get('reason') or "Sem confluência técnica suficiente."
            if signal['confidence'] < self.config['min_confidence']:
                return f"Score final {signal['confidence']:.1f}% abaixo do mínimo {self.config['min_confidence']:.1f}%."
            if signal.get('bar_time') == self._last_signal_bar:
                return "Este candle já originou uma operação."
            validation = signal.get('indicators', {}).get('ml_validation', {})
            if strategy_requires_ml(self.config['strategy_id'],self.config['strategy_params']) and not validation.get('validated'):
                return "IA sem vantagem demonstrada na validação temporal."
        return None

    def goal_status(self):
        account_currency=self.mt5.account_info.get('currency','') if self.config['execution_mode']=='LIVE_MT5' else 'BRL'
        currency='BRL' if self.config['goal_currency']=='BRL' else account_currency
        rate=1.;fx_symbol=None;error=None
        if currency!=account_currency:
            try:
                rate,fx_symbol=self.live.brl_rate(account_currency,self.config['goal_fx_symbol'])
            except MT5ExecutionError as exc:
                error=str(exc)
        realized=self.stats['daily_pnl']*rate if error is None else None
        remaining=max(0,self.config['stop_gain']-realized) if realized is not None else None
        return dict(target=self.config['stop_gain'],currency=currency,account_currency=account_currency,
                    realized=realized,remaining=remaining,remaining_account=remaining/rate if remaining is not None else None,
                    reached=realized is not None and realized>=self.config['stop_gain'],ready=error is None,error=error,
                    conversion_rate=rate if error is None else None,conversion_symbol=fx_symbol,
                    conversion_note='Conversão indicativa pelo preço médio bid/ask MT5; não é câmbio liquidado.' if fx_symbol else None)

    def execution_diagnostics(self):
        native=self.config['execution_mode']=='LIVE_MT5'
        permission_error=None
        try:
            self.live.account(self.config['mt5_account_login'] if native else None,
                              self.config['mt5_account_server'] if native else None,trading=True)
            permissions=True
        except MT5ExecutionError as exc:
            permissions=False;permission_error=str(exc)
        forex=self.config['market_type']!='SAFIRION_OB'
        if not native:
            reason='PAPER selecionado: nenhuma ordem será enviada ao MT5, mesmo com AlgoTrading habilitado no terminal.'
            if permission_error and self.mt5.connected and not self.mt5.simulation_mode:
                reason+=' Diagnóstico do terminal: '+permission_error
        else:
            reason='Envio nativo habilitado para a conta vinculada; entradas dependem dos filtros e limites.' if permissions else permission_error
        return dict(version='forex-price-exits-v1',mode=self.config['execution_mode'],market=self.config['market_type'],
                    exit_policy='SL_TP' if forex else 'BINARY_EXPIRATION',native_available=self.live.sdk is not None,
                    terminal_permissions=permissions,native_orders_enabled=native and permissions,
                    reason=reason,terminal_error=permission_error,
                    clock=self.live.clock_diagnostics(self.config['symbol'],self.config['timeframe']))

    def plan_preview(self, signal, entry=None):
        if self.config['market_type']=='SAFIRION_OB':
            return dict(ready=False,reason='Opções binárias usam expiração; plano Forex não se aplica.')
        try:
            goal=self.goal_status()
            if not goal['ready']:
                raise TradePlanError(goal['error'])
            plan=price_plan(self.config,signal,float(entry if entry is not None else signal['price']))
            budget,_=self._calculate_smart_entry_amount(signal)
            budget=goal_limited_budget(self.config,budget,goal['remaining_account'],plan['reward_risk'])
            cost=self.config['mt5_cost_buffer'] if self.config['execution_mode']=='LIVE_MT5' else self.config['paper_cost_per_trade']
            if budget<=cost:
                raise TradePlanError('Orçamento insuficiente após custos para outra entrada.')
            quantity=(budget-cost)/plan['risk_distance']
            profit=quantity*plan['reward_distance']-cost
            net_rr=profit/budget
            if net_rr+1e-8<self.config['strategy_params']['min_reward_risk']:
                raise TradePlanError('Retorno/risco insuficiente após custos.')
            return {**plan,'risk_budget':budget,'estimated_loss':budget,'estimated_profit':profit,
                    'net_reward_risk':net_rr,'paper_quantity':quantity,
                    'note':'Prévia indicativa; execução nativa revalida bid/ask, lote, margem, custos e regras do símbolo.'}
        except (TradePlanError,ValueError) as exc:
            return dict(ready=False,reason=str(exc),exit_policy='SL_TP')

    def _entry_count(self):
        day=datetime.now(timezone.utc).date().isoformat()
        return sum(t.get('mode','PAPER')==self.config['execution_mode'] and t.get('day')==day and t['status']!='REJECTED' for t in self.trade_history)

    def get_market_snapshot(self) -> Dict[str, Any]:
        if self.config.get('forex_multi_enabled') and not getattr(self, '_pair_analysis', False):
            from backend.forex_multi import ForexCoordinator
            if self._multi is None:
                self._multi = ForexCoordinator(self)
            return self._multi.snapshot()
        fault=self.recovery.faults.get('analysis')
        if fault and time.time()<fault['retry_at'] and not self._mt5_busy:
            return self._unavailable_snapshot(fault['message'])
        try:
            return self._build_market_snapshot()
        except Exception as exc:
            note=str(exc) if isinstance(exc,(MT5ExecutionError,ValueError,RuntimeError)) else 'Falha interna na análise; consulte os diagnósticos.'
            self._fault('analysis','ANALYSIS_UNAVAILABLE',note)
            self.stats['status_reason']='Análise em recuperação; novas entradas bloqueadas. '+note
            return self._unavailable_snapshot(note)

    def _unavailable_snapshot(self, reason):
        old=self._last_snapshot or {}
        currency=self.mt5.account_info.get('currency','') if self.config['execution_mode']=='LIVE_MT5' else 'BRL'
        signal=dict(symbol=self.config['symbol'],market_type=self.config['market_type'],timeframe=self.config['timeframe'],
                    strategy_id=self.config['strategy_id'],action='NEUTRAL',confidence=0,price=0,stop_loss=None,take_profit=None,
                    expiration_minutes=None,indicators={},bar_time=None,reason=reason)
        goal=dict(target=self.config['stop_gain'],currency=currency,account_currency=currency,realized=None,remaining=None,
                  remaining_account=None,reached=False,ready=False,error=reason)
        sf=self.safirion
        connections=dict(mt5=dict(connected=self.mt5.connected,simulation_mode=self.mt5.simulation_mode,account=self.mt5.account_info),
            safirion=dict(connected=sf.connected,simulation_mode=sf.simulation_mode,broker_url=sf.broker_url,account_type=sf.account_type,
                sub_type=self.config['safirion_sub_type'],call_bound=sf.call_bound,put_bound=sf.put_bound,
                ws_messages_received=sf.ws_messages_received,last_live_price=sf.last_live_price,balance=sf.balance))
        return {**old,'config':self.config,'is_running':self.is_running,'stats':self.stats,'currency':currency,
                'execution_mode':self.config['execution_mode'],'has_open_trade':self._has_open_trade,'signal':signal,
                'candles':[],'trade_plans':{},'connections':connections,'telegram':self.notifier.public_status(),
                'trades':self.trade_history[:25],'logs':self.logs[:30],'goal':goal,'entry_count':self._entry_count(),
                'account_balance':self.mt5.account_info.get('balance',0) if self.config['execution_mode']=='LIVE_MT5' else self.config['paper_balance'],
                'paper_balance':self.config['paper_balance'],'operational':self.operational_status(),
                'risk':dict(blocked_reason=reason,max_risk_pct=self.config['risk_per_trade_pct'],remaining_loss_budget=max(0,self.config['stop_loss']+self.stats['daily_pnl']),next_amount=0),
                'data_quality':dict(source='ANALYSIS_UNAVAILABLE',closed_candles=0,synthetic=False,symbol_verified=False,
                    forming_candle_excluded=True,dropped_gap_candles=0,history_fresh=False,history_error=reason,tick_age_seconds=None),
                'diagnostics':dict(version='forex-price-exits-v1',mode=self.config['execution_mode'],reason=reason,
                    terminal_permissions=False,native_orders_enabled=False,clock={})}

    def _build_market_snapshot(self) -> Dict[str, Any]:
        if self._mt5_busy and self._last_snapshot is None:
            raise RuntimeError('Conexão MT5 em andamento; aguarde a primeira atualização.')
        if self._mt5_busy and self._last_snapshot is not None:
            return {**self._last_snapshot,'is_running':self.is_running,'has_open_trade':True,
                    'operational':self.operational_status(),
                    'stats':dict(self.stats),'trades':self.trade_history[:25], 'telegram':self.notifier.public_status(),
                    'risk':{**self._last_snapshot['risk'],'blocked_reason':'MT5 processando; nenhuma segunda ordem será enviada.'}}
        day = datetime.now(timezone.utc).date().isoformat()
        if day != self._session_day and not self._has_open_trade:
            self._session_day = day
            self.trade_history = self._session_trades()
            self._refresh_stats()
            self._last_signal_bar = None
            self.add_log('INFO', 'Nova sessão UTC; histórico anterior preservado no SQLite.')
        symbol = self.config["symbol"]
        timeframe = self.config["timeframe"]
        market_type = self.config["market_type"]
        strategy_id = self.config["strategy_id"]

        history_error = None
        history_retry_wait = False
        saf_live_df = (
            self.safirion.get_live_candles_df(timeframe)
            if market_type == "SAFIRION_OB"
            else None
        )
        live_safirion = market_type == 'SAFIRION_OB' and (self.safirion.bridge_ws is not None or bool(self.safirion._live_candles))
        if live_safirion:
            df = saf_live_df if saf_live_df is not None else pd.DataFrame(columns=['time','open','high','low','close','volume'])
            source = 'SAFIRION_LIVE'
        else:
            if self.config['execution_mode']=='LIVE_MT5' and (self.mt5.simulation_mode or not self.mt5.connected):
                df=pd.DataFrame(columns=['time','open','high','low','close','volume'])
                source='MT5_UNAVAILABLE'
            else:
                history_fault=self.recovery.faults.get('market_data')
                if history_fault and time.time()<history_fault['retry_at']:
                    history_retry_wait=True
                    history_error=history_fault['message']
                    df=pd.DataFrame(columns=['time','open','high','low','close','volume'])
                    source='MT5_UNAVAILABLE'
                else:
                    try:
                        df = self.mt5.get_candles(symbol, timeframe, count=240)
                        source = 'SIMULATED' if self.mt5.simulation_mode else 'MT5_LIVE'
                    except RuntimeError as exc:
                        history_error = str(exc)
                        if self.config['execution_mode']!='LIVE_MT5':
                            raise
                        df=pd.DataFrame(columns=['time','open','high','low','close','volume'])
                        source='MT5_UNAVAILABLE'
        validate_candles(df)
        # The last candle is forming, so neither its signal nor label is usable.
        closed = df.iloc[:-1].copy()
        if df.empty:
            df = pd.DataFrame(columns=['time','open','high','low','close','volume'])
            closed = df.copy()

        # A market gap invalidates the old prefix, not every future candle.
        dropped_gap_candles = 0
        if len(closed) > 1:
            expected = self.mt5.TIMEFRAME_MAP.get(timeframe, 1)*60
            gaps = closed['time'].diff().dt.total_seconds().iloc[1:] != expected
            if gaps.any():
                dropped_gap_candles = int(gaps.to_numpy().nonzero()[0][-1]) + 1
                closed = closed.iloc[dropped_gap_candles:].copy()
        enriched = StrategyEngine.enrich_dataframe(df, self.config["strategy_params"])
        signal: SignalResult = StrategyEngine.analyze(
            df=closed,
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
            strategy_id=strategy_id,
            model_engine=getattr(self, "_model_engine", None),
            model_context=getattr(self, "_model_context", None),
            params={
                **self.config["strategy_params"],
                "expiration_minutes": self.config["expiration_minutes"],
                "data_source": source,
            },
        )
        self.last_signal = signal.to_dict()
        validation=self.last_signal.get('indicators',{}).get('ml_validation',{})
        model_reason=validation.get('reason','')
        if model_reason.startswith(('Modelo indisponível','scikit-learn indisponível')) and strategy_requires_ml(self.config['strategy_id'],self.config['strategy_params']):
            self._fault('model','MODEL_UNAVAILABLE',model_reason)
        elif validation.get('enabled') or not strategy_requires_ml(self.config['strategy_id'],self.config['strategy_params']):
            self._recover('model')
        if history_error:
            self.last_signal.update(action='NEUTRAL',confidence=0,reason=history_error)
        self.last_signal['bar_time'] = str(closed.iloc[-1]['time']) if len(closed) else None
        self.last_signal['price'] = float(df.iloc[-1]['close']) if len(df) else 0.0
        tick_age = max(0, datetime.now().timestamp()-self.safirion.last_tick_timestamp) if live_safirion else None
        if live_safirion and (tick_age is None or tick_age > self.config['max_tick_age_seconds']):
            self.last_signal.update(action='NEUTRAL', confidence=0, reason='Dados Safirion desatualizados: aguardando cotação válida.')
        data_quality = dict(source=source, closed_candles=len(closed), tick_age_seconds=round(tick_age, 1) if tick_age is not None else None, symbol_verified=(not live_safirion or self.safirion.symbol == symbol.upper().replace('/', '').replace('-', '')),
                            forming_candle_excluded=True, dropped_gap_candles=dropped_gap_candles, synthetic=source == 'SIMULATED')


        data_quality['history_error']=history_error
        data_quality['history_fresh']=source!='MT5_UNAVAILABLE'
        if source=='MT5_LIVE':
            latest=df.iloc[-1]['time'] if len(df) else None
            latest_epoch=(latest.tz_localize('UTC').timestamp() if latest.tzinfo is None else latest.timestamp()) if latest is not None else 0
            history_age=datetime.now().timestamp()-latest_epoch
            data_quality['history_fresh']=-5 <= history_age <= self.mt5.TIMEFRAME_MAP.get(timeframe,1)*120
            if not data_quality['history_fresh']:
                self.last_signal.update(action='NEUTRAL',confidence=0,reason='Histórico MT5 antigo ou inconsistente com a cotação atual.')
            data_quality['tick_age_seconds']=self.live.quote_age(symbol)
            age=data_quality['tick_age_seconds']
            if age is None or age>self.config['max_tick_age_seconds'] or age < -5:
                self.last_signal.update(action='NEUTRAL',confidence=0,reason='Cotação MT5 desatualizada ou indisponível.')
        if not data_quality['symbol_verified']:
            self.last_signal.update(action='NEUTRAL', confidence=0, reason='Ativo da ponte não corresponde ao ativo selecionado.')
        # Atualiza sugestão de lote em tempo real pela Gestão de Banca IA
        suggested_amt, sizing_reason = self._calculate_smart_entry_amount(self.last_signal)
        self.stats["next_suggested_amount"] = suggested_amt
        self.stats["sizing_reason"] = sizing_reason

        candles_out = []
        for _, row in enriched.tail(60).iterrows():
            candles_out.append(
                {
                    "time": int(row["time"].tz_localize("UTC").timestamp() if source=="MT5_LIVE" and row["time"].tzinfo is None else row["time"].timestamp()),
                    "time_str": row["time"].strftime("%H:%M"),
                    "open": round(float(row["open"]), 5),
                    "high": round(float(row["high"]), 5),
                    "low": round(float(row["low"]), 5),
                    "close": round(float(row["close"]), 5),
                    "ema_fast": round(float(row["ema_fast"]), 5),
                    "ema_slow": round(float(row["ema_slow"]), 5),
                    "bb_upper": round(float(row["bb_upper"]), 5),
                    "bb_lower": round(float(row["bb_lower"]), 5),
                    "rsi": round(float(row["rsi"]), 2),
                }
            )

        self._recover('analysis')
        if history_error and not history_retry_wait:
            self._fault('market_data','HISTORY_UNAVAILABLE',history_error)
        elif source=='MT5_LIVE':
            self._recover('market_data')
        snapshot = {
            "entry_count": self._entry_count(),
            "operational": self.operational_status(),
            "goal": self.goal_status(),
            "diagnostics": self.execution_diagnostics(),
            "trade_plans": {side:self.plan_preview({**self.last_signal,'action':side}) for side in ('BUY','SELL')} if market_type!='SAFIRION_OB' else {},
            "execution_mode": self.config['execution_mode'],
            "has_open_trade": self._has_open_trade,
            "currency": self.mt5.account_info.get('currency','') if self.config['execution_mode']=='LIVE_MT5' else 'BRL',
            "account_balance": self.mt5.account_info.get('balance',0) if self.config['execution_mode']=='LIVE_MT5' else round(self.config['paper_balance']+self.stats['daily_pnl'],2),
            "telegram": self.notifier.public_status(),
            "paper_balance": round(self.config['paper_balance'] + self.stats['daily_pnl'], 2),
            "data_quality": data_quality,
            "risk": {"blocked_reason": self._risk_block(self.last_signal), "max_risk_pct": self.config['risk_per_trade_pct'],
                     "remaining_loss_budget": max(0, self.config['stop_loss']+self.stats['daily_pnl']),
                     "next_amount": suggested_amt},
            "is_running": self.is_running,
            "config": self.config,
            "stats": self.stats,
            "signal": self.last_signal,
            "candles": candles_out,
            "trades": self.trade_history[:25],
            "logs": self.logs[:30],
            "connections": {
                "mt5": {
                    "connected": self.mt5.connected,
                    "simulation_mode": self.mt5.simulation_mode,
                    "account": self.mt5.account_info,
                },
                "safirion": {
                    "connected": self.safirion.connected,
                    "simulation_mode": self.safirion.simulation_mode,
                    "broker_url": self.safirion.broker_url,
                    "account_type": self.safirion.account_type,
                    "sub_type": self.config.get("safirion_sub_type", self.safirion.sub_type),
                    "call_bound": self.safirion.call_bound,
                    "put_bound": self.safirion.put_bound,
                    "ws_messages_received": self.safirion.ws_messages_received,
                    "last_live_price": self.safirion.last_live_price,
                    "balance": round(float(self.safirion.balance), 2),
                },
            },
        }

        event=analysis_event(snapshot,self._last_signal_bar,self._last_trade_timestamp)
        self._audit_state(self.audit.record(event))
        snapshot['entry_checks']=event['payload']['checks']
        snapshot['failed_checks']=event['payload']['failed_checks']
        snapshot['analysis_id']=event['payload']['analysis_id']
        snapshot['signal']['analysis_id']=snapshot['analysis_id']
        snapshot['operational']=self.operational_status()
        if not self.audit.healthy:
            snapshot['risk']['blocked_reason']=self.audit.error
        self._last_snapshot=snapshot
        return snapshot

    def _calculate_target_expiration_time(self) -> tuple[datetime, int]:
        from datetime import timedelta

        now = datetime.now()
        market_type = self.config["market_type"]
        sub_type = self.config.get("safirion_sub_type", "BINARY")
        exp_min = max(1, int(self.config.get("expiration_minutes", 1)))

        if market_type == "SAFIRION_OB" and sub_type == "BLITZ":
            target_dt = now + timedelta(seconds=15)
            return target_dt, 15

        next_minute_boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        seconds_to_next_min = (next_minute_boundary - now).total_seconds()

        if exp_min == 1:
            if seconds_to_next_min >= 30:
                target_dt = next_minute_boundary
            else:
                target_dt = next_minute_boundary + timedelta(minutes=1)
        else:
            target_dt = now.replace(second=0, microsecond=0) + timedelta(minutes=exp_min)

        remaining_sec = max(5, int(round((target_dt - now).total_seconds())))
        return target_dt, remaining_sec

    async def execute_trade_from_signal(self, signal: Dict[str, Any], manual: bool = False) -> Dict[str, Any]:
        async with self._order_lock:
            if self.config.get('forex_multi_enabled'):
                return dict(success=False,message='Motor multipares usa apenas execução automática pelo coordenador.')
            if signal.get('action') not in ('BUY', 'SELL'):
                return {"success": False, "message": "Direção inválida ou neutra."}
            blocked = self._risk_block(signal, manual)
            if blocked:
                return {"success": False, "message": blocked}
            snapshot = self.get_market_snapshot()
            if self.config['execution_mode']=='LIVE_MT5':
                return await self._execute_mt5(signal, snapshot, manual)
            if self.config['market_type']!='SAFIRION_OB':
                return await self._open_paper_position(signal,snapshot)
            quality = snapshot['data_quality']
            if quality['source'] == 'SAFIRION_LIVE':
                if not quality['symbol_verified'] or quality['closed_candles'] < 50 or quality['tick_age_seconds'] > self.config['max_tick_age_seconds']:
                    return {"success": False, "message": "Histórico real insuficiente ou cotação desatualizada."}
            price = snapshot['signal']['price']
            if not math.isfinite(price) or price <= 0:
                return {"success": False, "message": "Preço inválido."}
            amount, sizing = self._calculate_smart_entry_amount(signal)
            target_dt, duration = self._calculate_target_expiration_time()
            trade = dict(id='PAPER-'+uuid.uuid4().hex, day=datetime.now(timezone.utc).date().isoformat(),
                         time=datetime.now().strftime('%H:%M:%S'), created_at=datetime.now().timestamp(), expiration_time=target_dt.strftime('%H:%M:%S'),
                         symbol=self.config['symbol'], market=self.config['market_type'], action=signal['action'],
                         amount=amount, sizing_reason=sizing, entry_price=price, confidence=signal['confidence'],
                         strategy=self.config['strategy_id'], status='OPEN', remaining_sec=duration, pnl=0.0,
                         mode='PAPER', source=quality['source'], timeframe=self.config['timeframe'],
                         payout_pct=self.config['payout_pct'], stop_loss=signal.get('stop_loss'),
                         take_profit=signal.get('take_profit'), bar_time=signal.get('bar_time'),
                         validation=signal.get('indicators', {}).get('ml_validation', {}))
            self.journal.save(trade)
            self._has_open_trade = True
            self._last_trade_timestamp = datetime.now().timestamp()
            self._last_signal_bar = signal.get('bar_time')
            self.trade_history.insert(0, trade)
            task = asyncio.create_task(self._settle_trade_after_real_expiration(trade, target_dt))
            self._settlement_tasks.add(task)
            task.add_done_callback(self._settlement_tasks.discard)
            self.add_log('TRADE', f"PAPER {trade['action']} {trade['symbol']} • R$ {amount:.2f} • {quality['source']}")
            return dict(success=True, ticket=trade['id'], mode='PAPER', message='Ordem virtual registrada; nenhum envio à corretora.')

    def _paper_position_quote(self, trade):
        source='SIMULATED' if self.mt5.simulation_mode else 'MT5_LIVE'
        if source!=trade['source'] or not self.mt5.connected:
            raise ValueError('Fonte da posição PAPER indisponível ou alterada; aguardando a mesma fonte.')
        if source=='MT5_LIVE':
            self.live.account(trade.get('account_login'),trade.get('account_server'))
            tick=self.live._tick(trade['symbol'],self.config)
            return float(tick.bid if trade['action']=='BUY' else tick.ask)
        raw=self.mt5.get_candles(trade['symbol'],trade['timeframe'],count=240)
        price=float(raw['close'].iloc[-1])
        if not math.isfinite(price) or price<=0:
            raise ValueError('Preço inválido na posição PAPER.')
        return price

    async def _open_paper_position(self, signal, snapshot):
        entry=float(snapshot['signal']['price'])
        if snapshot['data_quality']['source']=='MT5_LIVE':
            if not snapshot['data_quality'].get('history_fresh',False):
                return dict(success=False,message='Histórico MT5 desatualizado; aguarde velas atuais para a entrada PAPER.')
            try:
                self.live.account()
                tick=self.live._tick(self.config['symbol'],self.config)
                entry=float(tick.ask if signal['action']=='BUY' else tick.bid)
            except MT5ExecutionError as exc:
                return dict(success=False,message=str(exc))
        plan=self.plan_preview(signal,entry)
        if not plan['ready']:
            return dict(success=False,message=plan['reason'])
        trade=dict(id='PAPER-'+uuid.uuid4().hex,day=datetime.now(timezone.utc).date().isoformat(),
                   time=datetime.now().strftime('%H:%M:%S'),created_at=datetime.now().timestamp(),
                   symbol=self.config['symbol'],market=self.config['market_type'],action=signal['action'],
                   mode='PAPER',source=snapshot['data_quality']['source'],status='OPEN',exit_policy='SL_TP',
                   expiration_time=None,remaining_sec=None,pnl=0.,amount=plan['risk_budget'],currency='BRL',
                   entry_price=entry,stop_loss=plan['stop_loss'],take_profit=plan['take_profit'],
                   quantity=plan['paper_quantity'],cost=self.config['paper_cost_per_trade'],plan=plan,
                   timeframe=self.config['timeframe'],strategy=self.config['strategy_id'],confidence=signal['confidence'],
                   bar_time=signal.get('bar_time'),sync_note='PAPER por preço: aguarda SL, TP ou encerramento manual; sem expiração.')
        if trade['source']=='MT5_LIVE':
            account=self.live.account()
            trade.update(account_login=account['login'],account_server=account['server'])
        self.journal.save(trade);self.trade_history.insert(0,trade)
        self._has_open_trade=True;self._last_signal_bar=signal.get('bar_time');self._last_trade_timestamp=trade['created_at']
        self._track_paper_position(trade)
        self.add_log('TRADE',f"PAPER {trade['action']} {trade['symbol']} • SL {trade['stop_loss']} • TP {trade['take_profit']} • sem expiração")
        return dict(success=True,ticket=trade['id'],mode='PAPER',message='Posição PAPER aberta com SL/TP por preço, sem expiração.')

    def _track_paper_position(self, trade):
        task=asyncio.create_task(self._monitor_paper_position(trade))
        self._settlement_tasks.add(task);task.add_done_callback(self._settlement_tasks.discard)

    def _finalize_paper_position(self, trade, price, reason):
        if trade['status']!='OPEN':
            return
        direction=1 if trade['action']=='BUY' else -1
        pnl=(price-trade['entry_price'])*trade['quantity']*direction-trade['cost']
        trade.update(status='WIN' if pnl>0 else ('LOSS' if pnl<0 else 'DRAW'),exit_price=price,pnl=round(pnl,8),
                     settled_at=datetime.now().timestamp(),exit_reason=reason,floating_pnl=0.,
                     settlement_note='PAPER: preenchimento no preço observado, incluindo gaps e custo configurado; não é execução da corretora.')
        self._save_trade(trade);self._refresh_stats()
        self._has_open_trade=any(t['status']=='OPEN' for t in self.trade_history) or any(t.get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE for t in self.trade_history)
        self._last_trade_timestamp=datetime.now().timestamp()
        self.add_log('INFO',f"PAPER {reason}: {pnl:.2f} BRL virtuais")
        if self.goal_status()['reached']:
            self.stats['status_reason']='Meta diária atingida; novas entradas bloqueadas, monitoramento ativo.'

    async def _monitor_paper_position(self, trade):
        while trade['status']=='OPEN':
            try:
                if self._mt5_busy:
                    await asyncio.sleep(.5);continue
                price=self._paper_position_quote(trade)
                self._recover('paper_quote')
                buy=trade['action']=='BUY'
                reason='STOP_LOSS' if (price<=trade['stop_loss'] if buy else price>=trade['stop_loss']) else (
                    'TAKE_PROFIT' if (price>=trade['take_profit'] if buy else price<=trade['take_profit']) else None)
                async with self._order_lock:
                    if reason:
                        self._finalize_paper_position(trade,price,reason)
                    elif trade['status']=='OPEN':
                        trade['floating_pnl']=(price-trade['entry_price'])*trade['quantity']*(1 if buy else -1)-trade['cost']
                        self.stats['floating_pnl']=trade['floating_pnl']
                        trade.pop('sync_error',None)
            except asyncio.CancelledError:
                raise
            except Exception:
                trade['sync_error']='Cotação indisponível. Posição PAPER permanece aberta; resultado não estimado.'
                self._fault('paper_quote','PAPER_QUOTE_UNAVAILABLE',trade['sync_error'])
            await asyncio.sleep(.5)

    async def close_paper_position(self, identifier):
        async with self._order_lock:
            trade=next((t for t in self.trade_history if t['id']==identifier),None)
            if trade is None or trade.get('mode')!='PAPER' or trade.get('exit_policy')!='SL_TP' or trade['status']!='OPEN':
                raise ValueError('Somente posições PAPER Forex/B3 abertas podem ser encerradas por esta ação.')
            price=self._paper_position_quote(trade)
            self._finalize_paper_position(trade,price,'MANUAL')
            return dict(success=True,message='Posição PAPER encerrada pelo preço observado.',trade=trade)

    @staticmethod
    def _preflight_code(reason):
        return preflight_code(reason)

    SDK_WAIT_SECONDS = 15

    async def _native_call(self, function, *args, **kwargs):
        # A timeout is only an alert: the original SDK call keeps ownership.
        # Never submit a second native call or retry an order on timeout.
        task=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(task),timeout=self.SDK_WAIT_SECONDS)
                except asyncio.TimeoutError:
                    if task.done():
                        return task.result()
                    self._fault('sdk','SDK_SLOW','MT5 demorando a responder. Solicitação original em acompanhamento; nenhum reenvio será realizado.')
        finally:
            if task.done():
                self._recover('sdk')

    async def _execute_mt5(self, signal, snapshot, manual=False):
        config = snapshot.get('config', self.config)
        preflight_key = 'pair-preflight:'+config['symbol'] if config.get('forex_multi_enabled') else 'preflight'
        if snapshot['data_quality']['source']!='MT5_LIVE' or not snapshot['data_quality'].get('history_fresh',False):
            return dict(success=False,message='Sem dados reais do MT5. Fallback para simulador não é permitido neste modo.')
        client_tag='PT'+uuid.uuid4().hex[:22]
        # Every side, including a discretionary SELL after a BUY signal, gets fresh directional stops.
        self._mt5_busy=True
        try:
            kwargs={'portfolio_trades':context(self.trade_history)} if position_limit(config)>1 else {}
            prepared=await self._native_call(self.live.prepare,config,signal,max(0,config['stop_loss']+self.stats['daily_pnl']),client_tag,self.goal_status()['remaining_account'],**kwargs)
        except Exception as exc:
            note=str(exc) if isinstance(exc,MT5ExecutionError) else 'Falha na pré-validação do terminal MT5. Nenhuma ordem enviada.'
            code=self._preflight_code(note)
            global_failure = not isinstance(exc, MT5ExecutionError) or code in ('ACCOUNT_PERMISSIONS','ACCOUNT_BINDING','EXTERNAL_EXPOSURE') or 'exposição global' in note
            self._fault('preflight' if global_failure else preflight_key,code,note,context=config)
            self._diagnostic_event('DECISION',code,dict(attempt_id=client_tag,analysis_id=signal.get('analysis_id') or snapshot.get('analysis_id'),action=signal['action'],input_signal=signal,reason=note,checks=[dict(code='NATIVE_PREFLIGHT',status='FAIL',reason=note)]),bar_time=signal.get('bar_time'), context=config)
            return dict(success=False,message=note)
        finally:
            self._mt5_busy=False
        if not manual and not self.is_running:
            return dict(success=False,message='Entrada automática cancelada: robô pausado durante a pré-validação.')
        if self.recovery.faults.get(preflight_key,{}).get('code')!='BROKER_REJECTED':
            self._recover(preflight_key)
        account=prepared['account']
        trade=dict(id='MT5-'+uuid.uuid4().hex,client_tag=client_tag,magic=config['mt5_magic'],
                   day=datetime.now(timezone.utc).date().isoformat(),created_at=datetime.now().timestamp(),
                   time=datetime.now().strftime('%H:%M:%S'),symbol=config['symbol'],market=config['market_type'],
                   forex_multi=bool(config.get('forex_multi_enabled')),
                   mode='LIVE_MT5',source='MT5_LIVE',status='SUBMITTING',pnl=0.,remaining_sec=0,
                   action=signal['action'],amount=prepared['estimated_risk'],risk_budget=prepared['risk_budget'],
                   exit_policy='SL_TP',plan=prepared['plan'],
                   entry_diagnostics=prepared['entry_diagnostics'],signal_snapshot=copy.deepcopy(signal),
                   plan_parameters=copy.deepcopy(config['strategy_params']),
                   entry_policy_version=signal.get('indicators',{}).get('entry_policy_version','legacy'),
                   requested_volume=prepared['volume'],entry_price=prepared['price'],stop_loss=prepared['stop_loss'],
                   take_profit=prepared['take_profit'],account_login=account['login'],account_server=account['server'],
                   currency=account['currency'],account_type=account['account_type'],request=prepared['request'],
                   strategy=config['strategy_id'],timeframe=config['timeframe'],confidence=signal['confidence'],
                   analysis_id=signal.get('analysis_id') or snapshot.get('analysis_id'),bar_time=signal.get('bar_time'),manual=manual)
        if position_limit(config)>1:
            trade.update(max_open_positions=position_limit(config),reserved_risk=prepared['estimated_risk'],
                cost_buffer=config['mt5_cost_buffer'],portfolio=prepared['portfolio'],
                portfolio_context=prepared['portfolio_context'],portfolio_config=config.copy(),
                remaining_loss_budget=max(0,config['stop_loss']+self.stats['daily_pnl']))
        self._diagnostic_event('DECISION','PREFLIGHT_APPROVED',dict(attempt_id=client_tag,analysis_id=signal.get('analysis_id') or snapshot.get('analysis_id'),action=signal['action'],input_signal=signal,checks=[dict(code='NATIVE_PREFLIGHT',status='PASS')],plan=prepared['plan'],entry_diagnostics=prepared['entry_diagnostics'],risk_budget=prepared['risk_budget'],volume=prepared['volume']),bar_time=signal.get('bar_time'), context=config)
        if not self.audit.healthy:
            return dict(success=False,message=self.audit.error)
        try:
            self.journal.reserve_live(trade)
        except ValueError as exc:
            self._has_open_trade=True
            self.trade_history=self._session_trades()
            return dict(success=False,message=str(exc))
        except Exception:
            self._intent_uncertain=True
            self._has_open_trade=True
            self._fault('persistence','INTENT_PERSISTENCE','Falha ao reservar intenção no diário; nenhuma ordem enviada.')
            return dict(success=False,message='Intenção não confirmada no diário; envio bloqueado.')
        self.trade_history.insert(0,trade)
        self._has_open_trade=True
        self._last_signal_bar=signal.get('bar_time')
        self._last_trade_timestamp=trade['created_at']
        self._mt5_busy=True
        try:
            result=await self._native_call(self.live.send,trade)
        except Exception:
            # An intent may have reached MT5 even if the local invocation failed.
            result=dict(status='PENDING',success=False,pending=True,message='Envio inconclusivo; aguarde reconciliação no MT5, sem repetir a entrada.')
        finally:
            self._mt5_busy=False
        trade['submission_confirmed_at'] = time.time()
        trade.update({k:v for k,v in result.items() if k in ('status','retcode','order_ticket','deal_ticket')})
        trade['sync_note']=result['message']
        if result.get('success'):
            self._recover(preflight_key)
            if config.get('forex_multi_enabled'):
                self._recover('preflight')
        self._save_trade(trade)
        self._diagnostic_event('EXECUTION',trade['status'],dict(attempt_id=trade.get('client_tag'),trade_id=trade['id'],analysis_id=trade.get('analysis_id'),retcode=trade.get('retcode'),reason=trade['sync_note']),bar_time=trade.get('bar_time'), context=config)
        self._has_open_trade=any(t.get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE for t in self.trade_history)
        if trade['status']=='REJECTED':
            self._fault(preflight_key,'BROKER_REJECTED',trade['sync_note'],context=config)
        self.add_log('TRADE' if result.get('success') else 'ALERT',result['message'])
        self._notify(trade['id']+':submission',f"MT5 {trade['account_type']} {trade['action']} {trade['symbol']}\nConta {trade['account_login']} / {trade['account_server']}\nVolume solicitado: {trade['requested_volume']} | SL {trade['stop_loss']} | TP {trade['take_profit']}\n{result['message']}")
        return {**result,'mode':'LIVE_MT5','ticket':trade['id']}

    async def reconcile_live(self):
        if self._mt5_busy:
            return
        for trade in list(self.trade_history):
            if trade.get('mode')!='LIVE_MT5' or trade['status'] not in ACTIVE_LIVE:
                continue
            old_status=trade['status']
            self._mt5_busy=True
            try:
                update=await self._native_call(self.live.reconcile,trade)
                trade.update(update)
                trade['last_reconciled_at'] = time.time()
                if trade['status']=='OPEN':
                    observe_exit_quote(trade,update.get('observed_exit_quote'),trade['last_reconciled_at'],'MT5_POSITION_POLL')
                if trade['status'] in ('WIN','LOSS','DRAW') and trade['status'] != old_status:
                    trade['closure_confirmed_at'] = trade['last_reconciled_at']
                    observe_exit_quote(trade,update.get('exit_price'),trade['last_reconciled_at'],'MT5_CONFIRMED_EXIT')
                trade.pop('sync_error',None)
                if trade['status']=='OPEN' and not trade.get('protected',False):
                    self._fault('position:'+trade['id'],'UNPROTECTED_POSITION',trade['sync_note'])
                else:
                    self._recover('position:'+trade['id'])
                self._recover('sync:'+trade['id'])
            except Exception as exc:
                note=str(exc) if isinstance(exc,MT5ExecutionError) else 'Falha ao consultar o terminal MT5; novas entradas bloqueadas até reconciliação.'
                self._fault('sync:'+trade['id'],'RECONCILIATION_UNAVAILABLE',note)
                if trade.get('sync_error')!=note:
                    self.add_log('ERROR',note)
                    self._notify(trade['id']+':sync:'+hashlib.sha256(note.encode()).hexdigest()[:12],'ALERTA MT5: '+note+' Posição não foi fechada pelo robô; monitoramento será retomado quando a conexão correta voltar.')
                trade['sync_error']=note
            finally:
                self._mt5_busy=False
            self._save_trade(trade)
            if trade['status']!=old_status:
                self._diagnostic_event('RECONCILIATION',trade['status'],dict(attempt_id=trade.get('client_tag'),trade_id=trade['id'],analysis_id=trade.get('analysis_id'),reason=trade.get('sync_note'),position_id=trade.get('position_id'),settled_at=trade.get('settled_at'),closure_confirmed_at=trade.get('closure_confirmed_at'),deal_time_offset_seconds=trade.get('deal_time_offset_seconds')),bar_time=trade.get('bar_time'))
                if trade['status'] in ('WIN','LOSS','DRAW'):
                    self._last_trade_timestamp=datetime.now().timestamp()
                    closed = datetime.fromtimestamp(trade['settled_at'], timezone.utc).isoformat(timespec='seconds')
                    confirmed = datetime.fromtimestamp(trade['closure_confirmed_at'], timezone.utc).isoformat(timespec='seconds')
                    self._notify(trade['id']+':closed',f"MT5 {trade['status']} {trade['symbol']}\nPosição {trade.get('position_id')} | PnL confirmado {trade['pnl']:.2f} {trade['currency']}\nFechamento UTC: {closed}\nConfirmação pelo robô UTC: {confirmed}\n{trade['sync_note']}")
                elif trade['status']=='REJECTED':
                    self._fault('preflight','BROKER_REJECTED',trade.get('sync_note','Ordem rejeitada pelo histórico.'))
                    self._notify(trade['id']+':rejected',f"MT5 {trade['symbol']}: {trade['sync_note']}")
                elif trade['status']=='OPEN':
                    self._notify(trade['id']+':filled',f"MT5: preenchimento confirmado {trade['symbol']} {trade['action']}\nPosição {trade['position_id']} | Volume {trade['filled_volume']} | Preço médio {trade['entry_price']}\n{trade['sync_note']}")
                self.add_log('MT5',trade.get('sync_note',trade['status']))
        self._has_open_trade=any(t.get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE for t in self.trade_history) or any(t.get('mode','PAPER')=='PAPER' and t['status']=='OPEN' for t in self.trade_history)
        self._refresh_stats()

    async def _settle_trade_after_real_expiration(self, trade, target_dt):
        if trade['market']!='SAFIRION_OB':
            raise ValueError('Forex/B3 não podem ser liquidados por expiração; use monitoramento SL/TP.')
        try:
            while datetime.now() < target_dt:
                trade['remaining_sec'] = max(0, int((target_dt-datetime.now()).total_seconds()))
                self.stats['open_trade_remaining_sec'] = trade['remaining_sec']
                await asyncio.sleep(.5)
            if trade['source'] == 'SAFIRION_LIVE':
                if self.safirion.symbol != trade['symbol'].upper().replace('/', '').replace('-', ''):
                    raise ValueError('Ativo da ponte mudou durante a operação.')
                age = datetime.now().timestamp()-self.safirion.last_tick_timestamp
                if self.safirion.bridge_ws is None or age > self.config['max_tick_age_seconds']:
                    raise ValueError('Cotação real indisponível ao encerrar a simulação.')
                close_price = self.safirion.last_live_price
            else:
                if ('SIMULATED' if self.mt5.simulation_mode else 'MT5_LIVE') != trade['source']:
                    raise ValueError('Fonte de dados mudou durante a operação.')
                close_price = float(self.mt5.get_candles(trade['symbol'], trade['timeframe'], count=240)['close'].iloc[-1])
            change = close_price-trade['entry_price']
            direction = 1 if trade['action'] == 'BUY' else -1
            if trade['market'] == 'SAFIRION_OB':
                pnl = 0 if change == 0 else (trade['amount']*trade['payout_pct']/100 if change*direction>0 else -trade['amount'])
                note = 'PAPER: payout configurado, empate devolve a entrada; sem confirmação da corretora.'
            trade.update(status='WIN' if pnl>0 else ('LOSS' if pnl<0 else 'DRAW'),
                         exit_price=close_price, pnl=round(pnl, 6), settlement_note=note)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            trade.update(status='INTERRUPTED', pnl=-trade['amount'],
                         settlement_note=f'Sem resultado verificável: reserva de risco debitada na simulação. {exc}')
            self._fault('paper_settlement','PAPER_INTERRUPTED',trade['settlement_note'])
        finally:
            # Cancellation leaves OPEN persisted for conservative recovery on restart.
            if trade['status'] != 'OPEN':
                trade['settled_at'] = datetime.now().timestamp()
            trade['remaining_sec'] = 0
            self._save_trade(trade)
            self._refresh_stats()
            self.stats['open_trade_remaining_sec'] = 0
            self._has_open_trade = False
            self._last_trade_timestamp = datetime.now().timestamp()
            self.add_log('INFO', f"PAPER {trade['id'][:14]}: {trade['status']} • R$ {trade['pnl']:.2f}")

    async def close(self):
        for task in tuple(self._settlement_tasks):
            task.cancel()
        await asyncio.gather(*self._settlement_tasks, return_exceptions=True)
        await self.notifier.close()
        await self.safirion.close()
        self.journal.release_runtime_lock()

    async def run_cycle(self):
        if getattr(self, "_research_busy", False):
            return
        if self._mt5_busy:
            return
        self._audit_state(self.audit.flush())
        self._flush_notifications()
        self._flush_trade_saves()
        await self.reconcile_live()
        day = datetime.now(timezone.utc).date().isoformat()
        if day != self._session_day and not self._has_open_trade:
            self._session_day = day
            self.trade_history = self._session_trades()
            self._refresh_stats()
            self._last_signal_bar = None
            self.add_log('INFO', 'Nova sessão UTC; risco global atualizado, histórico preservado.')
        if self.config.get('forex_multi_enabled'):
            from backend.forex_multi import ForexCoordinator
            if self._multi is None:
                self._multi = ForexCoordinator(self)
            await self._multi.cycle()
            return
        snapshot=self.get_market_snapshot()
        if self.is_running:
            signal=snapshot['signal']
            blocked=self._risk_block(signal)
            if blocked:
                self.stats['status_reason']=blocked+' '+signal['reason']
                if any(word in blocked for word in ('Stop loss','Meta diária','Limite de perdas','Limite de operações')):
                    self._risk_alert(blocked)
            else:
                result=await self.execute_trade_from_signal(signal)
                self.stats['status_reason']=result['message']

    def _risk_alert(self, reason):
        key='risk:'+digest([self.config['execution_mode'],self._session_day,reason])
        if key not in self.recovery.alerted:
            self.recovery.alerted[key]=time.time()
            self.add_log('ALERT',reason+' Monitoramento ativo; novas entradas bloqueadas.')
            self._diagnostic_event('RISK','GLOBAL_BLOCK',dict(reason=reason))
            self._notify(key,'Proteção de risco: '+reason+' Monitoramento continua; nenhuma entrada fora dos limites.')

    async def run_loop(self):
        for trade in self.trade_history:
            if trade.get('mode')=='PAPER' and trade['status']=='OPEN' and trade.get('exit_policy')=='SL_TP':
                self._track_paper_position(trade)
        while True:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                note=str(exc) if isinstance(exc,(MT5ExecutionError,ValueError,RuntimeError)) else 'Falha interna na análise; consulte os diagnósticos.'
                self.stats['status_reason']='Análise em recuperação; novas entradas bloqueadas. '+note
                self._fault('analysis','ANALYSIS_UNAVAILABLE',note)
            fault=self.recovery.faults.get('analysis')
            delay=max(1,min(60,fault['retry_at']-time.time())) if fault else 1
            await asyncio.sleep(delay)
