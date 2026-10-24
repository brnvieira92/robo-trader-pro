import time
from threading import Event
from test_api import client
from test_forex_research import request_data, fixed_signal


def test_experimental_strategy_config_is_rejected_without_mutating_execution(client):
    import copy
    import backend.main as main
    before=copy.deepcopy(main.bot.config)
    response=client.post('/api/config',json={'market_type':'MT5_FOREX','strategy_id':'forex_pullback_retest'})
    assert response.status_code==409
    assert 'somente no laboratório' in response.json()['detail']
    assert main.bot.config==before and not main.bot.trade_history


def test_background_job_exports_persistence_and_trade_isolation(client,monkeypatch):
    import backend.main as main
    from backend.forex_backtest import run_forex_backtest
    fixed_signal(monkeypatch)
    started=Event();release=Event()
    def slow(request,progress):
        started.set();assert release.wait(5)
        return run_forex_backtest(request,progress)
    monkeypatch.setattr(main,'run_forex_backtest',slow)
    before=main.bot.config.copy()
    payload=request_data();payload['exit_management']=dict(mode='BREAK_EVEN_ATR',activation_r=1,trailing_atr_mult=2)
    response=client.post('/api/research/forex/jobs',json=payload)
    assert response.status_code==202
    job_id=response.json()['id'];assert started.wait(5)
    try:
        assert client.post('/api/bot/toggle').status_code==409
        assert client.post('/api/config',json={'entry_amount':9}).status_code==409
        assert client.post('/api/order/manual',json={'action':'BUY'}).status_code==409
        assert client.post('/api/research/forex/jobs',json=request_data()).status_code==409
    finally:release.set()
    for _ in range(100):
        result=client.get('/api/research/forex/jobs/'+job_id).json()
        latest=client.get('/api/research/forex/latest').json()
        if result['state']=='DONE' and latest.get('id')==job_id:break
        time.sleep(.02)
    assert result['state']=='DONE' and latest['id']==job_id
    assert main.bot.config==before and not main.bot.trade_history and not main.bot.is_running
    assert result['result']['kind']=='FOREX_SL_TP'
    assert result['result']['assumptions']['exit_management']['mode']=='BREAK_EVEN_ATR'
    for kind in ('json','analyses','trades'):
        res=client.get(f'/api/research/forex/jobs/{job_id}/export?kind={kind}')
        assert res.status_code==200 and 'attachment' in res.headers['content-disposition']
    assert client.get('/api/research/forex/jobs/unknown').status_code==404


def test_failed_research_releases_safety_lock_without_changing_automation(client,monkeypatch):
    import backend.main as main
    def fail(*a):raise ValueError('Historical fixture failure')
    monkeypatch.setattr(main,'run_forex_backtest',fail)
    response=client.post('/api/research/forex/jobs',json=request_data())
    job_id=response.json()['id']
    for _ in range(100):
        state=client.get('/api/research/forex/jobs/'+job_id).json()
        if state['state']!='RUNNING':break
        time.sleep(.01)
    assert state['state']=='FAILED' and not main.research_lock.locked()
    assert not main.bot.is_running and not getattr(main.bot,'_research_busy')
    assert not main.bot.trade_history


def test_report_is_read_only_and_rejects_mixed_account_scope(client):
    import backend.main as main
    before=main.bot.journal.diagnostic_count()
    assert client.get('/api/diagnostics/pairs').status_code==409
    response=client.get('/api/diagnostics/pairs?mode=PAPER')
    assert response.status_code==200
    assert client.get('/api/diagnostics/pairs?mode=PAPER&start=20&end=1').status_code==422
    assert main.bot.journal.diagnostic_count()==before


def test_history_capture_does_not_fall_back_to_fake_prices(client):
    response=client.post('/api/research/forex/history')
    assert response.status_code==409
    assert 'Falha ao carregar dados reais' in response.json()['detail']


def test_detailed_gate_values_do_not_mutate_or_approve_the_signal(client):
    import copy
    from backend.diagnostics import analysis_event
    snapshot=client.get('/api/state').json()
    snapshot['config']['strategy_id']='ml_random_forest'
    snapshot['signal'].update(action='NEUTRAL',confidence=0)
    snapshot['signal']['indicators']['ml_validation'].update(enabled=True,validated=False,accuracy=42.1,
        baseline_accuracy=50.9,balanced_accuracy=41.7,brier_score=.2635,ml_confidence=80,
        validation_checks=dict(accuracy=False,balanced=False,brier=False))
    before=copy.deepcopy(snapshot)
    event=analysis_event(snapshot,None,0)
    checks={g['code']:g for g in event['payload']['checks']}
    assert snapshot==before
    assert checks['ML_ESTIMATE_MIN']['observed']==80 and checks['ML_ESTIMATE_MIN']['informational']
    assert checks['MIN_CONFIDENCE']['observed']==0 and checks['MIN_CONFIDENCE']['required']==78
    assert checks['ML_BRIER']['required']==.25 and checks['ML_BRIER']['status']=='FAIL'
    assert checks['ML_VALIDATED']['status']=='FAIL'


def test_research_waits_for_paused_sdk_cycle_without_overlapping(client):
    import backend.main as main
    from threading import Timer
    main.bot._mt5_busy=True
    timer=Timer(.1,lambda:setattr(main.bot,'_mt5_busy',False));timer.start()
    try:
        response=client.post('/api/research/forex/jobs',json=request_data())
        assert response.status_code==202,response.text
    finally:
        timer.join();main.bot._mt5_busy=False
