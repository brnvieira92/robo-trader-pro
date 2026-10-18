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
        self.connected = False
        self.simulation_mode = True
        self.account_info: Dict[str, Any] = {
            "login": 0,
            "server": "Simulador Local MT5",
            "balance": 10000.00,
            "equity": 10000.00,
            "currency": "BRL",
        }
        self._sim_series_cache: Dict[str, pd.DataFrame] = {}

    def connect(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
        use_simulation_if_unavailable: bool = True,
    ) -> Dict[str, Any]:
        if MT5_AVAILABLE:
            kwargs: Dict[str, Any] = {}
            if path:
                kwargs["path"] = path
            if login:
                kwargs["login"] = int(login)
            if password:
                kwargs["password"] = password
            if server:
                kwargs["server"] = server

            initialized = mt5.initialize(**kwargs) if kwargs else mt5.initialize()
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
            mt5.symbol_select(symbol, True)
            rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df = df.rename(columns={"tick_volume": "volume"})
                return df[["time", "open", "high", "low", "close", "volume"]]

        return self._generate_simulated_candles(symbol, timeframe, count)

    def _generate_simulated_candles(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}"
        minutes = self.TIMEFRAME_MAP.get(timeframe, 1)
        base_price = self.BASE_PRICES.get(symbol.upper(), 100.0)
        volatility = base_price * 0.0012

        now = datetime.now().replace(second=0, microsecond=0)
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

    def execute_order(
        self,
        symbol: str,
        action: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        if self.connected and not self.simulation_mode and MT5_AVAILABLE:
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return {"success": False, "message": f"Símbolo {symbol} não encontrado no MT5."}

            order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
            price = tick.ask if action == "BUY" else tick.bid

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume),
                "type": order_type,
                "price": price,
                "deviation": 20,
                "magic": 202610,
                "comment": "RoboTraderPro MT5",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }
            if stop_loss:
                request["sl"] = float(stop_loss)
            if take_profit:
                request["tp"] = float(take_profit)

            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {
                    "success": True,
                    "ticket": result.order,
                    "price": result.price,
                    "mode": "LIVE_MT5",
                    "message": f"Ordem {action} executada no MT5 ({symbol}) @ {result.price}",
                }
            code_msg = result.comment if result else "Sem resposta do terminal"
            return {"success": False, "message": f"Falha na ordem MT5: {code_msg}"}

        # Execução em Modo Simulador
        df = self.get_candles(symbol, "M1", 5)
        exec_price = float(df["close"].iloc[-1])
        ticket_id = random.randint(100000, 999999)
        return {
            "success": True,
            "ticket": ticket_id,
            "price": round(exec_price, 5),
            "mode": "SIMULATION_MT5",
            "message": f"[SIMULAÇÃO MT5] Ordem {action} ({volume} lote) executada em {symbol} @ {exec_price:.5f}",
        }
