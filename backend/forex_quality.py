"""Causal Forex RF entry confirmation; no SDK IO or signal generation."""
import pandas as pd

POLICY_VERSION='forex-quality-v3'


def entry_confirmation(df,timeframe,action,atr):
    """Use only CLOSED input bars; M1 context uses complete five-bar M5 bins."""
    current=df.iloc[-1];previous=df.iloc[-2]
    o,h,l,c=(float(current[k]) for k in ('open','high','low','close'))
    po,ph,pl,pc=(float(previous[k]) for k in ('open','high','low','close'))
    body=abs(c-o);span=h-l
    lower=min(o,c)-l;upper=h-max(o,c)
    patterns=dict(
        BUY=dict(breakout=c>ph,engulfing=c>o and pc<po and o<=pc and c>=po,
                 rejection=c>o and lower>=max(body,.1*atr) and span>0 and (c-l)/span>=.6),
        SELL=dict(breakout=c<pl,engulfing=c<o and pc>po and o>=pc and c<=po,
                  rejection=c<o and upper>=max(body,.1*atr) and span>0 and (c-l)/span<=.4))
    if timeframe=='M1':
        frame=df[['time','close']].copy();frame['time']=pd.to_datetime(frame['time'])
        bucket=frame['time'].dt.floor('5min')
        grouped=frame.groupby(bucket)['close'].agg(['last','count'])
        context=grouped[grouped['count']==5]['last']
        expected=(frame['time'].iloc[-1]+pd.Timedelta(minutes=1)).floor('5min')-pd.Timedelta(minutes=5)
        complete=not context.empty and context.index[-1]==expected
    else:
        context=df.set_index('time')['close'];complete=True
    ready=complete and len(context)>=24
    trend='UNAVAILABLE';fast=slow=slope=None
    if ready:
        ema_fast=context.ewm(span=9,adjust=False).mean()
        ema_slow=context.ewm(span=21,adjust=False).mean()
        fast=float(ema_fast.iloc[-1]);slow=float(ema_slow.iloc[-1])
        slope=float(ema_slow.iloc[-1]-ema_slow.iloc[-4])
        if fast>slow and slope>0 and context.iloc[-1]>slow:trend='UP'
        elif fast<slow and slope<0 and context.iloc[-1]<slow:trend='DOWN'
        else:trend='MIXED'
    checks=[]
    if action in ('BUY','SELL'):
        opposite='DOWN' if action=='BUY' else 'UP'
        checks=[dict(code='FOREX_CONTEXT',passed=ready and trend!=opposite,
                     observed=trend,required=f'Contexto disponível e diferente de {opposite}',
                     reason='Contexto de tendência contrário ou incompleto.'),
                dict(code='PRICE_ACTION_CONFIRMATION',passed=any(patterns[action].values()),
                     observed=[name for name,passed in patterns[action].items() if passed],
                     required='Rompimento, engolfo ou rejeição na direção da entrada.',
                     reason='Sem confirmação de price action na vela fechada.')]
    for check in checks:
        if check['passed']:check['reason']=''
    return dict(version=POLICY_VERSION,checked_action=action,
                passed=all(x['passed'] for x in checks) if checks else None,checks=checks,
                context_timeframe='M5' if timeframe=='M1' else timeframe,
                context_bars=len(context),context_last_bar=str(context.index[-1]) if len(context) else None,
                trend=trend,ema_fast=fast,ema_slow=slow,ema_slow_slope=slope,patterns=patterns)
