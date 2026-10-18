"""Price-based SL/TP planning from closed-bar structure and volatility.

A daily objective caps exposure; it never manufactures a signal or increases
position size to recover losses. Price plans are estimates, not win probabilities.
"""
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

class TradePlanError(ValueError):
    pass

def price_plan(config, signal, entry, min_stop_distance=0., min_target_distance=0., tick_size=None, digits=8):
    p=config['strategy_params']
    action=signal.get('action')
    if action not in ('BUY','SELL'):
        raise TradePlanError('Sem direção de entrada.')
    ind=signal.get('indicators') or {}
    atr=float(ind.get('atr',0))
    if not math.isfinite(entry) or entry<=0 or not math.isfinite(atr) or atr<=0:
        raise TradePlanError('ATR/preço indisponível: aguarde histórico antes de planejar uma posição.')
    anchor=float(ind.get('price',signal.get('price',entry)))
    if not math.isfinite(anchor) or abs(entry-anchor)>p['max_entry_drift_atr']*atr:
        raise TradePlanError('Preço se afastou do sinal fechado; aguarde outra oportunidade, sem perseguir a entrada.')
    buy=action=='BUY';direction=1 if buy else -1
    support=ind.get('support');resistance=ind.get('resistance')
    support=float(support) if support is not None else None
    resistance=float(resistance) if resistance is not None else None
    if any(x is not None and (not math.isfinite(x) or x<=0) for x in (support,resistance)):
        raise TradePlanError('Estrutura de preços inválida.')
    stop_distance=max(atr*p['sl_atr_mult'],float(min_stop_distance))
    stop_basis='ATR e distância mínima'
    level=support if buy else resistance
    if p['use_structure_stops'] and level is not None and (entry-level)*direction>0:
        stop_distance=max(stop_distance,abs(entry-level)+atr*p['structure_buffer_atr'])
        stop_basis='ATR + suporte' if buy else 'ATR + resistência'
    if stop_distance>atr*p['max_stop_atr']:
        raise TradePlanError('Stop estrutural distante demais para a volatilidade atual; entrada descartada.')
    target_distance=max(atr*p['tp_atr_mult'],float(min_target_distance))
    target_basis='projeção ATR (não é resistência/suporte confirmado)'
    obstacle=resistance if buy else support
    if p['use_structure_targets'] and obstacle is not None and (obstacle-entry)*direction>0:
        space=(obstacle-entry)*direction-atr*p['target_buffer_atr']
        target_distance=min(target_distance,space)
        target_basis='antes da resistência observada' if buy else 'antes do suporte observado'
    if target_distance<float(min_target_distance) or target_distance<=0:
        raise TradePlanError('Sem espaço até o próximo obstáculo para um take profit válido.')
    sl=entry-direction*stop_distance;tp=entry+direction*target_distance
    if tick_size:
        def snap(value,up):
            units=(Decimal(str(value))/Decimal(str(tick_size))).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
            return round(float(units*Decimal(str(tick_size))),digits)
        sl=snap(sl,not buy)
        # Never round a target through the observed obstacle.
        tp=snap(tp,not buy)
    risk=(entry-sl)*direction;reward=(tp-entry)*direction
    if min(sl,tp)<=0 or risk<=0 or reward<=0:
        raise TradePlanError('Plano de preços inconsistente.')
    rr=reward/risk
    if rr+1e-8<p['min_reward_risk']:
        raise TradePlanError(f'Espaço até o alvo insuficiente: retorno/risco {rr:.2f}, mínimo {p["min_reward_risk"]:.2f}.')
    return dict(entry_price=entry,stop_loss=sl,take_profit=tp,risk_distance=risk,reward_distance=reward,
                reward_risk=round(rr,6),stop_basis=stop_basis,target_basis=target_basis,
                exit_policy='SL_TP',expiration_time=None,ready=True)

def goal_limited_budget(config, risk_budget, remaining_goal_account, reward_risk):
    if remaining_goal_account is not None and remaining_goal_account<=0:
        raise TradePlanError('Meta diária já atingida; novas entradas pausadas.')
    profit_limit=config['profit_per_trade_cap']
    if remaining_goal_account is not None:
        profit_limit=min(profit_limit,remaining_goal_account)
    return max(0,min(risk_budget,profit_limit/reward_risk))
