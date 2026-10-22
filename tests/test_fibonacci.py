import asyncio
import copy
import json
import pandas as pd
import pytest
from backend.fibonacci import fibonacci_context
from backend.strategies import StrategyEngine
from test_mt5_live import live_bot, signal


def bars(down=False):
    high=[10,11,10,11,10,11,14,12,11,10.5]
    low=[9,8,7,8,9,10,11,10,9,9.5]
    close=[(h+l)/2 for h,l in zip(high,low)]
    close[-1]=10.5
    if down:
        high,low,close=[30-x for x in low],[30-x for x in high],[30-x for x in close]
    return pd.DataFrame(dict(time=pd.date_range('2026-01-01',periods=10,freq='min'),
                             open=close,high=high,low=low,close=close,volume=100))


@pytest.mark.parametrize('down',[False,True])
def test_confirmed_swing_levels_have_explicit_direction_and_no_trade_action(down):
    df=bars(down);original=df.copy(deep=True)
    f=fibonacci_context(df,1.)
    assert f['available'] and f['direction']==('DOWN' if down else 'UP')
    assert f['informational'] and f['within_swing'] and f['in_382_618_zone']
    assert f['retracement_fraction']==pytest.approx(.5)
    levels={l['ratio']:l['price'] for l in f['levels']}
    assert levels[.5]==pytest.approx(19.5 if down else 10.5)
    assert f['start']['time']==str(df.iloc[2]['time'])
    assert f['end']['time']==str(df.iloc[6]['time'])
    assert f['end']['confirmed_at']==str(df.iloc[8]['time'])
    assert f['extensions'][0]['price']==pytest.approx(30-15.904 if down else 15.904)
    assert not any(k in f for k in ('action','confidence','stop_loss','take_profit','validated'))
    pd.testing.assert_frame_equal(original,df)
    json.dumps(f,allow_nan=False)


def test_unconfirmed_pivot_is_not_used_and_future_prices_do_not_mutate_snapshot():
    df=bars();f=fibonacci_context(df.iloc[:8],1.)
    assert not f['available']  # High at 6 needs the CLOSED bar at 8.
    confirmed=fibonacci_context(df.iloc[:9],1.)
    saved=copy.deepcopy(confirmed)
    future=df.copy();future.loc[9,['high','close']]=[99,99]
    assert fibonacci_context(future.iloc[:9],1.)==saved
    assert confirmed==saved


def test_flat_and_tied_extremes_are_not_fabricated_pivots():
    df=bars();df[['high','low','close']]=10
    assert not fibonacci_context(df,1.)['available']
    assert not fibonacci_context(df.iloc[:3],1.)['available']


def test_outside_range_does_not_claim_active_retracement():
    df=bars();df.loc[9,['high','close']]=[15,15]
    f=fibonacci_context(df,1.)
    assert f['available'] and not f['within_swing'] and not f['in_382_618_zone']


def test_fibonacci_does_not_change_rf_signal_score_or_validation(live_bot,monkeypatch):
    b,_=live_bot
    frame=b.mt5.get_candles('EURUSD','M1')
    args=(frame,'EURUSD','MT5_FOREX','M1','ml_random_forest',b.config['strategy_params'])
    baseline=StrategyEngine.analyze(*args).to_dict()
    monkeypatch.setattr('backend.strategies.fibonacci_context',lambda *a:dict(available=False,reason='No pivots'))
    changed=StrategyEngine.analyze(*args).to_dict()
    baseline['indicators'].pop('fibonacci');changed['indicators'].pop('fibonacci')
    assert baseline==changed


def test_fibonacci_is_retained_in_trade_snapshot_without_affecting_order(live_bot):
    b,sdk=live_bot;s=signal()
    s['indicators']['fibonacci']=fibonacci_context(bars(),1.)
    assert asyncio.run(b.execute_trade_from_signal(s,manual=True))['success']
    saved=b.journal.all_trades()[0]
    assert saved['signal_snapshot']['indicators']['fibonacci']==s['indicators']['fibonacci']
    assert saved['confidence']==85 and len(sdk.sent)==1
