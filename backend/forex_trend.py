"""Independent, causal M1 trend continuation with CLOSED M5 context.

Scores count explicit technical rules, never model win probabilities.
"""
from backend.forex_quality import entry_confirmation

POLICY_VERSION='forex-trend-v1'


def continuation_signal(df,params,atr):
    context=entry_confirmation(df,'M1','NEUTRAL',atr)
    trend=context['trend'];side='BUY' if trend=='UP' else 'SELL' if trend=='DOWN' else 'NEUTRAL'
    current=df.iloc[-1];prev=df.iloc[-2];price=float(current['close'])
    direction=1 if side=='BUY' else -1
    fast=float(current['ema_fast']);slow=float(current['ema_slow'])
    lookback=params.get('trend_breakout_lookback',5)
    prior=df.iloc[-lookback-1:-1];pullback=df.iloc[-4:-1]
    boundary=float(prior['high'].max() if side=='BUY' else prior['low'].min())
    breakout=side!='NEUTRAL' and direction*(price-boundary)>.05*atr
    if side=='BUY':
        touched=bool(((pullback['low']<=pullback['ema_fast']+.15*atr)&(pullback['close']<pullback['open'])).any())
        resumed=price>float(prev['high']) and price>fast
    else:
        touched=bool(((pullback['high']>=pullback['ema_fast']-.15*atr)&(pullback['close']>pullback['open'])).any())
        resumed=price<float(prev['low']) and price<fast
    retrace=side!='NEUTRAL' and touched and resumed
    span=float(current['high']-current['low'])
    close_position=(price-float(current['low']))/span if span>0 else .5
    candle=side!='NEUTRAL' and direction*(price-float(current['open']))>0 and (close_position>=.6 if side=='BUY' else close_position<=.4)
    local=side!='NEUTRAL' and direction*(fast-slow)>0 and direction*(price-slow)>0 and direction*(fast-float(df.iloc[-4]['ema_fast']))>0
    extension=abs(price-fast)/atr
    checks=[dict(code='TREND_CONTEXT',passed=side!='NEUTRAL',observed=trend,required='UP ou DOWN no M5 completo',reason='M5 sem tendência confirmada ou histórico incompleto.'),
            dict(code='TREND_LOCAL',passed=local,observed=side,required='EMA e preço M1 alinhados ao M5',reason='M1 não acompanha a tendência M5.'),
            dict(code='TREND_TRIGGER',passed=breakout or retrace,observed='BREAKOUT' if breakout else 'PULLBACK' if retrace else 'NONE',required=f'Rompimento de {lookback} velas ou retomada após pullback',reason='Aguardando rompimento ou retomada após pullback.'),
            dict(code='TREND_CANDLE',passed=candle,observed=close_position,required='Corpo direcional e fechamento nos 40% favoráveis',reason='Vela sem confirmação direcional.'),
            dict(code='TREND_EXTENSION',passed=extension<=params.get('trend_max_extension_atr',1.5),observed=extension,required=params.get('trend_max_extension_atr',1.5),reason='Preço esticado em relação à EMA rápida; não perseguir entrada.')]
    for check in checks:
        if check['passed']:check['reason']=''
    passed=all(check['passed'] for check in checks)
    # Mandatory structure/context/trigger/candle = 80; optional evidence = 20.
    rsi=float(current['rsi']);momentum=(50<=rsi<=75) if side=='BUY' else (25<=rsi<=50)
    strong_close=close_position>=.75 if side=='BUY' else close_position<=.25
    score=80+10*momentum+10*strong_close if passed else 0
    stops=df.tail(params.get('trend_stop_lookback',3))
    # Only confirmed pivots strictly ahead can cap an ATR/R target. The latest
    # unconfirmed wick is not automatically treated as an opposing barrier.
    w=df.tail(60);highs=w['high'].tolist();lows=w['low'].tolist();supports=[];resistances=[]
    for i in range(2,len(w)-2):
        hi=highs[i]>max(highs[i-2:i]+highs[i+1:i+3])
        lo=lows[i]<min(lows[i-2:i]+lows[i+1:i+3])
        if hi and not lo and highs[i]>price:resistances.append(float(highs[i]))
        if lo and not hi and lows[i]<price:supports.append(float(lows[i]))
    structure=dict(stop_support=float(stops['low'].min()),stop_resistance=float(stops['high'].max()),
                   target_support=max(supports) if supports else None,
                   target_resistance=min(resistances) if resistances else None,
                   target_rr_projection=params.get('trend_target_r',2.))
    return dict(action=side if passed else 'NEUTRAL',score=float(score),structure=structure,
                reason=(f"Continuidade {side}: {'rompimento' if breakout else 'retomada de pullback'} M1 alinhado ao M5; score técnico {score}/100." if passed else
                        ' '.join(check['reason'] for check in checks if not check['passed'])),
                confirmation={**context,'version':POLICY_VERSION,'checked_action':side,'passed':passed,'checks':checks,
                              'trigger':'BREAKOUT' if breakout else 'PULLBACK' if retrace else None,
                              'score_components':dict(mandatory=80 if passed else 0,rsi=10 if passed and momentum else 0,close_strength=10 if passed and strong_close else 0)})
