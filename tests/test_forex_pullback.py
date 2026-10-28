import asyncio
import pandas as pd
import copy
import pytest
from backend.config import TradingConfig,strategy_requires_ml
from backend.strategies import StrategyEngine
from test_forex_trend import candles
from test_mt5_live import live_bot

STRATEGY='forex_pullback_retest'


def pullback_candles(side='BUY'):
    df=candles()
    df.loc[178,['open','high','low','close']]=[1.10532,1.10536,1.10514,1.10520]
    df.loc[179,['open','high','low','close']]=[1.10520,1.10540,1.10518,1.10538]
    if side=='SELL':
        original=df.copy()
        df['high']=2.2-original['low'];df['low']=2.2-original['high']
        df['open']=2.2-original['open'];df['close']=2.2-original['close']
    return df


def analyze(df):
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY)
    return StrategyEngine.analyze(df,'EURUSD#','MT5_FOREX','M1',STRATEGY,cfg.strategy_params.model_dump()).to_dict()


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_pullback_is_symmetric_and_does_not_call_rf(side,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Technical strategy cannot call RF')
    monkeypatch.setattr('backend.strategies.ml_engine.predict_next_candle',forbidden)
    s=analyze(pullback_candles(side))
    assert s['action']==side and s['confidence']>=78
    assert s['indicators']['entry_policy_version']=='forex-pullback-v1'
    assert s['indicators']['entry_confirmation']['trigger']=='PULLBACK_RETEST'
    assert not strategy_requires_ml(STRATEGY,{'use_ml_filter':True})


def test_plain_breakout_and_old_correction_do_not_authorize_new_strategy():
    s=analyze(candles())
    assert s['action']=='NEUTRAL'
    check=next(c for c in s['indicators']['entry_confirmation']['checks'] if c['code']=='PULLBACK_RETEST')
    assert not check['passed'] and not check['observed']['correction']
    df=pullback_candles();df.loc[178,'open']=df.loc[178,'close']
    assert analyze(df)['action']=='NEUTRAL'
    # An older correction can qualify the legacy continuation, but the new
    # strategy requires a correction on the immediately previous candle.
    df=candles(trigger='PULLBACK');df.loc[178,'open']=df.loc[178,'close']
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id='forex_trend_continuation')
    legacy=StrategyEngine.analyze(df,'EURUSD#','MT5_FOREX','M1','forex_trend_continuation',cfg.strategy_params.model_dump())
    assert legacy.action=='BUY' and analyze(df)['action']=='NEUTRAL'


def test_no_future_candle_changes_a_closed_pullback():
    df=pullback_candles();before=analyze(df)
    future=df.copy();future.loc[180]=future.iloc[-1]
    future.loc[180,'time']=future.loc[179,'time']+pd.Timedelta(minutes=1)
    future.loc[180,['high','close']]=[2.,2.]
    assert analyze(future.iloc[:180])==before


@pytest.mark.parametrize('change',[{'timeframe':'M5'},{'min_confidence':77},{'market_type':'MT5_B3'}])
def test_pullback_scope_and_confidence_are_preserved(change):
    with pytest.raises(ValueError):TradingConfig.model_validate(dict(strategy_id=STRATEGY,market_type='MT5_FOREX')|change)


def test_experimental_entry_is_not_selectable_for_live_execution(live_bot):
    b,sdk=live_bot
    before=copy.deepcopy(b.config)
    with pytest.raises(ValueError,match='somente no laboratório'):
        b.update_config({'strategy_id':STRATEGY})
    assert b.config==before and not sdk.sent


def test_stale_or_tampered_config_cannot_execute_experimental_orders(live_bot):
    b,sdk=live_bot
    b.config['strategy_id']=STRATEGY
    s=analyze(pullback_candles('SELL'))
    for manual in (False,True):
        assert 'laboratório' in b._risk_block(s,manual)
        result=asyncio.run(b.execute_trade_from_signal(s,manual=manual))
        assert not result['success'] and 'laboratório' in result['message']
    with pytest.raises(ValueError,match='somente no laboratório'):
        b.live.prepare(b.config,s,250,'experimental-blocked')
    assert not sdk.sent
