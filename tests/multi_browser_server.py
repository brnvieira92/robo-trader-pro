"""Isolated, fake-SDK server for browser_multi.py. Never connects to a terminal.
Run from repository root: PYTHONPATH=. .venv/bin/python tests/multi_browser_server.py
"""
import copy
import os
from tempfile import TemporaryDirectory
import numpy as np
import pandas as pd


def run():
    with TemporaryDirectory(prefix='forex-multi-browser-') as directory:
        os.environ['TRADER_JOURNAL_PATH'] = os.path.join(directory, 'browser.sqlite3')
        os.environ.pop('TELEGRAM_BOT_TOKEN', None)
        os.environ.pop('TELEGRAM_CHAT_ID', None)
        import backend.mt5_connector as connector
        connector.MT5_AVAILABLE = False
        connector.mt5 = None
        import backend.main as main
        from backend.mt5_execution import MT5Executor
        from test_mt5_live import FakeMT5
        sdk = FakeMT5()
        original = sdk.symbol_info

        def info(symbol):
            value = copy.copy(original(symbol))
            value.currency_base = symbol[:3]
            value.currency_profit = symbol[3:6]
            return value

        def no_send(request):
            raise AssertionError('Browser fixture never submits orders.')

        def candles(symbol, timeframe, count=240):
            close = 1.1 + np.sin(np.arange(240) / 7) * .001
            return pd.DataFrame(dict(
                time=pd.date_range(end=pd.Timestamp.now('UTC').floor('min').tz_localize(None), periods=240, freq='min'),
                open=close, high=close+.0003, low=close-.0003, close=close, volume=100))

        sdk.symbol_info = info
        sdk.order_send = no_send
        bot = main.bot
        bot.mt5.connected = True
        bot.mt5.simulation_mode = False  # Application exercises native-shaped fake data.
        bot.mt5.get_candles = candles
        bot.live = MT5Executor(bot.mt5, sdk)
        bot.update_config(dict(market_type='MT5_FOREX', execution_mode='LIVE_MT5',
            strategy_id='ml_random_forest', forex_multi_enabled=True, symbol='EURUSD#',
            forex_pairs=[dict(pair=p, symbol=p+'#', enabled=True)
                         for p in ('EURUSD', 'USDJPY', 'GBPUSD', 'USDCHF')]))
        import uvicorn
        uvicorn.run(main.app, host='127.0.0.1', port=8011)


if __name__ == '__main__':
    run()
