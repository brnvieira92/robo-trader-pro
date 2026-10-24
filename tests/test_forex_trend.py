import asyncio
import copy
import numpy as np
import pandas as pd
import pytest
from backend.config import TradingConfig,strategy_requires_ml
from backend.strategies import StrategyEngine
from backend.trade_plan import price_plan,TradePlanError
from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
from backend.mt5_execution import MT5ExecutionError
from test_mt5_live import live_bot
from test_forex_multi import enable
from test_forex_research import request_data

STRATEGY='forex_trend_continuation'


def candles(side='BUY',trigger='BREAKOUT'):
    close=1.1+np.arange(180)*.00003;opens=np.r_[close[0],close[:-1]]
    df=pd.DataFrame(dict(time=pd.date_range('2026-01-01',periods=180,freq='min'),
        open=opens,high=np.maximum(opens,close)+.00006,low=np.minimum(opens,close)-.00010,
        close=close,volume=100))
    if trigger=='BREAKOUT':
        p=float(df.iloc[-2]['close']);df.loc[179,['open','high','low','close']]=[p,p+.00014,p-.00002,p+.00012]
    else:
        p=float(df.iloc[-4]['close'])
        df.loc[177,['open','high','low','close']]=[p+.00003,p+.00070,p-.00018,p-.00012]
        df.loc[178,['open','high','low','close']]=[p-.00012,p-.00010,p-.00020,p-.00014]
        df.loc[179,['open','high','low','close']]=[p-.00014,p-.00003,p-.00016,p-.00004]
    if side=='SELL':
        old=df.copy();df['high']=2.2-old['low'];df['low']=2.2-old['high']
        df['open']=2.2-old['open'];df['close']=2.2-old['close']
    return df


def analyze(df):
    config=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY).model_dump()
    return StrategyEngine.analyze(df,'EURUSD#','MT5_FOREX','M1',STRATEGY,config['strategy_params']).to_dict()


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('trigger',['BREAKOUT','PULLBACK'])
def test_new_strategy_generates_both_directions_without_calling_rf(side,trigger,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('RF must not decide this strategy')
    monkeypatch.setattr('backend.strategies.ml_engine.predict_next_candle',forbidden)
    s=analyze(candles(side,trigger));c=s['indicators']['entry_confirmation']
    assert s['action']==side and s['confidence']>=78
    assert c['trigger']==trigger and c['passed']
    assert not s['indicators']['ml_validation']['enabled']
    assert not strategy_requires_ml(STRATEGY,{'use_ml_filter':True})
    assert s['indicators']['score_kind']=='technical_rules_not_win_probability'
    assert s['confidence']==sum(c['score_components'].values())
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY).model_dump()
    plan=price_plan(cfg,s,s['price'],tick_size=.00001,digits=5,spread=.00008)
    assert plan['reward_risk']>=1.5 and plan['stop_atr']<=4
    if trigger=='BREAKOUT':
        assert plan['structural_target_bid'] is None
        # Current wick/rolling extreme is not a confirmed opposing barrier.
        assert s['indicators']['target_support'] is None and s['indicators']['target_resistance'] is None
    else:
        assert plan['structural_target_bid'] is not None


def test_new_strategy_rejects_inside_doji_missing_context_and_overextension():
    df=candles();p=float(df.iloc[-2]['close'])
    df.loc[179,['open','high','low','close']]=[p,p+.00001,p-.00001,p]
    s=analyze(df);assert s['action']=='NEUTRAL' and s['confidence']==0
    assert not s['indicators']['entry_confirmation']['checks'][2]['passed']
    assert analyze(candles().iloc[:100])['action']=='NEUTRAL'
    df=candles();p=float(df.iloc[-2]['close'])
    df.loc[179,['open','high','low','close']]=[p,p+.00201,p-.00001,p+.002]
    s=analyze(df);assert s['action']=='NEUTRAL'
    assert any(not c['passed'] for c in s['indicators']['entry_confirmation']['checks'] if c['code']=='TREND_EXTENSION')


def test_future_candles_do_not_change_a_past_signal():
    df=candles();before=copy.deepcopy(analyze(df))
    future=pd.concat([df,df.tail(1)],ignore_index=True)
    future.loc[180,'time']=df.iloc[-1]['time']+pd.Timedelta(minutes=1)
    future.loc[180,['high','close']]=[2.,2.]
    assert analyze(future.iloc[:180])==before


@pytest.mark.parametrize('bad',[
    {'timeframe':'M5'},{'market_type':'MT5_B3'},{'min_confidence':77}])
def test_new_strategy_scope_is_explicit(bad):
    with pytest.raises(ValueError):TradingConfig.model_validate(dict(strategy_id=STRATEGY,market_type='MT5_FOREX')|bad)


def test_risk_gate_does_not_reintroduce_rf_dependency_and_native_risk_is_preserved(live_bot):
    b,sdk=live_bot;b.update_config({'strategy_id':STRATEGY})
    s=analyze(candles('SELL'));s['bar_time']='closed trend bar'
    assert b._risk_block(s) is None
    sdk.quote.bid=s['price'];sdk.quote.ask=s['price']+.00008
    prepared=b.live.prepare(b.config,s,250,'trend')
    assert prepared['estimated_risk']<=prepared['risk_budget']
    assert prepared['stop_loss']>prepared['price']>prepared['take_profit']
    assert prepared['plan']['spread_to_stop']<=b.config['mt5_max_spread_stop_ratio']
    assert not sdk.sent
    # A real nearby confirmed support still caps the target and can reject it.
    s['indicators']['target_support']=s['price']-.00001
    with pytest.raises(MT5ExecutionError):b.live.prepare(b.config,s,250,'blocked')
    assert not sdk.sent


def test_multipair_new_strategy_and_disabled_rf_are_not_mislabeled_as_ml_failure(live_bot,monkeypatch):
    b,_=live_bot;enable(b)
    b.update_config({'strategy_id':STRATEGY})
    def forbidden(*a,**k):raise AssertionError('RF should not execute')
    monkeypatch.setattr('backend.ml_engine.MLEngine.predict_next_candle',forbidden)
    from backend.forex_multi import ForexCoordinator
    multi=ForexCoordinator(b);asyncio.run(multi.cycle())
    assert len(multi.rows)==4
    for row in multi.rows.values():
        assert row['uses_ml'] is False and row['validated'] is None
        assert not any(c['code'].startswith('ML_') and c['status']=='FAIL' for c in row['checks'])
        assert row['entry_policy_version']=='forex-trend-v1'


def test_offline_backtest_accepts_and_evaluates_independent_strategy():
    data=request_data();data['config']['strategy_id']=STRATEGY
    data['config']['strategy_params']['use_ml_filter']=True
    df=candles('SELL');base=1700000100
    rows=[]
    for i,row in enumerate(df.to_dict('records')):
        rows.append({**row,'time':base+i*60,'spread_points':8})
    # The trigger at index 179 is evaluated at the NEXT bar's open.
    rows.append({**rows[-1],'time':base+180*60,'open':rows[-1]['close'],'high':rows[-1]['close']+.0001,'low':rows[-1]['close']-.0001})
    data['markets'][0]['candles']=rows
    result=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert result['analyses'][0]['entry_policy_version']=='forex-trend-v1'
    assert 'ML_VALIDATED' not in result['analyses'][0]['blocks']
    assert result['per_pair'][0]['entries']==1


def test_automatic_entry_persists_new_strategy_without_ml_approval(live_bot,monkeypatch):
    b,sdk=live_bot
    b.recovery.fail('model','MODEL_UNAVAILABLE','RF indisponível')
    b.update_config({'strategy_id':STRATEGY})
    assert 'model' not in b.recovery.faults
    s=analyze(candles('SELL'));s['bar_time']='closed trend bar'
    sdk.quote.bid=s['price'];sdk.quote.ask=s['price']+.00008
    monkeypatch.setattr(b,'get_market_snapshot',lambda:dict(config=b.config,
        data_quality=dict(source='MT5_LIVE',history_fresh=True)))
    b.is_running=True
    result=asyncio.run(b.execute_trade_from_signal(s,manual=False))
    assert result['success'] and len(sdk.sent)==1
    saved=b.journal.all_trades()[0]
    assert saved['strategy']==STRATEGY and saved['entry_policy_version']=='forex-trend-v1'
    assert saved['signal_snapshot']['indicators']['ml_validation']['validated'] is False
    assert saved['amount']<=saved['risk_budget']


def test_buy_resistance_inside_spread_cannot_be_ignored_as_already_broken():
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY).model_dump()
    s=analyze(candles('BUY'));s['indicators']['target_resistance']=s['price']+.00004
    with pytest.raises(TradePlanError,match='Sem espaço'):
        price_plan(cfg,s,s['price']+.00008,spread=.00008)
