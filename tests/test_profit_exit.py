import asyncio
import copy
import time
from types import SimpleNamespace as NS
import numpy as np
import pandas as pd
import pytest
from backend.profit_exit import ProfitExitConfig,closed_observation,decision
from backend import mt5_profit_close
from backend.mt5_execution import MT5ExecutionError
from backend.bot_manager import BotManager
from test_mt5_live import live_bot,signal
from test_forex_portfolio import portfolio


def frame(adverse=True,side='BUY'):
    ts=pd.Timestamp.now('UTC').floor('min').tz_localize(None)
    f=pd.DataFrame(dict(time=pd.date_range(end=ts,periods=61,freq='min'),open=1.1014,close=1.1014,high=1.1015,low=1.1013,volume=100))
    f.loc[59,['open','close','high','low']]=[1.1014,1.10115,1.10145,1.1011] if adverse else [1.1014,1.1016,1.10165,1.10135]
    if side=='SELL':
        old=f.copy();f['open']=2.2-old['open'];f['close']=2.2-old['close'];f['high']=2.2-old['low'];f['low']=2.2-old['high']
    return f


def opened(live_bot,monkeypatch,adverse=True,side='BUY',reply='done'):
    b,sdk=live_bot;sdk.info.margin_mode=2
    b.update_config({'profit_exit':{'enabled':True}})
    s=signal();s['action']=side
    assert asyncio.run(b.execute_trade_from_signal(s,True))['success']
    t=b.trade_history[0];t['created_at']=time.time()-121;sdk.deals[0].time=int(time.time()-120)
    asyncio.run(b.reconcile_live())
    p=sdk.positions[0];p.volume=t['filled_volume'];p.price_open=t['entry_price'];p.type=0 if side=='BUY' else 1;p.magic=t['magic']
    sdk.quote.bid=1.1010 if side=='BUY' else 1.0990;sdk.quote.ask=sdk.quote.bid+.00002
    monkeypatch.setattr(b.mt5,'get_candles',lambda *a,**k:frame(adverse,side))
    def close(request):
        assert request['position']==p.ticket and request['type']!=p.type and request['volume']==p.volume
        sdk.sent.append(dict(request))
        if reply=='reject':return NS(retcode=10006,order=0,deal=0)
        if reply=='lost_no_fill':return None
        volume=p.volume/2 if reply=='partial' else p.volume
        price=request['price'];gross=sdk.order_calc_profit(p.type,t['symbol'],volume,p.price_open,price)
        sdk.deals.append(NS(ticket=701,order=501,position_id=t['position_id'],magic=t['magic'],comment=request['comment'],
            type=request['type'],entry=1,volume=volume,price=price,time=int(time.time()),profit=gross,commission=-.2,swap=0.,fee=0.))
        if reply=='partial':p.volume-=volume
        else:sdk.positions=[]
        return None if reply=='lost_filled' else NS(retcode=10010 if reply=='partial' else 10009,order=501,deal=701)
    monkeypatch.setattr(sdk,'order_send',close)
    return b,sdk,t


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('mode',[0,2])
def test_full_profit_close_is_durable_reduce_only_and_only_history_can_mark_win(live_bot,monkeypatch,side,mode):
    live_bot[1].info.trade_mode=mode
    b,sdk,t=opened(live_bot,monkeypatch,side=side)
    original=sdk.order_send
    def checked(request):
        persisted=next(x for x in b.journal.all_trades() if x['id']==t['id'])
        assert persisted['close_intent']['status']=='SUBMITTING'
        return original(request)
    monkeypatch.setattr(sdk,'order_send',checked)
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==2 and t['status']=='OPEN' and not b.stats['wins']
    assert t['close_intent']['status']=='PENDING'
    assert 'sl' not in sdk.sent[-1] and 'tp' not in sdk.sent[-1]
    asyncio.run(b.manage_profit_exits());assert len(sdk.sent)==2
    asyncio.run(b.reconcile_live())
    assert t['status']=='WIN' and t['close_intent']['status']=='CONFIRMED'
    assert t['exit_reason']=='EARLY_PROFIT_WEAKNESS' and t['pnl']>0


def test_profitable_favorable_movement_keeps_original_tp(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch,adverse=False)
    original=(t['stop_loss'],t['take_profit'])
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==1 and not t.get('close_intent')
    assert original==(t['stop_loss'],t['take_profit'])


def test_gross_profit_is_not_enough_when_net_costs_fail_minimum(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch)
    sdk.quote.bid=1.1005;sdk.quote.ask=1.10052
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==1 and not t.get('close_intent')
    assert t['profit_exit_state']['last_decision']['estimated_net_profit']<1


@pytest.mark.parametrize('reply',['lost_no_fill','lost_filled'])
def test_lost_close_response_is_never_repeated_including_after_restart(live_bot,monkeypatch,reply):
    b,sdk,t=opened(live_bot,monkeypatch,reply=reply)
    asyncio.run(b.manage_profit_exits());assert t['close_intent']['status']=='PENDING'
    restored=BotManager()
    restored.live=b.live;restored.mt5=b.mt5
    asyncio.run(restored.manage_profit_exits());assert len(sdk.sent)==2
    asyncio.run(restored.reconcile_live())
    if reply=='lost_filled':assert restored.trade_history[0]['status']=='WIN'
    else:
        assert restored.trade_history[0]['status']=='OPEN'
        assert 'Fechamento' in restored._runtime_block()


def test_partial_broker_fill_is_not_a_win_and_never_blindly_retried(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch,reply='partial')
    asyncio.run(b.manage_profit_exits());asyncio.run(b.reconcile_live())
    assert t['status']=='OPEN' and t['close_intent']['status']=='PARTIAL_REVIEW'
    asyncio.run(b.manage_profit_exits());assert len(sdk.sent)==2
    assert 'Fechamento' in b._runtime_block() and sdk.positions[0].sl>0


def test_explicit_rejection_can_not_spam_same_closed_bar(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch,reply='reject')
    asyncio.run(b.manage_profit_exits());assert t['close_intent']['status']=='REJECTED'
    asyncio.run(b.manage_profit_exits());assert len(sdk.sent)==2 and t['status']=='OPEN'


def test_price_recovery_before_send_cancels_early_exit(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch)
    original=mt5_profit_close.send
    def recover(*args):
        sdk.quote.bid=1.1016;sdk.quote.ask=1.10162
        return original(*args)
    monkeypatch.setattr(mt5_profit_close,'send',recover)
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==1 and t['close_intent']['status']=='REJECTED'


@pytest.mark.parametrize('failure',['stale','account','volume','permission','order_check','journal'])
def test_close_safety_failures_leave_native_protections_and_no_extra_order(live_bot,monkeypatch,failure):
    b,sdk,t=opened(live_bot,monkeypatch)
    if failure=='stale':sdk.quote.time_msc-=60000
    elif failure=='account':sdk.info.login=456
    elif failure=='volume':sdk.positions[0].volume*=.5
    elif failure=='permission':sdk.terminal.tradeapi_disabled=True
    elif failure=='order_check':sdk.checkcode=10016
    else:monkeypatch.setattr(b.journal,'save',lambda *a:(_ for _ in ()).throw(OSError('disk')))
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==1 and t['status']=='OPEN' and sdk.positions[0].sl>0 and sdk.positions[0].tp>0


def test_disabled_or_legacy_position_never_gets_new_exit_rules(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch)
    t.pop('profit_exit_policy')
    asyncio.run(b.manage_profit_exits());assert len(sdk.sent)==1


def test_closed_bar_requires_post_entry_fresh_contiguous_history():
    f=frame();now=time.time()
    assert closed_observation(f,now,now-120)['closed_at']<=now
    with pytest.raises(ValueError):closed_observation(f,now,now)
    with pytest.raises(ValueError):closed_observation(f.iloc[:-5],now,now-1200)
    with pytest.raises(ValueError):closed_observation(f.drop(index=20),now,now-1200)


def test_giveback_can_confirm_weakness_without_ema_break_but_not_without_adverse_candle():
    p=ProfitExitConfig(enabled=True).model_dump()
    o=dict(bar_time='closed',open=1.102,close=1.1019,high=1.1021,low=1.10188,previous_close=1.102,ema9=1.101,
        close_location=.1)
    assert decision('BUY',o,2,4,1.1018,p)['eligible']
    assert not decision('BUY',o,3,4,1.1018,p)['eligible']
    assert not decision('BUY',o,2,4,1.1022,p)['eligible']


@pytest.mark.parametrize('mode',[0,1,None])
def test_netting_or_unknown_account_cannot_send_close_as_opposite_entry(live_bot,monkeypatch,mode):
    b,sdk,t=opened(live_bot,monkeypatch);sdk.info.margin_mode=mode
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==1 and t['status']=='OPEN'


def test_intent_persistence_failure_is_recorded_as_not_sent_not_uncertain_fill(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch);original=b.journal.save;calls=[]
    def fail_intent(trade):
        calls.append(1)
        if len(calls)==2:raise OSError('disk')
        return original(trade)
    monkeypatch.setattr(b.journal,'save',fail_intent)
    asyncio.run(b.manage_profit_exits())
    assert len(sdk.sent)==1 and t['close_intent']['status']=='REJECTED'
    b._flush_trade_saves()
    assert b.journal.all_trades()[0]['close_intent']['status']=='REJECTED'


def test_actual_negative_broker_result_is_loss_even_if_quote_was_profitable(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch)
    asyncio.run(b.manage_profit_exits());sdk.deals[-1].profit=-2
    asyncio.run(b.reconcile_live())
    assert t['status']=='LOSS' and t['pnl']<0 and not b.stats['wins']


def test_manual_or_native_close_does_not_get_falsely_attributed_to_early_exit(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch,reply='lost_no_fill')
    asyncio.run(b.manage_profit_exits());sdk.close_position()
    asyncio.run(b.reconcile_live())
    assert t['status']=='WIN' and t['close_intent']['status']=='RESOLVED_BY_HISTORY'
    assert t.get('exit_reason')!='EARLY_PROFIT_WEAKNESS'


def test_forming_candle_and_future_prices_do_not_change_closed_observation():
    f=frame();now=time.time();before=closed_observation(f,now,now-120)
    f.loc[60,['high','close']]=[2,2]
    assert closed_observation(f,now,now-120)==before


def test_historical_exit_uses_previous_closed_candle_and_next_open(monkeypatch):
    from test_forex_research import request_data,fixed_signal
    from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
    fixed_signal(monkeypatch)
    data=request_data(evaluated=4)
    data['config'].update(execution_mode='LIVE_MT5',mt5_account_login=123,mt5_account_server='Demo',entry_amount=100,
        profit_per_trade_cap=1000,risk_per_trade_pct=1,profit_exit=dict(enabled=True,min_net_profit=1,cost_buffer=.25))
    bars=data['markets'][0]['candles']
    for i in range(100,156):
        bars[i].update(open=1.1015,high=1.1016,low=1.1014,close=1.1015)
    bars[156].update(open=1.1,high=1.1015,low=1.0999,close=1.1014)
    bars[157].update(open=1.1014,high=1.1015,low=1.1008,close=1.1009)
    bars[158].update(open=1.1009,high=1.1010,low=1.1008,close=1.1009)
    bars[159].update(open=1.1009,high=1.1010,low=1.1008,close=1.1009)
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    early=[t for t in r['trades'] if t['exit_reason']=='EARLY_PROFIT_WEAKNESS']
    assert len(early)==1 and early[0]['exit_time']==bars[158]['time'] and early[0]['net_pnl']>0
    assert early[0]['exit_price']==pytest.approx(bars[158]['open']-.00002)


def test_profit_peak_cannot_reset_portfolio_equity_drawdown(monkeypatch):
    from test_forex_research import request_data,fixed_signal
    from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
    fixed_signal(monkeypatch)
    data=request_data(evaluated=2)
    data['config'].update(execution_mode='LIVE_MT5',mt5_account_login=123,mt5_account_server='Demo',entry_amount=100,
        profit_per_trade_cap=1000,risk_per_trade_pct=1,profit_exit=dict(enabled=True))
    data['markets'][0]['candles'][159]['low']=1.09
    r=run_forex_backtest(ForexBacktestRequest.model_validate(data))
    assert r['realized_pnl']<0
    assert r['max_drawdown']+1e-7>=-r['realized_pnl']


def test_pending_close_reserves_global_capacity_while_other_pairs_keep_scanning(portfolio):
    from test_forex_portfolio import next_cycle
    b,sdk,multi=portfolio
    next_cycle(b,multi);asyncio.run(b.reconcile_live())
    t=b.trade_history[0];assert t['status']=='OPEN'
    t['close_intent']=dict(status='PENDING',client_tag='pending-exit')
    b._save_trade(t)
    next_cycle(b,multi)
    assert len(sdk.sent)==1 and len(multi.rows)==4 and b.is_running
    assert 'Fechamento' in b._runtime_block()


@pytest.mark.parametrize('params',[{'min_net_profit':0},{'cost_buffer':-1},{'giveback_fraction':1},{'slippage_points':-1}])
def test_invalid_exit_controls_are_rejected(params):
    with pytest.raises(ValueError):ProfitExitConfig.model_validate(params)


def test_close_uses_current_position_ticket_when_identifier_is_different(live_bot,monkeypatch):
    b,sdk,t=opened(live_bot,monkeypatch)
    sdk.positions[0].ticket=901
    assert t['position_id']==900
    asyncio.run(b.manage_profit_exits())
    assert sdk.sent[-1]['position']==901
    assert t['close_intent']['submitted_request']==sdk.sent[-1]
    asyncio.run(b.reconcile_live());assert t['status']=='WIN'


def test_enabling_in_netting_account_is_rejected_without_changing_config(live_bot):
    b,sdk=live_bot;sdk.info.margin_mode=0;before=copy.deepcopy(b.config)
    with pytest.raises(ValueError,match='hedge'):b.update_config({'profit_exit':{'enabled':True}})
    assert b.config==before and not sdk.sent
