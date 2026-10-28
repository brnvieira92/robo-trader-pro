import copy
import asyncio
import numpy as np
import pandas as pd
import pytest
from backend.config import TradingConfig,strategy_requires_ml
from backend.forex_triple_atr import atr_bands
from backend.strategies import StrategyEngine
from backend.trade_plan import price_plan
from test_mt5_live import live_bot
from test_forex_portfolio import portfolio,next_cycle


def reversal(side='BUY',count=155):
    close=np.r_[1.1-np.arange(150)*.00003,1.1-149*.00003+np.arange(1,26)*.0001][:count]
    opens=np.r_[close[0],close[:-1]]
    f=pd.DataFrame(dict(time=pd.date_range('2026-01-01',periods=len(close),freq='min'),open=opens,close=close,
        high=np.maximum(opens,close)+.00005,low=np.minimum(opens,close)-.00005,volume=100))
    if side=='SELL':
        old=f.copy();f['high']=2.2-old['low'];f['low']=2.2-old['high'];f['close']=2.2-old['close'];f['open']=2.2-old['open']
    return f


def analyze(f):
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id='forex_triple_atr')
    return StrategyEngine.analyze(f,'EURUSD#','MT5_FOREX','M1',cfg.strategy_id,cfg.strategy_params.model_dump()).to_dict()


def test_true_range_wilder_seed_and_band_direction_on_hand_calculated_example():
    f=pd.DataFrame(dict(open=[10]*11,close=[10]*10+[3],high=[11]*10+[10],low=[9]*10+[2]))
    directions,lines,atr=atr_bands(f)
    assert np.isnan(atr[:9]).all() and atr[9]==2
    assert atr[10]==pytest.approx(2.6) # 0.9 * 2 + 0.1 * 8.
    assert lines[:,9].tolist()==[7,6,4]
    assert directions[:,9].tolist()==[1,1,1] and directions[:,10].tolist()==[-1,-1,-1]


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_fresh_alignment_is_symmetric_independent_of_rf_and_risk_planned(side,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Triple ATR cannot call RF')
    monkeypatch.setattr('backend.strategies.ml_engine.predict_next_candle',forbidden)
    signal=analyze(reversal(side));context=signal['indicators']['entry_confirmation']
    assert signal['action']==side and signal['confidence']>=78
    assert context['trigger']=='TRIPLE_ATR_ALIGNMENT' and context['passed']
    assert signal['indicators']['entry_policy_version']=='forex-triple-atr-v1'
    assert signal['indicators']['regime_basis']=='TRIPLE_ATR_M1_CONSENSUS'
    assert context['period']==10 and context['multipliers']==[1.5,2,3]
    assert not strategy_requires_ml('forex_triple_atr',{'use_ml_filter':True})
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id='forex_triple_atr').model_dump()
    plan=price_plan(cfg,signal,signal['price'],tick_size=.00001,digits=5)
    assert plan['reward_risk']>=1.5 and plan['stop_atr']<=4


def test_persistent_alignment_does_not_repeat_an_entry():
    f=reversal(count=156);s=analyze(f)
    assert s['action']=='NEUTRAL'
    check=next(c for c in s['indicators']['entry_confirmation']['checks'] if c['code']=='ATR_FRESH_ALIGNMENT')
    assert not check['passed'] and check['observed']['previous']==check['observed']['current']==1


def test_future_extremes_do_not_rewrite_previous_bands_or_signal():
    f=reversal(count=160);before=copy.deepcopy(analyze(f.iloc[:155]))
    directions,lines,atr=atr_bands(f)
    f.loc[159,['high','close']]=[2.,2.]
    after_directions,after_lines,after_atr=atr_bands(f)
    np.testing.assert_array_equal(directions[:,:159],after_directions[:,:159])
    np.testing.assert_allclose(lines[:,:159],after_lines[:,:159],equal_nan=True)
    np.testing.assert_allclose(atr[:159],after_atr[:159],equal_nan=True)
    assert analyze(f.iloc[:155])==before


def test_same_direction_lines_never_move_away_from_the_price():
    directions,lines,_=atr_bands(reversal(count=170))
    for ds,ls in zip(directions,lines):
        for i in range(10,len(ds)):
            if ds[i]==ds[i-1]:assert (ls[i]-ls[i-1])*ds[i]>=-1e-12


@pytest.mark.parametrize('change',[{'timeframe':'M5'},{'min_confidence':77},{'market_type':'MT5_B3'}])
def test_scope_does_not_silently_relax_confidence_or_timeframe(change):
    with pytest.raises(ValueError):TradingConfig.model_validate(dict(strategy_id='forex_triple_atr',market_type='MT5_FOREX')|change)


def test_demo_can_execute_automatically_once_and_persist_actual_policy(live_bot,monkeypatch):
    b,sdk=live_bot;b.update_config({'strategy_id':'forex_triple_atr'})
    signal=analyze(reversal('SELL'));signal['bar_time']='closed fresh alignment'
    sdk.quote.bid=signal['price'];sdk.quote.ask=signal['price']+.00002
    monkeypatch.setattr(b,'get_market_snapshot',lambda:dict(config=b.config,
        data_quality=dict(source='MT5_LIVE',history_fresh=True)))
    b.is_running=True
    result=asyncio.run(b.execute_trade_from_signal(signal,manual=False))
    assert result['success'] and len(sdk.sent)==1
    trade=b.journal.all_trades()[0]
    assert trade['strategy']=='forex_triple_atr' and trade['entry_policy_version']=='forex-triple-atr-v1'
    assert trade['account_type']=='DEMO/CONTEST' and trade['amount']<=trade['risk_budget']
    assert trade['stop_loss']>trade['entry_price']>trade['take_profit']
    result=asyncio.run(b.execute_trade_from_signal(signal,manual=False))
    assert not result['success'] and len(sdk.sent)==1 # No duplicate while pending.


@pytest.mark.parametrize('mode',[2,1,None,99])
def test_real_contest_or_unknown_account_cannot_select_or_prepare_experimental_strategy(live_bot,mode):
    b,sdk=live_bot;sdk.info.trade_mode=mode;before=copy.deepcopy(b.config)
    with pytest.raises(ValueError,match='DEMO'):
        b.update_config({'strategy_id':'forex_triple_atr'})
    assert b.config==before
    cfg={**b.config,'strategy_id':'forex_triple_atr'}
    with pytest.raises(ValueError,match='DEMO'):b.live.prepare(cfg,analyze(reversal()),250,'blocked')
    assert not sdk.sent


def test_account_switch_after_selection_blocks_new_orders_but_keeps_process_running(live_bot):
    b,sdk=live_bot;b.update_config({'strategy_id':'forex_triple_atr'});b.is_running=True
    sdk.info.trade_mode=2
    assert 'DEMO' in b._risk_block(analyze(reversal()))
    assert b.is_running and not sdk.sent


def test_demo_is_rechecked_after_reservation_before_actual_send(live_bot,monkeypatch):
    b,sdk=live_bot;b.update_config({'strategy_id':'forex_triple_atr'})
    signal=analyze(reversal('SELL'));signal['bar_time']='closed alignment'
    sdk.quote.bid=signal['price'];sdk.quote.ask=signal['price']+.00002
    monkeypatch.setattr(b,'get_market_snapshot',lambda:dict(config=b.config,
        data_quality=dict(source='MT5_LIVE',history_fresh=True)))
    original=b.live.send
    def switch(trade):
        sdk.info.trade_mode=2
        return original(trade)
    monkeypatch.setattr(b.live,'send',switch);b.is_running=True
    result=asyncio.run(b.execute_trade_from_signal(signal,manual=False))
    assert not result['success'] and not result['pending'] and 'DEMO' in result['message']
    assert not sdk.sent and b.trade_history[0]['status']=='REJECTED' and b.is_running


def test_demo_triple_atr_keeps_two_position_global_reservations(portfolio):
    b,sdk,multi=portfolio
    # Reuse deterministic candidate prices to isolate execution and global risk
    # from the separately tested indicator. No account or risk limit is changed.
    b.config['strategy_id']='forex_triple_atr'
    for view in multi.views.values():view.config['strategy_id']='forex_triple_atr'
    next_cycle(b,multi);next_cycle(b,multi);next_cycle(b,multi)
    assert len(sdk.sent)==2 and len(multi.rows)==4
    assert sum(t['reserved_risk'] for t in b.trade_history)<=100
    assert all(t['amount']<=t['risk_budget'] for t in b.trade_history)
