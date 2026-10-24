"""Frozen batch-02 hypotheses. Research only; closed candles, no fitting."""
from backend.forex_trend import confirmed_targets

CANDIDATES=('forex_range_reentry','forex_squeeze_breakout')


def research_signal(df, params, atr, strategy):
    current, previous=df.iloc[-1],df.iloc[-2]
    price=float(current['close']); span=float(current['high']-current['low'])
    location=(price-float(current['low']))/span if span>0 else .5
    body=price-float(current['open']); side=0; checks=[]
    def check(code, passed, observed, required):
        checks.append(dict(code=code,passed=bool(passed),observed=observed,required=required,
                           reason='' if passed else f'{code}: condição não atendida.'))
    if strategy=='forex_range_reentry':
        changes=df['close'].iloc[-21:].diff().abs().sum()
        efficiency=abs(price-float(df['close'].iloc[-21]))/changes if changes else 0.
        if previous['close']<previous['bb_lower'] and current['bb_lower']<price<current['bb_upper']:side=1
        elif previous['close']>previous['bb_upper'] and current['bb_lower']<price<current['bb_upper']:side=-1
        check('RANGE_EFFICIENCY',efficiency<=.35,float(efficiency),'<= 0.35')
        check('BAND_REENTRY',side!=0,side,'Fechamento anterior fora e atual dentro das bandas')
        candle=side*body>0 and (location>=.65 if side==1 else location<=.35)
        check('REJECTION_CANDLE',candle,location,'Corpo direcional e 35% favoráveis')
        rsi=float(current['rsi']); rsi_ok=rsi<=50 if side==1 else rsi>=50
        check('RANGE_RSI',rsi_ok,rsi,'Compra <=50; venda >=50')
        mean=float(current['bb_mid'])
        check('MEAN_AHEAD',side*(mean-price)>0,mean,'Média à frente da entrada')
        extra=10*(float(previous['rsi'])<=35 if side==1 else float(previous['rsi'])>=65)
        extra+=10*(location>=.75 if side==1 else location<=.25)
        structure=dict(mandatory_mean_target=mean,target_resistance=mean if side==1 else None,target_support=mean if side==-1 else None)
        trigger='BAND_REENTRY';version='forex-range-reentry-v1'
    elif strategy=='forex_squeeze_breakout':
        bandwidth=(df['bb_upper']-df['bb_lower'])/df['bb_mid']
        # The assessed prior bar is excluded from the reference distribution.
        threshold=float(bandwidth.iloc[-62:-2].quantile(.35))
        prior=float(bandwidth.iloc[-2])
        high=float(df['high'].iloc[-11:-1].max());low=float(df['low'].iloc[-11:-1].min())
        if price>high+.05*atr:side=1
        elif price<low-.05*atr:side=-1
        check('SQUEEZE_HISTORY',len(df)>=100,len(df),'>=100 velas fechadas')
        check('PRIOR_COMPRESSION',prior<=threshold,prior,f'<= quantil histórico {threshold:g}')
        check('TEN_BAR_BREAKOUT',side!=0,price,'Rompimento de 10 velas +0.05ATR')
        check('BREAKOUT_CANDLE',side*body>0 and (location>=.7 if side==1 else location<=.3),location,'Corpo direcional e 30% favoráveis')
        extra=10*(float(current['volume'])>float(current['vol_sma']))
        extra+=10*(location>=.85 if side==1 else location<=.15)
        structure=confirmed_targets(df,price);trigger='SQUEEZE_BREAKOUT';version='forex-squeeze-breakout-v1'
    else:raise ValueError('Unknown research candidate')
    passed=all(c['passed'] for c in checks)
    action='BUY' if side==1 else 'SELL' if side==-1 else 'NEUTRAL'
    structure.update(stop_support=float(df['low'].iloc[-3:].min()),stop_resistance=float(df['high'].iloc[-3:].max()))
    return dict(action=action if passed else 'NEUTRAL',score=float(80+extra) if passed else 0.,structure=structure,
        reason=f'{trigger} {action}: regras técnicas aprovadas.' if passed else ' '.join(c['reason'] for c in checks if not c['passed']),
        confirmation=dict(version=version,checked_action=action,passed=passed,checks=checks,trigger=trigger if passed else None,context_timeframe='M1'))
