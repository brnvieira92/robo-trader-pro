"""Explicit causal triple ATR bands, not an exact copy of an unspecified video.

ATR(10), Wilder-style EWM seeded from first TR; HL2 bands 1.5/2/3 ATR.
Entry only on fresh unanimous alignment, with closed-candle confirmation.
Parameters are fixed before the study, not selected to rescue known losses.
"""
import numpy as np
import pandas as pd
from backend.forex_trend import confirmed_targets

POLICY_VERSION='forex-triple-atr-v1'
PERIOD=10
MULTIPLIERS=(1.5,2.,3.)


def atr_bands(df):
    high=df['high'].to_numpy(dtype=float);low=df['low'].to_numpy(dtype=float)
    close=df['close'].to_numpy(dtype=float);previous=np.r_[close[0],close[:-1]]
    tr=np.maximum(high-low,np.maximum(abs(high-previous),abs(low-previous)))
    atr=pd.Series(tr).ewm(alpha=1/PERIOD,adjust=False,min_periods=PERIOD).mean().to_numpy()
    mid=(high+low)/2;directions=[];lines=[]
    for multiple in MULTIPLIERS:
        upper=mid+multiple*atr;lower=mid-multiple*atr
        direction=np.zeros(len(df),dtype=int);line=np.full(len(df),np.nan)
        first=PERIOD-1
        if len(df)<=first:directions.append(direction);lines.append(line);continue
        direction[first]=1 if close[first]>=mid[first] else -1
        line[first]=lower[first] if direction[first]==1 else upper[first]
        for i in range(first+1,len(df)):
            if not (upper[i]<upper[i-1] or close[i-1]>upper[i-1]):upper[i]=upper[i-1]
            if not (lower[i]>lower[i-1] or close[i-1]<lower[i-1]):lower[i]=lower[i-1]
            direction[i]=direction[i-1]
            if direction[i-1]==-1 and close[i]>upper[i]:direction[i]=1
            elif direction[i-1]==1 and close[i]<lower[i]:direction[i]=-1
            line[i]=lower[i] if direction[i]==1 else upper[i]
        directions.append(direction);lines.append(line)
    return np.array(directions),np.array(lines),atr


def triple_atr_signal(df,params,atr):
    directions,lines,volatility=atr_bands(df)
    current=df.iloc[-1];price=float(current['close'])
    last=directions[:,-1];previous=directions[:,-2]
    consensus=1 if np.all(last==1) else -1 if np.all(last==-1) else 0
    previous_consensus=1 if np.all(previous==1) else -1 if np.all(previous==-1) else 0
    side='BUY' if consensus==1 else 'SELL' if consensus==-1 else 'NEUTRAL'
    levels=lines[:,-1];span=float(current['high']-current['low'])
    close_position=(price-float(current['low']))/span if span>0 else .5
    price_side=bool(consensus and all(consensus*(price-float(level))>0 for level in levels))
    candle=bool(consensus and consensus*(price-float(current['open']))>0 and (close_position>=.6 if consensus==1 else close_position<=.4))
    checks=[dict(code='ATR_CONSENSUS',passed=bool(consensus),observed=last.tolist(),required='Três direções iguais',reason='Stops ATR sem alinhamento unânime.'),
        dict(code='ATR_FRESH_ALIGNMENT',passed=bool(consensus and consensus!=previous_consensus),observed=dict(previous=previous_consensus,current=consensus),required='Novo alinhamento após oposição ou desalinhamento',reason='Aguardando novo alinhamento; não repetir entrada no mesmo estado.'),
        dict(code='ATR_PRICE_SIDE',passed=price_side,observed=price,required='Preço acima de todas as linhas para compra; abaixo para venda',reason='Preço não confirmou o lado das três linhas.'),
        dict(code='ATR_CANDLE',passed=candle,observed=close_position,required='Corpo direcional e fechamento nos 40% favoráveis',reason='Vela sem confirmação direcional.')]
    for c in checks:
        if c['passed']:c['reason']=''
    passed=all(c['passed'] for c in checks)
    rsi=float(current['rsi']);momentum=50<=rsi<=75 if consensus==1 else 25<=rsi<=50
    strong_close=close_position>=.75 if consensus==1 else close_position<=.25
    score=float(80+10*momentum+10*strong_close) if passed else 0.
    structure=dict(stop_support=float(min(levels)) if consensus==1 else None,
        stop_resistance=float(max(levels)) if consensus==-1 else None,**confirmed_targets(df,price),
        target_rr_projection=params.get('trend_target_r',2.))
    return dict(action=side if passed else 'NEUTRAL',score=score,structure=structure,
        reason=f'Triplo ATR {side}: novo alinhamento e confirmação; score técnico {score:g}/100.' if passed else ' '.join(c['reason'] for c in checks if not c['passed']),
        confirmation=dict(version=POLICY_VERSION,checked_action=side,passed=passed,checks=checks,
            trigger='TRIPLE_ATR_ALIGNMENT' if passed else None,trend='UP' if consensus==1 else 'DOWN' if consensus==-1 else 'MIXED',
            context_timeframe='M1',period=PERIOD,multipliers=list(MULTIPLIERS),
            atr=float(volatility[-1]),lines=[float(x) for x in levels],directions=last.tolist(),
            score_components=dict(mandatory=80 if passed else 0,rsi=10 if passed and momentum else 0,close_strength=10 if passed and strong_close else 0)))
