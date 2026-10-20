from backend.journal import PaperJournal
from backend.pair_report import pair_report


def record(journal,key,kind,bar,stamp,payload,code='BLOCKED',symbol='EURUSD#'):
    journal.record_diagnostic(dict(event_key=key,kind=kind,symbol=symbol,timeframe='M1',bar_time=bar,code=code,
        observed_at=stamp,payload={'mode':'LIVE_MT5','account_login':123,'account_server':'Demo',**payload}))


def test_comparison_deduplicates_polls_and_uses_latest_candle_projection(tmp_path):
    j=PaperJournal(tmp_path/'report.db')
    cfg={'market_type':'MT5_FOREX'}
    def checks(passed):return [dict(code=k,status='PASS' if passed else 'FAIL') for k in ('ML_VALIDATED','MIN_CONFIDENCE','SIGNAL_DIRECTION','VOLATILITY','AUTOMATION_REQUESTED')]
    for _ in range(20):record(j,'old','ANALYSIS','bar-1',10,dict(config=cfg,checks=checks(False)))
    record(j,'new','ANALYSIS','bar-1',11,dict(config=cfg,checks=checks(True)),code='CANDIDATE')
    record(j,'radar','RADAR','bar-1',12,dict(checks=[dict(code='SYMBOL_TRADE_MODE',status='FAIL')]))
    record(j,'bar-2','ANALYSIS','bar-2',13,dict(config=cfg,checks=checks(False)))
    record(j,'pre-1','DECISION','bar-1',13,dict(attempt_id='attempt-1'),code='SPREAD_LIMIT')
    record(j,'pre-2','DECISION','bar-1',14,dict(attempt_id='attempt-2'),code='PREFLIGHT_APPROVED')
    record(j,'exec','EXECUTION','bar-1',14,dict(trade_id='trade-1'),code='PENDING')
    result=pair_report(j,0,20,'LIVE_MT5',123,'Demo');r=result['rows'][0]
    assert r['analyzed_candles']==2 and r['ml_validated_candles']==1 and r['score_passed_candles']==1
    assert r['signal_qualified_candles']==1 and r['candidate_candles']==0
    assert r['preflight_approved']==r['preflight_rejected']==r['recorded_intents']==1
    assert {g['code']:g['candles'] for g in r['blocks']}['SYMBOL_TRADE_MODE']==1


def test_report_separates_accounts_modes_periods_and_timeframes(tmp_path):
    j=PaperJournal(tmp_path/'report.db')
    p=dict(config={'market_type':'MT5_FOREX'},checks=[])
    record(j,'one','ANALYSIS','a',10,p)
    record(j,'other-account','ANALYSIS','b',11,dict(p,account_login=999))
    record(j,'paper','ANALYSIS','c',12,dict(p,mode='PAPER'))
    record(j,'later','ANALYSIS','d',30,p)
    r=pair_report(j,0,20,'LIVE_MT5',123,'Demo')
    assert r['rows'][0]['analyzed_candles']==1
    assert not pair_report(j,0,5,'LIVE_MT5',123,'Demo')['rows']


def test_operational_block_never_becomes_a_candidate_when_signal_gates_pass(tmp_path):
    j=PaperJournal(tmp_path/'report.db')
    checks=[dict(code=k,status='PASS') for k in ('ML_VALIDATED','MIN_CONFIDENCE','SIGNAL_DIRECTION','VOLATILITY','AUTOMATION_REQUESTED')]
    record(j,'blocked','ANALYSIS','a',10,dict(config={'market_type':'MT5_FOREX'},checks=checks,first_block='Persistência pendente.'),code='BLOCKED')
    row=pair_report(j,0,20,'LIVE_MT5',123,'Demo')['rows'][0]
    assert row['signal_qualified_candles']==1 and row['candidate_candles']==0


def test_report_reader_does_not_block_audit_writes(tmp_path):
    j=PaperJournal(tmp_path/'report.db')
    p=dict(config={'market_type':'MT5_FOREX'},checks=[])
    record(j,'one','ANALYSIS','a',10,p)
    record(j,'two','ANALYSIS','b',11,p)
    with j.connection() as reader:
        reader.execute('BEGIN')
        reader.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()
        record(j,'three','ANALYSIS','c',12,p)
        assert reader.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()[0]==2
    assert j.diagnostic_count()==3
