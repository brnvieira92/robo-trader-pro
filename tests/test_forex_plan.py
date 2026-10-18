"""Price exits, structural planning and cash goals without a real broker."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pytest
from backend.config import TradingConfig
from backend.bot_manager import BotManager
from backend.trade_plan import price_plan, goal_limited_budget, TradePlanError
from backend.mt5_execution import MT5ExecutionError
from test_mt5_live import live_bot, signal

@pytest.fixture
def paper(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADER_JOURNAL_PATH',str(tmp_path/'forex.db'))
    b=BotManager();b.update_config({'market_type':'MT5_FOREX'})
    return b

@pytest.mark.parametrize('side', ['BUY','SELL'])
def test_structural_plan_respects_obstacles_and_rounding(side):
    c=TradingConfig().model_dump();s=signal();s['action']=side
    s['indicators'].update(support=1.099 if side=='BUY' else 1.096,
                           resistance=1.104 if side=='BUY' else 1.101)
    p=price_plan(c,s,1.1,tick_size=.00001,digits=5)
    assert p['reward_risk']>=1.5 and p['expiration_time'] is None
    if side=='BUY':
        assert p['stop_loss']<1.099 and p['take_profit']<1.104
    else:
        assert p['stop_loss']>1.101 and p['take_profit']>1.096


def test_nearby_resistance_rejects_entry_instead_of_impossible_target():
    s=signal();s['indicators'].update(support=1.099,resistance=1.101)
    with pytest.raises(TradePlanError,match='retorno/risco'):
        price_plan(TradingConfig().model_dump(),s,1.1)


def test_entry_that_has_moved_too_far_is_not_chased():
    with pytest.raises(TradePlanError,match='afastou'):
        price_plan(TradingConfig().model_dump(),signal(),1.105)


def test_goal_caps_risk_without_chasing_losses():
    c=TradingConfig().model_dump()
    assert goal_limited_budget(c,5,150,2)==5
    assert goal_limited_budget(c,5,300,2)==5
    assert goal_limited_budget(c,5,1,2)==.5
    assert goal_limited_budget(c,100,150,2)==7.5
    with pytest.raises(TradePlanError):goal_limited_budget(c,5,0,2)


async def open_position(b,side='BUY',source='SIMULATED'):
    s=signal();s['action']=side
    result=await b._open_paper_position(s,{'signal':s,'data_quality':{'source':source,'history_fresh':True}})
    assert result['success'],result
    return b.trade_history[0]


@pytest.mark.parametrize('side,reason', [('BUY','TAKE_PROFIT'),('BUY','STOP_LOSS'),('SELL','TAKE_PROFIT'),('SELL','STOP_LOSS')])
def test_paper_waits_for_price_not_elapsed_time(paper,monkeypatch,side,reason):
    async def run():
        monkeypatch.setattr(paper,'_paper_position_quote',lambda t:1.1)
        t=await open_position(paper,side)
        # A long elapsed duration never settles a Forex position.
        t['created_at']=time.time()-86400;t['expiration_time']='2000-01-01T00:00:00'
        await asyncio.sleep(.55)
        assert t['status']=='OPEN' and t['pnl']==0
        assert not paper.is_running  # monitoring continues even while paused
        price=t['take_profit'] if reason=='TAKE_PROFIT' else t['stop_loss']
        monkeypatch.setattr(paper,'_paper_position_quote',lambda t:price)
        await asyncio.sleep(.55)
        assert t['exit_reason']==reason
        assert t['status']==('WIN' if reason=='TAKE_PROFIT' else 'LOSS')
        assert not paper._has_open_trade
        await paper.close()
    asyncio.run(run())


def test_unavailable_quote_keeps_position_pending(paper,monkeypatch):
    async def run():
        def missing(t):raise ValueError('no quote')
        monkeypatch.setattr(paper,'_paper_position_quote',missing)
        t=await open_position(paper)
        await asyncio.sleep(.01)
        assert t['status']=='OPEN' and t['pnl']==0 and t['sync_error']
        with pytest.raises(ValueError):await paper.close_paper_position(t['id'])
        await paper.close()
    asyncio.run(run())


def test_manual_paper_close_uses_price_and_configured_cost(paper,monkeypatch):
    paper.config['paper_cost_per_trade']=.1
    async def run():
        monkeypatch.setattr(paper,'_paper_position_quote',lambda t:1.101)
        t=await open_position(paper)
        await paper.close_paper_position(t['id'])
        assert t['exit_reason']=='MANUAL'
        assert t['pnl']==pytest.approx(.001*t['quantity']-.1)
        with pytest.raises(ValueError):await paper.close_paper_position(t['id'])
        await paper.close()
    asyncio.run(run())


def test_legacy_timer_refuses_forex(paper):
    with pytest.raises(ValueError,match='Forex/B3'):
        asyncio.run(paper._settle_trade_after_real_expiration({'market':'MT5_FOREX'},datetime.now()))


def test_diagnostics_explains_paper_and_goal_reached_blocks_entry(paper):
    assert not paper.execution_diagnostics()['native_orders_enabled']
    assert 'PAPER selecionado' in paper.execution_diagnostics()['reason']
    paper.stats['daily_pnl']=150
    assert paper.goal_status()['reached']
    assert 'Meta' in paper._risk_block(manual=True)


def test_minimum_native_lot_cannot_overshoot_remaining_goal(live_bot):
    b,sdk=live_bot
    with pytest.raises(MT5ExecutionError,match='mínimo'):
        b.live.prepare(b.config,signal(),250,'goal',remaining_goal_account=.01)
    assert sdk.sent==[]


@pytest.mark.parametrize('inverse',[False,True])
def test_brl_goal_requires_matching_fresh_quote(live_bot,inverse):
    b,sdk=live_bot
    sdk.symbol.currency_base='BRL' if inverse else 'USD'
    sdk.symbol.currency_profit='USD' if inverse else 'BRL'
    sdk.quote.bid=sdk.quote.ask=.2 if inverse else 5
    b.config.update(goal_currency='BRL',stop_gain=150)
    b.stats['daily_pnl']=10
    g=b.goal_status()
    assert g['ready'] and g['realized']==pytest.approx(50)
    assert g['remaining_account']==pytest.approx(20)
    sdk.quote.time_msc=0
    assert not b.goal_status()['ready']
    assert b._risk_block(manual=True)


def test_wrong_currency_pair_blocks_brl_conversion(live_bot):
    b,sdk=live_bot;sdk.symbol.currency_base='EUR';sdk.symbol.currency_profit='USD'
    b.config['goal_currency']='BRL'
    assert not b.goal_status()['ready']
    assert sdk.sent==[]


def test_recovered_native_feed_paper_position_keeps_account_and_no_fake_loss(live_bot,monkeypatch):
    b,sdk=live_bot;b.update_config({'execution_mode':'PAPER'})
    async def run():
        t=await open_position(b,source='MT5_LIVE')
        assert t['account_login']==123
        t['day']=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
        b.journal.save(t)
        await b.close()
    asyncio.run(run())
    recovered=BotManager()
    assert recovered._has_open_trade and recovered.trade_history[0]['status']=='OPEN'
    assert recovered.stats['daily_pnl']==0
    b.mt5.connected=True;b.mt5.simulation_mode=False
    sdk.info.login=999
    with pytest.raises(MT5ExecutionError):b._paper_position_quote(b.trade_history[0])


def test_native_api_rejects_bad_structure_without_send(live_bot,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    b,sdk=live_bot;monkeypatch.setattr(main,'bot',b)
    with TestClient(main.app,base_url='http://127.0.0.1:8000') as client:
        response=client.post('/api/order/manual',json={'action':'BUY'})
        assert response.status_code==409
        assert sdk.sent==[]
        assert client.get('/api/execution/diagnostics').json()['native_orders_enabled']


def test_paper_api_close_does_not_send_to_mt5(paper,monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    monkeypatch.setattr(main,'bot',paper)
    with TestClient(main.app,base_url='http://127.0.0.1:8000') as client:
        async def create():
            t=await open_position(paper)
            await paper.close()
            return t
        t=asyncio.run(create())
        monkeypatch.setattr(paper,'_paper_position_quote',lambda t:1.101)
        result=client.post('/api/paper/positions/'+t['id']+'/close')
        assert result.status_code==200 and result.json()['trade']['exit_reason']=='MANUAL'
        assert client.post('/api/paper/positions/unknown/close').status_code==409


def test_stale_native_history_cannot_open_paper_position(live_bot):
    b,sdk=live_bot;b.update_config({'execution_mode':'PAPER'})
    s=signal()
    result=asyncio.run(b._open_paper_position(s,{'signal':s,'data_quality':{'source':'MT5_LIVE','history_fresh':False}}))
    assert not result['success'] and not b.trade_history
    assert not sdk.sent


def test_overnight_paper_profit_is_attributed_to_settlement_day(paper,monkeypatch):
    async def run():
        monkeypatch.setattr(paper,'_paper_position_quote',lambda t:1.101)
        t=await open_position(paper)
        t['day']=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
        await paper.close_paper_position(t['id'])
        assert paper.stats['daily_pnl']==round(t['pnl'],2)
        assert t in paper._session_trades()
        # Trades settled yesterday must not be counted in today's goal.
        t['settled_at']=time.time()-86400;paper._refresh_stats()
        assert paper.stats['daily_pnl']==0
        await paper.close()
    asyncio.run(run())
