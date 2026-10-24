import copy
import pandas as pd
import pytest
from backend.config import TradingConfig,RESEARCH_ONLY_STRATEGIES
from backend.forex_research_candidates import research_signal,CANDIDATES
from backend.strategies import StrategyEngine
from backend.trade_plan import price_plan,TradePlanError
from backend.mt5_execution import MT5Executor,MT5ExecutionError
from scripts.research_forex_batch02 import qualifies
from test_mt5_live import live_bot


def range_frame(side='BUY'):
    # Oscillation has no net trend, followed by outside close and re-entry.
    close=[100+(-1)**i for i in range(99)]+[98.,99.]
    f=pd.DataFrame(dict(open=close,high=[x+.2 for x in close],low=[x-.2 for x in close],close=close,
        volume=100,rsi=40.,bb_lower=98.5,bb_upper=103.,bb_mid=102.,vol_sma=100.))
    f.loc[100,['open','high','low']]=[98.5,99.1,98.4]
    f.loc[99,'rsi']=30
    if side=='SELL':
        old=f.copy()
        for key in ['open','close','bb_mid']:f[key]=200-old[key]
        f['high']=200-old['low'];f['low']=200-old['high']
        f['bb_upper']=200-old['bb_lower'];f['bb_lower']=200-old['bb_upper'];f['rsi']=100-old['rsi']
    return f


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_range_reentry_is_directional_and_target_is_mean(side):
    f=range_frame(side);r=research_signal(f,{},1,'forex_range_reentry')
    assert r['action']==side and r['score']==100
    assert r['structure']['mandatory_mean_target']==(102 if side=='BUY' else 98)
    f.loc[99,'close']=f.loc[99,'bb_mid']
    assert research_signal(f,{},1,'forex_range_reentry')['action']=='NEUTRAL'


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_mean_target_is_mandatory_even_with_structural_targets_disabled(side):
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id='forex_range_reentry').model_dump()
    cfg['strategy_params']['use_structure_targets']=False
    buy=side=='BUY';entry=100 if buy else 100.1
    ind=dict(atr=1,price=100,mandatory_mean_target=104 if buy else 96)
    r=price_plan(cfg,dict(action=side,indicators=ind),entry,spread=.1)
    assert r['take_profit']==pytest.approx(103 if buy else 97.1) # 2R projection earlier than mean.
    ind['mandatory_mean_target']=100.2 if buy else 99.8
    with pytest.raises(TradePlanError):price_plan(cfg,dict(action=side,indicators=ind),entry,spread=.1)
    ind['mandatory_mean_target']=None
    with pytest.raises(TradePlanError):price_plan(cfg,dict(action=side,indicators=ind),entry,spread=.1)


def test_squeeze_requires_prior_compression_not_current_expansion():
    f=range_frame();f['bb_mid']=100.;f['bb_lower']=99.;f['bb_upper']=101.;f['vol_sma']=100
    f.loc[100,['open','close','high','low']]=[101,102,102.1,100.9]
    f.loc[100,['bb_lower','bb_upper']]=[90,110] # Current expansion must not enter reference.
    r=research_signal(f,{},1,'forex_squeeze_breakout')
    assert r['action']=='BUY'
    f.loc[99,['bb_lower','bb_upper']]=[90,110]
    assert research_signal(f,{},1,'forex_squeeze_breakout')['action']=='NEUTRAL'
    assert research_signal(f.iloc[-60:],{},1,'forex_squeeze_breakout')['action']=='NEUTRAL'


@pytest.mark.parametrize('strategy',CANDIDATES)
def test_native_executor_rejects_research_candidates_before_touching_sdk(strategy):
    assert strategy in RESEARCH_ONLY_STRATEGIES
    executor=object.__new__(MT5Executor)
    with pytest.raises(MT5ExecutionError,match='laboratório'):
        executor.prepare({'strategy_id':strategy},{},100,'test')


def test_screening_rejects_empty_small_samples_and_hidden_open_loss():
    rows=[dict(slippage=c,realized_pnl=10,equity_change=10,closed_trades=20) for c in [2,5]]
    assert qualifies(rows,[2,5],20)
    for field,value in [('equity_change',-1),('realized_pnl',0),('closed_trades',1)]:
        bad=copy.deepcopy(rows);bad[0][field]=value
        assert not qualifies(bad,[2,5],20)
    assert not qualifies([],[2,5],20)


@pytest.mark.parametrize('strategy',CANDIDATES)
def test_engine_prefix_is_causal_and_does_not_invoke_rf(strategy,monkeypatch):
    from test_forex_trend import candles
    def forbidden(*a,**k):raise AssertionError('Technical candidate must not call RF')
    monkeypatch.setattr('backend.strategies.ml_engine.predict_next_candle',forbidden)
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id=strategy).model_dump()
    def analyze(f):return StrategyEngine.analyze(f,'EURUSD#','MT5_FOREX','M1',strategy,cfg['strategy_params']).to_dict()
    f=candles();before=analyze(f)
    future=pd.concat([f,f.tail(1)],ignore_index=True)
    future.loc[180,'time']=f.iloc[-1]['time']+pd.Timedelta(minutes=1)
    future.loc[180,['close','high']]=[2.,2.]
    assert analyze(future.iloc[:180])==before
    assert before['indicators']['entry_policy_version'].endswith('-v1')
    assert not before['indicators']['ml_validation']['enabled']


@pytest.mark.parametrize('strategy',CANDIDATES)
def test_bot_configuration_rejects_research_candidate(strategy,live_bot):
    bot,sdk=live_bot
    with pytest.raises(ValueError,match='laboratório'):
        bot.update_config({'strategy_id':strategy})
