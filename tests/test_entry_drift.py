"""Bid/Ask regression: execution costs must not masquerade as signal drift."""
import asyncio
import pytest
from backend.config import TradingConfig
from backend.trade_plan import price_plan, TradePlanError
from test_mt5_live import live_bot, signal


def setup(side='BUY'):
    cfg=TradingConfig().model_dump()
    cfg['strategy_params'].update(use_structure_stops=False,use_structure_targets=False,
                                  sl_atr_mult=3.,tp_atr_mult=6.)
    s=signal();s.update(action=side,price=.82533)
    s['indicators'].update(price=.82533,atr=.00014)
    return cfg,s


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('movement',[0., .00007, -.00007, .00014, -.00014])
def test_symmetric_bid_movement_including_exact_atr_boundary(side,movement):
    cfg,s=setup(side);spread=.00018;bid=.82533+movement
    entry=bid+spread if side=='BUY' else bid
    p=price_plan(cfg,s,entry,spread=spread)
    d=p['entry_drift']
    assert d['current_bid']==pytest.approx(bid)
    assert d['distance_price']==pytest.approx(abs(movement))
    assert d['execution_price']==entry
    assert p['entry_price']==entry
    assert p['risk_distance']==pytest.approx(.00042)


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('movement',[.000141,-.000141])
def test_real_movement_beyond_limit_still_rejected_with_evidence(side,movement):
    cfg,s=setup(side);spread=.00018;bid=.82533+movement
    with pytest.raises(TradePlanError,match='afastou') as error:
        price_plan(cfg,s,bid+spread if side=='BUY' else bid,spread=spread)
    d=error.value.diagnostics['entry_drift']
    assert d['distance_atr']>1 and d['maximum_distance_atr']==1
    assert d['reference_quote']=='BID' and d['spread_price']==spread


def native_setup(live_bot):
    b,sdk=live_bot;_,s=setup()
    b.config['strategy_params'].update(use_structure_stops=False,use_structure_targets=False,
                                      sl_atr_mult=4.,tp_atr_mult=8.)
    sdk.quote.bid=.82533;sdk.quote.ask=.82548
    return b,sdk,s


def test_native_buy_uses_ask_for_money_and_bid_for_drift_and_persists(live_bot):
    b,sdk,s=native_setup(live_bot)
    result=asyncio.run(b.execute_trade_from_signal(s,manual=True))
    assert result['success'] and len(sdk.sent)==1
    t=b.journal.all_trades()[0];d=t['entry_diagnostics']['entry_drift']
    assert d['distance_price']==pytest.approx(0,abs=1e-12)
    assert d['spread_price']>s['indicators']['atr']
    assert t['request']['price']==sdk.quote.ask
    assert t['plan']['spread_to_stop']<=b.config['mt5_max_spread_stop_ratio']
    assert t['plan']['stop_atr']<=4+1e-8
    assert t['amount']<=t['risk_budget']
    assert t['plan']['net_reward_risk']>=1.5


@pytest.mark.parametrize('guard',['spread','spread_stop','stale','stop','reward_risk','margin'])
def test_removing_false_drift_never_bypasses_other_native_guards(live_bot,guard):
    b,sdk,s=native_setup(live_bot)
    if guard=='spread':b.config['mt5_max_spread_points']=10
    if guard=='spread_stop':b.config['mt5_max_spread_stop_ratio']=.1
    if guard=='stale':sdk.quote.time_msc-=60000
    if guard=='stop':b.config['strategy_params']['max_stop_atr']=3
    if guard=='reward_risk':b.config['strategy_params']['tp_atr_mult']=1.
    if guard=='margin':sdk.info.margin_free=0
    result=asyncio.run(b.execute_trade_from_signal(s,manual=True))
    assert not result['success'] and not sdk.sent


def test_native_true_drift_records_numeric_rejection(live_bot):
    b,sdk,s=native_setup(live_bot)
    sdk.quote.bid+=.00015;sdk.quote.ask+=.00015
    result=asyncio.run(b.execute_trade_from_signal(s,manual=True))
    assert not result['success'] and not sdk.sent
    event=b.audit.last_event
    assert event['code']=='ENTRY_DRIFT'
    assert event['payload']['entry_diagnostics']['entry_drift']['distance_atr']>1


def test_historical_adapter_uses_same_bid_rule_and_versions_results(monkeypatch):
    from test_forex_research import request_data,fixed_signal
    from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
    from backend.trade_plan import PRICING_VERSION
    fixed_signal(monkeypatch)
    data=request_data()
    # 1.1 Bid remains flat; Ask is 1.1011 (spread exceeds the 0.001 ATR).
    data['markets'][0]['candles'][-1]['spread_points']=110
    data['config']['mt5_max_spread_points']=120
    data['config']['strategy_params'].update(use_structure_stops=False,use_structure_targets=False,
        sl_atr_mult=4.,tp_atr_mult=8.)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert r['version']==6 and r['pricing_version']==PRICING_VERSION
    assert len(r['open_positions'])==1
    t=r['open_positions'][0]
    assert t['entry_price']==pytest.approx(1.1011)
    assert t['plan']['entry_drift']['distance_price']==pytest.approx(0,abs=1e-12)
