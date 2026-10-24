import copy
from types import SimpleNamespace as NS
import pytest
from backend.forex_exit_research import ResearchExitManagement,update_research_stop
from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
from test_forex_research import request_data,fixed_signal


def fixture(side='BUY'):
    direction=1 if side=='BUY' else -1
    spec=NS(point=.01,tick_size=.01,digits=2,stops_level=0,tick_value_profit=1)
    trade=dict(action=side,entry_price=100.,stop_loss=100-2*direction,take_profit=100+6*direction,
        initial_stop_loss=100-2*direction,initial_risk_distance=2.,volume=1.,swap_cost=.5,
        reserved_risk=200.,cost_buffer_configured=1.,stop_updates=[])
    mark=100+direction*2.5
    bar=NS(close=mark-(.02 if side=='SELL' else 0),spread_points=2,high=110,low=90)
    return trade,bar,spec


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_break_even_covers_known_costs_and_exit_slippage(side):
    trade,bar,spec=fixture(side);policy=ResearchExitManagement(mode='BREAK_EVEN')
    update_research_stop(trade,bar,spec,1,policy,1,2,120)
    assert trade['stop_loss']==pytest.approx(100.06 if side=='BUY' else 99.94)
    assert trade['reserved_risk']==200 and trade['initial_risk_distance']==2
    assert trade['stop_updates'][0]['effective_at']==120
    assert trade['stop_updates'][0]['reason']=='BREAK_EVEN'


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_trailing_is_monotone_and_can_combine_with_break_even(side):
    trade,bar,spec=fixture(side);policy=ResearchExitManagement(mode='BREAK_EVEN_ATR')
    update_research_stop(trade,bar,spec,1,policy,1,2,120)
    assert trade['stop_loss']==pytest.approx(100.5 if side=='BUY' else 99.5)
    assert trade['stop_updates'][0]['reason']=='ATR_TRAILING'
    before=copy.deepcopy(trade)
    # Increased volatility must not widen a previously tightened stop.
    update_research_stop(trade,bar,spec,2,policy,1,2,180)
    assert trade==before


def test_intrabar_high_does_not_activate_protection_and_fixed_is_unchanged():
    trade,bar,spec=fixture();bar.close=101
    before=copy.deepcopy(trade)
    update_research_stop(trade,bar,spec,1,ResearchExitManagement(mode='BREAK_EVEN'),0,0,120)
    assert trade==before # Despite bar.high=110.
    bar.close=104
    update_research_stop(trade,bar,spec,1,ResearchExitManagement(),0,0,120)
    assert trade==before


def test_activated_trailing_keeps_monitoring_after_price_returns_below_trigger():
    trade,bar,spec=fixture();policy=ResearchExitManagement(mode='ATR_TRAILING')
    update_research_stop(trade,bar,spec,1,policy,0,0,120)
    assert trade['exit_management_activated']
    bar.close=101.9
    update_research_stop(trade,bar,spec,.5,policy,0,0,180)
    assert trade['stop_loss']==pytest.approx(100.9) and len(trade['stop_updates'])==2


def test_stop_cannot_cross_market_or_broker_distance():
    trade,bar,spec=fixture();spec.stops_level=300
    before=copy.deepcopy(trade)
    update_research_stop(trade,bar,spec,1,ResearchExitManagement(mode='BREAK_EVEN'),0,0,120)
    assert trade==before
    spec.stops_level=0
    update_research_stop(trade,bar,spec,1,ResearchExitManagement(mode='BREAK_EVEN'),1000,0,120)
    assert trade==before # Cost-covering stop would be beyond the current quote.


@pytest.mark.parametrize('invalid',[{'activation_r':0},{'trailing_atr_mult':0},{'mode':'NATIVE_TRAILING'},{'activation_r':float('nan')}])
def test_exit_experiments_have_bounded_explicit_parameters(invalid):
    with pytest.raises(ValueError):ResearchExitManagement.model_validate(invalid)


def test_research_exit_configuration_cannot_be_used_as_a_live_config_field():
    from backend.config import TradingConfig
    with pytest.raises(ValueError):TradingConfig.model_validate(dict(exit_management={'mode':'BREAK_EVEN'}))


def test_adjustment_is_effective_only_next_bar_and_frozen_risk_remains_denominator(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(evaluated=3)
    # First candle's low precedes/follows activation ambiguously. The new stop
    # must never be applied retroactively to that candle.
    data['markets'][0]['candles'][-3].update(high=1.1025,low=1.0997,close=1.1024)
    data['markets'][0]['candles'][-2].update(open=1.1024,high=1.1025,low=1.1001,close=1.101)
    data['exit_management']=dict(mode='BREAK_EVEN',activation_r=1,trailing_atr_mult=2)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data));t=r['trades'][0]
    assert t['status']=='DRAW' and t['net_pnl']==0
    assert r['draws']==1 and r['wins']==r['losses']==0
    assert len(t['stop_updates'])==1
    assert t['exit_time']==data['markets'][0]['candles'][-2]['time']+60
    assert t['stop_updates'][0]['effective_at']==data['markets'][0]['candles'][-2]['time']
    assert t['initial_stop_loss']<t['entry_price']==t['stop_loss']
    assert t['sampled_mae_r']>0 and t['initial_risk_distance']>0 # No division by BE distance zero.
    assert t['reserved_risk']==t['estimated_risk']


def test_future_gap_still_loses_after_break_even(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(evaluated=2)
    data['markets'][0]['candles'][-2].update(high=1.1025,low=1.0997,close=1.1024)
    data['markets'][0]['candles'][-1].update(open=1.099,high=1.1,low=1.098,close=1.099)
    data['exit_management']=dict(mode='BREAK_EVEN')
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data));t=r['trades'][0]
    assert t['status']=='LOSS' and t['exit_reason']=='GAP_SL' and t['net_pnl']<0
    assert t['stop_updates'] and t['exit_price']<t['stop_loss']


def test_no_intrabar_activation_from_unknown_price_order(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(evaluated=1)
    data['markets'][0]['candles'][-1].update(high=1.1025,low=1.0997,close=1.1005)
    data['exit_management']=dict(mode='BREAK_EVEN')
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert r['open_positions'][0]['stop_updates']==[]
