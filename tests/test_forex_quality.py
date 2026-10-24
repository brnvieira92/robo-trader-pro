import asyncio
import numpy as np
import pandas as pd
import pytest
from backend.forex_quality import entry_confirmation
from backend.ml_engine import MLEngine
from backend.strategies import StrategyEngine
from backend.mt5_execution import MT5ExecutionError
from backend.diagnostics import preflight_code,RecoveryState
from test_mt5_live import live_bot,signal


def frame(up=True,n=150):
    close=1.1+(1 if up else -1)*np.arange(n)*.00001
    opens=np.r_[close[0],close[:-1]]
    return pd.DataFrame(dict(time=pd.date_range('2026-01-01',periods=n,freq='min'),
        open=opens,high=np.maximum(opens,close)+.000004,low=np.minimum(opens,close)-.000004,
        close=close,volume=100))


@pytest.mark.parametrize('up,side',[(True,'BUY'),(False,'SELL')])
def test_aligned_breakout_can_pass_without_generating_or_inflating_signal(up,side):
    result=entry_confirmation(frame(up),'M1',side,.0001)
    assert result['passed'] and result['trend']==('UP' if up else 'DOWN')
    assert result['context_timeframe']=='M5' and result['context_bars']==30
    assert result['patterns'][side]['breakout']
    assert not any(key in result for key in ('action','confidence','stop_loss','take_profit'))


def test_countertrend_sell_is_blocked_even_when_last_candle_breaks_down():
    df=frame();pc=df.iloc[-2]['close']
    df.loc[len(df)-1,['open','high','low','close']]=[pc+.000002,pc+.000006,pc-.000024,pc-.00002]
    result=entry_confirmation(df,'M1','SELL',.0001)
    assert result['patterns']['SELL']['breakout']
    assert result['trend']=='UP' and not result['passed']
    assert result['checks'][0]['code']=='FOREX_CONTEXT' and not result['checks'][0]['passed']


def test_incomplete_m5_bar_cannot_change_context_and_missing_context_blocks():
    df=frame();baseline=entry_confirmation(df,'M1','BUY',.0001)
    appended=pd.concat([df,df.tail(1)],ignore_index=True)
    appended.loc[150,'time']=df.iloc[-1]['time']+pd.Timedelta(minutes=1)
    appended.loc[150,'close']=.5
    current=entry_confirmation(appended,'M1','BUY',.0001)
    assert current['ema_slow']==baseline['ema_slow'] and current['context_last_bar']==baseline['context_last_bar']
    assert not entry_confirmation(df.drop(index=148),'M1','BUY',.0001)['passed']
    assert not entry_confirmation(df.iloc[:100],'M1','BUY',.0001)['passed']


def test_inside_doji_has_no_price_action_confirmation():
    df=frame();pc=df.iloc[-2]['close']
    df.loc[len(df)-1,['open','high','low','close']]=[pc,pc+.000003,pc-.000003,pc]
    result=entry_confirmation(df,'M1','BUY',.0001)
    assert result['trend']=='UP' and not result['passed']
    assert not result['checks'][1]['passed']


def test_walk_forward_is_purged_and_uses_disjoint_future_test_blocks():
    X=np.arange(200).reshape(-1,1);y=np.arange(200)%2
    fits=[];tests=[]
    class Forest:
        classes_=np.array([0,1])
        def fit(self,X,y):fits.append(X.copy());return self
        def predict(self,X):tests.append(X.copy());return X[:,0]%2
        def predict_proba(self,X):
            up=np.where(X[:,0]%2,.9,.1);return np.column_stack((1-up,up))
    result=MLEngine.walk_forward(X,y,Forest)
    assert result['validated'] and result['passed_folds']==3
    assert result['validation_samples']==120 and result['brier_score']==pytest.approx(.01)
    seen=[]
    for fitted,test in zip(fits,tests):
        assert fitted[-1,0]+2==test[0,0]
        seen.extend(test[:,0])
    assert len(seen)==len(set(seen))


def test_one_successful_window_cannot_approve_inconsistent_model():
    X=np.arange(200).reshape(-1,1);y=np.arange(200)%2
    class Forest:
        classes_=np.array([0,1])
        def fit(self,X,y):return self
        def predict(self,X):return X[:,0]%2 if X[0,0]<100 else 1-X[:,0]%2
        def predict_proba(self,X):
            up=np.where(self.predict(X),.9,.1);return np.column_stack((1-up,up))
    result=MLEngine.walk_forward(X,y,Forest)
    assert result['passed_folds']==1 and not result['validated']


def test_short_or_single_class_windows_never_pass():
    for n in (60,200):
        result=MLEngine.walk_forward(np.arange(n).reshape(-1,1),np.ones(n),lambda:None)
        assert not result['validated'] and not result['complete']


def test_rf_forex_applies_new_validation_and_has_versioned_cache(live_bot):
    b,_=live_bot;df=b.mt5.get_candles('EURUSD','M1')
    df['open']=df['close'].shift(1).fillna(df['close'].iloc[0])
    enriched=StrategyEngine.enrich_dataframe(df,{})
    engine=MLEngine()
    old=engine.predict_next_candle(enriched,'same')
    new=engine.predict_next_candle(enriched,'same',validation_mode='walk_forward_v2')
    assert old['model_version']!=new['model_version'] and len(engine._cache)==2
    assert len(new['walk_forward']['folds'])==3
    if not new['walk_forward']['validated']:assert not new['validated'] and new['ml_action']=='NEUTRAL'
    result=StrategyEngine.analyze(df,'EURUSD','MT5_FOREX','M1','ml_random_forest',{}).to_dict()
    assert result['indicators']['entry_policy_version']=='forex-quality-v3'
    assert result['indicators']['ml_validation']['validation_method']=='walk_forward_v2'


def test_relative_spread_gate_rejects_costly_stop_without_sending(live_bot):
    b,sdk=live_bot;b.config['mt5_max_spread_stop_ratio']=.1
    with pytest.raises(MT5ExecutionError,match='Spread/stop') as error:
        b.live.prepare(b.config,signal(),250,'test')
    assert preflight_code(str(error.value))=='SPREAD_STOP_RATIO'
    assert 'SPREAD_STOP_RATIO' in RecoveryState.OPPORTUNITY_REJECTIONS
    assert not sdk.sent and not b.journal.all_trades()


def test_spread_ratio_below_limit_still_preserves_financial_budget(live_bot):
    b,sdk=live_bot
    result=asyncio.run(b.execute_trade_from_signal(signal(),manual=True))
    assert result['success']
    trade=b.trade_history[0]
    assert trade['plan']['spread_to_stop']<=.30 and trade['amount']<=trade['risk_budget']


def test_approved_rf_can_still_enter_after_confirmation_and_score_is_not_inflated():
    class Model:
        def predict_next_candle(self,df,context,validation_mode):
            assert validation_mode=='walk_forward_v2'
            return dict(enabled=True,validated=True,prob_up=80.,prob_down=20.,ml_action='BUY',
                ml_confidence=80.,accuracy=65.,samples=200,current_features=[],reason='test',
                validation_method=validation_mode,walk_forward=dict(validated=True,passed_folds=3))
    df=frame(n=240)
    result=StrategyEngine.analyze(df,'EURUSD','MT5_FOREX','M1','ml_random_forest',{},model_engine=Model()).to_dict()
    assert result['action']=='BUY' and result['confidence']==80
    pc=df.iloc[-2]['close']
    df.loc[len(df)-1,['open','high','low','close']]=[pc,pc+.000003,pc-.000003,pc]
    rejected=StrategyEngine.analyze(df,'EURUSD','MT5_FOREX','M1','ml_random_forest',{},model_engine=Model()).to_dict()
    assert rejected['action']=='NEUTRAL' and rejected['confidence']==0
    assert 'price action' in rejected['reason']
