import copy
import pytest
from backend.forex_backtest import ForexBacktestRequest, run_forex_backtest
from test_forex_research import request_data, fixed_signal


def parallel_data(evaluated=4):
    data=request_data(('EURUSD#','GBPUSD#'),evaluated=evaluated)
    data['config'].update(forex_max_open_positions=2,bankroll_mode='PERCENT',
        risk_per_trade_pct=1,profit_per_trade_cap=1000)
    return data


def run(data):
    return run_forex_backtest(ForexBacktestRequest.model_validate(data))


def test_two_positions_share_production_risk_caps_and_keep_analyzing(monkeypatch):
    calls=fixed_signal(monkeypatch)
    r=run(parallel_data())
    assert len(r['open_positions'])==2 and r['open_position'] is None
    assert r['max_open_positions']==2 and max(p['open_positions'] for p in r['equity_curve'])==2
    assert len(calls)==8 # Monitoring continues with both slots occupied.
    assert {t['symbol'] for t in r['open_positions']}=={'EURUSD#','GBPUSD#'}
    assert all(t['simulated'] and t['source']=='HISTORICAL_FOREX' and 'position_id' not in t for t in r['open_positions'])
    assert sum(t['reserved_risk'] for t in r['open_positions'])<=100
    assert all(t['reserved_risk']<=t['risk_budget']<=50 for t in r['open_positions'])
    assert any('POSITION_LIMIT' in a['blocks'] for a in r['analyses'])
    assert any('COOLDOWN' in a['blocks'] for a in r['analyses'])


def test_second_position_uses_its_own_tick_values_and_available_margin(monkeypatch):
    fixed_signal(monkeypatch)
    data=parallel_data()
    data['markets'][1]['specification'].update(tick_size=.0001,tick_value_loss=5,tick_value_profit=5)
    r=run(data)
    assert len(r['open_positions'])==2
    first,second=r['open_positions']
    assert second['volume']>first['volume']
    assert second['reserved_risk']<=second['risk_budget']
    data['markets'][1]['specification']['margin_per_lot']=10000000
    r=run(data)
    assert len(r['open_positions'])==1
    assert any('MARGIN' in a['blocks'] for a in r['analyses'] if a['symbol']=='GBPUSD#')


def test_gap_loss_in_first_position_blocks_other_pair_globally(monkeypatch):
    fixed_signal(monkeypatch)
    data=parallel_data()
    data['config']['stop_loss']=50
    for bar in data['markets'][0]['candles'][-3:]:
        bar.update(open=1.09,high=1.091,low=1.089,close=1.09)
    r=run(data)
    assert r['trades'][0]['exit_reason']=='GAP_SL'
    assert r['realized_pnl'] < -50
    assert not r['open_positions']
    assert any('DAILY_LOSS' in a['blocks'] for a in r['analyses'] if a['symbol']=='GBPUSD#')


def test_simultaneous_closures_have_separate_pnl_and_floating_sum(monkeypatch):
    fixed_signal(monkeypatch)
    data=parallel_data()
    data['markets'][0]['candles'][-1].update(high=1.104)
    data['markets'][1]['candles'][-1].update(low=1.098)
    r=run(data)
    assert len(r['trades'])==2 and r['wins']==r['losses']==1
    assert not r['open_positions']
    assert r['closed_trade_net_pnl']==pytest.approx(sum(t['net_pnl'] for t in r['trades']))
    assert r['ending_equity']==r['ending_balance']
    groups=r['financial_breakdown']['symbol']
    assert sum(g['net_pnl'] for g in groups)==pytest.approx(r['closed_trade_net_pnl'])
    assert all(g['closed_trades']==1 for g in groups)


def test_extremes_after_exit_are_not_misrepresented_as_excursions(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data(evaluated=1)
    data['markets'][0]['candles'][-1].update(low=1.09,high=1.11)
    r=run(data);t=r['trades'][0]
    assert t['sampled_mae_r']==pytest.approx(1)
    assert t['sampled_mfe_r']==0 # Unknown high before/after SL is excluded.
    assert t['ambiguous'] and 'LOWER_BOUND' in t['excursion_basis']
    assert r['financial_breakdown']['overall']['win_rate_ci95'][1]<100


def test_full_nonexit_bar_records_bid_ask_excursions(monkeypatch):
    fixed_signal(monkeypatch,action='SELL')
    data=request_data(evaluated=2)
    r=run(data);t=r['open_positions'][0]
    # Entry Bid 1.1; highest Ask 1.1005, lowest Ask 1.0999.
    assert t['sampled_mae_price']==pytest.approx(.0005)
    assert t['sampled_mfe_price']==pytest.approx(.0001)
    assert r['financial_breakdown']['overall']['expectancy'] is None


def test_adverse_slippage_cannot_overreserve_or_enter_beyond_stop(monkeypatch):
    fixed_signal(monkeypatch)
    data=parallel_data();data['adverse_slippage_points']=500
    r=run(data)
    assert not r['open_positions'] and not r['trades']
    assert any('MINIMUM_VOLUME' in a['blocks'] or 'REWARD_RISK' in a['blocks'] for a in r['analyses'])


def test_parallel_simulation_is_causal_at_entry(monkeypatch):
    fixed_signal(monkeypatch)
    data=parallel_data();before=run(data)
    changed=copy.deepcopy(data)
    changed['markets'][1]['candles'][-1].update(high=1.12,low=1.08)
    after=run(changed)
    assert before['analyses'][:4]==after['analyses'][:4]
    assert before['request_hash']!=after['request_hash']


def test_liquid_reward_risk_after_costs_is_not_just_quote_ratio(monkeypatch):
    fixed_signal(monkeypatch)
    data=request_data()
    data['config'].update(entry_amount=10,profit_per_trade_cap=1000)
    data['config']['strategy_params'].update(sl_atr_mult=1.5,tp_atr_mult=2.4)
    data['adverse_slippage_points']=4
    r=run(data)
    analysis=r['analyses'][0]
    assert not r['open_positions']
    assert 'REWARD_RISK' in analysis['blocks']
    assert 'Retorno/risco' in analysis['preflight_reason'] # Rejected by shared preparation before reserving.


def test_larger_evaluation_keeps_warmup_and_closed_bar_causality(monkeypatch):
    calls=fixed_signal(monkeypatch)
    data=request_data(evaluated=1)
    first=data['markets'][0]['candles'][0]
    data['markets'][0]['candles']=[{**first,'time':first['time']+i*60} for i in range(2100)]
    data['evaluated_bars']=250
    r=run(data)
    assert r['evaluated_bars']==250 and len(calls)==250
    assert all(len(times)==239 for _,times,_ in calls)
    assert all(max(t.timestamp() for t in times)<a['time'] for (_,times,_),a in zip(calls,r['analyses']))


def test_known_friction_reduces_lot_before_reserving_instead_of_rejecting_every_trade(monkeypatch):
    fixed_signal(monkeypatch)
    data=parallel_data();data['config']['strategy_params']['tp_atr_mult']=4
    clean=run(data)
    data.update(adverse_slippage_points=10,commission_per_lot_side=2)
    stressed=run(data)
    assert len(clean['open_positions'])==len(stressed['open_positions'])==2
    assert sum(t['reserved_risk'] for t in stressed['open_positions'])<=100
    for clean_trade,t in zip(clean['open_positions'],stressed['open_positions']):
        assert t['volume']<clean_trade['volume']
        assert t['reserved_risk']<=t['risk_budget']
        assert t['assumed_net_reward_risk']>=1.5
        assert t['plan']['estimated_loss']==pytest.approx(t['reserved_risk'])
