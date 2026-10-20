"""Single-pair audit and recovery without a real broker or Telegram API."""
import asyncio
import csv
import io
import sqlite3
import time
from unittest.mock import AsyncMock
import pytest
from backend.bot_manager import BotManager
from backend.diagnostics import analysis_event
from test_mt5_live import live_bot, signal

@pytest.fixture
def bot(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADER_JOURNAL_PATH',str(tmp_path/'decisions.db'))
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN',raising=False)
    return BotManager()


def test_analysis_survives_restart_and_panel_reads_are_deduplicated(bot):
    first=bot.get_market_snapshot()
    again=bot.get_market_snapshot()
    assert first['analysis_id']==again['analysis_id']
    events=bot.journal.diagnostics(kind='ANALYSIS')
    assert len(events)==1
    assert events[0]['payload']['analysis_id']==first['analysis_id']
    assert events[0]['payload']['config_version']
    assert events[0]['payload']['model_version']
    assert events[0]['payload']['raw_signal']
    assert events[0]['payload']['validation']['dataset_version']
    restored=BotManager()
    assert restored.journal.diagnostics(kind='ANALYSIS')==events
    assert not restored.is_running


def test_diagnostic_gate_explains_ml_rejection_without_changing_threshold(bot):
    snapshot=bot.get_market_snapshot()
    snapshot['config']['strategy_id']='ml_random_forest'
    snapshot['signal'].update(action='NEUTRAL',confidence=0)
    snapshot['signal']['indicators']['ml_validation'].update(enabled=True,validated=False,accuracy=42.1,baseline_accuracy=50.9,
        balanced_accuracy=41.7,brier_score=.2635,ml_confidence=56.8)
    # Evaluate diagnostics using exact gate flags when present (metrics shown in UI are rounded).
    snapshot['signal']['indicators']['ml_validation']['validation_checks']=dict(accuracy=False,balanced=False,brier=False)
    event=analysis_event(snapshot,None,0)
    checks={g['code']:g['status'] for g in event['payload']['checks']}
    assert all(checks[k]=='FAIL' for k in ['ML_ACCURACY','ML_BALANCED','ML_BRIER','ML_VALIDATED','MIN_CONFIDENCE'])
    assert checks['NATIVE_PREFLIGHT']=='NOT_EVALUATED'
    assert snapshot['config']['min_confidence']==78


def test_spread_rejection_keeps_monitoring_and_retries_only_after_backoff(live_bot):
    b,sdk=live_bot;b.is_running=True;sdk.quote.ask=1.2
    async def run():
        result=await b.execute_trade_from_signal(signal(),True)
        assert not result['success'] and b.is_running and not sdk.sent
        assert b.recovery.faults['preflight']['code']=='SPREAD_LIMIT'
        assert 'pré-validação' in b._risk_block(manual=True)
        sdk.quote.ask=1.1002
        assert not (await b.execute_trade_from_signal(signal(),True))['success']
        assert not sdk.sent
        b.recovery.faults['preflight']['retry_at']=0
        assert (await b.execute_trade_from_signal(signal(),True))['success']
        assert b.is_running and len(sdk.sent)==1 and 'preflight' not in b.recovery.faults
        await b.reconcile_live()
        assert b._has_open_trade
    asyncio.run(run())
    assert b.journal.diagnostics(kind='DECISION')[0]['code']=='PREFLIGHT_APPROVED'
    assert b.journal.diagnostics(kind='RECOVERY')


def test_telegram_alerts_are_throttled_and_recovery_is_recorded(bot,monkeypatch):
    sent=[];monkeypatch.setattr(bot.notifier,'enqueue',lambda *a:sent.append(a))
    for _ in range(8):bot._fault('preflight','SPREAD_LIMIT','Spread acima do limite.')
    assert len(sent)==1
    assert len(bot.journal.diagnostics(kind='ERROR'))==1
    bot._recover('preflight')
    assert len(sent)==2 and bot.journal.diagnostics(kind='RECOVERY')


def test_telegram_failure_cannot_break_monitoring_or_journal(bot,monkeypatch):
    def fail(*a):raise RuntimeError('notification unavailable')
    monkeypatch.setattr(bot.notifier,'enqueue',fail)
    bot.is_running=True
    bot._fault('analysis','ANALYSIS_UNAVAILABLE','Análise indisponível.')
    assert bot.is_running and bot.journal.diagnostics(kind='ERROR')
    assert 'Notificação' in bot.notifier.last_error


def test_analysis_failure_returns_safe_state_and_recovers_without_reactivation(bot,monkeypatch):
    bot.is_running=True
    original=bot._build_market_snapshot
    def broken():raise RuntimeError('Falha temporária de dados')
    monkeypatch.setattr(bot,'_build_market_snapshot',broken)
    state=bot.get_market_snapshot()
    assert state['is_running'] and state['operational']['state']=='RECOVERING'
    assert state['signal']['action']=='NEUTRAL' and not state['candles']
    assert state['risk']['next_amount']==0 and bot._risk_block(manual=True)
    assert bot.journal.diagnostics(kind='ERROR')
    monkeypatch.setattr(bot,'_build_market_snapshot',original)
    bot.recovery.faults['analysis']['retry_at']=0
    state=bot.get_market_snapshot()
    assert bot.is_running and state['operational']['state']=='MONITORING'
    assert len(state['candles'])==60 and bot.journal.diagnostics(kind='RECOVERY')


def test_manual_pause_is_not_reversed_after_error_recovery(bot,monkeypatch):
    bot.is_running=True
    bot._fault('analysis','ANALYSIS_UNAVAILABLE','Falha')
    bot.pause_from_telegram()
    bot.recovery.faults['analysis']['retry_at']=0
    bot.get_market_snapshot()
    assert not bot.is_running
    assert bot.operational_status()['state']=='PAUSED'


def test_audit_failure_blocks_orders_until_pending_records_are_saved(live_bot,monkeypatch):
    b,sdk=live_bot;b.is_running=True
    original=b.journal.record_diagnostic
    def fail(*a):raise sqlite3.OperationalError('locked')
    monkeypatch.setattr(b.journal,'record_diagnostic',fail)
    b.get_market_snapshot()
    assert not b.audit.healthy and b.audit.pending
    result=asyncio.run(b.execute_trade_from_signal(signal(),True))
    assert not result['success'] and not sdk.sent and b.is_running
    monkeypatch.setattr(b.journal,'record_diagnostic',original);b.audit.last_flush=0
    b.audit.flush()
    assert b.audit.healthy and not b.audit.pending
    assert b.journal.diagnostics(kind='ANALYSIS')


def test_post_send_database_failure_does_not_duplicate_order(live_bot,monkeypatch):
    b,sdk=live_bot;b.is_running=True
    original=b.journal.save
    def fail(*a):raise sqlite3.OperationalError('locked')
    async def run():
        monkeypatch.setattr(b.journal,'save',fail)
        assert (await b.execute_trade_from_signal(signal(),True))['success']
        assert b._pending_trade_saves and b._has_open_trade and b.is_running
        assert not (await b.execute_trade_from_signal(signal(),True))['success']
        assert len(sdk.sent)==1
        monkeypatch.setattr(b.journal,'save',original)
        b._flush_trade_saves()
        await b.reconcile_live()
        assert not b._pending_trade_saves and b.trade_history[0]['status']=='OPEN'
        assert len(sdk.sent)==1
    asyncio.run(run())


def test_uncertain_response_keeps_global_block_and_never_resends(live_bot):
    b,sdk=live_bot;b.is_running=True;sdk.no_response=True
    async def run():
        assert (await b.execute_trade_from_signal(signal(),True))['pending']
        await b.reconcile_live()
        for _ in range(3):assert not (await b.execute_trade_from_signal(signal(),True))['success']
        assert len(sdk.sent)==1 and b.is_running and b._has_open_trade
    asyncio.run(run())


@pytest.mark.parametrize('key,value',[('daily_pnl',-250),('daily_pnl',150),('loss_streak',3)])
def test_global_risk_holds_entries_while_monitoring_continues(live_bot,monkeypatch,key,value):
    b,sdk=live_bot;b.is_running=True;b.stats[key]=value
    monkeypatch.setattr(b,'reconcile_live',AsyncMock())
    async def run():
        await b.run_cycle();await b.run_cycle()
        assert b.is_running and not sdk.sent
        assert b._risk_block(manual=True)
    asyncio.run(run())


def test_sdk_watchdog_alerts_without_retrying_or_overlapping_send(live_bot,monkeypatch):
    b,sdk=live_bot;b.is_running=True
    monkeypatch.setattr(b,'SDK_WAIT_SECONDS',.005)
    original=sdk.order_send;calls=[]
    def slow(request):
        calls.append(request);time.sleep(.04)
        return original(request)
    monkeypatch.setattr(sdk,'order_send',slow)
    async def run():
        result=await b.execute_trade_from_signal(signal(),True)
        assert result['pending'] and b.is_running
        assert len(calls)==1 and len(sdk.sent)==1
        assert b.journal.diagnostics(kind='ERROR')
        assert any(e['code']=='SDK_SLOW' for e in b.journal.diagnostics(kind='RECOVERY'))
    asyncio.run(run())


def test_supervisor_survives_cycle_exception_and_remains_cancellable(bot,monkeypatch):
    bot.is_running=True;calls=[]
    async def cycle():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('one failure')
        assert bot.is_running
    async def sleep(delay):
        if len(calls)>=2:raise asyncio.CancelledError()
    monkeypatch.setattr(bot,'run_cycle',cycle)
    monkeypatch.setattr(asyncio,'sleep',sleep)
    with pytest.raises(asyncio.CancelledError):asyncio.run(bot.run_loop())
    assert len(calls)==2 and bot.is_running


def test_diagnostic_api_export_pagination_and_failure_state(bot,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setattr(main,'bot',bot)
    with TestClient(main.app,base_url='http://127.0.0.1:8000') as client:
        state=client.get('/api/state').json()
        for i in range(3):bot._diagnostic_event('DECISION','TEST',dict(reason=f'Evento {i}'))
        response=client.get('/api/diagnostics/events',params={'symbol':state['config']['symbol'],'limit':2}).json()
        assert len(response['items'])==2 and response['next_before']
        older=client.get('/api/diagnostics/events',params={'before':response['next_before']}).json()['items']
        assert all(e['id']<response['next_before'] for e in older)
        exported=client.get('/api/diagnostics/export.csv',params={'kind':'DECISION'})
        rows=list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
        assert len(rows)==3 and all(r['kind']=='DECISION' for r in rows)
        assert client.get('/api/diagnostics/summary').json()['items']
        assert client.get('/api/diagnostics/events?limit=501').status_code==422
        assert client.get('/api/diagnostics/events',params={'symbol':"' OR 1=1 --"}).json()['items']==[]
        def broken():raise RuntimeError('Falha de análise')
        monkeypatch.setattr(bot,'_build_market_snapshot',broken)
        bot.is_running=True
        failed=client.get('/api/state')
        assert failed.status_code==200 and failed.json()['operational']['state']=='RECOVERING'


def test_history_failure_obeys_backoff_then_resumes_without_new_start(live_bot,monkeypatch):
    b,sdk=live_bot;b.is_running=True
    original=b.mt5.get_candles;calls=[]
    def fail(*a,**k):calls.append(1);raise RuntimeError('MT5 sem histórico temporariamente')
    monkeypatch.setattr(b.mt5,'get_candles',fail)
    assert b.get_market_snapshot()['data_quality']['source']=='MT5_UNAVAILABLE'
    assert b.get_market_snapshot()['data_quality']['source']=='MT5_UNAVAILABLE'
    assert len(calls)==1 and b.is_running
    monkeypatch.setattr(b.mt5,'get_candles',original)
    b.recovery.faults['market_data']['retry_at']=0
    assert b.get_market_snapshot()['data_quality']['source']=='MT5_LIVE'
    assert 'market_data' not in b.recovery.faults and b.is_running


def test_uncertain_intent_commit_is_reloaded_without_sending(live_bot,monkeypatch):
    b,sdk=live_bot;b.is_running=True
    reserve=b.journal.reserve_live
    def uncertain(trade):
        reserve(trade)
        raise sqlite3.OperationalError('commit response lost')
    monkeypatch.setattr(b.journal,'reserve_live',uncertain)
    async def run():
        assert not (await b.execute_trade_from_signal(signal(),True))['success']
        assert b._intent_uncertain and b._has_open_trade and not sdk.sent
        b._flush_trade_saves()
        assert not b._intent_uncertain and b._has_open_trade
        await b.reconcile_live()
        assert not (await b.execute_trade_from_signal(signal(),True))['success']
        assert b.is_running and not sdk.sent
    asyncio.run(run())


def test_broker_rejection_keeps_service_running_and_is_not_resubmitted_same_bar(live_bot):
    b,sdk=live_bot;b.is_running=True;sdk.sendcode=10018;sdk.fill=False
    async def run():
        result=await b.execute_trade_from_signal(signal(),True)
        assert not result['success'] and b.is_running
        assert b.trade_history[0]['status']=='REJECTED'
        assert b._risk_block(signal()) # cooldown / backoff / used bar still applies
        assert not (await b.execute_trade_from_signal(signal(),True))['success']
        assert len(sdk.sent)==1
    asyncio.run(run())


def test_pending_notification_is_retried_without_blocking_market_analysis(bot,monkeypatch):
    delivered=[]
    monkeypatch.setattr(bot.notifier,'enqueue',lambda *a:False)
    bot._fault('analysis','TEMPORARY','Falha temporária')
    assert bot._pending_notifications
    monkeypatch.setattr(bot.notifier,'enqueue',lambda *a:delivered.append(a) or True)
    bot._flush_notifications()
    assert delivered and not bot._pending_notifications


def test_data_error_can_recover_but_explicit_pause_stays_paused(live_bot,monkeypatch):
    b,sdk=live_bot;b.is_running=True
    original=b.mt5.get_candles
    def fail(*a,**k):raise ValueError('invalid OHLCV')
    monkeypatch.setattr(b.mt5,'get_candles',fail)
    assert b.get_market_snapshot()['operational']['state']=='RECOVERING'
    b.pause_from_telegram()
    monkeypatch.setattr(b.mt5,'get_candles',original)
    b.recovery.faults['analysis']['retry_at']=0
    state=b.get_market_snapshot()
    assert state['operational']['state']=='PAUSED' and not sdk.sent


def test_model_unavailable_alerts_without_turning_automation_off(bot,monkeypatch):
    import backend.ml_engine as ml
    bot.update_config({'strategy_id':'ml_random_forest'})
    bot.is_running=True
    with monkeypatch.context() as patch:
        patch.setattr(ml,'SKLEARN_AVAILABLE',False)
        state=bot.get_market_snapshot()
    assert bot.is_running and state['signal']['action']=='NEUTRAL'
    assert bot.recovery.faults['model']['code']=='MODEL_UNAVAILABLE'
    state=bot.get_market_snapshot()
    assert bot.is_running and state['signal']['indicators']['ml_validation']['enabled']
    assert 'model' not in bot.recovery.faults
