import asyncio
import copy
import json
import sqlite3
import time
import pytest
from backend.journal import PaperJournal
from backend.diagnostic_codec import encode_payload,decode_payload,PREFIX
from backend.journal_maintenance import compact
from backend.pair_report import pair_report
from backend.runtime_info import RuntimeIdentity
from test_mt5_live import live_bot,signal


def record(journal,key='one',symbol='EURUSD#',kind='RECONCILIATION',payload=None):
    journal.record_diagnostic(dict(event_key=key,kind=kind,symbol=symbol,timeframe='M1',bar_time='bar',code='WIN',observed_at=100,
        payload=payload or dict(trade_id='trade1',position_id=7,account_login=123,account_server='Demo')))


def test_diagnostic_codec_is_lossless_and_legacy_json_stays_readable():
    p=dict(reason='São Paulo',checks=[dict(code='REASON',observed=1,reason='Histórico fechado '*80)]*20)
    raw=encode_payload(p)
    assert raw.startswith(PREFIX) and len(raw)<len(json.dumps(p).encode())*.3
    assert decode_payload(raw)==p and decode_payload(json.dumps(p))==p
    assert not encode_payload({'small':1}).startswith(PREFIX)


def test_compact_diagnostics_are_transparent_in_api_and_pair_reports(tmp_path):
    j=PaperJournal(tmp_path/'db')
    payload=dict(mode='LIVE_MT5',account_login=123,account_server='Demo',config=dict(market_type='MT5_FOREX'),
        entry_policy_version='forex-structure-retest-v1',reason='Motivo detalhado '*500,
        checks=[dict(code=x,status='PASS') for x in ['SIGNAL_DIRECTION','MIN_CONFIDENCE','VOLATILITY']])
    record(j,kind='ANALYSIS',payload=payload)
    assert j.diagnostics()[0]['payload']==payload
    r=pair_report(j,0,200,'LIVE_MT5',123,'Demo')
    assert r['rows'][0]['signal_qualified_candles']==1


def test_historical_label_repair_is_correlated_idempotent_and_financially_inert(tmp_path):
    j=PaperJournal(tmp_path/'db')
    trade=dict(id='trade1',day='2026-10-24',mode='LIVE_MT5',symbol='USDCHF#',timeframe='M1',position_id=7,account_login=123,account_server='Demo',pnl=12.34,status='WIN')
    j.save(trade);record(j)
    before=j.diagnostics()[0]
    assert j.repair_reconciliation_context()==dict(repaired=1,skipped=0)
    row=j.diagnostics(symbol='USDCHF#')[0]
    assert all(row[k]==before[k] for k in ['id','event_key','first_seen','last_seen','observations','code'])
    assert row['payload']['context_repair']['original_symbol']=='EURUSD#'
    assert j.all_trades()==[trade] and j.repair_reconciliation_context()['repaired']==0
    record(j,'conflict',payload=dict(trade_id='trade1',position_id=999))
    assert j.repair_reconciliation_context()['skipped']==1
    assert j.diagnostics(symbol='EURUSD#')[0]['event_key']=='conflict'


def test_new_reconciliation_and_recovery_use_trade_symbol_not_selected_symbol(live_bot,monkeypatch):
    b,sdk=live_bot
    result=asyncio.run(b.execute_trade_from_signal(signal(),True));assert result['success']
    trade=b.trade_history[0];actual=trade['symbol'];b.config['symbol']='GBPUSD#'
    b._fault('sync:'+trade['id'],'RECONCILIATION_UNAVAILABLE','Teste',context={**b.config,'symbol':actual})
    asyncio.run(b.reconcile_live())
    events=b.journal.diagnostics(kind='RECONCILIATION')
    assert events and all(e['symbol']==actual for e in events)
    assert b.journal.diagnostics(kind='RECOVERY')[0]['symbol']==actual


def test_operational_status_counts_persistent_and_memory_queues(live_bot,monkeypatch):
    b,sdk=live_bot;b.notifier.enabled=True;b.notifier.chat_id='123'
    b.notifier.enqueue('persisted','Mensagem')
    b._pending_notifications['unsaved']='Ainda não persistida'
    status=b.operational_status()
    assert status['pending_notifications']==2 and status['pending_notification_saves']==1
    assert status['notification_outbox']['pending']==1


def test_offline_compaction_requires_lock_backup_and_preserves_all_events_and_trades(tmp_path):
    path=tmp_path/'db';j=PaperJournal(path);j.save(dict(id='closed',day='today',pnl=7,status='WIN'))
    j.save_config({'preserve_this':True})
    with j.connection() as db:
        db.execute('CREATE TABLE telegram_outbox (id TEXT PRIMARY KEY,text TEXT,sent INTEGER)')
        db.execute('INSERT INTO telegram_outbox VALUES (?,?,?)',('pending','Alerta original',0))
    original={'detail':'Dados de diagnóstico '*1000}
    record(j,payload=original)
    # Simulate an old uncompressed event.
    with j.connection() as db:db.execute('UPDATE diagnostic_events SET payload=?',(json.dumps(original),))
    j.acquire_runtime_lock()
    with pytest.raises(RuntimeError,match='instância'):compact(path,tmp_path/'backup')
    j.release_runtime_lock()
    result=compact(path,tmp_path/'backup')
    assert result['converted_events']==1 and result['financial_settings_outbox_unchanged']
    assert j.diagnostics()[0]['payload']==original and j.all_trades()[0]['pnl']==7
    assert j.load_config()=={'preserve_this':True}
    with j.connection() as db:assert db.execute('SELECT * FROM telegram_outbox').fetchall()==[('pending','Alerta original',0)]
    with sqlite3.connect(tmp_path/'backup') as db:
        assert json.loads(db.execute('SELECT payload FROM diagnostic_events').fetchone()[0])==original
    with pytest.raises(ValueError,match='backup'):compact(path,tmp_path/'backup')


def test_compaction_refuses_pending_exposure_without_creating_backup(tmp_path):
    j=PaperJournal(tmp_path/'db');j.save(dict(id='pending',day='today',status='PENDING',mode='LIVE_MT5'))
    with pytest.raises(ValueError,match='pendente'):compact(j.path,tmp_path/'backup')
    assert not (tmp_path/'backup').exists()


def test_runtime_identity_reports_source_changes_without_changing_saved_identity(monkeypatch):
    import backend.runtime_info as runtime
    monkeypatch.setattr(runtime,'source_fingerprint',lambda:'initial')
    monkeypatch.setattr(runtime,'_git',lambda *a:'abc123' if a[0]=='rev-parse' else '')
    identity=RuntimeIdentity();assert identity.status()['backend_files_match_load']
    monkeypatch.setattr(runtime,'source_fingerprint',lambda:'changed')
    s=identity.status();assert not s['backend_files_match_load'] and s['git_head_at_load']=='abc123'
    assert s['backend_fingerprint_at_load']=='initial'


def test_compressed_decode_rejects_truncation_and_expansion_beyond_limit(monkeypatch):
    import base64,zlib
    import backend.diagnostic_codec as codec
    packed=PREFIX+base64.b64encode(zlib.compress(b'"'+b'a'*200+b'"')).decode()
    monkeypatch.setattr(codec,'MAX_DECODED_BYTES',100)
    with pytest.raises(ValueError):decode_payload(packed)
    with pytest.raises(ValueError):decode_payload(PREFIX+base64.b64encode(b'not-zlib').decode())


def test_runtime_and_storage_endpoints_are_read_only(live_bot,monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    bot,sdk=live_bot;monkeypatch.setattr(main,'bot',bot)
    client=TestClient(main.app,base_url='http://127.0.0.1')
    runtime=client.get('/api/runtime');storage=client.get('/api/diagnostics/storage')
    assert runtime.status_code==200 and runtime.json()['backend_fingerprint_at_load']
    assert storage.status_code==200 and storage.json()['historical_compaction_automatic'] is False
    assert not sdk.sent and not bot.is_running
