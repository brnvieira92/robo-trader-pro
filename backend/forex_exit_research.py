"""Closed-bar exit experiments. Never sends or modifies native MT5 orders."""
import math
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ResearchExitManagement(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    mode: Literal['FIXED','BREAK_EVEN','ATR_TRAILING','BREAK_EVEN_ATR']='FIXED'
    activation_r: float = Field(1.,ge=.25,le=5)
    trailing_atr_mult: float = Field(2.,ge=.5,le=5)


def update_research_stop(trade,bar,spec,atr,policy,commission_per_lot_side,slippage_points,effective_at):
    """Use this bar's CLOSE only; the new SL starts on the NEXT bar.

    An intrabar high never activates a stop and then benefits from an unknown
    low/high sequence. Initial risk and portfolio reservations stay frozen.
    """
    if policy.mode=='FIXED':return
    direction=1 if trade['action']=='BUY' else -1
    mark=bar.close+(bar.spread_points*spec.point if direction==-1 else 0)
    initial_risk=trade['initial_risk_distance'];entry=trade['entry_price']
    if not trade.get('exit_management_activated') and direction*(mark-entry)<policy.activation_r*initial_risk:return
    if not math.isfinite(atr) or atr<=0:return
    candidates=[]
    if policy.mode in ('BREAK_EVEN','BREAK_EVEN_ATR'):
        # Pay round-trip commissions, configured buffer, accrued swap and the
        # assumed exit slippage. Future gaps/swaps can still produce a loss.
        costs=2*commission_per_lot_side*trade['volume']+trade.get('cost_buffer_configured',0)+trade['swap_cost']
        money_per_price=spec.tick_value_profit*trade['volume']/spec.tick_size
        offset=costs/money_per_price+slippage_points*spec.point
        candidates.append((entry+direction*offset,'BREAK_EVEN'))
    if policy.mode in ('ATR_TRAILING','BREAK_EVEN_ATR'):
        candidates.append((mark-direction*policy.trailing_atr_mult*atr,'ATR_TRAILING'))
    candidate,reason=(max(candidates,key=lambda x:x[0]) if direction==1 else min(candidates,key=lambda x:x[0]))
    # Snap towards profit so the cost-covering offset is not rounded away.
    units=(Decimal(str(candidate))/Decimal(str(spec.tick_size))).to_integral_value(
        rounding=ROUND_CEILING if direction==1 else ROUND_FLOOR)
    candidate=round(float(units*Decimal(str(spec.tick_size))),spec.digits)
    minimum=(spec.stops_level+1)*spec.point
    if candidate<=0 or direction*(mark-candidate)<minimum-1e-10:return
    if direction*(candidate-trade['stop_loss'])<spec.tick_size-1e-10:return
    if direction*(trade['take_profit']-candidate)<=0:return
    old=trade['stop_loss'];trade['stop_loss']=candidate
    trade['exit_management_activated']=True
    trade.setdefault('stop_updates',[]).append(dict(old_stop=old,new_stop=candidate,reason=reason,
        observed_close=mark,atr_from_previous_closed_bar=atr,effective_at=effective_at,
        initial_risk_distance=initial_risk))
