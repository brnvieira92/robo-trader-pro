import numpy as np
import pandas as pd
import pytest
from backend.strategies import StrategyEngine
from backend.ml_engine import MLEngine
from backend.config import TradingConfig
from backend.backtest import BacktestRequest, run_backtest

@pytest.fixture
def candles():
    rng=np.random.default_rng(42)
    close=100+np.cumsum(rng.normal(0,.2,240))
    opens=np.r_[close[0],close[:-1]]
    return pd.DataFrame(dict(time=pd.date_range('2026-01-01',periods=240,freq='min'),open=opens,
                             high=np.maximum(opens,close)+.1,low=np.minimum(opens,close)-.1,close=close,volume=100))

def test_indicators_do_not_look_into_future(candles):
    before=StrategyEngine.enrich_dataframe(candles.iloc[:80],{})
    after=StrategyEngine.enrich_dataframe(candles,{})
    pd.testing.assert_frame_equal(before,after.iloc[:80])

def test_rsi_uninterrupted_rise_is_overbought():
    assert StrategyEngine.calculate_rsi(pd.Series(range(60),dtype=float),14).iloc[-1] == 100

def test_ml_scores_are_uninflated_and_chronological(candles):
    engine=MLEngine(); result=engine.predict_next_candle(StrategyEngine.enrich_dataframe(candles,{}),'test')
    assert result['enabled']
    assert result['validation_samples'] >=20
    assert result['validation_samples'] < result['samples']
    assert result['ml_confidence'] == max(result['prob_up'],result['prob_down'])
    assert result['prob_up']+result['prob_down']==pytest.approx(100)
    if not result['validated']:
        assert result['ml_action']=='NEUTRAL'
    assert 0<=result['brier_score']<=1

def test_ml_cache_isolated_by_context(candles):
    e=MLEngine(); enriched=StrategyEngine.enrich_dataframe(candles,{})
    e.predict_next_candle(enriched,'EURUSD:M1')
    e.predict_next_candle(enriched,'GBPUSD:M1')
    assert len(e._cache)==2

def test_ml_trade_pnl_does_not_become_one_bar_label(candles):
    e=MLEngine(); frame=StrategyEngine.enrich_dataframe(candles,{})
    before=e.predict_next_candle(frame)
    e.record_trade_feedback([1.]*10,'BUY',True)
    assert e.predict_next_candle(frame)==before

def test_ml_insufficient_history_abstains(candles):
    result=MLEngine().predict_next_candle(candles.iloc[:30])
    assert not result['enabled'] and result['ml_action']=='NEUTRAL'

@pytest.mark.parametrize('payload',[
    {'entry_amount':-1}, {'risk_per_trade_pct':50}, {'stop_loss':0}, {'entry_amount':float('nan')},
    {'use_martingale':True}, {'execution_mode':'LIVE'}, {'timeframe':'broken'},
    {'strategy_params':{'ema_fast':40,'ema_slow':10}}, {'unknown':1}])
def test_config_rejects_unsafe_or_invalid_parameters(payload):
    with pytest.raises(ValueError): TradingConfig.model_validate(payload)

def request_from_frame(frame,**kwargs):
    records=frame.assign(time=frame.time.map(lambda value: int(value.timestamp()))).to_dict('records')
    return BacktestRequest(candles=records,**kwargs)

def test_backtest_rejects_out_of_order_and_bad_ohlc(candles):
    with pytest.raises(ValueError): request_from_frame(candles.iloc[::-1])
    broken=candles.copy();broken.loc[2,'high']=0.1
    with pytest.raises(ValueError): request_from_frame(broken)

def test_backtest_can_return_no_trades_and_no_claim_of_edge(candles):
    req=request_from_frame(candles.iloc[:150],strategy_id='ml_random_forest',min_confidence=100)
    result=run_backtest(req)
    assert result['trades']==0
    assert result['win_rate_ci95'] is None and result['evidence']=='INSUFFICIENT'
    assert result['profit_factor'] is None

def test_backtest_next_bar_and_cost_accounting(candles,monkeypatch):
    from types import SimpleNamespace
    seen=[]
    def analyze(history,*args,**kw):
        seen.append(history.time.iloc[-1])
        return SimpleNamespace(action='BUY',confidence=80)
    monkeypatch.setattr(StrategyEngine,'analyze',analyze)
    req=request_from_frame(candles.iloc[:150],cost_per_trade=.1)
    result=run_backtest(req)
    expected=sum((4 if row.close>row.open else -5)-.1 for row in candles.iloc[120:150].itertuples())
    assert result['net_pnl']==pytest.approx(round(expected,2))
    assert seen==list(candles.time.iloc[119:149])
    assert result['trades']==30

def test_validation_uses_future_partition_and_never_training_score(candles,monkeypatch):
    import backend.ml_engine as module
    fits=[]; evaluated=[]
    class SpyForest:
        def __init__(self,**kwargs): self.classes_=np.array([0,1])
        def fit(self,X,y):
            fits.append(X.copy());return self
        def predict(self,X):
            evaluated.append(X.copy());return np.ones(len(X),dtype=int)
        def predict_proba(self,X): return np.tile([.4,.6],(len(X),1))
        def score(self,*args): raise AssertionError('Training score must never be published')
    monkeypatch.setattr(module,'RandomForestClassifier',SpyForest)
    enriched=StrategyEngine.enrich_dataframe(candles,{})
    X,_,_=MLEngine.extract_features_matrix(enriched.iloc[25:])
    MLEngine().predict_next_candle(enriched)
    split=int(len(X)*.7)
    np.testing.assert_array_equal(fits[0],X[:split-1])
    np.testing.assert_array_equal(evaluated[0],X[split:])
    np.testing.assert_array_equal(fits[1],X)

@pytest.mark.parametrize('column,value',[('close',float('nan')),('volume',-1),('high',.01)])
def test_corrupt_market_data_cannot_generate_signal(candles,column,value):
    bad=candles.copy();bad.loc[70,column]=value
    with pytest.raises(ValueError):
        StrategyEngine.analyze(bad,'EURUSD','MT5_FOREX','M1','multi_confluence')
