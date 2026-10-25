"""Strict reduce-only full-position closing adapter for MT5.

Position ticket is mandatory; never send a naked opposite entry as a close.
"""
import math
from backend.mt5_execution import MT5ExecutionError
from backend.profit_exit import decision


def quote(executor,trade,config,policy,client_tag):
    sdk=executor.sdk
    account=executor.account(trade['account_login'],trade['account_server'],trading=True)
    if getattr(sdk.account_info(),'margin_mode',None)!=getattr(sdk,'ACCOUNT_MARGIN_MODE_RETAIL_HEDGING',2):
        raise MT5ExecutionError('Saída antecipada exige conta hedge confirmada; SL/TP mantidos em netting ou modo desconhecido.')
    if account['currency']!=trade['currency']:
        raise MT5ExecutionError('Moeda da conta mudou; estimativa de fechamento bloqueada.')
    if trade.get('status')!='OPEN' or not trade.get('protected') or trade.get('sync_error') or trade.get('deal_time_warning'):
        raise MT5ExecutionError('Posição/proteção não confirmada para fechar antecipadamente.')
    positions=sdk.positions_get();orders=sdk.orders_get()
    if positions is None or orders is None:raise MT5ExecutionError('Exposição indisponível para fechar posição.')
    matches=[p for p in positions if int(getattr(p,'identifier',0) or p.ticket)==int(trade['position_id'])]
    if len(matches)!=1:raise MT5ExecutionError('Posição exata não está confirmada; aguarde reconciliação.')
    p=matches[0]
    if int(getattr(p,'ticket',0))<=0:raise MT5ExecutionError('Ticket de posição inválido; nenhuma ordem de saída preparada.')
    buy=trade['action']=='BUY';kind=sdk.ORDER_TYPE_BUY if buy else sdk.ORDER_TYPE_SELL
    volume=float(p.volume);entry=float(p.price_open)
    if (p.symbol!=trade['symbol'] or int(p.type)!=kind or int(p.magic)!=trade['magic'] or
        not math.isfinite(volume) or volume<=0 or abs(volume-float(trade['filled_volume']))>1e-8 or
        not math.isclose(entry,float(trade['entry_price']),rel_tol=1e-8,abs_tol=1e-8)):
        raise MT5ExecutionError('Volume/direção/propriedade da posição mudou; fechamento bloqueado.')
    if any(getattr(o,'symbol',None)==trade['symbol'] for o in orders):
        raise MT5ExecutionError('Há ordem pendente no símbolo; fechamento exige reconciliação.')
    sl=float(p.sl);tp=float(p.tp)
    if sl<=0 or (sl<trade['stop_loss']-1e-8 if buy else sl>trade['stop_loss']+1e-8) or not (tp>entry if buy else 0<tp<entry):
        raise MT5ExecutionError('SL/TP não confirmados; fechamento automático adicional bloqueado.')
    info=sdk.symbol_info(trade['symbol'])
    if info is None:raise MT5ExecutionError('Especificação do símbolo indisponível para fechamento.')
    point=float(info.point);step=float(info.volume_step)
    if not (math.isfinite(point) and point>0 and math.isfinite(step) and step>0 and
        info.volume_min-1e-8<=volume<=info.volume_max+1e-8 and abs(volume/step-round(volume/step))<1e-6):
        raise MT5ExecutionError('Volume não corresponde à especificação real do símbolo.')
    tick=executor._tick(trade['symbol'],config)
    price=float(tick.bid if buy else tick.ask)
    slip=min(policy['slippage_points'],int(config['mt5_deviation_points']))
    adverse_price=price+(-1 if buy else 1)*slip*point
    gross=sdk.order_calc_profit(kind,trade['symbol'],volume,entry,adverse_price)
    cash=float(trade.get('realized_pnl',0));swap=min(0.,float(getattr(p,'swap',0)))
    # Opening costs are already included in related cashflow. Reserve another
    # side of observed opening commission/fee plus configurable extra buffer.
    closing_cost=sum(abs(float(d.get('commission',0)))+abs(float(d.get('fee',0)))
                     for d in trade.get('deals',[]) if d.get('entry')==sdk.DEAL_ENTRY_IN)
    if gross is None or not all(math.isfinite(float(x)) for x in (gross,cash,swap,closing_cost)):
        raise MT5ExecutionError('Custos/lucro não puderam ser estimados para fechamento.')
    net=float(gross)+cash+swap-closing_cost-policy['cost_buffer']
    flags=int(info.filling_mode)
    if flags & getattr(sdk,'SYMBOL_FILLING_FOK',1):filling=sdk.ORDER_FILLING_FOK
    elif flags & getattr(sdk,'SYMBOL_FILLING_IOC',2):filling=sdk.ORDER_FILLING_IOC
    elif info.trade_exemode!=getattr(sdk,'SYMBOL_TRADE_EXECUTION_MARKET',2):filling=sdk.ORDER_FILLING_RETURN
    else:raise MT5ExecutionError('Preenchimento de fechamento não suportado.')
    request=dict(action=sdk.TRADE_ACTION_DEAL,position=int(p.ticket),symbol=trade['symbol'],volume=volume,
        type=sdk.ORDER_TYPE_SELL if buy else sdk.ORDER_TYPE_BUY,price=price,deviation=slip,magic=trade['magic'],
        comment=client_tag,type_time=sdk.ORDER_TIME_GTC,type_filling=filling)
    return dict(request=request,estimated_net_profit=net,bid=float(tick.bid),ask=float(tick.ask),account_currency=account['currency'],
        closing_cost_reserve=closing_cost,extra_cost_buffer=policy['cost_buffer'],assumed_exit_price=adverse_price,
        position_ticket=int(p.ticket))


def prepare(executor,trade,config,policy,client_tag):
    result=quote(executor,trade,config,policy,client_tag)
    if result['estimated_net_profit']<policy['min_net_profit']:
        raise MT5ExecutionError('Lucro líquido estimado abaixo do mínimo; SL/TP mantidos.')
    check=executor.sdk.order_check(result['request'])
    if check is None or check.retcode!=0:raise MT5ExecutionError('Pré-validação MT5 recusou fechamento antecipado.')
    return result


def send(executor,trade,config,policy):
    intent=trade['close_intent']
    try:
        current=prepare(executor,trade,config,policy,intent['client_tag'])
        gate=decision(trade['action'],intent['observation'],current['estimated_net_profit'],
            max(intent['decision']['peak_estimated_net_profit'],current['estimated_net_profit']),current['bid'],policy)
        if not gate['eligible']:raise MT5ExecutionError('Preço recuperou força ou lucro mínimo não confirmado; SL/TP mantidos.')
        expected=intent['request']
        for key in ('position','symbol','volume','type','magic'):
            if current['request'][key]!=expected[key]:raise MT5ExecutionError('Posição mudou antes do envio de fechamento.')
    except Exception as exc:
        return dict(status='REJECTED',reason=str(exc) if isinstance(exc,MT5ExecutionError) else 'Revalidação do fechamento indisponível; nenhuma ordem enviada.')
    # The persisted position/ticket/side/volume/comment are immutable. Refresh
    # only price, bounded by the same fresh executable-net checks.
    request={**expected,'price':current['request']['price']}
    sent=dict(submitted_request=request,revalidated_estimated_net_profit=current['estimated_net_profit'])
    try:result=executor.sdk.order_send(request)
    except Exception:return dict(**sent,status='PENDING',reason='Envio de fechamento inconclusivo; não repetir. Aguardando histórico MT5.')
    if result is None:return dict(**sent,status='PENDING',reason='MT5 não confirmou fechamento; não repetir. Aguardando histórico.')
    code=int(result.retcode)
    details=dict(**sent,retcode=code,order_ticket=int(getattr(result,'order',0)),deal_ticket=int(getattr(result,'deal',0)))
    if code in {10004,10006,10007,10013,10014,10015,10016,10017,10018,10019,10020,10021,10022,10026,10027,10030,10035,10036,10038,10042,10043,10044,10045,10046}:
        return dict(**details,status='REJECTED',reason='MT5 recusou fechamento; SL/TP originais mantidos.')
    return dict(**details,status='PENDING',reason='Solicitação de fechamento enviada; aguardando confirmação integral pelo histórico MT5.')
