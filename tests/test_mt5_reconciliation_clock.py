"""Broker clock offsets must never delay identity discovery or fabricate PnL."""
import asyncio
import copy
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import pytest
from backend.bot_manager import BotManager
from backend.config import TradingConfig
from backend.mt5_execution import MT5Executor, MT5ExecutionError
from test_mt5_live import live_bot, signal


@pytest.mark.parametrize('offset', [0, 10800, -10800, 20700])
def test_order_ticket_discovers_fill_without_waiting_for_server_clock(live_bot, offset):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    trade = b.trade_history[0]
    sdk.deals[0].time = int(trade['created_at']) + offset
    original = sdk.history_deals_get
    calls = []
    def history(*args, **kwargs):
        calls.append((args, kwargs))
        if args:
            begin, end = args
            return tuple(d for d in sdk.deals if begin.timestamp() <= d.time <= end.timestamp())
        return original(*args, **kwargs)
    sdk.history_deals_get = history
    # Tick/bar offsets deliberately differ: deal timestamps are independent.
    b.mt5.tick_time_offset_minutes = 300
    b.mt5.bar_time_offset_minutes = -120
    asyncio.run(b.reconcile_live())
    assert trade['status'] == 'OPEN' and trade['protected']
    assert calls[0] == ((), {'ticket': trade['order_ticket']})
    assert not any(args for args, _ in calls)
    assert trade['deal_time_offset_seconds'] == offset
    assert trade['deals'][0]['time_raw'] == int(trade['created_at']) + offset
    assert trade['deals'][0]['time'] == int(trade['created_at'])
    assert b.stats['floating_pnl'] == 1 and len(sdk.sent) == 1


def test_reported_three_hour_delay_closes_immediately_and_notifies_once(live_bot, monkeypatch):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    trade = b.trade_history[0]
    actual_entry = int(time.time()) - 235
    trade['created_at'] = actual_entry
    trade['submission_confirmed_at'] = actual_entry + 1
    sdk.deals[0].time = actual_entry + 10800
    sdk.close_position()
    sdk.deals[-1].time = actual_entry + 235 + 10800
    sdk.deals[-1].profit = -9.1
    sdk.deals[0].commission = 0
    sdk.deals[-1].commission = sdk.deals[-1].swap = sdk.deals[-1].fee = 0
    sent = []
    monkeypatch.setattr(b, '_notify', lambda *args: sent.append(args))
    asyncio.run(b.reconcile_live())
    assert trade['status'] == 'LOSS' and trade['pnl'] == -9.1
    assert trade['settled_at'] == actual_entry + 235
    assert trade['settled_at_raw'] == actual_entry + 235 + 10800
    assert trade['closure_confirmed_at'] - trade['settled_at'] < 5
    assert b.stats['daily_pnl'] == -9.1 and not b._has_open_trade
    assert len(sent) == 1 and sent[0][0].endswith(':closed')
    assert 'Fechamento UTC:' in sent[0][1] and 'Confirmação pelo robô UTC:' in sent[0][1]
    event = b.journal.diagnostics(kind='RECONCILIATION')[0]['payload']
    assert event['settled_at'] == trade['settled_at']
    asyncio.run(b.reconcile_live())
    assert len(sent) == 1 and len(sdk.sent) == 1


def test_missing_order_ticket_uses_wide_window_with_strict_correlation(live_bot):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    trade = b.trade_history[0]
    trade.pop('order_ticket')
    sdk.deals[0].time = int(trade['created_at']) + 10800
    own = sdk.deals[0]
    foreign = NS(**{**vars(own), 'magic': 999, 'comment': 'foreign', 'order': 1, 'ticket': 2, 'position_id': 3})
    original = sdk.history_deals_get
    def history(*args, **kwargs):
        if args:
            begin, end = args
            return tuple(d for d in (own, foreign) if begin.timestamp() <= d.time <= end.timestamp())
        return original(*args, **kwargs)
    sdk.history_deals_get = history
    asyncio.run(b.reconcile_live())
    assert trade['position_id'] == own.position_id and trade['status'] == 'OPEN'
    assert len(sdk.sent) == 1


def test_failed_ticket_query_never_unblocks_or_sends_again(live_bot):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    sdk.history_deals_get = lambda *args, **kwargs: None
    asyncio.run(b.reconcile_live())
    assert b._has_open_trade and b.trade_history[0]['status'] == 'PENDING'
    assert 'sync_error' in b.trade_history[0]
    assert not asyncio.run(b.execute_trade_from_signal(signal(), True))['success']
    assert len(sdk.sent) == 1


@pytest.mark.parametrize('failure', ['none', 'exception'])
def test_failed_ticket_query_recovers_real_gain_from_window_and_survives_restart(live_bot, monkeypatch, failure):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    trade = b.trade_history[0]
    actual_entry = int(time.time()) - 600
    trade.update(created_at=actual_entry, submission_confirmed_at=actual_entry+1)
    sdk.deals[0].time = actual_entry + 10800
    sdk.close_position()
    sdk.deals[-1].time = actual_entry + 420 + 10800
    original = sdk.history_deals_get
    calls = []
    def history(*args, **kwargs):
        calls.append((args, kwargs))
        if 'ticket' in kwargs:
            if failure == 'exception':
                raise RuntimeError('SDK ticket query failed')
            return None
        if args:
            begin, end = args
            return tuple(d for d in sdk.deals if begin.timestamp() <= d.time <= end.timestamp())
        return original(*args, **kwargs)
    sdk.history_deals_get = history
    sdk.last_error = lambda: (-2, 'Invalid arguments')
    sent = []
    monkeypatch.setattr(b, '_notify', lambda *args: sent.append(args))
    asyncio.run(b.reconcile_live())
    assert trade['status'] == 'WIN' and trade['pnl'] == pytest.approx(2.45)
    assert trade['settled_at'] == actual_entry+420
    assert not b._has_open_trade and not trade.get('sync_error')
    assert calls[0][1] == {'ticket': trade['order_ticket']}
    assert any(args for args, _ in calls)
    assert trade['history_queries'][0]['error']
    assert trade['history_queries'][-1]['method'] == 'time_window'
    assert len(sent) == 1 and len(sdk.sent) == 1
    restored = BotManager()
    saved = restored.journal.all_trades()[0]
    assert saved['id'] == trade['id'] and saved['pnl'] == pytest.approx(2.45)
    assert saved['status'] == 'WIN' and saved['history_queries'][0]['error']
    assert restored.stats['daily_pnl'] == pytest.approx(2.45)
    restored.mt5.connected = True
    restored.mt5.simulation_mode = False
    restored.live = MT5Executor(restored.mt5, sdk)
    monkeypatch.setattr(restored, '_notify', lambda *args: sent.append(args))
    asyncio.run(restored.reconcile_live())
    assert len(sent) == 1 and len(sdk.sent) == 1


def test_ticket_error_and_unrelated_window_cannot_settle_or_release_pending(live_bot):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    own = sdk.deals[0]
    foreign = NS(**{**vars(own), 'magic': 999, 'comment': 'foreign', 'order': 1, 'ticket': 2, 'position_id': 3})
    wrong_symbol = NS(**{**vars(own), 'symbol': 'GBPUSD'})
    sdk.history_deals_get = lambda *args, **kwargs: (foreign, wrong_symbol) if args else None
    asyncio.run(b.reconcile_live())
    assert b.trade_history[0]['status'] == 'PENDING' and b._has_open_trade
    assert b.trade_history[0]['pnl'] == 0 and len(sdk.sent) == 1


def test_history_sdk_error_code_is_preserved_when_all_queries_fail(live_bot):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    sdk.history_deals_get = lambda *args, **kwargs: None
    sdk.last_error = lambda: (-2, 'Invalid arguments')
    asyncio.run(b.reconcile_live())
    assert '-2' in b.trade_history[0]['sync_error']
    assert 'time_window' in b.trade_history[0]['sync_error']
    assert b._has_open_trade and len(sdk.sent) == 1


def legacy_trade():
    return dict(id='legacy', mode='LIVE_MT5', status='LOSS', day='2026-10-20',
        created_at=1792497724.570737, retcode=10009, deal_ticket=2307222746,
        deals=[dict(ticket=2307222746,time=1792508524,pnl=0),dict(ticket=2307227841,time=1792508759,pnl=-9.1)],
        settled_at=1792508759, pnl=-9.1)


def test_legacy_report_times_are_repaired_once_without_changing_money():
    trade = legacy_trade()
    trade.update(MT5Executor.normalize_trade_times(trade))
    assert trade['deal_time_offset_seconds'] == 10800
    assert trade['settled_at'] == 1792497959
    assert trade['settled_at_raw'] == 1792508759
    assert trade['pnl'] == -9.1 and trade['deals'][1]['pnl'] == -9.1
    assert MT5Executor.normalize_trade_times(trade) == {}


def test_day_boundary_uses_normalized_deals_on_restart(live_bot):
    b, sdk = live_bot
    trade = legacy_trade()
    # Last night UTC; raw server timestamps incorrectly appear to be today.
    midnight = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    trade.update(created_at=midnight-1800,day=datetime.fromtimestamp(midnight-1800,timezone.utc).date().isoformat(),
        settled_at=midnight-600+10800,account_login=b.config['mt5_account_login'],account_server=b.config['mt5_account_server'],symbol='EURUSD')
    trade['deals'][0]['time'] = midnight-1800+10800
    trade['deals'][1]['time'] = midnight-600+10800
    b.journal.save(trade)
    restored = BotManager()
    assert restored.stats['daily_pnl'] == 0
    saved = restored.journal.all_trades()[0]
    assert saved['deal_times_version'] == 1 and saved['settled_at'] < midnight
    again = BotManager().journal.all_trades()[0]
    assert saved == again


@pytest.mark.parametrize('change', [dict(retcode=10008),dict(submission_confirmed_at=1792497724.570737+120),dict(created_at=1792497724.570737-100)])
def test_ambiguous_clock_requires_explicit_independent_override(change):
    trade = {**legacy_trade(), **change}
    original = copy.deepcopy(trade)
    with pytest.raises(MT5ExecutionError): MT5Executor.normalize_trade_times(trade)
    assert trade == original
    explicit = MT5Executor.normalize_trade_times(trade, 180)
    assert explicit['deal_time_basis'] == 'EXPLICIT'
    assert explicit['settled_at'] == 1792497959


def test_deal_clock_override_validation():
    assert TradingConfig().mt5_deal_time_offset_minutes is None
    assert TradingConfig(mt5_deal_time_offset_minutes=180).mt5_deal_time_offset_minutes == 180
    with pytest.raises(ValueError): TradingConfig(mt5_deal_time_offset_minutes=900)


def test_ambiguous_fill_can_be_reconciled_with_paused_clock_only_override(live_bot):
    b, sdk = live_bot
    asyncio.run(b.execute_trade_from_signal(signal(), True))
    trade = b.trade_history[0]
    trade['retcode'] = 10008  # Accepted, not proof of immediate fill.
    sdk.deals[0].time = int(trade['created_at']) + 10800
    asyncio.run(b.reconcile_live())
    assert b._has_open_trade and trade['status'] == 'PENDING' and 'sync_error' in trade
    with pytest.raises(ValueError): b.update_config({'risk_per_trade_pct': .5})
    with pytest.raises(ValueError): b.update_config({'mt5_deal_time_offset_minutes':180, 'min_confidence':79})
    b.is_running = True
    with pytest.raises(ValueError): b.update_config({'mt5_deal_time_offset_minutes':180})
    b.is_running = False
    b.update_config({'mt5_deal_time_offset_minutes':180})
    asyncio.run(b.reconcile_live())
    assert b.trade_history[0]['status'] == 'OPEN' and b._has_open_trade
    assert b.trade_history[0]['deal_time_basis'] == 'EXPLICIT'
    assert len(sdk.sent) == 1 and b.config['min_confidence'] == 78


def test_ambiguous_legacy_financial_day_blocks_entries_until_explicit_repair(live_bot):
    b, sdk = live_bot
    trade = legacy_trade()
    trade.update(day=datetime.now(timezone.utc).date().isoformat(), retcode=10008,
                 account_login=b.config['mt5_account_login'], account_server=b.config['mt5_account_server'], symbol='EURUSD')
    b.journal.save(trade)
    b.trade_history = b._session_trades()
    assert 'deal_time_warning' in b.trade_history[0]
    assert 'Horário de negócios antigos' in b._runtime_block()
    b.update_config({'mt5_deal_time_offset_minutes':180})
    assert not b.trade_history[0].get('deal_time_warning')
    assert b.trade_history[0]['pnl'] == -9.1 and b._runtime_block() is None
    assert not sdk.sent
