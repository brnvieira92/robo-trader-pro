"""Read-only comparisons of persisted decisions; counts never drive trading."""
from collections import Counter
from backend.diagnostics import LABELS


def pair_report(journal, start, end, mode, login=None, server=None):
    groups={}; attempts={}; executions={}
    # Stream the interval, rather than silently truncating to an API page.
    with journal.connection() as db:
        rows=db.execute('SELECT symbol,timeframe,bar_time,kind,code,last_seen,payload FROM diagnostic_events WHERE last_seen>=? AND last_seen<=? AND kind IN (\'ANALYSIS\',\'RADAR\',\'DECISION\',\'EXECUTION\',\'ERROR\') ORDER BY last_seen,id',(start,end))
        import json
        for symbol,tf,bar,kind,code,stamp,raw in rows:
            p=json.loads(raw)
            cfg=p.get('config',{})
            if p.get('mode',cfg.get('execution_mode'))!=mode: continue
            if login is not None and (p.get('account_login',cfg.get('mt5_account_login'))!=login or p.get('account_server',cfg.get('mt5_account_server'))!=server): continue
            if kind=='ANALYSIS' and cfg.get('market_type')!='MT5_FOREX': continue
            bucket=groups.setdefault((symbol,tf),dict(candles={},errors=Counter(),last_seen=stamp))
            bucket['last_seen']=stamp
            if kind in ('ANALYSIS','RADAR') and bar:
                # Latest projection for a candle wins. Polling and gate transitions
                # on that candle do not become additional independent opportunities.
                key=str(bar)
                old=bucket['candles'].get(key,{})
                checks={g['code']:g for g in old.get('checks',[])}
                checks.update({g['code']:g for g in p.get('checks',[])})
                bucket['candles'][key]={**old,**p,'checks':list(checks.values()),'latest_code':code}
            elif kind=='ERROR': bucket['errors'][code]+=1
            elif kind=='DECISION': attempts[(symbol,tf,p.get('attempt_id') or p.get('analysis_id') or stamp)]=code
            elif kind=='EXECUTION': executions[(symbol,tf,p.get('trade_id') or stamp)]=code
    result=[]
    for (symbol,tf),bucket in sorted(groups.items()):
        failures=Counter(); validated=score_pass=qualified=candidates=0; sources=Counter()
        for p in bucket['candles'].values():
            gates={g['code']:g['status'] for g in p.get('checks',[]) if not g.get('informational')}
            validated+=int(gates.get('ML_VALIDATED')=='PASS')
            score_pass+=int(gates.get('MIN_CONFIDENCE')=='PASS')
            required=('SIGNAL_DIRECTION','MIN_CONFIDENCE','VOLATILITY') if p.get('entry_policy_version') in ('forex-trend-v1','forex-pullback-v1') else ('ML_VALIDATED','SIGNAL_DIRECTION','MIN_CONFIDENCE','VOLATILITY')
            qualified+=int(all(gates.get(k)=='PASS' for k in required))
            candidates+=int(bool(gates) and all(g!='FAIL' for g in gates.values()) and gates.get('AUTOMATION_REQUESTED')=='PASS'
                and p.get('latest_code') in ('CANDIDATE','CANDIDATO') and not p.get('blocked_reason',p.get('first_block')))
            failures.update(k for k,v in gates.items() if v=='FAIL')
            sources[p.get('data_quality',{}).get('source','UNKNOWN')]+=1
        decisions=Counter(v for (s,t,_),v in attempts.items() if (s,t)==(symbol,tf))
        sent=Counter(v for (s,t,_),v in executions.items() if (s,t)==(symbol,tf))
        result.append(dict(symbol=symbol,timeframe=tf,analyzed_candles=len(bucket['candles']),
            ml_validated_candles=validated,score_passed_candles=score_pass,signal_qualified_candles=qualified,
            candidate_candles=candidates,preflight_approved=decisions['PREFLIGHT_APPROVED'],
            preflight_rejected=sum(v for k,v in decisions.items() if k not in ('PREFLIGHT_APPROVED','REVALIDATION_BLOCKED')),
            revalidation_blocked=decisions['REVALIDATION_BLOCKED'],
            preflight_rejection_reasons={k:v for k,v in decisions.items() if k not in ('PREFLIGHT_APPROVED','REVALIDATION_BLOCKED')},
            preflight_rejection_details=[dict(code=k,label=LABELS.get(k,k),attempts=v) for k,v in decisions.items() if k not in ('PREFLIGHT_APPROVED','REVALIDATION_BLOCKED')],
            recorded_intents=sum(sent.values()),execution_states=dict(sent),errors=dict(bucket['errors']),
            blocks=[dict(code=k,label=LABELS.get(k,k),candles=v) for k,v in failures.most_common()],
            sources=dict(sources),last_seen=bucket['last_seen']))
    return dict(start=start,end=end,mode=mode,account_login=login,account_server=server,rows=result,
        counting='Última projeção de cada símbolo/timeframe/candle nos registros selecionados por last_seen. Não conta polls nem observações como oportunidades.',
        limitations=['Bloqueios podem coexistir; suas contagens não devem ser somadas como oportunidades distintas.',
            'Registros antigos sem filtros detalhados têm cobertura parcial; ausente não significa aprovado.',
            'Intervalo usa a última observação persistida do evento, em UTC. Não reconstrói o estado passado de eventos atualizados depois do intervalo.',
            'Envio de intenção não comprova preenchimento nem resultado financeiro.'])
