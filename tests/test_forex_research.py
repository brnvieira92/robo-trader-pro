import asyncio
import copy
from types import SimpleNamespace as NS
import pytest
from pydantic import ValidationError
from backend.config import TradingConfig
from backend.forex_backtest import ForexBacktestRequest, run_forex_backtest, exit_on_bar, HistoricalBar, SymbolSpecification
from backend.strategies import StrategyEngine


def request_data(symbols=('EURUSD#',),evaluated=1):
    config=TradingConfig(market_type='MT5_FOREX',strategy_id='ml_random_forest',symbol=symbols[0]).model_dump()
    # The execution mode is irrelevant to the offline simulation. Multi config
    # retains actual native-shape validation, but never reaches a native SDK.
    if len(symbols)>1:
        config.update(execution_mode='LIVE_MT5',mt5_account_login=123,mt5_account_server='Demo',forex_multi_enabled=True,forex_max_open_positions=1,
            forex_pairs=[dict(pair=s[:6],symbol=s,enabled=True) for s in symbols])
    candles=[dict(time=1700000040+i*60,open=1.1,high=1.1003,low=1.0997,close=1.1,volume=100,spread_points=20) for i in range(160)]
    spec=dict(point=.00001,tick_size=.00001,digits=5,tick_value_profit=1,tick_value_loss=1,
        volume_min=.01,volume_step=.01,volume_max=100,stops_level=10,margin_per_lot=1000,trade_mode=4)
    return dict(config=config,account_currency='USD',assumptions_acknowledged=True,initial_balance=10000,
        evaluated_bars=evaluated,markets=[dict(symbol=s,source='UPLOADED',specification_basis='Test fixture estimates',
            specification=copy.deepcopy(spec),candles=copy.deepcopy(candles)) for s in symbols])


def fixed_signal(monkeypatch,action='BUY',validated=True,confidence=85):
    calls=[]
    def analyze(df,symbol,*args,**kwargs):
        calls.append((symbol,list(df['time']),kwargs.get('model_engine')))
        signal=dict(symbol=symbol,action=action,confidence=confidence,price=1.1,reason='fixture',
            indicators={'price':1.1,'atr':.001,'ml_validation':{'validated':validated,'ml_confidence':confidence}},
            diagnostic={'extreme_volatility':False})
        return NS(to_dict=lambda:copy.deepcopy(signal))
    monkeypatch.setattr(StrategyEngine,'analyze',analyze)
    return calls


def test_request_requires_explicit_cost_basis_chronology_and_current_filters():
    data=request_data();ForexBacktestRequest.model_validate(data)
    for change in ('duplicate','future','spread','score','ack','missing_symbol'):
        bad=copy.deepcopy(data)
        if change=='duplicate':bad['markets'][0]['candles'][-1]['time']=bad['markets'][0]['candles'][-2]['time']
        elif change=='future':bad['markets'][0]['candles'][-1]['time']=4102444800
        elif change=='spread':bad['markets'][0]['candles'][-1].pop('spread_points')
        elif change=='score':bad['config']['min_confidence']=77
        elif change=='ack':bad['assumptions_acknowledged']=False
        elif change=='missing_symbol':bad['markets'][0]['symbol']='OTHER'
        with pytest.raises(ValidationError):ForexBacktestRequest.model_validate(bad)


def test_ambiguous_bar_uses_stop_and_costs_instead_of_binary_direction(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data();data.update(commission_per_lot_side=.5,adverse_slippage_points=2)
    data['markets'][0]['candles'][-1].update(high=1.104,low=1.098,close=1.103)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    trade=r['trades'][0]
    assert trade['exit_reason']=='SL' and trade['ambiguous']
    assert trade['net_pnl']==pytest.approx(-4.65)
    assert r['ambiguous_bars']==1 and r['losses']==1 and r['realized_pnl']<0
    assert r['max_drawdown']>=4.65-1e-8


def test_take_profit_and_open_at_end_has_no_timer_expiry(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data();data['markets'][0]['candles'][-1]['high']=1.104
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert r['trades'][0]['exit_reason']=='TP' and r['wins']==1
    data=request_data(evaluated=3)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert not r['trades'] and r['open_position']['status']=='OPEN'
    assert r['evidence']=='NO_TRADES' and r['ending_equity']<10000 # Spread mark-to-market.
    assert all(p['open_positions']==1 for p in r['equity_curve'])


def test_gap_stop_does_not_fill_at_unavailable_price(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(evaluated=2)
    data['markets'][0]['candles'][-1].update(open=1.095,high=1.096,low=1.094,close=1.095)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    trade=r['trades'][0]
    assert trade['exit_reason']=='GAP_SL' and trade['exit_price']==pytest.approx(1.095)
    assert trade['net_pnl'] < -trade['estimated_risk']


def test_sell_uses_ask_high_low_and_spread():
    data=request_data();spec=SymbolSpecification.model_validate(data['markets'][0]['specification'])
    bar=HistoricalBar(time=1700000040,open=1.1,high=1.1009,low=1.099,close=1.1,spread_points=20)
    trade=dict(action='SELL',stop_loss=1.101,take_profit=1.098)
    result=exit_on_bar(trade,bar,spec,0)
    assert result[1]=='SL' # Bid high misses SL, Ask high reaches it.


def test_models_causal_and_global_slot_shared(monkeypatch):
    calls=fixed_signal(monkeypatch)
    data=request_data(('EURUSD#','GBPUSD#'),evaluated=3)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert sum(p['entries'] for p in r['per_pair'])==1
    assert all(p['open_positions']<=1 for p in r['equity_curve'])
    for (symbol,times,engine),analysis in zip(calls,r['analyses']):
        assert max(t.timestamp() for t in times)<analysis['time']
    assert len({id(c[2]) for c in calls})==2
    assert any('ONE_POSITION' in a['blocks'] for a in r['analyses'])


def test_failed_ml_and_low_score_never_enter(monkeypatch):
    fixed_signal(monkeypatch,validated=False,confidence=70)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(request_data()))
    assert not r['trades'] and not r['open_position']
    assert 'ML_VALIDATED' in r['analyses'][0]['blocks'] and 'MIN_CONFIDENCE' in r['analyses'][0]['blocks']


def test_spread_lot_and_daily_filters_are_retained(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data();data['markets'][0]['candles'][-1]['spread_points']=50
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert 'SPREAD_LIMIT' in r['analyses'][0]['blocks']
    data=request_data();data['markets'][0]['specification']['volume_min']=1
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert 'MINIMUM_VOLUME' in r['analyses'][0]['blocks']
    data=request_data(evaluated=3);data['config']['stop_loss']=1
    data['markets'][0]['candles'][-3].update(high=1.104,low=1.098)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    # Budget is capped at daily loss before entering: minimum lot exceeds it.
    assert not r['trades']


def test_brl_goal_needs_explicit_conversion():
    data=request_data();data['config']['goal_currency']='BRL'
    with pytest.raises(ValidationError,match='taxa histórica'):ForexBacktestRequest.model_validate(data)
    data['goal_fx_account_per_brl']=.2
    ForexBacktestRequest.model_validate(data)


def test_real_strategy_with_no_trades_is_valid_research_result():
    r=run_forex_backtest(ForexBacktestRequest.model_validate(request_data()))
    assert r['evidence']=='NO_TRADES' and len(r['analyses'])==1
    assert r['datasets'][0]['hash'] and r['request_hash']


@pytest.mark.parametrize('limit,block',[('max_daily_trades','DAILY_ENTRIES'),('max_consecutive_losses','LOSS_STREAK'),('stop_gain','DAILY_GOAL')])
def test_global_daily_and_streak_limits_across_pairs(monkeypatch,limit,block):
    fixed_signal(monkeypatch)
    data=request_data(('EURUSD#','GBPUSD#'),evaluated=5)
    data['config'][limit]=6 if limit=='stop_gain' else 1
    if limit=='stop_gain':
        # Exact ratio avoids an unrelated floor-to-lot-step residual below goal.
        data['config']['strategy_params'].update(sl_atr_mult=1,tp_atr_mult=2)
    for market in data['markets']:
        for bar in market['candles'][-5:]:
            bar['high']=1.104
            if limit=='max_consecutive_losses':bar['low']=1.098
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert r['closed_trades']==1
    assert any(block in a['blocks'] for a in r['analyses'])
    assert sum(p['entries'] for p in r['per_pair'])==1


def test_swap_and_entry_commission_remain_realized_for_open_position(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(evaluated=2)
    data.update(commission_per_lot_side=.5,swap_cost_per_lot_utc_day=5)
    midnight=1704153600
    for index,bar in enumerate(data['markets'][0]['candles']):bar['time']=midnight-(159-index)*60
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert not r['trades'] and r['open_position']
    assert r['open_position']['swap_cost']==pytest.approx(.15)
    assert r['realized_pnl']==pytest.approx(-.165)


def test_mutating_future_bar_preserves_earlier_real_model_predictions():
    import numpy as np
    data=request_data(evaluated=2)
    rng=np.random.default_rng(42)
    for bar in data['markets'][0]['candles']:
        bar['close']=1.1+float(rng.uniform(-.0001,.0001))
    before=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    data['markets'][0]['candles'][-1].update(high=1.2,low=1.,close=1.15)
    after=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert before['analyses'][0]==after['analyses'][0]
    assert before['request_hash']!=after['request_hash']


def test_missing_bar_while_holding_position_never_invents_a_profitable_path(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(('EURUSD#','GBPUSD#'),evaluated=3)
    del data['markets'][0]['candles'][-2]
    with pytest.raises(ValueError,match='Lacuna no histórico.*posição aberta'):
        run_forex_backtest(ForexBacktestRequest.model_validate(data))
