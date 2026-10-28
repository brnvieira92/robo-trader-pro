"""Full early-profit exit: causal closed candle + live executable net estimate.

No SDK IO, no probabilities, no fictional settlement. Native SL/TP stay in place.
"""
import math
import pandas as pd
from pydantic import BaseModel,ConfigDict,Field
from backend.market_data import validate_candles

VERSION='profit-weakness-v1'
PENDING_CLOSE={'SUBMITTING','PENDING','PARTIAL_REVIEW'}


class ProfitExitConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    enabled:bool=False
    min_net_profit:float=Field(1.,ge=.1,le=100000)
    giveback_fraction:float=Field(.35,ge=.1,le=.8)
    slippage_points:int=Field(2,ge=0,le=1000)
    cost_buffer:float=Field(.25,ge=0,le=10000)


def closed_observation(history,now,opened_at,timeframe='M1'):
    validate_candles(history)
    step={'M1':60,'M5':300,'M15':900,'M30':1800,'H1':3600}[timeframe]
    f=history.copy();f['time']=pd.to_datetime(f['time'],utc=True)
    f=f[f['time']+pd.Timedelta(seconds=step)<=pd.Timestamp(now,unit='s',tz='UTC')].tail(60)
    if len(f)<50:raise ValueError('Saída antecipada aguarda 50 velas fechadas.')
    if not (f['time'].diff().dt.total_seconds().iloc[1:]==step).all():
        raise ValueError('Histórico descontínuo para avaliar saída antecipada.')
    current=f.iloc[-1];previous=f.iloc[-2];end=current['time'].timestamp()+step
    if not 0<=now-end<=step+5:raise ValueError('Vela de saída desatualizada.')
    if end<=opened_at:raise ValueError('Aguardando vela fechada após a entrada.')
    ema=float(f['close'].ewm(span=9,adjust=False).mean().iloc[-1])
    span=float(current['high']-current['low'])
    return dict(bar_time=current['time'].isoformat(),closed_at=end,ema9=ema,
        **{k:float(current[k]) for k in ('open','high','low','close')},
        previous_close=float(previous['close']),close_location=(float(current['close'])-float(current['low']))/span if span>0 else .5)


def decision(action,observation,net_profit,peak_profit,bid,policy):
    if not all(math.isfinite(float(x)) for x in (net_profit,peak_profit,bid)):
        raise ValueError('Estimativa financeira inválida para saída antecipada.')
    buy=action=='BUY';direction=1 if buy else -1
    o=observation
    adverse=direction*(o['close']-o['open'])<0 and direction*(o['close']-o['previous_close'])<0 and (o['close_location']<=.35 if buy else o['close_location']>=.65)
    ema_lost=direction*(o['close']-o['ema9'])<0
    giveback=max(0,(peak_profit-net_profit)/peak_profit) if peak_profit>0 else 0.
    recovered=direction*(bid-(o['high'] if buy else o['low']))>0
    eligible=bool(policy['enabled'] and net_profit>=policy['min_net_profit'] and adverse and not recovered and
        (ema_lost or giveback>=policy['giveback_fraction']))
    return dict(version=VERSION,eligible=eligible,bar_time=o['bar_time'],estimated_net_profit=net_profit,
        peak_estimated_net_profit=peak_profit,adverse_candle=adverse,ema9_lost=ema_lost,
        giveback_fraction=giveback,live_recovery=recovered,min_net_profit=policy['min_net_profit'],
        reason='Lucro líquido estimado e enfraquecimento confirmados.' if eligible else
            'Manter SL/TP: lucro mínimo ou enfraquecimento ainda não confirmados.')
