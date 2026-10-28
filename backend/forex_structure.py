"""Closed-bar structural pivot/retest strategy; no order IO or future pivots.

Replay the bounded input chronologically. Setups retain frozen thresholds and
can confirm once only, irrespective of a later execution/context rejection.
"""
from backend.forex_quality import entry_confirmation
from backend.forex_trend import confirmed_targets

POLICY_VERSION='forex-structure-retest-v1'
PIVOT_BARS=2
MAX_PATTERN_SPAN=60
MIN_LEG_ATR=1.
WAIT_BREAKOUT=20
BREAKOUT_MARGIN=.05
RETEST_ZONE=.20
WAIT_RETEST=12
WAIT_CONFIRMATION=4


def pivot_retest_event(df):
    """Return last-bar lifecycle diagnostics, using causal pivot confirmations.

    Prices/indices of future bars are never used before their confirmation step.
    Outside bars with both extrema are omitted: their intrabar order is unknown.
    """
    highs=df['high'].to_numpy(dtype=float);lows=df['low'].to_numpy(dtype=float)
    closes=df['close'].to_numpy(dtype=float);opens=df['open'].to_numpy(dtype=float)
    atrs=df['atr'].to_numpy(dtype=float);times=df['time'].astype(str).tolist()
    pivots=[];active={};latest=None;last=len(df)-1
    for j in range(2*PIVOT_BARS,len(df)):
        i=j-PIVOT_BARS
        hi=highs[i]>max(*highs[i-2:i],*highs[i+1:j+1])
        lo=lows[i]<min(*lows[i-2:i],*lows[i+1:j+1])
        if hi!=lo:
            pivot=dict(kind='HIGH' if hi else 'LOW',index=i,price=float(highs[i] if hi else lows[i]),time=times[i],confirmed_at=times[j])
            changed=False
            if pivots and pivots[-1]['kind']==pivot['kind']:
                if (pivot['price']-pivots[-1]['price'])*(1 if hi else -1)>0:
                    pivots[-1]=pivot;changed=True
            else:pivots.append(pivot);changed=True
            if changed and len(pivots)>=3:
                p1,p2,p3=pivots[-3:];direction=1 if p1['kind']=='LOW' else -1
                volatility=float(atrs[j])
                valid=(volatility>0 and p3['index']-p1['index']<=MAX_PATTERN_SPAN and
                       direction*(p3['price']-p1['price'])>0 and direction*(p2['price']-p3['price'])>0 and
                       direction*(p2['price']-p1['price'])>=MIN_LEG_ATR*volatility)
                prior=active.get(direction)
                if valid and (prior is None or prior['phase'] in ('INVALIDATED','EXPIRED','CONSUMED','WAIT_BREAKOUT')):
                    setup=dict(id=f"{POLICY_VERSION}:{direction}:{p1['time']}:{p2['time']}:{p3['time']}",
                        direction=direction,points=[dict(p) for p in (p1,p2,p3)],created_index=j,confirmed_at=times[j],
                        level=p2['price'],invalidation=p3['price'],atr=volatility,phase='WAIT_BREAKOUT',
                        breakout_index=None,breakout_at=None,retest_index=None,retest_at=None,confirmation_at=None)
                    if prior is None or setup['id']!=prior['id']:active[direction]=setup;latest=setup
        for direction,setup in active.items():
            if setup['phase'] in ('INVALIDATED','EXPIRED','CONSUMED'):continue
            if direction*((lows[j] if direction==1 else highs[j])-setup['invalidation'])<=0:
                setup.update(phase='INVALIDATED',terminal_at=times[j]);continue
            phase=setup['phase']
            origin=setup['created_index'] if phase=='WAIT_BREAKOUT' else setup['breakout_index'] if phase=='WAIT_RETEST' else setup['retest_index']
            lifetime=WAIT_BREAKOUT if phase=='WAIT_BREAKOUT' else WAIT_RETEST if phase=='WAIT_RETEST' else WAIT_CONFIRMATION
            if j-origin>lifetime:
                setup.update(phase='EXPIRED',terminal_at=times[j]);continue
            margin=BREAKOUT_MARGIN*setup['atr'];level=setup['level']
            if phase=='WAIT_BREAKOUT':
                if direction*(closes[j]-level)>margin and direction*(closes[j-1]-level)<=margin:
                    setup.update(phase='WAIT_RETEST',breakout_index=j,breakout_at=times[j]);latest=setup
            elif phase=='WAIT_RETEST':
                zone=RETEST_ZONE*setup['atr']
                if j>setup['breakout_index'] and lows[j]<=level+zone and highs[j]>=level-zone and direction*(closes[j]-level)>=-zone:
                    setup.update(phase='WAIT_CONFIRMATION',retest_index=j,retest_at=times[j]);latest=setup
            elif phase=='WAIT_CONFIRMATION':
                span=highs[j]-lows[j];location=(closes[j]-lows[j])/span if span>0 else .5
                boundary=highs[j-1] if direction==1 else lows[j-1]
                candle=direction*(closes[j]-opens[j])>0 and (location>=.65 if direction==1 else location<=.35)
                if j>setup['retest_index'] and direction*(closes[j]-boundary)>margin and direction*(closes[j]-level)>0 and candle:
                    setup.update(phase='CONSUMED',confirmation_at=times[j]);latest=setup
                    if j==last:return dict(setup),True
    # Prefer the most recently created lifecycle, not a stale opposite setup.
    if active:latest=max(active.values(),key=lambda s:s['created_index'])
    return dict(latest) if latest else None,False


def structure_retest_signal(df,params,atr):
    context=entry_confirmation(df,'M1','NEUTRAL',atr)
    setup,fresh=pivot_retest_event(df)
    side='BUY' if setup and setup['direction']==1 else 'SELL' if setup else 'NEUTRAL'
    phase=setup['phase'] if setup else 'NO_PATTERN'
    trend=context['trend'];aligned=trend==('UP' if side=='BUY' else 'DOWN') if setup else False
    integrity=bool(setup and phase not in ('INVALIDATED','EXPIRED'))
    stage_reasons=dict(NO_PATTERN='Aguardando pivô 1-2-3 confirmado.',WAIT_BREAKOUT='Pivô confirmado; aguardando rompimento do ponto 2.',
        WAIT_RETEST='Rompimento confirmado; aguardando reteste do nível.',WAIT_CONFIRMATION='Reteste observado; aguardando retomada em outra vela fechada.',
        INVALIDATED='Ponto 3 violado; estrutura invalidada.',EXPIRED='Janela do setup encerrada; aguardando nova estrutura.',
        CONSUMED='Setup já confirmado em vela anterior; aguardando nova estrutura.')
    checks=[dict(code='STRUCTURE_PATTERN',passed=bool(setup),observed=setup['points'] if setup else None,required='Pivô 1-2-3 confirmado, amplitude >=1ATR e duração <=60 velas',reason='' if setup else stage_reasons[phase]),
        dict(code='STRUCTURE_CONTEXT',passed=aligned,observed=trend,required='M5 completo UP para compra / DOWN para venda',reason='' if aligned else 'Contexto M5 contrário, misto ou incompleto.'),
        dict(code='STRUCTURE_INTEGRITY',passed=integrity,observed=phase,required='Estrutura válida e dentro da janela',reason='' if integrity else stage_reasons[phase]),
        dict(code='STRUCTURE_BREAKOUT',passed=bool(setup and setup['breakout_at']),observed=setup['breakout_at'] if setup else None,required='Fechamento cruza ponto 2 +0.05ATR',reason='' if setup and setup['breakout_at'] else 'Aguardando rompimento após confirmação do pivô.'),
        dict(code='STRUCTURE_RETEST',passed=bool(setup and setup['retest_at']),observed=setup['retest_at'] if setup else None,required='Reteste posterior ao rompimento, zona 0.20ATR',reason='' if setup and setup['retest_at'] else 'Aguardando reteste em vela posterior ao rompimento.'),
        dict(code='STRUCTURE_RESUMPTION',passed=fresh,observed=setup['confirmation_at'] if setup else None,required='Retomada nesta vela, após reteste; corpo direcional e 35% favoráveis',reason='' if fresh else stage_reasons[phase])]
    passed=all(c['passed'] for c in checks)
    current=df.iloc[-1];span=float(current['high']-current['low']);price=float(current['close'])
    location=(price-float(current['low']))/span if span>0 else .5
    rsi=float(current['rsi']);momentum=50<=rsi<=75 if side=='BUY' else 25<=rsi<=50
    strong=location>=.75 if side=='BUY' else location<=.25
    score=float(80+10*momentum+10*strong) if passed else 0.
    structure=dict(stop_support=setup['invalidation'] if setup and side=='BUY' else None,
        stop_resistance=setup['invalidation'] if setup and side=='SELL' else None,**confirmed_targets(df,price))
    return dict(action=side if passed else 'NEUTRAL',score=score,structure=structure,
        reason=f'Pivô estrutural {side}: rompimento, reteste e retomada confirmados; score técnico {score:g}/100.' if passed else
            ' '.join(dict.fromkeys(c['reason'] for c in checks if not c['passed'])),
        confirmation={**context,'version':POLICY_VERSION,'checked_action':side,'passed':passed,'checks':checks,
            'trigger':'PIVOT_RETEST' if passed else None,'phase':'CONFIRMED' if fresh else phase,'setup':setup,
            'score_components':dict(mandatory=80 if passed else 0,rsi=10 if passed and momentum else 0,close_strength=10 if passed and strong else 0)})
