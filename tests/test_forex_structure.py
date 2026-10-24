import asyncio
import copy
import numpy as np
import pandas as pd
import pytest
from backend.config import TradingConfig,strategy_requires_ml,RESEARCH_ONLY_STRATEGIES,DEMO_ONLY_STRATEGIES
from backend.forex_structure import pivot_retest_event,structure_retest_signal,POLICY_VERSION
from backend.strategies import StrategyEngine
from backend.trade_plan import price_plan,TradePlanError
from test_mt5_live import live_bot
from test_forex_portfolio import portfolio,next_cycle

STRATEGY='forex_structure_retest'


def candles(side='BUY',count=191):
    close=1.1+np.arange(180)*.00001;opens=np.r_[close[0],close[:-1]]
    f=pd.DataFrame(dict(time=pd.date_range('2026-01-01',periods=180,freq='min'),
        open=opens,close=close,high=np.maximum(opens,close)+.00003,low=np.minimum(opens,close)-.00003,volume=100))
    values=[
        (1.10179,1.10185,1.1014,1.1016),
        (1.1016,1.10182,1.10152,1.10175),
        (1.10175,1.10198,1.10168,1.1019),
        (1.1019,1.1022,1.10184,1.1021),
        (1.1021,1.1021,1.10182,1.1019),
        (1.1019,1.10195,1.1017,1.1018),
        (1.1018,1.102,1.10178,1.10195),
        (1.10195,1.1021,1.10185,1.102),
        (1.102,1.10235,1.10198,1.10232),
        (1.10232,1.10233,1.10218,1.10225),
        (1.10225,1.1025,1.10221,1.10248),
        (1.10248,1.10261,1.10245,1.10259)]
    for o,h,l,c in values:
        f.loc[len(f)]=[f.iloc[-1]['time']+pd.Timedelta(minutes=1),o,c,h,l,100]
    if side=='SELL':
        old=f.copy()
        f['open']=2.2-old['open'];f['close']=2.2-old['close']
        f['high']=2.2-old['low'];f['low']=2.2-old['high']
    return f.iloc[:count].copy()


def analyze(f):
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY)
    return StrategyEngine.analyze(f,'EURUSD#','MT5_FOREX','M1',STRATEGY,cfg.strategy_params.model_dump()).to_dict()


def event(f):
    params=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY).strategy_params.model_dump()
    return pivot_retest_event(StrategyEngine.enrich_dataframe(f,params))


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_closed_pivot_breakout_retest_and_later_confirmation_are_symmetric(side,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('Structural strategy cannot call RF')
    monkeypatch.setattr('backend.strategies.ml_engine.predict_next_candle',forbidden)
    s=analyze(candles(side));c=s['indicators']['entry_confirmation']
    assert s['action']==side and s['confidence']>=78
    assert c['trigger']=='PIVOT_RETEST' and c['phase']=='CONFIRMED' and c['passed']
    assert c['setup']['confirmed_at']<c['setup']['breakout_at']<c['setup']['retest_at']<c['setup']['confirmation_at']
    assert s['indicators']['entry_policy_version']==POLICY_VERSION
    assert not strategy_requires_ml(STRATEGY,{'use_ml_filter':True})
    assert not s['indicators']['ml_validation']['enabled']
    cfg=TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY).model_dump()
    p=price_plan(cfg,s,s['price'],spread=.00002,tick_size=.00001,digits=5)
    assert p['reward_risk']>=1.5 and p['stop_atr']<=4
    assert p['structural_stop_bid']==c['setup']['invalidation']


@pytest.mark.parametrize('count,phase',[(188,'WAIT_BREAKOUT'),(189,'WAIT_RETEST'),(190,'WAIT_CONFIRMATION')])
def test_each_stage_waits_for_a_distinct_closed_bar(count,phase):
    s=analyze(candles(count=count));assert s['action']=='NEUTRAL'
    assert s['indicators']['entry_confirmation']['phase']==phase


def test_consumed_setup_does_not_generate_a_second_signal():
    assert analyze(candles(count=191))['action']=='BUY'
    s=analyze(candles(count=192));assert s['action']=='NEUTRAL'
    assert s['indicators']['entry_confirmation']['phase']=='CONSUMED'


def test_point3_invalidation_prevents_entry_and_future_extremes_do_not_repaint():
    f=candles(count=192);past=copy.deepcopy(analyze(f.iloc[:191]))
    f.loc[191,['high','close']]=[2,2]
    assert analyze(f.iloc[:191])==past
    f=candles();f.loc[189,'low']=1.10169
    setup,fresh=event(f)
    assert not fresh and setup['phase']=='INVALIDATED'
    assert analyze(f)['action']=='NEUTRAL'


def test_a_pivot_cannot_be_known_before_two_following_bars():
    early,_=event(candles(count=187));ready,_=event(candles(count=188))
    assert ready['points'][2]['index']==185 and ready['confirmed_at'].endswith('03:07:00')
    assert early is None or early['points'][2]['index']!=185


@pytest.mark.parametrize('strategy_flag',['use_structure_stops','use_structure_targets'])
def test_configuration_cannot_turn_off_structural_invalidation_or_targets(strategy_flag):
    with pytest.raises(ValueError,match='estrutura'):
        TradingConfig(market_type='MT5_FOREX',strategy_id=STRATEGY,strategy_params={strategy_flag:False})


@pytest.mark.parametrize('change',[{'timeframe':'M5'},{'min_confidence':77},{'market_type':'MT5_B3'}])
def test_technical_scope_and_minimum_score_remain_enforced(change):
    with pytest.raises(ValueError):TradingConfig.model_validate(dict(market_type='MT5_FOREX',strategy_id=STRATEGY)|change)


@pytest.mark.parametrize('mode',[0,2])
def test_linked_demo_or_real_can_execute_automatically_and_persist_policy(live_bot,monkeypatch,mode):
    b,sdk=live_bot;sdk.info.trade_mode=mode
    assert STRATEGY not in RESEARCH_ONLY_STRATEGIES and STRATEGY not in DEMO_ONLY_STRATEGIES
    b.update_config({'strategy_id':STRATEGY})
    s=analyze(candles('SELL'));s['bar_time']='closed structure confirmation'
    sdk.quote.bid=s['price'];sdk.quote.ask=s['price']+.00002
    monkeypatch.setattr(b,'get_market_snapshot',lambda:dict(config=b.config,data_quality=dict(source='MT5_LIVE',history_fresh=True)))
    b.is_running=True
    result=asyncio.run(b.execute_trade_from_signal(s,manual=False))
    assert result['success'] and len(sdk.sent)==1
    trade=b.journal.all_trades()[0]
    assert trade['strategy']==STRATEGY and trade['entry_policy_version']==POLICY_VERSION
    assert trade['signal_snapshot']['indicators']['entry_confirmation']['setup']['id']==s['indicators']['entry_confirmation']['setup']['id']
    assert trade['stop_loss']>trade['entry_price']>trade['take_profit']
    assert trade['amount']<=trade['risk_budget']
    assert not asyncio.run(b.execute_trade_from_signal(s,manual=False))['success'] and len(sdk.sent)==1


def test_structure_strategy_keeps_two_slots_and_gross_global_budget(portfolio):
    b,sdk,multi=portfolio
    b.config['strategy_id']=STRATEGY
    for view in multi.views.values():view.config['strategy_id']=STRATEGY
    next_cycle(b,multi);next_cycle(b,multi);next_cycle(b,multi)
    assert len(sdk.sent)==2 and len(multi.rows)==4
    assert sum(t['reserved_risk'] for t in b.trade_history)<=100


def extend(f,n,kind):
    for _ in range(n):
        previous=float(f.iloc[-1]['close']);timestamp=f.iloc[-1]['time']+pd.Timedelta(minutes=1)
        if kind=='no_retest':o=previous;c=previous+.00001;h=c+.00003;l=previous-.000001
        else:o=1.10225;c=1.10224;h=1.10233;l=1.10220
        f.loc[len(f)]=[timestamp,o,c,h,l,100]
    return f


def test_setup_expires_without_retest_or_without_timely_resumption():
    for f in [extend(candles(count=189),14,'no_retest'),extend(candles(count=190),6,'no_confirmation')]:
        setup,fresh=event(f)
        assert not fresh and setup['phase']=='EXPIRED'
        assert analyze(f)['action']=='NEUTRAL'


def test_contrary_m5_blocks_confirmation_and_does_not_create_a_late_entry():
    f=candles(count=192)
    for i in range(180):
        c=1.11-(1.11-1.10179)*i/179;o=c+.00004
        f.loc[i,['open','close','high','low']]=[o,c,o+.00003,c-.00003]
    s=analyze(f.iloc[:191]);c=s['indicators']['entry_confirmation']
    assert c['phase']=='CONFIRMED' and not c['passed'] and s['action']=='NEUTRAL'
    assert c['trend']=='DOWN'
    assert not next(g for g in c['checks'] if g['code']=='STRUCTURE_CONTEXT')['passed']
    assert analyze(f)['indicators']['entry_confirmation']['phase']=='CONSUMED'


@pytest.mark.parametrize('failure',['spread','permissions','margin','score','account','structural_stop'])
def test_live_entry_still_rejects_unsafe_conditions_and_keeps_monitoring(live_bot,monkeypatch,failure):
    b,sdk=live_bot;b.update_config({'strategy_id':STRATEGY})
    s=analyze(candles('SELL'));s['bar_time']='closed structure safety case'
    sdk.quote.bid=s['price'];sdk.quote.ask=s['price']+.00002
    monkeypatch.setattr(b,'get_market_snapshot',lambda:dict(config=b.config,data_quality=dict(source='MT5_LIVE',history_fresh=True)))
    if failure=='spread':sdk.quote.ask+=.001
    elif failure=='permissions':sdk.terminal.tradeapi_disabled=True
    elif failure=='margin':sdk.info.margin_free=.01
    elif failure=='score':s['confidence']=77
    elif failure=='account':sdk.info.login=456
    else:s['indicators']['stop_resistance']=s['price']+.01
    b.is_running=True
    result=asyncio.run(b.execute_trade_from_signal(s,manual=False))
    assert not result['success'] and not sdk.sent and b.is_running
