"""
Conector para MetaTrader 5 (Forex + B3 Mini Índice WIN e Mini Dólar WDO).
Possui fallback automático para Modo Simulador Realista caso o terminal MetaTrader 5
não esteja aberto ou conectado no momento, permitindo testar o dashboard e as estratégias
imediatamente.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import math
import random
import time
import os
import pandas as pd
import numpy as np

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False


class MT5Connector:
    TIMEFRAME_MAP = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
    }

    BASE_PRICES = {
        "WIN$N": 128500.0,
        "WDO$N": 5650.0,
        "EURUSD": 1.08450,
        "GBPUSD": 1.29320,
        "USDJPY": 152.400,
        "XAUUSD": 2735.50,
        "BTCUSD": 68400.0,
    }

    def __init__(self):
        self.tick_time_offset_minutes = 0
        self.bar_time_offset_minutes = 0
        self.bar_clock = {}
        self.connected = False
        self.simulation_mode = True
        self.account_info: Dict[str, Any] = {
            "login": 0,
            "server": "Simulador Local MT5",
            "balance": 10000.00,
            "equity": 10000.00,
            "currency": "BRL",
        }
        self._sim_update_times = {}
        self._sim_series_cache: Dict[str, pd.DataFrame] = {}

    def configure_time(self, config):
        # Explicit offsets describe non-UTC API timestamps, not chart display zones.
        self.tick_time_offset_minutes = config['mt5_tick_time_offset_minutes']
        self.bar_time_offset_minutes = config['mt5_bar_time_offset_minutes']
        self.bar_clock.clear()

    def tick_timestamp(self, tick):
        raw = getattr(tick, 'time_msc', 0) / 1000 or getattr(tick, 'time', 0)
        return float(raw) - self.tick_time_offset_minutes * 60

    def connect(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
        use_simulation_if_unavailable: bool = True,
    ) -> Dict[str, Any]:
        path = path or os.environ.get('MT5_TERMINAL_PATH')
        if MT5_AVAILABLE:
            kwargs: Dict[str, Any] = {}
            if login:
                kwargs["login"] = int(login)
            if password:
                kwargs["password"] = password
            if server:
                kwargs["server"] = server

            initialized = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
            if initialized:
                info = mt5.account_info()
                if info is not None:
                    self.connected = True
                    self.simulation_mode = False
                    self.account_info = {
                        "login": info.login,
                        "server": info.server,
                        "balance": float(info.balance),
                        "equity": float(info.equity),
                        "currency": info.currency,
                    }
                    return {
                        "status": "connected",
                        "simulation_mode": False,
                        "message": f"Conectado com sucesso ao MetaTrader 5 (Conta: {info.login} - {info.server})",
                        "account": self.account_info,
                    }

        if use_simulation_if_unavailable:
            self.connected = True
            self.simulation_mode = True
            return {
                "status": "simulated",
                "simulation_mode": True,
                "message": "Terminal MetaTrader 5 não detectado ou desconectado. Rodando em Modo Simulador em Tempo Real.",
                "account": self.account_info,
            }

        self.connected = False
        return {
            "status": "error",
            "simulation_mode": False,
            "message": "Não foi possível inicializar o MetaTrader 5. Verifique se o terminal MT5 está aberto no Windows.",
            "account": None,
        }

    def get_candles(self, symbol: str, timeframe: str = "M1", count: int = 100) -> pd.DataFrame:
        if self.connected and not self.simulation_mode and MT5_AVAILABLE:
            tf_map = {
                "M1": mt5.TIMEFRAME_M1,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1,
            }
            mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M1)
            if not mt5.symbol_select(symbol, True):
                error = mt5.last_error()
                raise RuntimeError(f'MT5 não conseguiu selecionar {symbol}: {error}. Confira o nome exato na Observação do Mercado, incluindo sufixos.')
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                raw_last = float(df['time'].iloc[-1])
                normalized = df['time'] - self.bar_time_offset_minutes * 60
                df["time"] = pd.to_datetime(normalized, unit="s")
                self.bar_clock[(symbol,timeframe)] = dict(raw_epoch=raw_last,
                    utc_epoch=raw_last-self.bar_time_offset_minutes*60)
                df = df.rename(columns={"tick_volume": "volume"})
                return df[["time", "open", "high", "low", "close", "volume"]]
            error = mt5.last_error()
            raise RuntimeError(f'MT5 sem histórico para {symbol} / {timeframe} (solicitadas {count} velas): {error}. Abra esse gráfico no terminal e carregue o histórico com Home; confira conexão e símbolo. Nenhum preço simulado será usado.')

        if self.connected and not self.simulation_mode:
            raise RuntimeError('MT5 não retornou dados reais; simulação automática de preços bloqueada.')
        return self._generate_simulated_candles(symbol, timeframe, count)

    def _generate_simulated_candles(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}"
        minutes = self.TIMEFRAME_MAP.get(timeframe, 1)
        base_price = self.BASE_PRICES.get(symbol.upper(), 100.0)
        volatility = base_price * 0.0012

        now = datetime.now().replace(second=0, microsecond=0)
        now = now.replace(minute=(now.minute // minutes) * minutes)
        if cache_key in self._sim_series_cache and time.monotonic()-self._sim_update_times.get(cache_key, 0) < 1:
            return self._sim_series_cache[cache_key].copy()
        self._sim_update_times[cache_key] = time.monotonic()
        if cache_key not in self._sim_series_cache:
            times = [now - timedelta(minutes=minutes * i) for i in range(count)][::-1]
            prices = [base_price]
            for i in range(1, count):
                wave = math.sin(i / 4.5) * volatility * 0.65
                noise = random.gauss(0, volatility * 0.55)
                prices.append(max(base_price * 0.5, prices[-1] + wave + noise))

            rows = []
            for i, t in enumerate(times):
                c = prices[i]
                o = prices[i - 1] if i > 0 else c - volatility * 0.2
                h = max(o, c) + abs(random.gauss(0, volatility * 0.4))
                l = min(o, c) - abs(random.gauss(0, volatility * 0.4))
                v = int(random.uniform(800, 3500))
                rows.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
            self._sim_series_cache[cache_key] = pd.DataFrame(rows)
        else:
            df = self._sim_series_cache[cache_key]
            last_row = df.iloc[-1].to_dict()
            step_wave = math.sin(datetime.now().timestamp() / 15.0) * volatility * 0.45
            delta = step_wave + random.gauss(0, volatility * 0.5)
            new_close = max(base_price * 0.5, float(last_row["close"]) + delta)

            if (now - last_row["time"]).total_seconds() >= minutes * 60:
                new_row = {
                    "time": now,
                    "open": float(last_row["close"]),
                    "high": max(float(last_row["close"]), new_close) + abs(delta) * 0.3,
                    "low": min(float(last_row["close"]), new_close) - abs(delta) * 0.3,
                    "close": new_close,
                    "volume": int(random.uniform(900, 4200)),
                }
                df = pd.concat([df.iloc[1:], pd.DataFrame([new_row])], ignore_index=True)
            else:
                df.at[df.index[-1], "close"] = new_close
                df.at[df.index[-1], "high"] = max(float(last_row["high"]), new_close)
                df.at[df.index[-1], "low"] = min(float(last_row["low"]), new_close)
                df.at[df.index[-1], "volume"] = int(last_row["volume"]) + int(random.uniform(25, 180))
            self._sim_series_cache[cache_key] = df

        return self._sim_series_cache[cache_key].copy()

    def execute_order(self, *args, **kwargs):
        raise RuntimeError('Use MT5Executor e o diário de intenções; envio direto sem reconciliação está desativado.')
