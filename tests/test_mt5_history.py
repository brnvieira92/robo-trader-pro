from types import SimpleNamespace as NS
import numpy as np
import pytest
import backend.mt5_connector as native
from test_mt5_live import live_bot

@pytest.fixture
def connector(monkeypatch):
    sdk=NS(TIMEFRAME_M1=1,TIMEFRAME_M5=5,TIMEFRAME_M15=15,TIMEFRAME_M30=30,TIMEFRAME_H1=60,
           symbol_select=lambda *a:True,copy_rates_from_pos=lambda *a:None,last_error=lambda:(-4,'No history'))
    monkeypatch.setattr(native,'MT5_AVAILABLE',True);monkeypatch.setattr(native,'mt5',sdk)
    c=native.MT5Connector();c.connected=True;c.simulation_mode=False
    return c,sdk

@pytest.mark.parametrize('rates',[None,[]])
def test_missing_history_reports_api_error_without_synthetic_fallback(connector,rates):
    c,sdk=connector;sdk.copy_rates_from_pos=lambda *a:rates
    with pytest.raises(RuntimeError,match=r'EURUSD / M5.*-4.*No history'):
        c.get_candles('EURUSD','M5',240)
    assert not c._sim_series_cache


def test_unknown_symbol_is_not_reported_as_generic_history_failure(connector):
    c,sdk=connector;sdk.symbol_select=lambda *a:False
    sdk.copy_rates_from_pos=lambda *a:pytest.fail('must not read unselected symbol')
    with pytest.raises(RuntimeError,match='nome exato'):c.get_candles('EURUSD.s','M5')


def test_real_history_returns_only_broker_prices(connector):
    c,sdk=connector
    sdk.copy_rates_from_pos=lambda *a:np.array([(1700000000,1.1,1.2,1.,1.15,50)],dtype=[('time','i8'),('open','f8'),('high','f8'),('low','f8'),('close','f8'),('tick_volume','i8')])
    df=c.get_candles('EURUSD','M5')
    assert len(df)==1 and df['close'].iloc[0]==1.15 and df['volume'].iloc[0]==50
    assert not c._sim_series_cache


def test_native_snapshot_preserves_history_diagnosis_and_blocks_entry(live_bot,monkeypatch):
    b,sdk=live_bot
    def missing(*a,**k):raise RuntimeError('MT5 sem histórico EURUSD / M5: (-4, No history)')
    monkeypatch.setattr(b.mt5,'get_candles',missing)
    state=b.get_market_snapshot()
    assert state['signal']['action']=='NEUTRAL'
    assert 'No history' in state['signal']['reason']
    assert 'No history' in state['data_quality']['history_error']
    assert not state['data_quality']['history_fresh']
    assert state['risk']['blocked_reason']
    assert sdk.sent==[]
