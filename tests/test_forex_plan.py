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


def test_sell_structural_barriers_are_translated_from_bid_to_ask():
    c=TradingConfig().model_dump()
    c['strategy_params'].update(sl_atr_mult=1.,tp_atr_mult=4.)
    s=signal();s['action']='SELL'
    s['indicators'].update(support=1.0965,resistance=1.1014)
    baseline=price_plan(c,s,1.1,tick_size=.00001,digits=5)
    adjusted=price_plan(c,s,1.1,tick_size=.00001,digits=5,spread=.0002)
    assert adjusted['stop_loss']==pytest.approx(baseline['stop_loss']+.0002)
    assert adjusted['take_profit']==pytest.approx(baseline['take_profit']+.0002)
    # In Bid coordinates the same invalidation/obstacle buffers are preserved.
    assert adjusted['stop_loss']-.0002>=1.1014+.001*.15-1e-10
    assert adjusted['take_profit']-.0002>=1.0965+.001*.1-1e-10
    assert adjusted['exit_quote']=='ASK' and adjusted['structure_quote']=='BID'
    assert adjusted['reward_risk']>=1.5


def test_buy_structure_is_not_shifted_or_charged_spread_twice():
    c=TradingConfig().model_dump();s=signal()
    s['indicators'].update(support=1.099,resistance=1.104)
    baseline=price_plan(c,s,1.1,tick_size=.00001,digits=5)
    adjusted=price_plan(c,s,1.1,tick_size=.00001,digits=5,spread=.0002)
    assert adjusted['stop_loss']==baseline['stop_loss']
    assert adjusted['take_profit']==baseline['take_profit']
    assert adjusted['exit_quote']=='BID' and adjusted['structure_shift']==0


def test_spread_adjustment_cannot_relax_minimum_reward_risk():
    c=TradingConfig().model_dump();s=signal();s['action']='SELL'
    s['indicators'].update(resistance=1.1015,support=1.095)
    assert price_plan(c,s,1.1)['reward_risk']>=1.5
    with pytest.raises(TradePlanError,match='retorno/risco'):
        price_plan(c,s,1.1,spread=.0002)


def test_sell_target_without_executable_space_is_rejected():
    s=signal();s['action']='SELL';s['indicators'].update(support=1.0999)
    with pytest.raises(TradePlanError,match='Sem espaço'):
        price_plan(TradingConfig().model_dump(),s,1.1,spread=.0002)


def test_spread_adjusted_structural_stop_still_obeys_four_atr_ceiling():
    c=TradingConfig().model_dump();c['strategy_params']['tp_atr_mult']=8.
    s=signal();s['action']='SELL';s['indicators'].update(resistance=1.1037,support=1.09)
    assert price_plan(c,s,1.1)['stop_atr']<4
    with pytest.raises(TradePlanError,match='Stop estrutural distante'):
        price_plan(c,s,1.1,spread=.0002)


def test_tick_rounding_cannot_widen_stop_past_configured_atr_limit():
    c=TradingConfig().model_dump();c['strategy_params'].update(sl_atr_mult=1.,tp_atr_mult=8.)
    s=signal();s['action']='SELL';s['indicators'].update(resistance=1.10386,support=1.09)
    with pytest.raises(TradePlanError,match='após ajuste ao tick'):
        price_plan(c,s,1.10002,tick_size=.0001,digits=4)


@pytest.mark.parametrize('spread',[float('nan'),float('inf'),-.0001])
def test_invalid_spread_cannot_produce_a_price_plan(spread):
    with pytest.raises(TradePlanError,match='Spread inválido'):
        price_plan(TradingConfig().model_dump(),signal(),1.1,spread=spread)


def test_native_sell_adjustment_resizes_lot_and_persists_quote_and_ml(live_bot):
    b,sdk=live_bot
    b.config['strategy_params'].update(sl_atr_mult=1.,tp_atr_mult=4.)
    s=signal();s['action']='SELL'
    s['indicators'].update(support=1.0965,resistance=1.1014)
    reference=price_plan(b.config,s,float(sdk.quote.bid),tick_size=.00001,digits=5)
    # Positive execution costs and existing portfolio gates remain independently checked.
    result=asyncio.run(b.execute_trade_from_signal(s,manual=True))
    assert result['success']
    trade=b.trade_history[0];p=trade['plan']
    assert p['stop_loss']>reference['stop_loss']
    assert sdk.order_calc_profit(sdk.ORDER_TYPE_SELL,b.config['symbol'],trade['requested_volume'],
                                 trade['request']['price'],trade['stop_loss'])>=-trade['risk_budget']-1e-8
    assert trade['amount']<=trade['risk_budget']
    assert trade['entry_diagnostics']['spread_points']==pytest.approx(20)
    assert trade['entry_diagnostics']['bid']==sdk.quote.bid
    assert p['spread_to_stop']==pytest.approx(.0002/p['risk_distance'])
    assert trade['signal_snapshot']['indicators']['ml_validation']['validated']
    assert trade['signal_snapshot']['confidence']==85
    s['indicators']['ml_validation']['validated']=False
    assert trade['signal_snapshot']['indicators']['ml_validation']['validated']
    restored=BotManager().journal.all_trades()[0]
    assert restored['entry_diagnostics']==trade['entry_diagnostics']
    asyncio.run(b.reconcile_live())
    sdk.close_position();asyncio.run(b.reconcile_live())
    assert 'commission' in trade['deals'][0] and 'reason' in trade['deals'][0]
    assert trade['execution_summary']['costs']['commission']==pytest.approx(-.4)
    assert len(sdk.sent)==1


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
