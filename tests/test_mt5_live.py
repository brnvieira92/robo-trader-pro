import asyncio
import time
from types import SimpleNamespace as NS
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from backend.bot_manager import BotManager
from backend.config import TradingConfig
from backend.mt5_execution import MT5Executor, MT5ExecutionError

class FakeMT5:
    ACCOUNT_TRADE_MODE_REAL=2
    SYMBOL_TRADE_MODE_FULL=4
    SYMBOL_TRADE_MODE_LONGONLY=1
    SYMBOL_TRADE_MODE_SHORTONLY=2
    SYMBOL_TRADE_EXECUTION_MARKET=2
    SYMBOL_FILLING_FOK=1;SYMBOL_FILLING_IOC=2
    ORDER_TYPE_BUY=0;ORDER_TYPE_SELL=1
    ORDER_FILLING_FOK=0;ORDER_FILLING_IOC=1;ORDER_FILLING_RETURN=2
    TRADE_ACTION_DEAL=1;ORDER_TIME_GTC=0
    TRADE_RETCODE_DONE=10009;TRADE_RETCODE_DONE_PARTIAL=10010;TRADE_RETCODE_PLACED=10008
    DEAL_TYPE_BUY=0;DEAL_TYPE_SELL=1;DEAL_ENTRY_IN=0;DEAL_ENTRY_OUT=1;DEAL_ENTRY_INOUT=2;DEAL_ENTRY_OUT_BY=3
    def __init__(self):
        self.info=NS(login=123,server='Broker-Demo',balance=1000.,equity=1000.,margin_free=1000.,currency='USD',trade_mode=0,trade_allowed=True,trade_expert=True)
        self.terminal=NS(connected=True,trade_allowed=True,tradeapi_disabled=False)
        self.symbol=NS(point=.00001,trade_tick_size=.00001,digits=5,trade_stops_level=10,volume_min=.01,volume_step=.01,volume_max=100.,filling_mode=3,trade_exemode=2,trade_mode=4)
        self.quote=NS(ask=1.1002,bid=1.1,time_msc=int(time.time()*1000))
        self.positions=[];self.orders=[];self.deals=[];self.sent=[]
        self.checkcode=0;self.sendcode=10009;self.fill=True;self.no_response=False
    def account_info(self):return self.info
    def terminal_info(self):return self.terminal
    def symbol_select(self,*a):return True
    def symbol_info(self,*a):return self.symbol
    def symbol_info_tick(self,*a):return self.quote
    def positions_get(self,**kw):
        if self.positions is None:return None
        return tuple(p for p in self.positions if not kw.get('symbol') or p.symbol==kw['symbol'])
    def orders_get(self,**kw):return None if self.orders is None else tuple(self.orders)
    def order_calc_profit(self,kind,symbol,volume,entry,exit):return (exit-entry)*volume*100000*(1 if kind==0 else -1)
    def order_calc_margin(self,kind,symbol,volume,price):return volume*1000
    def order_check(self,request):return NS(retcode=self.checkcode)
    def order_send(self,request):
        self.sent.append(dict(request))
        if self.fill:
            volume=request['volume']*(.5 if self.sendcode==10010 else 1)
            self.deals.append(NS(ticket=700,order=500,position_id=900,magic=request['magic'],comment=request['comment'],type=request['type'],entry=0,volume=volume,price=request['price'],time=int(time.time()),profit=0.,commission=-.2,swap=0.,fee=0.))
            self.positions=[NS(ticket=900,identifier=900,symbol=request['symbol'],sl=request['sl'],tp=request['tp'],profit=1.,swap=0.)]
        if self.no_response:return None
        return NS(retcode=self.sendcode,order=500 if self.fill else 0,deal=700 if self.fill else 0)
    def history_deals_get(self,*args,**kw):
        return None if self.deals is None else tuple(d for d in self.deals if not kw.get('position') or d.position_id==kw['position'])
    def close_position(self):
        self.positions=[]
        incoming=self.deals[0]
        self.deals.append(NS(ticket=701,order=501,position_id=900,magic=0,comment='manual close',type=1-incoming.type,entry=1,volume=incoming.volume,price=1.102,time=int(time.time()),profit=3.,commission=-.2,swap=-.1,fee=-.05))

@pytest.fixture
def live_bot(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADER_JOURNAL_PATH',str(tmp_path/'live.db'))
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN',raising=False);monkeypatch.delenv('TELEGRAM_CHAT_ID',raising=False)
    b=BotManager();sdk=FakeMT5()
    b.mt5.connected=True;b.mt5.simulation_mode=False
    b.live=MT5Executor(b.mt5,sdk)
    close=1.1+np.sin(np.arange(240)/7)*.001
    frame=pd.DataFrame(dict(time=pd.date_range(end=pd.Timestamp.now('UTC').floor('min').tz_localize(None),periods=240,freq='min'),open=close,high=close+.0003,low=close-.0003,close=close,volume=100))
    monkeypatch.setattr(b.mt5,'get_candles',lambda *a,**kw:frame.copy())
    b.update_config({'market_type':'MT5_FOREX','execution_mode':'LIVE_MT5'})
    return b,sdk

def signal():return dict(action='BUY',price=1.1,confidence=85,bar_time='a closed bar',indicators={'atr':.001,'ml_validation':{'validated':True}})

def test_live_sends_once_with_server_stops_and_tracks_actual_closure(live_bot):
    b,sdk=live_bot
    async def run():
        result=await b.execute_trade_from_signal(signal(),manual=True)
        assert result['success'] and result['pending']
        assert b._has_open_trade and not b._settlement_tasks
        assert b.trade_history[0]['status']=='PENDING'
        request=sdk.sent[0]
        assert request['sl']<request['price']<request['tp']
        assert request['volume']==pytest.approx(.03)
        await b.reconcile_live()
        assert b.trade_history[0]['status']=='OPEN'
        assert b.stats['daily_pnl']==pytest.approx(-.2)
        assert not (await b.execute_trade_from_signal(signal(),True))['success']
        sdk.close_position();await b.reconcile_live()
        trade=b.trade_history[0]
        assert trade['status']=='WIN' and trade['pnl']==pytest.approx(2.45)
        assert b.stats['daily_pnl']==pytest.approx(2.45)
        assert not b._has_open_trade and len(sdk.sent)==1
    asyncio.run(run())

def test_manual_sell_recomputes_directional_stops(live_bot):
    b,sdk=live_bot;s=signal();s['action']='SELL';s['stop_loss']=1.;s['take_profit']=2.
    asyncio.run(b.execute_trade_from_signal(s,True))
    request=sdk.sent[0]
    assert request['tp']<request['price']<request['sl']

@pytest.mark.parametrize('failure',['stale','spread','account','tradeapi','margin','minimum','positions','check','positions_unavailable'])
def test_preflight_blocks_invalid_conditions_without_send(live_bot,failure):
    b,sdk=live_bot
    if failure=='stale':sdk.quote.time_msc-=60000
    if failure=='spread':sdk.quote.ask=1.2
    if failure=='account':sdk.info.login=456
    if failure=='tradeapi':sdk.terminal.tradeapi_disabled=True
    if failure=='margin':sdk.info.margin_free=.1
    if failure=='minimum':sdk.symbol.volume_min=1;sdk.symbol.volume_step=1
    if failure=='positions':sdk.positions=[NS(symbol='EURUSD')]
    if failure=='check':sdk.checkcode=10016
    if failure=='positions_unavailable':sdk.positions=None
    result=asyncio.run(b.execute_trade_from_signal(signal(),True))
    assert not result['success'] and not sdk.sent

@pytest.mark.parametrize('code',[10006,10016,10019])
def test_explicit_rejections_are_not_recorded_as_fills(live_bot,code):
    b,sdk=live_bot;sdk.sendcode=code;sdk.fill=False
    result=asyncio.run(b.execute_trade_from_signal(signal(),True))
    assert not result['success'] and not result['pending']
    assert b.trade_history[0]['status']=='REJECTED' and b.stats['wins']==0
    assert not b._has_open_trade

def test_response_lost_after_broker_fill_is_reconciled_without_retry(live_bot):
    b,sdk=live_bot;sdk.no_response=True
    async def run():
        result=await b.execute_trade_from_signal(signal(),True)
        assert result['pending'] and not result['success']
        await b.reconcile_live()
        assert b.trade_history[0]['status']=='OPEN'
        await b.execute_trade_from_signal(signal(),True)
        assert len(sdk.sent)==1
    asyncio.run(run())

def test_partial_fill_is_not_topped_up(live_bot):
    b,sdk=live_bot;sdk.sendcode=10010
    async def run():
        await b.execute_trade_from_signal(signal(),True);await b.reconcile_live()
        assert b.trade_history[0]['filled_volume']==pytest.approx(sdk.sent[0]['volume']*.5)
        assert b._has_open_trade and len(sdk.sent)==1
    asyncio.run(run())

def test_restart_does_not_turn_live_position_into_paper_loss(live_bot):
    b,sdk=live_bot
    asyncio.run(b.execute_trade_from_signal(signal(),True))
    restored=BotManager();restored.mt5.connected=True;restored.mt5.simulation_mode=False
    restored.live=MT5Executor(restored.mt5,sdk)
    assert restored._has_open_trade and restored.trade_history[0]['status']=='PENDING'
    asyncio.run(restored.reconcile_live())
    assert restored.trade_history[0]['status']=='OPEN' and len(sdk.sent)==1

def test_missing_history_keeps_position_pending_without_fake_pnl(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        sdk.deals=None
        await b.reconcile_live()
        assert b._has_open_trade and b.trade_history[0]['status']=='PENDING'
        assert b.trade_history[0]['pnl']==0 and not b.is_running
    asyncio.run(run())

def test_no_fill_response_never_unblocks_itself_on_elapsed_time(live_bot):
    b,sdk=live_bot;sdk.fill=False;sdk.no_response=True
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        b.trade_history[0]['created_at']-=86400
        await b.reconcile_live()
        assert b._has_open_trade and b.trade_history[0]['status']=='PENDING'
        assert len(sdk.sent)==1
    asyncio.run(run())

def test_config_cannot_switch_mode_while_live_order_pending(live_bot):
    b,sdk=live_bot;asyncio.run(b.execute_trade_from_signal(signal(),True))
    with pytest.raises(ValueError):b.update_config({'execution_mode':'PAPER'})

def test_minimum_risk_and_currency_are_not_paper_values(live_bot):
    b,sdk=live_bot
    assert b.get_market_snapshot()['currency']=='USD'
    assert b.get_market_snapshot()['execution_mode']=='LIVE_MT5'
    assert b.config['mt5_account_login']==123

def test_atomic_journal_reservation_blocks_duplicate_sender(live_bot):
    b,sdk=live_bot;asyncio.run(b.execute_trade_from_signal(signal(),True))
    trade={**b.trade_history[0],'id':'another-intent'}
    with pytest.raises(ValueError):b.journal.reserve_live(trade)

def test_runtime_lock_rejects_second_instance(live_bot):
    b,_=live_bot;other=BotManager()
    b.journal.acquire_runtime_lock()
    try:
        with pytest.raises(RuntimeError):other.journal.acquire_runtime_lock()
    finally:b.journal.release_runtime_lock()

def test_reconciliation_is_idempotent_and_open_positions_have_no_timer(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        await b.reconcile_live();await b.reconcile_live()
        assert b.stats['daily_pnl']==pytest.approx(-.2)
        assert b.trade_history[0]['status']=='OPEN'
        sdk.close_position()
        await b.reconcile_live();await b.reconcile_live()
        assert b.stats['total_trades']==1 and b.stats['daily_pnl']==pytest.approx(2.45)
    asyncio.run(run())

def test_unprotected_position_blocks_entries_and_keeps_monitoring(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        sdk.positions[0].sl=0;b.is_running=True
        await b.reconcile_live()
        assert b.is_running and b._has_open_trade
        assert b._risk_block(manual=True)
        assert b.trade_history[0]['status']=='OPEN' and not b.trade_history[0]['protected']
    asyncio.run(run())

def test_net_result_includes_costs_even_when_gross_profit_is_positive(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        sdk.close_position();sdk.deals[-1].profit=.1
        await b.reconcile_live()
        assert b.trade_history[0]['status']=='LOSS'
        assert b.trade_history[0]['pnl']==pytest.approx(-.45)
    asyncio.run(run())

def test_foreign_entry_in_same_position_requires_intervention(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        sdk.deals.append(NS(**{**vars(sdk.deals[0]),'ticket':702,'magic':0,'order':502,'comment':'manual'}))
        await b.reconcile_live()
        assert b._has_open_trade and 'sync_error' in b.trade_history[0]
        assert not b.is_running and len(sdk.sent)==1
    asyncio.run(run())

def test_changed_account_is_never_rebound_by_saving_risk_parameter(live_bot):
    b,sdk=live_bot;sdk.info.login=456
    with pytest.raises(ValueError):b.update_config({'risk_per_trade_pct':.5})
    assert b.config['mt5_account_login']==123

def test_automatic_live_signal_respects_temporal_filter(live_bot):
    b,sdk=live_bot;b.is_running=True;s=signal();s['indicators']['ml_validation']['validated']=False
    assert not asyncio.run(b.execute_trade_from_signal(s))['success']
    assert not sdk.sent
    s['indicators']['ml_validation']['validated']=True
    assert asyncio.run(b.execute_trade_from_signal(s))['success']

def test_preflight_uses_validated_account_currency_risk(live_bot):
    b,sdk=live_bot;sdk.info.equity=200;sdk.info.currency='EUR'
    asyncio.run(b.execute_trade_from_signal(signal(),True))
    trade=b.trade_history[0]
    assert trade['currency']=='EUR'
    assert trade['amount']<=2

def test_sdk_unavailable_in_live_mode_never_falls_back_to_paper(live_bot):
    b,sdk=live_bot;b.mt5.simulation_mode=True
    result=asyncio.run(b.execute_trade_from_signal(signal(),True))
    assert not result['success'] and not sdk.sent
    assert b.get_market_snapshot()['data_quality']['source']=='MT5_UNAVAILABLE'

def test_cancellation_is_resolved_only_by_matching_unfilled_broker_history(live_bot):
    b,sdk=live_bot;sdk.fill=False;sdk.no_response=True
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        request=sdk.sent[0]
        sdk.history_orders_get=lambda *args:(NS(ticket=500,magic=request['magic'],comment=request['comment'],state=2,volume_initial=request['volume'],volume_current=request['volume'],time_done=time.time()-20),)
        await b.reconcile_live()
        assert b.trade_history[0]['status']=='REJECTED' and not b._has_open_trade
        assert b.stats['wins']==0 and b.stats['daily_pnl']==0
    asyncio.run(run())

def test_widening_stop_blocks_entries_and_keeps_monitoring(live_bot):
    b,sdk=live_bot
    async def run():
        await b.execute_trade_from_signal(signal(),True)
        sdk.positions[0].sl-=.01;b.is_running=True
        await b.reconcile_live()
        assert b._has_open_trade and b.is_running
        assert b._risk_block(manual=True)
        assert not b.trade_history[0]['protected']
    asyncio.run(run())

def test_native_send_does_not_block_status_or_telegram_pause(live_bot,monkeypatch):
    import threading
    b,sdk=live_bot;entered=threading.Event();release=threading.Event();original=sdk.order_send
    def slow_send(request):
        entered.set()
        if not release.wait(5):raise RuntimeError('test timeout')
        return original(request)
    sdk.order_send=slow_send
    async def run():
        task=asyncio.create_task(b.execute_trade_from_signal(signal(),True))
        try:
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.01)
            assert entered.is_set() and b._mt5_busy
            monkeypatch.setattr(b.mt5,'get_candles',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('SDK read during send')))
            snapshot=b.get_market_snapshot()
            assert snapshot['has_open_trade']
            b.is_running=True;b.pause_from_telegram()
            assert not b.is_running
            assert 'PAUSADO' in b.telegram_status()
        finally:
            release.set()
            await task
        assert b._has_open_trade and len(sdk.sent)==1
    asyncio.run(run())

def test_stale_historical_bars_cannot_feed_live_entry(live_bot,monkeypatch):
    b,sdk=live_bot;frame=b.mt5.get_candles('EURUSD')
    frame['time']=frame['time']-pd.Timedelta(days=1)
    monkeypatch.setattr(b.mt5,'get_candles',lambda *a,**kw:frame.copy())
    assert not asyncio.run(b.execute_trade_from_signal(signal(),True))['success']
    assert not sdk.sent

def test_native_api_round_trip_reports_fill_and_broker_pnl(live_bot,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    b,sdk=live_bot
    # This test isolates transport and reconciliation; structural gates are tested separately.
    b.update_config({"strategy_params":{"use_structure_stops":False,"use_structure_targets":False}})
    monkeypatch.setattr(main,'bot',b)
    with TestClient(main.app,base_url='http://127.0.0.1:8000') as client:
        response=client.post('/api/order/manual',json={'action':'BUY'})
        assert response.status_code==202 and response.json()['mode']=='LIVE_MT5'
        state=client.post('/api/mt5/reconcile').json()
        assert state['has_open_trade'] and state['trades'][0]['status']=='OPEN'
        sdk.close_position()
        state=client.post('/api/mt5/reconcile').json()
        assert not state['has_open_trade'] and state['trades'][0]['pnl']==pytest.approx(2.45)
        assert client.get('/api/state').json()['currency']=='USD'


def test_terminal_path_is_passed_as_documented_positional_argument(monkeypatch):
    import backend.mt5_connector as native
    calls=[]
    sdk=FakeMT5()
    fake=NS(initialize=lambda *args,**kw:calls.append((args,kw)) or True,account_info=sdk.account_info)
    monkeypatch.setattr(native,'mt5',fake);monkeypatch.setattr(native,'MT5_AVAILABLE',True)
    connector=native.MT5Connector()
    assert connector.connect(path='C:/Broker/terminal64.exe',use_simulation_if_unavailable=False)['status']=='connected'
    assert calls==[(('C:/Broker/terminal64.exe',),{})]

def test_live_configuration_cannot_exist_without_an_account_binding():
    with pytest.raises(ValueError):TradingConfig(execution_mode='LIVE_MT5',market_type='MT5_FOREX')


@pytest.mark.parametrize('target,field,value,expected', [
    ('terminal','trade_allowed',False,'ative AlgoTrading'),
    ('terminal','tradeapi_disabled',True,'desmarque Desabilitar'),
    ('info','trade_allowed',False,'senha principal'),
    ('info','trade_expert',False,'corretora'),
])
def test_permission_failure_identifies_action_and_never_sends(live_bot,target,field,value,expected):
    b,sdk=live_bot
    setattr(getattr(sdk,target),field,value)
    result=asyncio.run(b.execute_trade_from_signal(signal(),True))
    assert not result['success'] and expected in result['message']
    assert field in result['message'] and not sdk.sent
    # The same diagnosis is visible before switching from PAPER to native.
    b.config['execution_mode']='PAPER'
    diagnostic=b.execution_diagnostics()
    assert expected in diagnostic['reason']
    assert not diagnostic['native_orders_enabled']


def test_all_terminal_permission_failures_are_reported_together(live_bot):
    b,sdk=live_bot
    sdk.terminal.trade_allowed=False;sdk.terminal.tradeapi_disabled=True
    sdk.info.trade_allowed=False;sdk.info.trade_expert=False
    with pytest.raises(MT5ExecutionError) as error:b.live.account(trading=True)
    for field in ('terminal.trade_allowed','terminal.tradeapi_disabled','account.trade_allowed','account.trade_expert'):
        assert field in str(error.value)
    assert not sdk.sent


def test_unknown_python_api_permission_is_not_assumed_allowed(live_bot):
    b,sdk=live_bot;del sdk.terminal.tradeapi_disabled
    with pytest.raises(MT5ExecutionError,match='indisponível'):b.live.account(trading=True)
    assert not sdk.sent


def test_explicit_tick_offset_normalizes_server_time_without_accepting_stale_quotes(live_bot):
    b,sdk=live_bot
    sdk.quote.time_msc+=180*60*1000
    assert b.live.quote_age('EURUSD') < -10790
    with pytest.raises(MT5ExecutionError,match='desatualizada'):
        b.live._tick('EURUSD',b.config)
    b.update_config({'mt5_tick_time_offset_minutes':180})
    assert -1 < b.live.quote_age('EURUSD') < 5
    assert b.live._tick('EURUSD',b.config).ask==sdk.quote.ask
    prepared=b.live.prepare(b.config,signal(),250,'clock-check')
    assert prepared['price']==sdk.quote.ask
    sdk.quote.time_msc-=60000
    with pytest.raises(MT5ExecutionError,match='desatualizada'):
        b.live._tick('EURUSD',b.config)
    sdk.quote.time_msc+=120000
    with pytest.raises(MT5ExecutionError,match='desatualizada'):
        b.live._tick('EURUSD',b.config)
    assert not sdk.sent


def test_tick_offset_is_also_used_for_brl_quote_freshness(live_bot):
    b,sdk=live_bot
    sdk.symbol.currency_base='USD';sdk.symbol.currency_profit='BRL'
    sdk.quote.bid=5.;sdk.quote.ask=5.01;sdk.quote.time_msc+=10800000
    with pytest.raises(MT5ExecutionError,match='desatualizada'):b.live.brl_rate('USD')
    b.update_config({'mt5_tick_time_offset_minutes':180})
    assert b.live.brl_rate('USD')[0]==pytest.approx(5.005)
    sdk.quote.time_msc-=61000
    with pytest.raises(MT5ExecutionError,match='desatualizada'):b.live.brl_rate('USD')


def test_clock_settings_persist_and_are_locked_with_open_position(live_bot):
    b,sdk=live_bot
    b.update_config({'mt5_tick_time_offset_minutes':180,'mt5_bar_time_offset_minutes':180})
    assert b.journal.load_config()['mt5_tick_time_offset_minutes']==180
    restored=BotManager()
    assert restored.mt5.tick_time_offset_minutes==180 and restored.mt5.bar_time_offset_minutes==180
    b._has_open_trade=True
    with pytest.raises(ValueError):b.update_config({'mt5_tick_time_offset_minutes':0})
    assert b.mt5.tick_time_offset_minutes==180


@pytest.mark.parametrize('value',[-841,841,1.5])
def test_invalid_clock_offsets_do_not_mutate_runtime(live_bot,value):
    b,sdk=live_bot
    with pytest.raises(ValueError):b.update_config({'mt5_tick_time_offset_minutes':value})
    assert b.mt5.tick_time_offset_minutes==0 and b.config['mt5_tick_time_offset_minutes']==0


def test_clock_diagnostics_include_raw_and_normalized_times(live_bot):
    b,sdk=live_bot;sdk.quote.time_msc+=10800000
    b.update_config({'mt5_tick_time_offset_minutes':180})
    c=b.execution_diagnostics()['clock']
    assert c['raw_tick_age_seconds'] < -10790
    assert -1 < c['tick_age_seconds'] < 5
    assert c['raw_tick_utc']!=c['tick_utc']
    assert c['bar_offset_minutes']==0  # No unverified bar conversion.


@pytest.mark.parametrize('offset',[-180,180])
def test_offset_sign_and_seconds_only_tick_timestamp(live_bot,offset):
    b,sdk=live_bot
    sdk.quote.time_msc=0;sdk.quote.time=int(time.time())+offset*60
    b.update_config({'mt5_tick_time_offset_minutes':offset})
    assert -1<b.live.quote_age('EURUSD')<5
    assert b.live._tick('EURUSD',b.config) is sdk.quote


def test_nonfinite_tick_timestamp_is_not_authorized_by_clock_adjustment(live_bot):
    b,sdk=live_bot;sdk.quote.time_msc=float('nan')
    assert b.live.quote_age('EURUSD') is None
    with pytest.raises(MT5ExecutionError,match='desatualizada'):b.live._tick('EURUSD',b.config)
    assert not sdk.sent


def test_hash_suffixed_symbol_reaches_native_sdk_and_reconciliation(live_bot,monkeypatch):
    b,sdk=live_bot
    before=dict(b.config)
    b.update_config({'symbol':'EURUSD#'})
    assert b.config=={**before,'symbol':'EURUSD#'}
    queried=[]
    for method in ('symbol_select','symbol_info','symbol_info_tick'):
        original=getattr(sdk,method)
        def exact_symbol(symbol,*args,_method=method,_original=original,**kwargs):
            assert symbol=='EURUSD#'
            queried.append(_method)
            return _original(symbol,*args,**kwargs)
        monkeypatch.setattr(sdk,method,exact_symbol)
    candles=b.mt5.get_candles
    def exact_history(symbol,*args,**kwargs):
        assert symbol=='EURUSD#'
        queried.append('get_candles')
        return candles(symbol,*args,**kwargs)
    monkeypatch.setattr(b.mt5,'get_candles',exact_history)
    async def run():
        result=await b.execute_trade_from_signal(signal(),manual=True)
        assert result['success'] and result['pending']
        assert len(sdk.sent)==1 and sdk.sent[0]['symbol']=='EURUSD#'
        assert b.trade_history[0]['symbol']=='EURUSD#'
        await b.reconcile_live()
        assert b.trade_history[0]['status']=='OPEN'
    asyncio.run(run())
    assert {'get_candles','symbol_select','symbol_info','symbol_info_tick'}<=set(queried)
