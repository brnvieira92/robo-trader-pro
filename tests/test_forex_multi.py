"""Multi-instrument isolation and global execution safety, without broker orders."""
import asyncio
import copy
import time
from types import SimpleNamespace as NS
import pytest
from pydantic import ValidationError
from backend.config import TradingConfig
from backend.forex_multi import ForexCoordinator
from backend.mt5_execution import MT5ExecutionError
from test_mt5_live import live_bot, signal


def enable(b, **updates):
    old_info = b.live.sdk.symbol_info
    def info(symbol):
        metadata = copy.copy(old_info(symbol))
        metadata.currency_base = symbol[:3]
        metadata.currency_profit = symbol[3:6]
        return metadata
    b.live.sdk.symbol_info = info
    b.update_config(dict(forex_multi_enabled=True, strategy_id='ml_random_forest',
        forex_pairs=[dict(pair=p, symbol=p+'#', enabled=True) for p in ('EURUSD','USDJPY','GBPUSD','USDCHF')],
        symbol='EURUSD#', **updates))
    return ForexCoordinator(b)


def test_config_preserves_legacy_and_rejects_weakened_multi():
    c=TradingConfig().model_dump()
    assert not c['forex_multi_enabled'] and c['forex_pairs']==[]
    valid=dict(c, execution_mode='LIVE_MT5',market_type='MT5_FOREX',mt5_account_login=123,
               mt5_account_server='Demo',forex_multi_enabled=True,strategy_id='ml_random_forest',
               forex_pairs=[dict(pair='EURUSD',symbol='EURUSD',enabled=True)])
    TradingConfig.model_validate(valid)
    for change in (dict(min_confidence=77), dict(strategy_id='multi_confluence'),
                   dict(forex_pairs=[]),dict(symbol='NOT_SELECTED'),
                   dict(strategy_params={'use_ml_filter':False}),dict(execution_mode='PAPER'),
                   dict(forex_pairs=valid['forex_pairs']*2)):
        with pytest.raises(ValidationError): TradingConfig.model_validate(dict(valid,**change))


def test_independent_models_and_analysis_context(live_bot):
    b,sdk=live_bot;multi=enable(b)
    assert len({id(v._model_engine) for v in multi.views.values()})==4
    assert len({id(v.recovery) for v in multi.views.values()})==4
    original=copy.deepcopy(b.config)
    asyncio.run(multi.cycle())
    assert b.config==original
    assert {r['symbol'] for r in multi.snapshot()['forex_radar']['rows']}=={'EURUSD#','USDJPY#','GBPUSD#','USDCHF#'}
    assert {e['symbol'] for e in b.journal.diagnostics(kind='ANALYSIS')}=={'EURUSD#','USDJPY#','GBPUSD#','USDCHF#'}
    assert not sdk.sent


def test_pair_error_isolated_and_read_requests_have_no_side_effect(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);b._multi=multi;b.is_running=True
    original=b.mt5.get_candles;calls=[]
    def candles(symbol,*args,**kwargs):
        calls.append(symbol)
        if symbol=='EURUSD#': raise RuntimeError('history unavailable for EURUSD#')
        return original(symbol,*args,**kwargs)
    monkeypatch.setattr(b.mt5,'get_candles',candles)
    asyncio.run(multi.cycle())
    assert len(calls)==4 and 'USDCHF#' in calls
    assert b.is_running
    count=b.journal.diagnostic_count()
    for _ in range(10): b.get_market_snapshot()
    assert len(calls)==4 and b.journal.diagnostic_count()==count and not sdk.sent
    assert not multi.rows['EURUSD']['validated']
    assert multi.rows['USDJPY']['status']!='ERRO'


def candidate(view):
    s=signal();s['symbol']=view.config['symbol']
    return dict(config=view.config, signal=s, analysis_id=view.config['symbol'],
                risk={'blocked_reason':None},data_quality={'source':'MT5_LIVE','history_fresh':True},
                stats={},candles=[])


def candidates(multi,monkeypatch):
    for view in multi.views.values():
        monkeypatch.setattr(view,'refresh',lambda v=view:candidate(v))


def test_single_global_reservation_and_exact_symbol(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    asyncio.run(multi.cycle())
    assert len(sdk.sent)==1 and sdk.sent[0]['symbol']=='EURUSD#'
    assert b._has_open_trade and b.trade_history[0]['symbol']=='EURUSD#'
    assert b.trade_history[0]['amount']<=b.config['entry_amount']
    asyncio.run(multi.cycle())
    assert len(sdk.sent)==1
    assert b.is_running


@pytest.mark.parametrize('unknown',[False,True])
def test_external_or_unknown_exposure_blocks_all_but_keeps_scanning(live_bot,monkeypatch,unknown):
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    sdk.positions=None if unknown else [NS(symbol='GOLD#')]
    asyncio.run(multi.cycle())
    assert not sdk.sent and len(multi.rows)==4 and b.is_running
    assert multi.global_block
    sdk.positions=[]
    asyncio.run(multi.cycle())
    assert len(sdk.sent)==1


def test_ambiguous_send_never_retries_another_pair(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    sdk.no_response=True;sdk.fill=False
    asyncio.run(multi.cycle());asyncio.run(multi.cycle())
    assert len(sdk.sent)==1 and b._has_open_trade
    assert b.trade_history[0]['status']=='PENDING'


def test_local_preflight_rejection_allows_next_pair(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    prepare=b.live.prepare
    def guarded(config,*args):
        if config['symbol']=='EURUSD#': raise MT5ExecutionError('Spread acima do limite')
        return prepare(config,*args)
    monkeypatch.setattr(b.live,'prepare',guarded)
    asyncio.run(multi.cycle())
    assert len(sdk.sent)==1 and sdk.sent[0]['symbol']=='USDJPY#'
    assert b.config['symbol']=='EURUSD#'
    decisions=b.journal.diagnostics(kind='DECISION')
    assert any(e['symbol']=='EURUSD#' and e['code']=='SPREAD_LIMIT' for e in decisions)
    assert any(e['symbol']=='USDJPY#' and e['code']=='PREFLIGHT_APPROVED' for e in decisions)


def test_global_daily_budget_is_not_multiplied(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);b.is_running=True
    b.stats['daily_pnl']=-b.config['stop_loss']
    asyncio.run(multi.cycle())
    assert len(multi.rows)==4 and not sdk.sent
    assert all('Stop loss' in row['blocked_reason'] for row in multi.rows.values())


def test_pause_during_scan_cancels_automatic_execution(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    v=multi.views['USDCHF']
    def paused():
        b.is_running=False
        return candidate(v)
    monkeypatch.setattr(v,'refresh',paused)
    asyncio.run(multi.cycle())
    assert not sdk.sent


def test_global_exposure_checked_inside_prepare_too(live_bot):
    b,sdk=live_bot;multi=enable(b)
    sdk.orders=[NS(symbol='OTHER')]
    with pytest.raises(MT5ExecutionError,match='global'):
        b.live.prepare(b.config,signal(),100,'test')
    assert not sdk.sent


def test_real_symbol_identity_mismatch_is_local(live_bot):
    b,sdk=live_bot;multi=enable(b);b.is_running=True
    original=sdk.symbol_info
    def wrong(symbol):
        info=original(symbol)
        if symbol=='USDJPY#': info.currency_profit='USD'
        return info
    sdk.symbol_info=wrong
    asyncio.run(multi.cycle())
    assert multi.rows['USDJPY']['status']=='ERRO'
    assert multi.rows['EURUSD']['status']!='ERRO'
    assert not sdk.sent and b.is_running


def test_per_symbol_digits_steps_and_profit_calculation(live_bot):
    b,sdk=live_bot
    multi=enable(b,entry_amount=20,profit_per_trade_cap=100)
    sdk.info.equity=10000;sdk.info.margin_free=10000
    base=sdk.symbol_info
    def specs(symbol):
        info=base(symbol)
        if symbol=='USDJPY#':
            info.point=info.trade_tick_size=.001;info.digits=3
            info.volume_min=info.volume_step=.1
        return info
    sdk.symbol_info=specs
    fx=NS(ask=1.1002,bid=1.1,time_msc=int(time.time()*1000))
    jpy=NS(ask=150.010,bid=150.,time_msc=int(time.time()*1000))
    sdk.symbol_info_tick=lambda symbol:jpy if symbol=='USDJPY#' else fx
    sdk.order_calc_profit=lambda kind,symbol,vol,entry,exit: (exit-entry)*vol*(1000 if symbol=='USDJPY#' else 100000)*(1 if kind==0 else -1)
    results=[]
    for pair in ('EURUSD','USDJPY'):
        config=multi.views[pair].config
        s=signal()
        if pair=='USDJPY': s.update(price=150.010,indicators={'atr':.1,'ml_validation':{'validated':True}})
        results.append(b.live.prepare(config,s,250,'spec-'+pair,150))
    eur,jpy_result=results
    assert eur['request']['symbol']=='EURUSD#' and jpy_result['request']['symbol']=='USDJPY#'
    assert eur['volume']!=jpy_result['volume']
    assert jpy_result['volume']==pytest.approx(.1)
    assert jpy_result['stop_loss']==round(jpy_result['stop_loss'],3)
    assert all(0 < r['estimated_risk']<=20 for r in results)
    assert not sdk.sent


def test_api_radar_and_state_never_call_sdk_or_predict(live_bot,monkeypatch):
    import httpx
    import backend.main as main
    b,sdk=live_bot;multi=enable(b);b._multi=multi
    asyncio.run(multi.cycle())
    monkeypatch.setattr(main,'bot',b)
    def forbidden(*a,**kw): raise AssertionError('read endpoint called SDK/model')
    monkeypatch.setattr(sdk,'symbol_info_tick',forbidden)
    monkeypatch.setattr(sdk,'account_info',forbidden)
    monkeypatch.setattr(b.mt5,'get_candles',forbidden)
    for v in multi.views.values(): monkeypatch.setattr(v._model_engine,'predict_next_candle',forbidden)
    before=b.journal.diagnostic_count()
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://127.0.0.1:8000') as client:
            for _ in range(5):
                assert (await client.get('/api/state')).status_code==200
                res=await client.get('/api/forex/radar')
                assert res.status_code==200 and len(res.json()['rows'])==4
            assert (await client.post('/api/order/manual',json={'action':'BUY'})).status_code==409
    asyncio.run(check())
    assert b.journal.diagnostic_count()==before and not sdk.sent


def test_pending_intent_survives_restart_and_disabled_pairs(live_bot,monkeypatch):
    from backend.bot_manager import BotManager
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    sdk.no_response=True;sdk.fill=False
    asyncio.run(multi.cycle())
    restored=BotManager()
    assert not restored.is_running and restored._has_open_trade
    assert restored.trade_history[0]['symbol']=='EURUSD#'
    with pytest.raises(ValueError,match='Pause'):
        restored.update_config({'forex_multi_enabled':False})
    asyncio.run(restored.close())


def test_sdk_access_remains_serialized_during_ui_polling(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);b._multi=multi
    busy=False
    original=b.mt5.get_candles
    def slow(*args,**kwargs):
        nonlocal busy
        assert not busy
        busy=True
        try:
            time.sleep(.03)
            return original(*args,**kwargs)
        finally: busy=False
    monkeypatch.setattr(b.mt5,'get_candles',slow)
    async def exercise():
        task=asyncio.create_task(b.run_cycle())
        while not task.done():
            b.get_market_snapshot()
            await asyncio.sleep(.001)
        await task
    asyncio.run(exercise())
    assert len(multi.rows)==4 and not sdk.sent


def test_late_manual_exposure_cancels_before_order_send(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b);candidates(multi,monkeypatch);b.is_running=True
    send=b.live.send
    def late(trade):
        sdk.positions=[NS(symbol='MANUAL')]
        return send(trade)
    monkeypatch.setattr(b.live,'send',late)
    asyncio.run(multi.cycle())
    assert not sdk.sent
    assert b.trade_history[0]['status']=='REJECTED'
    assert not b._has_open_trade


def test_pair_recovers_without_losing_other_models(live_bot,monkeypatch):
    b,sdk=live_bot;multi=enable(b)
    original=sdk.symbol_info
    broken=True
    def intermittent(symbol):
        if symbol=='EURUSD#' and broken: raise RuntimeError('temporary metadata error')
        return original(symbol)
    monkeypatch.setattr(sdk,'symbol_info',intermittent)
    other_model=multi.views['USDJPY']._model_engine
    asyncio.run(multi.cycle())
    assert multi.rows['EURUSD']['status']=='ERRO'
    broken=False
    multi.views['EURUSD'].recovery.faults['pair']['retry_at']=0
    asyncio.run(multi.cycle())
    assert multi.rows['EURUSD']['status']!='ERRO'
    assert multi.views['USDJPY']._model_engine is other_model
    assert not sdk.sent


def test_expired_stop_rejection_keeps_scanning_and_revalidates_before_send(live_bot, monkeypatch):
    b, sdk = live_bot
    multi = enable(b)
    b._multi = multi
    b.is_running = True
    attempts = []
    def reject(*args):
        attempts.append(args[0]['symbol'])
        raise MT5ExecutionError('Stop estrutural distante demais para a volatilidade atual; entrada descartada.')
    monkeypatch.setattr(b.live, 'prepare', reject)
    snapshot = candidate(multi.views['EURUSD'])
    asyncio.run(b._execute_mt5(snapshot['signal'], snapshot))
    key = 'pair-preflight:EURUSD#'
    assert b.operational_status()['state'] == 'RECOVERING'
    b.recovery.faults[key]['retry_at'] = 0
    asyncio.run(multi.cycle())
    assert len(multi.rows) == 4 and not sdk.sent
    assert b.operational_status()['state'] == 'MONITORING'
    assert key in b.operational_status()['pending_revalidation']
    candidates(multi, monkeypatch)
    asyncio.run(multi.cycle())
    assert attempts.count('EURUSD#') == 2 and not sdk.sent
    assert b.recovery.faults[key]['attempts'] == 2
    assert b.operational_status()['state'] == 'RECOVERING'
    assert b.config['min_confidence'] == 78
    assert b.config['strategy_params']['use_ml_filter']


def test_pair_operational_failure_is_visible_without_blocking_other_pairs(live_bot, monkeypatch):
    b, sdk = live_bot
    multi = enable(b)
    b._multi = multi
    b.is_running = True
    def broken():
        raise RuntimeError('Falha de metadados EURUSD#')
    monkeypatch.setattr(multi.views['EURUSD'], 'refresh', broken)
    asyncio.run(multi.cycle())
    status = b.operational_status()
    assert status['state'] == 'RECOVERING'
    assert 'pair:EURUSD#:pair' in status['faults']
    assert len(multi.rows) == 4 and multi.rows['USDCHF']['status'] != 'ERRO'
    assert b._runtime_block() is None
    assert 'Falha de metadados EURUSD#' in b.telegram_status()
    assert 'Última atualização USDCHF#:' in b.telegram_status()
    assert not sdk.sent


def test_only_selected_pairs_are_analyzed_and_budget_projection_is_current(live_bot,monkeypatch):
    b,sdk=live_bot
    multi=enable(b,entry_amount=8)
    b.update_config({'forex_pairs':[dict(pair=p,symbol=p+'#',enabled=p=='EURUSD') for p in ('EURUSD','USDJPY','GBPUSD','USDCHF')]})
    multi=ForexCoordinator(b)
    original=b.mt5.get_candles;seen=[]
    def candles(symbol,*a,**kw):
        seen.append(symbol)
        return original(symbol,*a,**kw)
    monkeypatch.setattr(b.mt5,'get_candles',candles)
    asyncio.run(multi.cycle())
    assert seen==['EURUSD#'] and len(multi.views)==1
    state=multi.snapshot()
    assert state['stats']['next_suggested_amount']==state['risk']['next_amount']==8
    assert len([row for row in state['forex_radar']['rows'] if row['status']=='DESATIVADO'])==3
    assert not sdk.sent
