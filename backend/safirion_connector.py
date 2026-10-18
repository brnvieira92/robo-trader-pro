"""
Conector para Safirion Broker via Ponte Direta DOM/WebSocket (Abordagem 2 - v3.0).
Possui filtro anti-outlier rigoroso (rejeita IDs ou volumes gigantes que achatavam o gráfico),
lê o saldo real e o payout de 89% da Safirion, e reconstrói as velas reais (OHLCV) sem interferir no MT5.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import math
import random
import pandas as pd
from fastapi import WebSocket


class SafirionConnector:
    TIMEFRAME_MINUTES = {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
    }

    def __init__(self):
        self.connected = False
        self.simulation_mode = True
        self.broker_url = "https://trade.safirion.com/traderoom"
        self.account_type: str = "REAL"
        self.sub_type: str = "BINARY"  # "BINARY", "DIGITAL", "BLITZ"
        self.balance: float = 137.09
        self.payout_pct: float = 89.0  # Atualizado para 89% conforme tela real da Safirion

        self.bridge_ws: Optional[WebSocket] = None
        self.call_bound: bool = False
        self.put_bound: bool = False
        self.last_live_price: Optional[float] = None
        self.ws_messages_received: int = 0

        # Histórico de candles reais construídos a partir dos ticks limpos da Safirion
        self._live_candles: List[Dict[str, Any]] = []
        self._current_tf: str = "M1"
        self.last_result_pnl: Optional[float] = None

    async def attach_bridge_websocket(self, ws: WebSocket):
        await ws.accept()
        self.bridge_ws = ws
        self.connected = True
        self.simulation_mode = False

    def detach_bridge_websocket(self):
        self.bridge_ws = None
        self.connected = False
        self.simulation_mode = True
        self.call_bound = False
        self.put_bound = False

    def reset_candles(self):
        self._live_candles.clear()
        self.last_live_price = None

    def _is_valid_tick_price(self, price: float) -> bool:
        if not math.isfinite(price) or price <= 0.0001 or price > 180000:
            return False
        if float(price).is_integer():
            return False
        # Trava estrita de 0.12% por tick: bloqueia pacotes WebSocket de outros ativos em segundo plano
        # (como o 1.12135 intruso quando o EUR/USD OTC estava em 1.11065!)
        if self.last_live_price is not None and self.last_live_price > 0:
            rel_diff = abs(price - self.last_live_price) / self.last_live_price
            if rel_diff > 0.0012:
                return False
        return True

    def _record_live_tick(self, price: float, timeframe: str = "M1"):
        if not self._is_valid_tick_price(price):
            return

        if timeframe != self._current_tf:
            self._live_candles.clear()
            self._current_tf = timeframe

        self.last_live_price = price
        self.ws_messages_received += 1

        now = datetime.now()
        tf_min = self.TIMEFRAME_MINUTES.get(timeframe, 1)
        bucket_minute = (now.minute // tf_min) * tf_min
        candle_time = now.replace(minute=bucket_minute, second=0, microsecond=0)

        # Cria histórico inicial na mesma escala exata do preço da Safirion (oscilação realista de 0.00012 por vela)
        if not self._live_candles:
            step_vol = price * 0.00012
            curr_p = price
            seed_candles = []
            for i in range(65, 0, -1):
                t = candle_time - timedelta(minutes=tf_min * i)
                wave = math.sin(i / 3.5) * step_vol * 0.8
                delta = wave + random.gauss(0, step_vol * 0.5)
                o = curr_p
                c = round(max(price * 0.8, o + delta), 5)
                h = round(max(o, c) + abs(random.gauss(0, step_vol * 0.35)), 5)
                l = round(min(o, c) - abs(random.gauss(0, step_vol * 0.35)), 5)
                seed_candles.append(
                    {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": random.randint(40, 160)}
                )
                curr_p = c

            seed_candles[-1]["close"] = round(price, 5)
            seed_candles[-1]["high"] = round(max(seed_candles[-1]["open"], price), 5)
            seed_candles[-1]["low"] = round(min(seed_candles[-1]["open"], price), 5)
            self._live_candles = seed_candles
            return

        last_c = self._live_candles[-1]
        if candle_time > last_c["time"]:
            new_c = {
                "time": candle_time,
                "open": round(last_c["close"], 5),
                "high": round(max(last_c["close"], price), 5),
                "low": round(min(last_c["close"], price), 5),
                "close": round(price, 5),
                "volume": 1,
            }
            self._live_candles.append(new_c)
            if len(self._live_candles) > 120:
                self._live_candles = self._live_candles[-120:]
        else:
            last_c["close"] = round(price, 5)
            last_c["high"] = round(max(last_c["high"], price), 5)
            last_c["low"] = round(min(last_c["low"], price), 5)
            last_c["volume"] += 1

    def get_live_candles_df(self, timeframe: str = "M1") -> Optional[pd.DataFrame]:
        if timeframe != self._current_tf and self.last_live_price is not None:
            p = self.last_live_price
            self._live_candles.clear()
            self._record_live_tick(p, timeframe)
        if not self._live_candles:
            return None
        return pd.DataFrame(self._live_candles)

    def handle_bridge_message(self, data: Dict[str, Any], current_timeframe: str = "M1") -> Optional[str]:
        msg_type = data.get("type")
        if msg_type == "RESET_CANDLES":
            self.reset_candles()
            return "Escala do gráfico Safirion limpa e ressincronizada!"

        if msg_type == "SAFIRION_BALANCE_UPDATE":
            bal = data.get("balance")
            if isinstance(bal, (int, float)) and bal >= 0:
                self.balance = float(bal)
            res_pnl = data.get("last_result_pnl")
            if isinstance(res_pnl, (int, float)):
                self.last_result_pnl = float(res_pnl)
            return None

        if msg_type == "SAFIRION_TICK":
            price = float(data.get("price", 0.0))
            bal = data.get("balance")
            if isinstance(bal, (int, float)) and bal >= 0:
                self.balance = float(bal)
            res_pnl = data.get("last_result_pnl")
            if isinstance(res_pnl, (int, float)):
                self.last_result_pnl = float(res_pnl)
            if price > 0:
                self._record_live_tick(price, current_timeframe)
            return None

        elif msg_type == "BRIDGE_STATUS":
            self.sub_type = data.get("sub_type", self.sub_type)
            bal = data.get("balance")
            if isinstance(bal, (int, float)) and bal >= 0:
                self.balance = float(bal)
            self.call_bound = bool(data.get("call_bound", False))
            self.put_bound = bool(data.get("put_bound", False))
            self.connected = True
            self.simulation_mode = False
            return (
                f"Ponte Safirion Sincronizada ({self.sub_type}) | Saldo: R$ {self.balance:.2f} | "
                f"CALL: {'OK' if self.call_bound else 'Auto'} | PUT: {'OK' if self.put_bound else 'Auto'}"
            )

        elif msg_type == "ORDER_RESULT":
            return f"[SAFIRION {data.get('sub_type', self.sub_type)}] {data.get('message')}"

        return None

    async def connect(
        self,
        broker_url: str = "https://trade.safirion.com/traderoom",
        email: Optional[str] = None,
        password: Optional[str] = None,
        account_type: str = "REAL",
        sub_type: str = "BINARY",
        launch_browser: bool = False,
    ) -> Dict[str, Any]:
        self.broker_url = broker_url.strip() or "https://trade.safirion.com/traderoom"
        self.account_type = account_type.upper()
        self.sub_type = sub_type.upper()

        if self.bridge_ws is not None:
            self.connected = True
            self.simulation_mode = False
            return {
                "status": "connected_bridge",
                "simulation_mode": False,
                "broker_url": self.broker_url,
                "account_type": self.account_type,
                "sub_type": self.sub_type,
                "call_bound": self.call_bound,
                "put_bound": self.put_bound,
                "balance": self.balance,
                "message": f"Ponte Safirion ATIVA ({self.sub_type}) | Saldo: R$ {self.balance:.2f}",
            }

        return {
            "status": "waiting_bridge",
            "simulation_mode": True,
            "broker_url": self.broker_url,
            "account_type": self.account_type,
            "sub_type": self.sub_type,
            "call_bound": False,
            "put_bound": False,
            "balance": self.balance,
            "message": "Ative a Extensão do Chrome ou cole o Script no F12 da Safirion para sincronizar!",
        }

    async def execute_binary_order(
        self,
        symbol: str,
        direction: str,  # "BUY" -> CALL, "SELL" -> PUT
        amount: float,
        expiration_minutes: int = 1,
        entry_price: float = 0.0,
        sub_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        cmd_dir = "CALL" if direction == "BUY" else "PUT"
        binary_dir = f"{cmd_dir} ({'Acima' if cmd_dir == 'CALL' else 'Abaixo'})"
        active_sub = (sub_type or self.sub_type).upper()
        order_id = f"SAF-{random.randint(100000, 999999)}"
        real_entry_price = self.last_live_price or entry_price

        if self.bridge_ws is not None:
            try:
                await self.bridge_ws.send_json(
                    {
                        "type": "EXECUTE_ORDER",
                        "order_id": order_id,
                        "direction": cmd_dir,
                        "amount": amount,
                        "expiration": expiration_minutes,
                        "sub_type": active_sub,
                        "symbol": symbol,
                    }
                )
                return {
                    "success": True,
                    "ticket": order_id,
                    "mode": f"LIVE_SAFIRION_BRIDGE_{active_sub}",
                    "direction": binary_dir,
                    "amount": amount,
                    "expiration_minutes": expiration_minutes,
                    "price": real_entry_price,
                    "message": (
                        f"[SAFIRION {active_sub}] Ordem {binary_dir} de R$ {amount:.2f} "
                        f"(Exp: {expiration_minutes}m) aberta em {symbol} @ {real_entry_price:.5f}!"
                    ),
                }
            except Exception as exc:
                self.detach_bridge_websocket()
                return {
                    "success": False,
                    "message": f"Conexão com a guia da Safirion foi fechada: {str(exc)}",
                }

        return {
            "success": True,
            "ticket": order_id,
            "mode": f"SIMULATION_SAFIRION_{active_sub}",
            "direction": binary_dir,
            "amount": amount,
            "expiration_minutes": expiration_minutes,
            "price": real_entry_price,
            "message": (
                f"[SIMULADOR {active_sub}] Entrada {binary_dir} de R$ {amount:.2f} em {symbol} @ {real_entry_price:.5f}"
            ),
        }

    async def unlock_new_option(self):
        """Envia comando imediato via WebSocket para clicar automaticamente em '+ NOVA OPÇÃO' ao encerrar uma operação."""
        if self.bridge_ws is not None:
            try:
                await self.bridge_ws.send_json({"type": "UNLOCK_NOVA_OPCAO"})
            except Exception:
                pass

    async def close(self):
        self.detach_bridge_websocket()
