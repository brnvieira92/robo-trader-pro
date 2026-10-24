"""Audited MT5 market execution. All SDK writes go through a durable intent.

An absent response is ambiguous, never a reason to repeat order_send. Position
closure and PnL come from deals, not a local expiration timer.
"""
import math
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from backend.trade_plan import price_plan, goal_limited_budget, TradePlanError
from backend.portfolio_risk import inspect_portfolio, position_limit, context
from backend.config import RESEARCH_ONLY_STRATEGIES

ACTIVE_LIVE = {'SUBMITTING', 'PENDING', 'OPEN', 'NEEDS_REVIEW'}

class MT5ExecutionError(ValueError):
    pass

class MT5Executor:
    historical_research=False
    def __init__(self, connector, sdk=None):
        if sdk is None:
            from backend.mt5_connector import mt5
            sdk = mt5
        self.connector = connector
        self.sdk = sdk

    @staticmethod
    def normalize_trade_times(trade, explicit_offset_minutes=None):
        """Normalize independently of ticks/bars, preserving raw broker epochs.

        Auto calibration requires an immediate market fill and its exact deal
        ticket. Never infer a timezone from the time reconciliation happened.
        """
        if trade.get('deal_times_version') == 1 or not trade.get('deals'):
            return {}
        deals = trade['deals']
        if explicit_offset_minutes is not None:
            offset = int(explicit_offset_minutes) * 60
            basis = 'EXPLICIT'
        elif trade.get('deal_time_offset_seconds') is not None:
            offset = trade['deal_time_offset_seconds']
            basis = trade.get('deal_time_basis', 'SAVED')
        else:
            entry = next((d for d in deals if d['ticket'] == trade.get('deal_ticket')), None)
            if entry is None:
                raise MT5ExecutionError('Relógio dos negócios não confirmado; configure o ajuste de timestamp dos negócios, independente de tick/velas.')
            raw = entry.get('time_raw', entry['time'])
            delta = raw - trade['created_at']
            offset = round(delta / 900) * 900
            immediate = trade.get('retcode') in (10009, 10010)
            # A long/unknown fill delay cannot be mistaken for a timezone.
            response_delay = trade.get('submission_confirmed_at', trade['created_at']) - trade['created_at']
            if abs(offset) > 14*3600 or abs(delta-offset) > 60 or (offset and (not immediate or response_delay > 60)):
                raise MT5ExecutionError('Relógio dos negócios ambíguo; configure explicitamente o ajuste de timestamp dos negócios. Nenhuma ordem será reenviada.')
            basis = 'IMMEDIATE_FILL_ANCHOR'
        normalized = [dict(d, time_raw=d.get('time_raw', d['time']), time=d.get('time_raw', d['time'])-offset) for d in deals]
        result = dict(deals=normalized, deal_time_offset_seconds=offset, deal_time_basis=basis, deal_times_version=1)
        if trade.get('settled_at') is not None:
            raw = trade.get('settled_at_raw', trade['settled_at'])
            result.update(settled_at_raw=raw, settled_at=raw-offset)
        return result

    def _discovery_deals(self, trade, queries=None):
        """Identifiers first; bounded clock-independent discovery as fallback."""
        sdk = self.sdk
        queries = queries if queries is not None else []
        def query(method, *args, **kwargs):
            try:
                deals = sdk.history_deals_get(*args, **kwargs)
            except Exception as exc:
                queries.append(dict(method=method, error=str(exc)))
                return None
            if deals is None:
                # Read immediately: another SDK call can overwrite last_error.
                try:
                    error = sdk.last_error()
                except Exception:
                    error = 'Código de erro MT5 indisponível'
                queries.append(dict(method=method, error=error))
            else:
                queries.append(dict(method=method, count=len(deals)))
            return deals
        if trade.get('position_id'):
            deals = query('position', position=int(trade['position_id']))
            if deals:
                return deals
        if trade.get('order_ticket'):
            # MetaTrader's ticket= argument filters DEAL_ORDER (order ticket),
            # not DEAL_TICKET. Keep both identifiers for ownership validation.
            deals = query('order_ticket', ticket=int(trade['order_ticket']))
            if deals:
                return deals
        # Some accepted/ambiguous sends do not provide a deal ticket. Search
        # across possible broker clock offsets, still requiring exact ownership.
        begin = datetime.fromtimestamp(trade['created_at']-15*3600, timezone.utc)
        end = datetime.fromtimestamp(time.time()+15*3600, timezone.utc)
        deals = query('time_window', begin, end)
        if deals is None:
            raise MT5ExecutionError(f'Histórico MT5 indisponível; intenção permanece pendente. Consultas: {queries}')
        return deals

    def account(self, expected_login=None, expected_server=None, trading=False):
        sdk = self.sdk
        if sdk is None or not self.connector.connected or self.connector.simulation_mode:
            raise MT5ExecutionError('MT5 real indisponível. Conecte o terminal no Windows, sem fallback simulado.')
        info = sdk.account_info()
        terminal = sdk.terminal_info()
        if info is None or terminal is None or not getattr(terminal, 'connected', False):
            raise MT5ExecutionError('Sem conexão confirmada com o servidor MT5.')
        if expected_login is not None and (int(info.login) != int(expected_login) or info.server != expected_server):
            raise MT5ExecutionError('A conta/servidor MT5 mudou. Conecte a conta vinculada antes de continuar.')
        if trading:
            blocked = []
            if not getattr(terminal, 'trade_allowed', False):
                blocked.append('terminal.trade_allowed=False: ative AlgoTrading no MT5 e marque Permitir negociação algorítmica em Ferramentas > Opções > Expert Advisors')
            if getattr(terminal, 'tradeapi_disabled', True):
                blocked.append('terminal.tradeapi_disabled=True ou indisponível: desmarque Desabilitar negociação automática pela API Python externa em Ferramentas > Opções > Expert Advisors')
            if not getattr(info, 'trade_allowed', False):
                blocked.append('account.trade_allowed=False: confira se entrou com a senha principal de negociação, não a senha de investidor; se persistir, peça à corretora para verificar a permissão da conta')
            if not getattr(info, 'trade_expert', False):
                blocked.append('account.trade_expert=False: a conta não autoriza negociação por robôs; confirme a permissão de Expert Advisors/API com a corretora')
            if blocked:
                raise MT5ExecutionError('Envio MT5 bloqueado. ' + '; '.join(blocked) + '.')
        if not all(math.isfinite(float(getattr(info, name))) for name in ('balance','equity','margin_free')):
            raise MT5ExecutionError('Saldo, patrimônio ou margem inválidos no MT5.')
        snapshot = dict(login=int(info.login), server=info.server, balance=float(info.balance),
                        equity=float(info.equity), margin_free=float(info.margin_free), currency=info.currency,
                        account_type='REAL' if info.trade_mode == getattr(sdk,'ACCOUNT_TRADE_MODE_REAL',2) else 'DEMO/CONTEST')
        self.connector.account_info = snapshot
        return snapshot

    def _tick(self, symbol, config):
        tick = self.sdk.symbol_info_tick(symbol)
        if tick is None:
            raise MT5ExecutionError('Sem cotação MT5 para o símbolo exato selecionado.')
        quote_time = self.connector.tick_timestamp(tick)
        age = time.time()-quote_time
        if not math.isfinite(age) or age < -5 or age > config['max_tick_age_seconds']:
            raise MT5ExecutionError('Cotação MT5 desatualizada; nenhuma ordem enviada.')
        if not all(math.isfinite(float(x)) and x>0 for x in (tick.ask,tick.bid)) or tick.ask < tick.bid:
            raise MT5ExecutionError('Bid/ask inválidos no MT5.')
        return tick

    def quote_age(self, symbol):
        if self.sdk is None or self.connector.simulation_mode or not self.connector.connected:
            return None
        tick = self.sdk.symbol_info_tick(symbol)
        if tick is None:
            return None
        timestamp = self.connector.tick_timestamp(tick)
        age = time.time()-timestamp
        return age if math.isfinite(age) else None

    def _quote_timestamp(self, tick):
        return self.connector.tick_timestamp(tick)

    def clock_diagnostics(self, symbol, timeframe):
        now = time.time()
        result = dict(computer_utc=datetime.fromtimestamp(now,timezone.utc).isoformat(),
                      tick_offset_minutes=self.connector.tick_time_offset_minutes,
                      bar_offset_minutes=self.connector.bar_time_offset_minutes,
                      deal_offset_minutes=getattr(self.connector, 'deal_time_offset_minutes', None))
        if self.sdk is None or not self.connector.connected or self.connector.simulation_mode:
            return result
        tick = self.sdk.symbol_info_tick(symbol)
        def iso(value):
            return datetime.fromtimestamp(value,timezone.utc).isoformat()
        if tick is not None:
            normalized = self.connector.tick_timestamp(tick)
            raw = normalized + self.connector.tick_time_offset_minutes*60
            if math.isfinite(raw) and raw>0:
                result.update(raw_tick_utc=iso(raw),tick_utc=iso(normalized),
                              raw_tick_age_seconds=round(now-raw,3),tick_age_seconds=round(now-normalized,3))
        bar = self.connector.bar_clock.get((symbol,timeframe))
        if bar:
            result.update(raw_bar_utc=iso(bar['raw_epoch']),bar_utc=iso(bar['utc_epoch']))
        return result

    def brl_rate(self, account_currency, symbol=''):
        if account_currency=='BRL':
            return 1.,None
        sdk=self.sdk
        if sdk is None or self.connector.simulation_mode or not self.connector.connected:
            raise MT5ExecutionError('Conversão BRL indisponível: conecte o MT5 ou use meta na moeda da conta.')
        symbol=symbol or account_currency+'BRL'
        if not sdk.symbol_select(symbol,True):
            raise MT5ExecutionError('Informe o símbolo de câmbio da corretora para converter a meta BRL.')
        info=sdk.symbol_info(symbol);tick=sdk.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise MT5ExecutionError('Sem símbolo/cotação de conversão para BRL.')
        base=getattr(info,'currency_base','');quote=getattr(info,'currency_profit','')
        timestamp=self.connector.tick_timestamp(tick)
        if not -5<=time.time()-timestamp<=60 or not all(math.isfinite(float(x)) and x>0 for x in (tick.bid,tick.ask)) or tick.ask<tick.bid:
            raise MT5ExecutionError('Cotação de conversão BRL ausente ou desatualizada (limite 60s).')
        mid=(float(tick.bid)+float(tick.ask))/2
        if (base,quote)==(account_currency,'BRL'):
            return mid,symbol
        if (base,quote)==('BRL',account_currency):
            return 1/mid,symbol
        raise MT5ExecutionError('O símbolo de conversão não corresponde à moeda da conta/BRL.')

    def prepare(self, config, signal, remaining_loss_budget, client_tag, remaining_goal_account=None, portfolio_trades=None):
        if config.get('strategy_id') in RESEARCH_ONLY_STRATEGIES and not self.historical_research:
            raise MT5ExecutionError('Estratégia experimental disponível somente no laboratório Forex; nenhuma ordem preparada.')
        sdk = self.sdk
        account = self.account(config['mt5_account_login'], config['mt5_account_server'], trading=True)
        portfolio = None
        if config.get('forex_multi_enabled'):
            positions, orders = sdk.positions_get(), sdk.orders_get()
            if positions is None or orders is None:
                raise MT5ExecutionError('Não foi possível confirmar exposição global da conta.')
            if position_limit(config) > 1:
                try:
                    portfolio = inspect_portfolio(config, portfolio_trades or [], positions, orders, account,
                        remaining_loss_budget, sdk.order_calc_profit, symbol=config['symbol'])
                except ValueError as exc:
                    raise MT5ExecutionError(str(exc)) from exc
            elif positions or orders:
                raise MT5ExecutionError('Já há posição ou ordem na conta; exposição global bloqueada.')
        symbol = config['symbol']
        if not sdk.symbol_select(symbol, True):
            raise MT5ExecutionError('Símbolo não disponível. Use o código negociável exato da corretora, incluindo sufixos.')
        info = sdk.symbol_info(symbol)
        if info is None or getattr(info,'trade_mode',0) not in (getattr(sdk,'SYMBOL_TRADE_MODE_FULL',4),
               getattr(sdk,'SYMBOL_TRADE_MODE_LONGONLY',1),getattr(sdk,'SYMBOL_TRADE_MODE_SHORTONLY',2)):
            raise MT5ExecutionError('Símbolo indisponível para abrir posições (contrato contínuo/mercado fechado pode não ser negociável).')
        action = signal['action']
        if action not in ('BUY','SELL'):
            raise MT5ExecutionError('Direção de ordem inválida.')
        if (action=='BUY' and info.trade_mode==getattr(sdk,'SYMBOL_TRADE_MODE_SHORTONLY',2)) or (action=='SELL' and info.trade_mode==getattr(sdk,'SYMBOL_TRADE_MODE_LONGONLY',1)):
            raise MT5ExecutionError('Direção não permitida para este símbolo.')
        positions = sdk.positions_get(symbol=symbol)
        orders = sdk.orders_get(symbol=symbol)
        if positions is None or orders is None:
            raise MT5ExecutionError('Não foi possível verificar posições e ordens pendentes no MT5.')
        if positions or orders:
            raise MT5ExecutionError('Já há posição/ordem no símbolo, inclusive manual. Não serão misturadas exposições.')
        tick = self._tick(symbol, config)
        point = float(info.point)
        size = float(getattr(info,'trade_tick_size',0) or point)
        if not math.isfinite(point) or point<=0 or not math.isfinite(size) or size<=0:
            raise MT5ExecutionError('Especificação de preço inválida no MT5.')
        spread = (tick.ask-tick.bid)/point
        if spread > config['mt5_max_spread_points']:
            raise MT5ExecutionError(f'Spread {spread:.1f} pontos acima do limite configurado.')
        atr = float(signal.get('indicators',{}).get('atr',0))
        if not math.isfinite(atr) or atr<=0:
            raise MT5ExecutionError('ATR indisponível: não é possível calcular stops e risco.')
        buy = action=='BUY'
        price = float(tick.ask if buy else tick.bid)
        minimum = (max(0, int(info.trade_stops_level))+1)*point
        try:
            plan=price_plan(config,signal,price,minimum+(tick.ask-tick.bid),minimum,size,int(info.digits),
                            spread=float(tick.ask-tick.bid))
        except TradePlanError as exc:
            raise MT5ExecutionError(str(exc)) from exc
        sl=plan['stop_loss'];tp=plan['take_profit']
        if config['market_type']=='MT5_FOREX' and plan['spread_to_stop']>config.get('mt5_max_spread_stop_ratio',.30):
            raise MT5ExecutionError(f"Spread/stop {plan['spread_to_stop']:.1%} acima do limite {config.get('mt5_max_spread_stop_ratio',.30):.1%}; entrada descartada.")
        order_type=sdk.ORDER_TYPE_BUY if buy else sdk.ORDER_TYPE_SELL
        budget=min(float(remaining_loss_budget), max(0,account['equity'])*config['risk_per_trade_pct']/100)
        if config['bankroll_mode'] != 'PERCENT':
            budget=min(budget,config['entry_amount'])
        if portfolio is not None:
            budget=min(budget, portfolio['remaining_risk'], portfolio['per_position_cap'])
        try:
            budget=goal_limited_budget(config,budget,remaining_goal_account,plan['reward_risk'])
        except TradePlanError as exc:
            raise MT5ExecutionError(str(exc)) from exc
        buffer=config['mt5_cost_buffer']
        available=budget-buffer
        minimum_volume=float(info.volume_min)
        step=float(info.volume_step)
        maximum_volume=min(float(info.volume_max),config['mt5_volume_cap'])
        if not all(math.isfinite(x) and x>0 for x in (minimum_volume,step,maximum_volume)):
            raise MT5ExecutionError('Especificação de volume inválida no MT5.')
        loss=sdk.order_calc_profit(order_type,symbol,minimum_volume,price,sl)
        if loss is None or not math.isfinite(float(loss)) or loss>=0 or available<=0:
            raise MT5ExecutionError('Não foi possível calcular o risco monetário até o stop.')
        raw=min(available/abs(float(loss))*minimum_volume,maximum_volume)
        volume=float((Decimal(str(raw))/Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR)*Decimal(str(step)))
        if volume < minimum_volume:
            raise MT5ExecutionError('O lote mínimo ultrapassa o orçamento de risco. Nenhuma ordem enviada.')
        estimated=sdk.order_calc_profit(order_type,symbol,volume,price,sl)
        if estimated is None or not math.isfinite(float(estimated)) or estimated>=0 or abs(estimated)+buffer>budget+1e-8:
            raise MT5ExecutionError('Risco calculado excede o orçamento de entrada.')
        projected=sdk.order_calc_profit(order_type,symbol,volume,price,tp)
        if projected is None or not math.isfinite(float(projected)) or projected<=0:
            raise MT5ExecutionError('Não foi possível calcular o retorno monetário do alvo.')
        net_reward=float(projected)-buffer
        total_risk=abs(float(estimated))+buffer
        if net_reward/total_risk+1e-8<config['strategy_params']['min_reward_risk']:
            raise MT5ExecutionError('Retorno/risco insuficiente após reserva de custos; entrada descartada.')
        margin=sdk.order_calc_margin(order_type,symbol,volume,price)
        if margin is None or not math.isfinite(float(margin)) or margin>account['margin_free']:
            raise MT5ExecutionError('Margem livre insuficiente ou não verificável.')
        flags=int(info.filling_mode)
        if flags & getattr(sdk,'SYMBOL_FILLING_FOK',1):
            filling=sdk.ORDER_FILLING_FOK
        elif flags & getattr(sdk,'SYMBOL_FILLING_IOC',2):
            filling=sdk.ORDER_FILLING_IOC
        elif info.trade_exemode != getattr(sdk,'SYMBOL_TRADE_EXECUTION_MARKET',2):
            filling=sdk.ORDER_FILLING_RETURN
        else:
            raise MT5ExecutionError('Nenhum modo de preenchimento suportado foi identificado.')
        request=dict(action=sdk.TRADE_ACTION_DEAL,symbol=symbol,volume=volume,type=order_type,
                     price=price,sl=sl,tp=tp,deviation=config['mt5_deviation_points'],magic=config['mt5_magic'],
                     comment=client_tag,type_time=sdk.ORDER_TIME_GTC,type_filling=filling)
        checked=sdk.order_check(request)
        if checked is None or checked.retcode!=0:
            code=getattr(checked,'retcode','sem resposta')
            raise MT5ExecutionError(f'Pré-validação MT5 recusou a ordem (código {code}).')
        return dict(request=request, account=account, risk_budget=budget, estimated_risk=abs(float(estimated))+buffer,
                    portfolio=portfolio, portfolio_context=context(portfolio_trades or []),
                    volume=volume, price=price, stop_loss=sl, take_profit=tp, spread_points=round(spread,2),
                    entry_diagnostics=dict(bid=float(tick.bid),ask=float(tick.ask),
                        quote_utc=self._quote_timestamp(tick),point=point,tick_size=size,digits=int(info.digits),
                        spread_points=float(spread),spread_price=float(tick.ask-tick.bid),
                        trade_stops_level=int(info.trade_stops_level),volume_min=minimum_volume,volume_step=step,
                        volume_max=float(info.volume_max),margin_required=float(margin),
                        currency_profit=getattr(info,'currency_profit',None),
                        contract_size=getattr(info,'trade_contract_size',None),
                        tick_value_profit=getattr(info,'trade_tick_value_profit',None),
                        tick_value_loss=getattr(info,'trade_tick_value_loss',None),
                        cost_buffer=float(buffer),estimated_loss=total_risk,estimated_profit=net_reward),
                    plan={**plan,'estimated_profit':net_reward,'estimated_loss':total_risk,'net_reward_risk':net_reward/total_risk})

    def send(self, trade):
        """Called once, only after the SUBMITTING intent has been committed."""
        sdk=self.sdk
        account=self.account(trade['account_login'],trade['account_server'],trading=True)
        if trade.get('forex_multi'):
            positions, orders = sdk.positions_get(), sdk.orders_get()
            if positions is None or orders is None:
                return dict(status='REJECTED', success=False, pending=False,
                    message='Envio cancelado antes de order_send: exposição global não confirmada.')
            if trade.get('max_open_positions', 1) > 1:
                try:
                    inspect_portfolio(trade['portfolio_config'], trade['portfolio_context'], positions, orders, account,
                        trade['remaining_loss_budget'], sdk.order_calc_profit, symbol=trade['symbol'],
                        proposed_risk=trade['reserved_risk'])
                except ValueError as exc:
                    return dict(status='REJECTED', success=False, pending=False, message='Envio cancelado antes de order_send: '+str(exc))
            elif positions or orders:
                return dict(status='REJECTED', success=False, pending=False,
                    message='Envio cancelado antes de order_send: surgiu posição ou ordem na conta.')
        try:
            result=sdk.order_send(trade['request'])
        except Exception:
            return dict(status='PENDING',success=False,pending=True,message='Exceção no envio MT5; estado incerto. Reconciliação obrigatória, sem reenvio automático.')
        if result is None:
            return dict(status='PENDING',success=False,pending=True,message='MT5 não confirmou o envio. Reconciliação em andamento; a ordem não será repetida.')
        code=int(result.retcode)
        details=dict(retcode=code,order_ticket=int(getattr(result,'order',0)),deal_ticket=int(getattr(result,'deal',0)))
        if code in (getattr(sdk,'TRADE_RETCODE_DONE',10009),getattr(sdk,'TRADE_RETCODE_DONE_PARTIAL',10010),getattr(sdk,'TRADE_RETCODE_PLACED',10008)):
            return dict(**details,status='PENDING',success=True,pending=True,
                        message='MT5 aceitou a solicitação; aguardando confirmação pelo histórico e posição. Preenchimento parcial não será reenviado.')
        # Only explicit rejections are final. Timeout, IPC/network failures stay pending.
        if code in {10004,10006,10007,10013,10014,10015,10016,10017,10018,10019,10020,10021,10022,10026,10027,10030,10035,10038,10042,10043,10044,10045,10046}:
            return dict(**details,status='REJECTED',success=False,pending=False,message=f'Ordem recusada pelo MT5 (código {code}). Nenhum reenvio automático.')
        return dict(**details,status='PENDING',success=False,pending=True,message=f'Resposta MT5 inconclusiva (código {code}); novas entradas bloqueadas até reconciliação.')

    def reconcile(self, trade):
        sdk=self.sdk
        self.account(trade['account_login'],trade['account_server'])
        positions=sdk.positions_get()
        orders=sdk.orders_get()
        if positions is None or orders is None:
            raise MT5ExecutionError('Posições/ordens não puderam ser consultadas; novas entradas bloqueadas.')
        queries=[]
        deals=self._discovery_deals(trade, queries)
        trade['history_queries']=queries
        if deals is None:
            raise MT5ExecutionError('Histórico MT5 indisponível; nenhum resultado será estimado.')
        own=[d for d in deals if getattr(d,'symbol',trade['symbol'])==trade['symbol'] and int(getattr(d,'magic',0))==trade['magic'] and
             (getattr(d,'comment','')==trade['client_tag'] or int(getattr(d,'order',0))==trade.get('order_ticket',-1)
              or int(getattr(d,'ticket',0))==trade.get('deal_ticket',-1)) and
             int(getattr(d,'type',-1)) in (sdk.DEAL_TYPE_BUY,sdk.DEAL_TYPE_SELL)]
        ids={int(d.position_id) for d in own if getattr(d,'position_id',0)}
        if trade.get('position_id'):
            ids.add(int(trade['position_id']))
        if len(ids)>1:
            raise MT5ExecutionError('Mais de uma posição correlacionada; intervenção no terminal necessária.')
        if not ids:
            # A final, unfilled cancelled order can be resolved only by broker history.
            history_method=getattr(sdk,'history_orders_get',None)
            if history_method is not None:
                begin=datetime.fromtimestamp(trade['created_at']-15*3600,timezone.utc)
                history=history_method(begin,datetime.fromtimestamp(time.time()+15*3600,timezone.utc))
                if history is None:
                    raise MT5ExecutionError('Histórico de ordens MT5 indisponível; envio permanece pendente.')
                matched=[o for o in history if int(getattr(o,'ticket',0))==trade.get('order_ticket',-1) or
                         (getattr(o,'magic',0)==trade['magic'] and getattr(o,'comment','')==trade['client_tag'])]
                active=[o for o in orders if int(getattr(o,'ticket',0))==trade.get('order_ticket',-1) or
                        (getattr(o,'magic',0)==trade['magic'] and getattr(o,'comment','')==trade['client_tag'])]
                final={getattr(sdk,'ORDER_STATE_CANCELED',2),getattr(sdk,'ORDER_STATE_REJECTED',5),getattr(sdk,'ORDER_STATE_EXPIRED',6)}
                if matched and not active and all(getattr(o,'state',-1) in final and float(getattr(o,'volume_initial',0))>0 and
                      float(getattr(o,'volume_current',0))>=float(o.volume_initial)-1e-8 and
                      0<float(getattr(o,'time_done',0))<time.time()-5 for o in matched):
                    return dict(status='REJECTED',sync_note='Histórico MT5 confirmou cancelamento/rejeição sem preenchimento.',pnl=0.)
            # Absence of evidence never authorizes a second send, including across restarts.
            return dict(status='PENDING',sync_note='Aguardando negócio/posição correlacionados. Não reenviar a ordem.')
        position_id=next(iter(ids))
        all_deals=sdk.history_deals_get(position=position_id)
        if all_deals is None:
            raise MT5ExecutionError('Histórico completo da posição indisponível.')
        related=[d for d in all_deals if int(getattr(d,'position_id',0))==position_id]
        trading=[d for d in related if int(getattr(d,'type',-1)) in (sdk.DEAL_TYPE_BUY,sdk.DEAL_TYPE_SELL)]
        incoming=[d for d in trading if d.entry==sdk.DEAL_ENTRY_IN]
        outgoing=[d for d in trading if d.entry in (sdk.DEAL_ENTRY_OUT,getattr(sdk,'DEAL_ENTRY_OUT_BY',3))]
        if not incoming or any(d.entry==getattr(sdk,'DEAL_ENTRY_INOUT',2) for d in trading):
            raise MT5ExecutionError('Histórico de entrada/reversão não pode ser reconciliado automaticamente.')
        if any(int(getattr(d,'magic',0))!=trade['magic'] or (getattr(d,'comment','')!=trade['client_tag'] and int(getattr(d,'order',0))!=trade.get('order_ticket',-1)) for d in incoming):
            raise MT5ExecutionError('Posição misturada com outra entrada/manual. Reconciliação automática bloqueada.')
        serialized=[]
        for d in related:
            pnl=sum(float(getattr(d,key,0)) for key in ('profit','commission','swap','fee'))
            if not math.isfinite(pnl):
                raise MT5ExecutionError('Resultado financeiro não finito no histórico MT5.')
            serialized.append(dict(ticket=int(d.ticket),time=int(d.time),time_raw=int(d.time),pnl=pnl,
                profit=float(getattr(d,'profit',0)),commission=float(getattr(d,'commission',0)),
                swap=float(getattr(d,'swap',0)),fee=float(getattr(d,'fee',0)),
                price=float(getattr(d,'price',0)),volume=float(getattr(d,'volume',0)),
                entry=getattr(d,'entry',None),reason=getattr(d,'reason',None)))
        entry_volume=sum(float(d.volume) for d in incoming)
        exit_volume=sum(float(d.volume) for d in outgoing)
        entry_price=sum(float(d.price)*float(d.volume) for d in incoming)/entry_volume
        open_positions=[p for p in positions if int(getattr(p,'identifier',0) or p.ticket)==position_id]
        pending_orders=[o for o in orders if int(getattr(o,'ticket',0))==trade.get('order_ticket',-1) or
                        (getattr(o,'magic',0)==trade['magic'] and getattr(o,'comment','')==trade['client_tag'])]
        common=dict(position_id=position_id,filled_volume=entry_volume,entry_price=entry_price,deals=serialized,
                    realized_pnl=sum(x['pnl'] for x in serialized))
        direction=1 if trade['action']=='BUY' else -1
        requested_entry=(trade.get('request') or {}).get('price')
        common['execution_summary']=dict(
            requested_entry=requested_entry,filled_entry=entry_price,
            entry_slippage_price=(entry_price-float(requested_entry))*direction if requested_entry is not None else None,
            actual_stop_distance=(entry_price-trade['stop_loss'])*direction,
            actual_target_distance=(trade['take_profit']-entry_price)*direction,
            costs={key:sum(d[key] for d in serialized) for key in ('commission','swap','fee')})
        clock_trade = {**trade, **common, 'deal_times_version': 0}
        if not clock_trade.get('deal_ticket'):
            # A lost send response may omit tickets; incoming ownership was
            # independently checked above before using this timestamp anchor.
            clock_trade['deal_ticket'] = int(min(incoming, key=lambda d: d.time).ticket)
        clock = self.normalize_trade_times(clock_trade, getattr(self.connector, 'deal_time_offset_minutes', None))
        common.update(clock)
        if open_positions or pending_orders:
            buy=trade['action']=='BUY'
            protected=bool(open_positions)
            for position in open_positions:
                sl=float(getattr(position,'sl',0));tp=float(getattr(position,'tp',0))
                stop_not_widened=(sl>=trade['stop_loss']-1e-8) if buy else (sl<=trade['stop_loss']+1e-8)
                target_direction=(tp>entry_price) if buy else (0<tp<entry_price)
                loss=sdk.order_calc_profit(sdk.ORDER_TYPE_BUY if buy else sdk.ORDER_TYPE_SELL,trade['symbol'],
                                           float(getattr(position,'volume',entry_volume)),float(getattr(position,'price_open',entry_price)),sl) if sl>0 else None
                within_budget=loss is not None and math.isfinite(float(loss)) and max(0,-float(loss))<=trade['risk_budget']+1e-8
                protected=protected and sl>0 and stop_not_widened and target_direction and within_budget
            quote=getattr(open_positions[0],'price_current',None) if len(open_positions)==1 else None
            try:
                quote=float(quote)
                if not math.isfinite(quote) or quote<=0:quote=None
            except (TypeError,ValueError):
                quote=None
            return dict(**common,status='OPEN',floating_pnl=sum(float(getattr(p,'profit',0))+float(getattr(p,'swap',0)) for p in open_positions),
                        observed_exit_quote=quote,
                        protected=protected,sync_note='Posição confirmada no terminal.' if protected else 'Proteções não confirmadas: novas entradas bloqueadas; monitoramento ativo. Verifique SL/TP no terminal.')
        if exit_volume+1e-8 < entry_volume:
            return dict(**common,status='PENDING',sync_note='Posição ausente, mas fechamento ainda incompleto no histórico. Aguardando sincronização.')
        total=common['realized_pnl']
        return dict(**common,status='WIN' if total>0 else ('LOSS' if total<0 else 'DRAW'),pnl=round(total,8),
                    exit_price=sum(float(d.price)*float(d.volume) for d in outgoing)/exit_volume,
                    settled_at=max(float(d.time) for d in outgoing)-clock['deal_time_offset_seconds'],
                    settled_at_raw=max(float(d.time) for d in outgoing),floating_pnl=0.,
                    sync_note='Fechamento confirmado pelo histórico MT5; inclui profit, commission, swap e fee disponíveis nos negócios.')
