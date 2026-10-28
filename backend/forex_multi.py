"""Sequential Forex coordinator. Radar reads cached projections, never decisions.

Analysis views reuse the single-instrument pipeline with isolated mutable analysis
state. Only the owner may reserve/send/reconcile; no view runs an execution loop.
SDK work is serialized by the owner's busy guard and awaited worker ownership.
"""
import copy
import time
from backend.bot_manager import BotManager
from backend.ml_engine import MLEngine
from backend.diagnostics import RecoveryState, digest, supplemental_check
from backend.mt5_execution import MT5ExecutionError
from backend.portfolio_risk import position_limit, active_trades, local_block, inspect_portfolio
from backend.config import strategy_requires_ml

PAIRS = ('EURUSD', 'USDJPY', 'GBPUSD', 'USDCHF')


class PairAnalysis(BotManager):
    def __init__(self, owner, item):
        # Deliberately do not initialize connectors, journals, notifier or tasks.
        self.__dict__ = owner.__dict__.copy()
        self.owner = owner
        self.config = copy.deepcopy(owner.config)
        self.config['symbol'] = item['symbol']
        self._pair_analysis = True
        self._model_engine = MLEngine()
        self._model_context = digest([self.config['mt5_account_login'], self.config['mt5_account_server'], self.config['market_type'], self.config['strategy_params']])
        self.recovery = RecoveryState()
        self._last_snapshot = None
        self._mt5_busy = False
        self._last_signal_bar = None
        self.last_signal = None

    def _runtime_block(self):
        return self.owner._runtime_block() or super()._runtime_block()

    def refresh(self):
        o = self.owner
        self._session_day = o._session_day
        self.is_running = o.is_running
        self.stats = dict(o.stats)
        self.trade_history = o.trade_history
        self._has_open_trade = (any(t['symbol']==self.config['symbol'] for t in active_trades(o.trade_history))
                                if position_limit(self.config)>1 else o._has_open_trade)
        self._last_trade_timestamp = o._last_trade_timestamp
        self._last_signal_bar = next((t.get('bar_time') for t in o.trade_history
                                     if t.get('symbol') == self.config['symbol']), None)
        snapshot = self.get_market_snapshot()
        # Symbol metadata is read even without a candidate: display the actual
        # broker restriction, not an inference from successful quote retrieval.
        sdk = o.live.sdk
        if not sdk.symbol_select(self.config['symbol'], True):
            raise MT5ExecutionError('Símbolo não disponível; confira o código exato na corretora.')
        info = sdk.symbol_info(self.config['symbol'])
        if info is None:
            raise MT5ExecutionError('Especificação do símbolo indisponível.')
        expected = next(p['pair'] for p in self.config['forex_pairs'] if p['symbol'] == self.config['symbol'])
        if getattr(info, 'currency_base', None) != expected[:3] or getattr(info, 'currency_profit', None) != expected[3:]:
            raise MT5ExecutionError('Símbolo não corresponde às moedas do par selecionado; confira a especificação na corretora.')
        spec = {k: getattr(info, k, None) for k in (
            'trade_mode', 'digits', 'point', 'trade_tick_size', 'trade_tick_value',
            'volume_min', 'volume_step', 'volume_max', 'trade_stops_level',
            'currency_base', 'currency_profit')}
        snapshot['symbol_spec'] = spec
        mode = spec['trade_mode']
        action = snapshot['signal']['action']
        restriction = None
        if mode not in (1, 2, 4):
            restriction = 'Símbolo indisponível para abrir posições; negociação desativada ou somente encerramento.'
        elif (mode == 1 and action == 'SELL') or (mode == 2 and action == 'BUY'):
            restriction = 'Direção não permitida pela especificação do símbolo.'
        snapshot['entry_checks'] = [*snapshot.get('entry_checks', []), supplemental_check(
            'SYMBOL_TRADE_MODE', not restriction, mode, '1=compra, 2=venda, 4=ambas; direção precisa ser permitida', restriction or '')]
        if restriction:
            snapshot['risk']['blocked_reason'] = restriction
        return snapshot


class ForexCoordinator:
    def __init__(self, owner):
        self.owner = owner
        self.views = {p['pair']: PairAnalysis(owner, p) for p in owner.config['forex_pairs'] if p['enabled']}
        self.rows = {}
        self.snapshots = {}
        self.cursor = 0
        self.updated_at = None
        self.global_block = 'Aguardando primeira verificação global do MT5.'
        self.portfolio = None

    def exposure_check(self):
        o = self.owner
        self.portfolio = None
        account=o.live.account(o.config['mt5_account_login'], o.config['mt5_account_server'], trading=True)
        positions, orders = o.live.sdk.positions_get(), o.live.sdk.orders_get()
        if positions is None or orders is None:
            raise MT5ExecutionError('Não foi possível confirmar exposição global da conta.')
        if position_limit(o.config)>1:
            try:
                self.portfolio=inspect_portfolio(o.config, o.trade_history, positions, orders, account,
                    max(0, o.config['stop_loss']+o.stats['daily_pnl']), o.live.sdk.order_calc_profit, require_slot=False)
                return local_block(o.config, o.trade_history)
            except ValueError as exc:
                self.portfolio=None
                return str(exc)
        if positions or orders:
            return 'Há posição ou ordem na conta MT5; limite global de uma posição/intenção.'
        return None

    def snapshot(self):
        o = self.owner
        # No SDK, model prediction, logging or persistence on a read request.
        base = copy.deepcopy(self.snapshots.get(o.config['symbol']) or
                             o._unavailable_snapshot('Motor multipares aguardando análise.'))
        analysis_stats = base.get('stats', {})
        display_stats = {**o.stats, 'next_suggested_amount': base['risk']['next_amount'],
                         'sizing_reason': analysis_stats.get('sizing_reason', o.stats['sizing_reason'])}
        base.update(config=copy.deepcopy(o.config), is_running=o.is_running,
                    has_open_trade=o._has_open_trade, stats=display_stats,
                    trades=copy.deepcopy(o.trade_history[:25]), logs=list(o.logs[:30]),
                    operational=o.operational_status())
        radar = []
        configured = {p['pair']: p for p in o.config['forex_pairs']}
        for pair in PAIRS:
            item = configured.get(pair, dict(pair=pair, symbol=pair, enabled=False))
            row = dict(pair=pair, symbol=item['symbol'], enabled=item['enabled'],
                       status='AGUARDANDO' if item['enabled'] else 'DESATIVADO',
                       action='NEUTRAL', confidence=None, validated=None,
                       blocked_reason='Aguardando análise.' if item['enabled'] else 'Monitoramento não selecionado.')
            row.update(copy.deepcopy(self.rows.get(pair, {})))
            radar.append(row)
        focus=next((row for row in radar if row['symbol']==o.config['symbol']),{})
        if focus.get('checks'):
            base['entry_checks']=copy.deepcopy(focus['checks'])
            base['failed_checks']=copy.deepcopy(focus.get('failed_checks',[]))
        base['forex_radar'] = dict(enabled=True, updated_at=self.updated_at,
                                  global_block=self.global_block, rows=radar,
                                  max_open_positions=position_limit(o.config), portfolio=copy.deepcopy(self.portfolio), read_only=True)
        current_block=local_block(o.config, o.trade_history) if position_limit(o.config)>1 else None
        if current_block:
            base['forex_radar']['global_block']=current_block
            for row in radar:
                if row['enabled']:
                    row.update(status='BLOQUEADO', blocked_reason=current_block)
        if o._has_open_trade:
            for row in radar:
                occupied=any(t['symbol']==row['symbol'] for t in active_trades(o.trade_history))
                if row['enabled'] and (occupied or position_limit(o.config)==1):
                    row['status'] = 'POSIÇÃO/INTENÇÃO' if occupied else 'BLOQUEADO'
                    row['blocked_reason'] = 'Posição/intenção neste símbolo; monitoramento continua.' if occupied else 'Já existe uma posição/intenção global; monitoramento continua.'
        if self.global_block:
            base['risk']['blocked_reason'] = self.global_block
        return base

    async def cycle(self):
        o = self.owner
        # Root analysis faults must be healed only after the coordinator itself
        # becomes usable. Pair faults live in the individual analysis view.
        o._mt5_busy = True
        try:
            self.global_block = await o._native_call(self.exposure_check)
            o._recover('global-exposure')
        except Exception as exc:
            self.global_block = str(exc)
            o._fault('global-exposure', 'GLOBAL_EXPOSURE_UNAVAILABLE', self.global_block)
        finally:
            o._mt5_busy = False
        for pair, view in self.views.items():
            fault = view.recovery.faults.get('pair')
            if fault and time.time() < fault['retry_at']:
                self.rows[pair].update(status='ERRO', action='NEUTRAL', blocked_reason=fault['message'])
                continue
            o._mt5_busy = True
            try:
                snap = await o._native_call(view.refresh)
                view._recover('pair')
                self.snapshots[view.config['symbol']] = snap
                signal = snap['signal']
                ml = signal.get('indicators', {}).get('ml_validation', {})
                local = snap['risk']['blocked_reason']
                preflight = o.recovery.faults.get('pair-preflight:'+view.config['symbol'])
                if preflight and time.time() < preflight['retry_at']:
                    local = preflight['message']
                block = self.global_block or local
                checks = [*snap.get('entry_checks', []),
                    supplemental_check('GLOBAL_EXPOSURE', not self.global_block, self.global_block or 'Carteira correlacionada e verificada',
                        'Exposição conhecida, vagas e orçamento global disponíveis' if position_limit(o.config)>1 else 'Sem posição/ordem na conta'),
                    supplemental_check('PAIR_RETRY', not (preflight and time.time()<preflight['retry_at']), preflight['message'] if preflight else None, 'Sem espera de recuperação local')]
                row = dict(checks=checks, failed_checks=[g for g in checks if g['status']=='FAIL' and not g.get('informational')], status='BLOQUEADO' if block else 'CANDIDATO', action=signal['action'],
                           confidence=ml.get('ml_confidence'), signal_score=signal['confidence'],
                           validated=ml.get('validated', False) if strategy_requires_ml(view.config['strategy_id'],view.config['strategy_params']) else None,
                           uses_ml=strategy_requires_ml(view.config['strategy_id'],view.config['strategy_params']),validation=ml,
                           fibonacci=signal.get('indicators',{}).get('fibonacci'),
                           entry_confirmation=signal.get('indicators',{}).get('entry_confirmation'),
                           entry_policy_version=signal.get('indicators',{}).get('entry_policy_version','legacy'),
                           blocked_reason=block, local_block=local, global_block=self.global_block,
                           signal_reason=signal.get('reason'), data_quality=snap['data_quality'],
                           symbol_spec=snap.get('symbol_spec'), analysis_id=snap.get('analysis_id'),
                           updated_at=time.time(), automation_requested=o.is_running)
                if not o.is_running:
                    row['status'] = 'PAUSADO'
                self.rows[pair] = row
                o._diagnostic_event('RADAR', row['status'], row,
                    event_key=digest([snap.get('analysis_id'), row['status'], block, signal['action'], signal['confidence']]),
                    bar_time=signal.get('bar_time'), context=view.config)
            except Exception as exc:
                view._fault('pair', 'PAIR_ANALYSIS_UNAVAILABLE', str(exc))
                self.snapshots.pop(view.config['symbol'], None)
                self.rows[pair] = dict(status='ERRO', action='NEUTRAL', confidence=None, validated=False,
                                       blocked_reason=str(exc), local_block=str(exc), updated_at=time.time())
            finally:
                o._mt5_busy = False
        self.updated_at = time.time()
        if o.is_running and not self.global_block:
            o.stats['status_reason'] = 'Monitorando pares: ' + ' • '.join(pair+': '+(row.get('blocked_reason') or row['status']) for pair,row in self.rows.items())
        o._recover('analysis')
        if not o.is_running or self.global_block:
            o.stats['status_reason'] = self.global_block or 'Robô pausado pelo usuário; Radar somente monitoramento.'
            return
        # Round robin among eligible pairs; no sum of budgets and no queue of
        # stale signals to execute after the first trade has closed.
        pairs = list(self.views)
        start = self.cursor
        for step in range(len(pairs)):
            index = (start + step) % len(pairs)
            pair = pairs[index]
            if self.rows.get(pair, {}).get('status') != 'CANDIDATO':
                continue
            view = self.views[pair]
            async with o._order_lock:
                if not o.is_running or (o._has_open_trade and position_limit(o.config)==1) or o._runtime_block():
                    break
                # Refresh all gates for this candidate after scanning the rest.
                o._mt5_busy = True
                try:
                    global_block = await o._native_call(self.exposure_check)
                except Exception as exc:
                    o._fault('global-exposure', 'GLOBAL_REVALIDATION_FAILED', str(exc))
                    break
                finally:
                    o._mt5_busy = False
                o._mt5_busy = True
                try:
                    snap = await o._native_call(view.refresh)
                except Exception as exc:
                    view._fault('pair', 'PAIR_ANALYSIS_UNAVAILABLE', str(exc))
                    self.rows[pair].update(status='ERRO', blocked_reason=str(exc))
                    continue
                finally:
                    o._mt5_busy = False
                if global_block or snap['risk']['blocked_reason'] or not o.is_running:
                    reason = global_block or snap['risk']['blocked_reason'] or 'Pausado pelo usuário.'
                    self.rows[pair].update(status='BLOQUEADO', blocked_reason=reason)
                    o._diagnostic_event('DECISION', 'REVALIDATION_BLOCKED',
                        dict(analysis_id=snap.get('analysis_id'), reason=reason),
                        bar_time=snap['signal'].get('bar_time'), context=view.config)
                    if global_block:
                        self.global_block = global_block
                        break
                    continue
                result = await o._execute_mt5(snap['signal'], snap)
                occupied=any(t['symbol']==view.config['symbol'] for t in active_trades(o.trade_history))
                if any(t['status']!='OPEN' for t in active_trades(o.trade_history)):
                    self.portfolio=None
                self.rows[pair].update(status='POSIÇÃO/INTENÇÃO' if occupied else 'BLOQUEADO',
                                       blocked_reason=result['message'])
                o.stats['status_reason'] = result['message']
                self.cursor = (index + 1) % len(pairs)
                # A preparation rejection is local; an ambiguous/successful send
                # reserves the single global slot and stops the entire scan.
                if o._has_open_trade or o._runtime_block():
                    break
