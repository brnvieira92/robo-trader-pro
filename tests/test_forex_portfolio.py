"""Two-position execution with a real-shaped fake SDK; never broker IO."""
import asyncio
import copy
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import pytest
from backend.journal import PaperJournal
from backend.bot_manager import BotManager
from backend.forex_multi import ForexCoordinator
from backend.mt5_execution import MT5ExecutionError, MT5Executor
from backend.portfolio_risk import inspect_portfolio, reserved_risk
from backend.forex_backtest import ForexBacktestRequest
from test_mt5_live import live_bot
from test_forex_multi import enable, candidate
from test_forex_research import request_data


@pytest.fixture
def portfolio(live_bot, monkeypatch):
    b, sdk = live_bot
    multi = enable(b, forex_max_open_positions=2, entry_amount=100, profit_per_trade_cap=1000)
    b._multi = multi
    sdk.info.equity = sdk.info.balance = sdk.info.margin_free = 10000
    original = sdk.order_send
    def send(request):
        previous = list(sdk.positions)
        number = len(sdk.sent)
        result = original(request)
        incoming = sdk.deals[-1]
        incoming.ticket += number*10
        incoming.order += number*10
        incoming.position_id += number*10
        position = sdk.positions[-1]
        position.ticket = position.identifier = incoming.position_id
        position.volume = incoming.volume
        position.price_open = incoming.price
        sdk.positions = previous+[position]
        return NS(retcode=result.retcode,order=incoming.order,deal=incoming.ticket)
    monkeypatch.setattr(sdk, 'order_send', send)
    for view in multi.views.values():
        def refresh(v=view):
            v.is_running=b.is_running
            v.stats=dict(b.stats)
            v.trade_history=b.trade_history
            v._last_trade_timestamp=b._last_trade_timestamp
            v._last_signal_bar=next((t.get('bar_time') for t in b.trade_history if t['symbol']==v.config['symbol']),None)
            snap=candidate(v)
            amount,_=v._calculate_smart_entry_amount(snap['signal'])
            snap['risk']=dict(blocked_reason=v._risk_block(snap['signal']),next_amount=amount)
            return snap
        monkeypatch.setattr(view,'refresh',refresh)
    b.is_running = True
    return b, sdk, multi


def next_cycle(b, multi):
    # Simulate the existing global cooldown having elapsed; never weaken it.
    b._last_trade_timestamp = time.time()-61
    asyncio.run(b.run_cycle())


def test_two_different_pairs_open_then_third_is_blocked_and_scans_continue(portfolio):
    b, sdk, multi = portfolio
    next_cycle(b, multi)
    assert len(sdk.sent)==1 and b.trade_history[0]['status']=='PENDING'
    # Only reconciled/protected exposure permits the second send.
    next_cycle(b, multi)
    assert len(sdk.sent)==2 and len({r['symbol'] for r in sdk.sent})==2
    next_cycle(b, multi)
    assert len(sdk.sent)==2 and len(multi.rows)==4
    assert all(t['status']=='OPEN' and t['protected'] for t in b.trade_history)
    risk=sum(reserved_risk(t) for t in b.trade_history)
    assert risk<=100 and all(t['amount']<=50 for t in b.trade_history)
    state=multi.snapshot()['forex_radar']
    assert state['max_open_positions']==2 and state['portfolio']['open_positions']==2
    assert sum(state['portfolio']['usd_risk'].values())==pytest.approx(risk)
    assert 'Limite simultâneo: 2' in b.telegram_status()
    assert b.config['min_confidence']==78 and b.config['strategy_params']['use_ml_filter']


def test_confirmed_first_position_keeps_other_pair_entry_available(portfolio):
    b, sdk, multi = portfolio
    next_cycle(b, multi)
    asyncio.run(b.reconcile_live())
    b._last_trade_timestamp=time.time()-61
    symbol=b.trade_history[0]['symbol']
    for view in multi.views.values():
        view._has_open_trade=view.config['symbol']==symbol
        view._last_trade_timestamp=b._last_trade_timestamp
        if view.config['symbol']==symbol:
            assert 'símbolo' in view._risk_block()
        else:
            assert view._risk_block() is None
    assert multi.exposure_check() is None


@pytest.mark.parametrize('failure', ['pending','external','orders','missing','unprotected','sync_error','over_budget','changed_volume'])
def test_uncertain_or_unsafe_portfolio_blocks_new_entries_but_not_monitoring(portfolio, failure):
    b, sdk, multi = portfolio
    next_cycle(b, multi)
    asyncio.run(b.reconcile_live())
    trade=b.trade_history[0]
    if failure=='pending':trade['status']='PENDING'
    elif failure=='external':sdk.positions.append(NS(identifier=999,ticket=999,symbol='GOLD#'))
    elif failure=='orders':sdk.orders=[NS(ticket=55)]
    elif failure=='missing':sdk.positions=[]
    elif failure=='unprotected':sdk.positions[0].sl=0
    elif failure=='sync_error':trade['sync_error']='unavailable'
    elif failure=='changed_volume':sdk.positions[0].volume+=.01
    else:sdk.info.equity=100  # Old risk exceeds the new global equity budget.
    assert multi.exposure_check()
    assert len(sdk.sent)==1 and b.is_running
    # Preflight independently rejects bypassing the coordinator.
    cfg={**b.config,'symbol':'GBPUSD#'}
    with pytest.raises(MT5ExecutionError):
        b.live.prepare(cfg, {'action':'BUY'}, 300, 'new', portfolio_trades=b.trade_history)
    assert len(sdk.sent)==1


def test_pending_response_never_allows_second_pair(portfolio):
    b, sdk, multi = portfolio
    sdk.no_response=True;sdk.fill=False
    for _ in range(3):next_cycle(b,multi)
    assert len(sdk.sent)==1 and b._has_open_trade
    assert b.trade_history[0]['status']=='PENDING' and len(multi.rows)==4


def test_global_cooldown_remains_between_entries(portfolio):
    b, sdk, multi = portfolio
    next_cycle(b,multi)
    asyncio.run(b.run_cycle())
    assert len(sdk.sent)==1
    b._last_trade_timestamp=time.time()-61
    asyncio.run(b.run_cycle())
    assert len(sdk.sent)==2


def test_sdk_exposure_failure_blocks_all_entries_while_scanning_continues(portfolio):
    b,sdk,multi=portfolio
    sdk.positions=None
    next_cycle(b,multi)
    assert not sdk.sent and len(multi.rows)==4 and b.is_running
    assert b.operational_status()['state']=='RECOVERING'


def test_closing_one_position_keeps_other_and_frees_only_its_risk(portfolio):
    b, sdk, multi = portfolio
    next_cycle(b,multi);next_cycle(b,multi);next_cycle(b,multi)
    closed=b.trade_history[-1]
    incoming=next(d for d in sdk.deals if d.position_id==closed['position_id'])
    sdk.positions=[p for p in sdk.positions if p.identifier!=closed['position_id']]
    sdk.deals.append(NS(ticket=9999,order=9998,position_id=closed['position_id'],magic=0,comment='sl',type=1,entry=1,
        volume=incoming.volume,price=closed['stop_loss'],time=int(time.time()),profit=-closed['amount'],commission=0,swap=0,fee=0))
    asyncio.run(b.reconcile_live())
    assert closed['status']=='LOSS' and b._has_open_trade
    assert len([t for t in b.trade_history if t['status']=='OPEN'])==1
    assert multi.exposure_check() is None
    assert multi.portfolio['reserved_risk']==pytest.approx(b.trade_history[0]['reserved_risk'])
    assert b.stats['daily_pnl']<0


def test_restart_preserves_both_reservations_and_starts_paused(portfolio):
    b,sdk,multi=portfolio
    next_cycle(b,multi);next_cycle(b,multi);next_cycle(b,multi)
    restored=BotManager()
    restored.mt5.connected=True;restored.mt5.simulation_mode=False
    restored.live=MT5Executor(restored.mt5,sdk)
    asyncio.run(restored.reconcile_live())
    coordinator=ForexCoordinator(restored)
    assert len(restored.trade_history)==2 and restored._has_open_trade and not restored.is_running
    assert 'Limite global' in coordinator.exposure_check()
    assert coordinator.portfolio['reserved_risk']==pytest.approx(sum(t['reserved_risk'] for t in restored.trade_history))
    with pytest.raises(ValueError):restored.update_config({'forex_max_open_positions':1})
    assert len(sdk.sent)==2


def test_late_external_exposure_is_rechecked_before_order_send(portfolio, monkeypatch):
    b, sdk, multi = portfolio
    original=b.live.send
    def send(trade):
        sdk.orders=[NS(ticket=55)]
        return original(trade)
    monkeypatch.setattr(b.live,'send',send)
    next_cycle(b,multi)
    assert not sdk.sent and b.trade_history[0]['status']=='REJECTED'


def intent(config, identifier, symbol, amount, status='SUBMITTING'):
    return dict(id=identifier,day='2026-10-20',mode='LIVE_MT5',status=status,forex_multi=True,
        account_login=123,account_server='Demo',symbol=symbol,reserved_risk=amount,
        amount=amount,risk_budget=amount,max_open_positions=2,protected=True,position_id=1,
        portfolio_config=config,portfolio={'risk_limit':100})


def test_atomic_reservation_prevents_same_pair_third_and_overspending(tmp_path):
    journal=PaperJournal(tmp_path/'atomic.db')
    cfg={'forex_multi_enabled':True,'forex_max_open_positions':2}
    first=intent(cfg,'one','EURUSD#',60,'OPEN');journal.save(first)
    with pytest.raises(ValueError):journal.reserve_live(intent(cfg,'bad','GBPUSD#',41))
    with pytest.raises(ValueError):journal.reserve_live(intent(cfg,'same','EURUSD#',10))
    journal.reserve_live(intent(cfg,'two','GBPUSD#',40))
    with pytest.raises(ValueError):journal.reserve_live(intent(cfg,'three','USDJPY#',1))
    assert len(journal.all_trades())==2


def test_competing_reservations_cannot_consume_both_slots_while_unconfirmed(tmp_path):
    path=tmp_path/'race.db';journal=PaperJournal(path)
    cfg={'forex_multi_enabled':True,'forex_max_open_positions':2}
    def reserve(pair):
        try:journal.reserve_live(intent(cfg,pair,pair,50));return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(reserve,['EURUSD#','GBPUSD#']))
    assert sum(results)==1 and len(journal.all_trades())==1


def test_single_position_backtest_cannot_claim_to_validate_parallel_portfolio():
    request=request_data(symbols=('EURUSD#','GBPUSD#'))
    request['config']['forex_max_open_positions']=2
    with pytest.raises(ValueError, match='Não valida a carteira simultânea'):
        ForexBacktestRequest.model_validate(request)
    request['config'].pop('forex_max_open_positions')
    assert ForexBacktestRequest.model_validate(request).config.forex_max_open_positions==1


@pytest.mark.parametrize('pnl,expected', [(1000,'Meta diária'),(-300,'Stop loss'),(0,None)])
def test_daily_goal_and_loss_limits_remain_global_for_second_pair(portfolio,pnl,expected):
    b,sdk,multi=portfolio
    next_cycle(b,multi);asyncio.run(b.reconcile_live())
    b._last_trade_timestamp=time.time()-61
    b.config['stop_gain']=1000
    b.stats['daily_pnl']=pnl
    view=multi.views['GBPUSD']
    view.config['stop_gain']=1000
    snapshot=view.refresh()
    if expected:assert expected in snapshot['risk']['blocked_reason']
    else:assert snapshot['risk']['blocked_reason'] is None
    assert len(sdk.sent)==1


def test_usd_opposite_directions_use_broker_money_values_without_hedge_credit():
    cfg=dict(forex_multi_enabled=True,forex_max_open_positions=2,risk_per_trade_pct=1,
        bankroll_mode='FIXED',entry_amount=100,forex_pairs=[dict(pair=p,symbol=p+'#') for p in ['EURUSD','USDJPY']])
    account=dict(login=1,server='Demo',equity=10000)
    common=dict(mode='LIVE_MT5',status='OPEN',protected=True,account_login=1,account_server='Demo',action='BUY',filled_volume=.1)
    eur=dict(common,id='eur',symbol='EURUSD#',position_id=1,entry_price=1.1,stop_loss=1.0995,take_profit=1.101,
        reserved_risk=5.5,risk_budget=6,cost_buffer=.5)
    jpy=dict(common,id='jpy',symbol='USDJPY#',position_id=2,entry_price=150,stop_loss=149.9,take_profit=150.2,
        reserved_risk=7.25,risk_budget=10,cost_buffer=1)
    positions=[NS(identifier=t['position_id'],symbol=t['symbol'],sl=t['stop_loss'],tp=t['take_profit'],volume=.1,price_open=t['entry_price']) for t in [eur,jpy]]
    calls=[]
    def converted_loss(kind,symbol,volume,entry,exit):
        calls.append(symbol)
        return -5 if symbol=='EURUSD#' else -6.25
    report=inspect_portfolio(cfg,[eur,jpy],positions,[],account,300,converted_loss,require_slot=False)
    assert calls==['EURUSD#','USDJPY#']
    assert report['reserved_risk']==12.75
    assert report['usd_risk']=={'LONG':7.25,'SHORT':5.5}
    with pytest.raises(ValueError,match='Orçamento global'):
        inspect_portfolio(cfg,[eur,jpy],positions,[],account,12,converted_loss,require_slot=False)
