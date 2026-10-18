import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from backend.bot_manager import BotManager
from backend.safirion_connector import SafirionConnector
from backend.mt5_connector import MT5Connector

@pytest.fixture
def bot(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADER_JOURNAL_PATH',str(tmp_path/'paper.db'))
    return BotManager()

def signal(bot):
    return {**bot.get_market_snapshot()['signal'],'action':'BUY','confidence':90}

def test_real_ticks_never_create_random_historical_candles():
    c=SafirionConnector(); c._record_live_tick(1.23456)
    assert len(c._live_candles)==1
    assert c._live_candles[0]['open']==1.23456

def test_live_bridge_with_no_history_does_not_use_simulator(bot):
    bot.safirion.bridge_ws=object()
    state=bot.get_market_snapshot()
    assert not state['data_quality']['synthetic']
    assert state['candles']==[] and state['signal']['action']=='NEUTRAL'

def test_wrong_symbol_blocks_live_analysis(bot):
    bot.safirion.bridge_ws=object()
    bot.safirion.handle_bridge_message({'type':'SAFIRION_TICK','symbol':'GBPUSD','price':1.23456})
    state=bot.get_market_snapshot()
    assert not state['data_quality']['symbol_verified'] and state['signal']['action']=='NEUTRAL'

def test_disconnected_real_feed_never_falls_back(bot):
    bot.safirion._record_live_tick(1.23456)
    state=bot.get_market_snapshot()
    assert state['data_quality']['source']=='SAFIRION_LIVE'
    assert state['data_quality']['closed_candles']==0

def test_simulator_reads_do_not_accelerate_prices():
    c=MT5Connector(); a=c.get_candles('EURUSD',count=100); b=c.get_candles('EURUSD',count=100)
    assert a.equals(b)

@pytest.mark.parametrize('mode',['FIXED','PERCENT','SMART_AI','SOROS'])
def test_risk_caps_every_sizing_mode(bot,mode):
    bot.config.update(bankroll_mode=mode,paper_balance=1000,entry_amount=500,risk_per_trade_pct=1)
    amount,_=bot._calculate_smart_entry_amount()
    assert amount<=10
    bot.stats['daily_pnl']=-249
    assert bot._calculate_smart_entry_amount()[0]==0

def test_risk_budget_below_minimum_abstains(bot):
    bot.config['paper_balance']=100
    assert bot._calculate_smart_entry_amount()[0]==0

@pytest.mark.parametrize('key,value', [('daily_pnl',-250),('daily_pnl',500),('loss_streak',3)])
def test_manual_orders_cannot_bypass_limits(bot,key,value):
    bot.stats[key]=value
    result=asyncio.run(bot.execute_trade_from_signal(signal(bot),manual=True))
    assert not result['success'] and bot.trade_history==[]

def test_concurrent_manual_orders_send_no_real_commands(bot):
    bot.safirion.execute_binary_order=AsyncMock(side_effect=AssertionError('Live order!'))
    bot.mt5.execute_order=lambda **kw: (_ for _ in ()).throw(AssertionError('Live order!'))
    async def run():
        s=signal(bot)
        results=await asyncio.gather(bot.execute_trade_from_signal(s,True),bot.execute_trade_from_signal(s,True))
        assert sum(r['success'] for r in results)==1
        await asyncio.sleep(0)
        await bot.close()
    asyncio.run(run())
    assert len(bot.trade_history)==1 and bot.trade_history[0]['mode']=='PAPER'
    bot.safirion.execute_binary_order.assert_not_awaited()

def test_candle_id_and_cooldown_prevent_duplicate_automatic_entry(bot):
    s=signal(bot); s['indicators']['ml_validation']={'validated':True}
    bot._last_signal_bar=s['bar_time']
    assert bot._risk_block(s)=='Este candle já originou uma operação.'
    bot._last_signal_bar=None; bot._last_trade_timestamp=datetime.now().timestamp()
    assert 'Intervalo' in bot._risk_block(s)

def test_config_is_atomic_and_locked_while_running(bot):
    before=dict(bot.config)
    with pytest.raises(ValueError): bot.update_config({'entry_amount':-1})
    assert bot.config==before
    bot.is_running=True
    with pytest.raises(ValueError): bot.update_config({'symbol':'GBPUSD'})

@pytest.mark.parametrize('movement,expected_status,expected_pnl',[(.1,'WIN',4),(-.1,'LOSS',-5),(0,'DRAW',0)])
def test_paper_settlement_ties_and_payout(bot,movement,expected_status,expected_pnl,monkeypatch):
    import pandas as pd
    record=dict(id='test',day=datetime.now(timezone.utc).date().isoformat(),status='OPEN',amount=5,
                source='SIMULATED',symbol='EURUSD',timeframe='M1',market='SAFIRION_OB',
                action='BUY',entry_price=1.,payout_pct=80,remaining_sec=0)
    bot.trade_history=[record];bot._has_open_trade=True
    monkeypatch.setattr(bot.mt5,'get_candles',lambda *a,**kw:pd.DataFrame({'close':[1+movement]}))
    asyncio.run(bot._settle_trade_after_real_expiration(record,datetime.now()-timedelta(seconds=1)))
    assert record['status']==expected_status and record['pnl']==expected_pnl
    assert not bot._has_open_trade
    assert 'PAPER' in record['settlement_note']

def test_stale_feed_settlement_never_becomes_a_win(bot):
    record=dict(id='test',day=datetime.now(timezone.utc).date().isoformat(),status='OPEN',amount=5,
                source='SAFIRION_LIVE',symbol='EURUSD',timeframe='M1',market='SAFIRION_OB',
                action='BUY',entry_price=1.,payout_pct=80,remaining_sec=0)
    bot.trade_history=[record]
    asyncio.run(bot._settle_trade_after_real_expiration(record,datetime.now()-timedelta(seconds=1)))
    assert record['status']=='INTERRUPTED' and record['pnl']==-5
    assert bot.stats['wins']==0

def test_restart_preserves_losses_and_interrupted_risk(bot):
    record=dict(id='pending',day=datetime.now(timezone.utc).date().isoformat(),status='OPEN',amount=5,pnl=0)
    bot.journal.save(record)
    restored=BotManager()
    assert restored.stats['daily_pnl']==-5 and restored.stats['loss_streak']==1
    assert restored.trade_history[0]['status']=='INTERRUPTED'

def test_risk_rounds_down_never_above_budget(bot):
    bot.config.update(paper_balance=1000.9,entry_amount=100,risk_per_trade_pct=1)
    amount,_=bot._calculate_smart_entry_amount()
    assert amount<=10.009

def test_validated_config_survives_restart(bot):
    bot.update_config({'paper_balance':2000,'risk_per_trade_pct':.5})
    restored=BotManager()
    assert restored.config['paper_balance']==2000
    assert restored.config['risk_per_trade_pct']==.5

def test_reading_different_timeframe_does_not_refresh_stale_quote():
    connector=SafirionConnector()
    connector._record_live_tick(1.23456,'M1')
    connector.last_tick_timestamp=1
    assert connector.get_live_candles_df('M5') is None
    assert connector.last_tick_timestamp==1

def test_analysis_excludes_forming_candle(bot):
    state=bot.get_market_snapshot()
    raw=bot.mt5.get_candles('EURUSD','M1',count=240)
    assert state['signal']['bar_time']==str(raw.iloc[-2]['time'])
    assert state['data_quality']['closed_candles']==len(raw)-1

def test_empty_bridge_then_disconnect_does_not_corrupt_simulation_cache(bot):
    bot.safirion.bridge_ws=object()
    assert bot.get_market_snapshot()['candles']==[]
    bot.safirion.detach_bridge_websocket()
    assert len(bot.get_market_snapshot()['candles'])==60
