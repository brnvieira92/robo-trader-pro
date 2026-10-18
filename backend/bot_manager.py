"""
Orquestrador Central (BotManager) e Gerenciador de Risco (v3.0).
Correções fundamentais:
1. Aguarda o TEMPO REAL DE EXPIRAÇÃO da operação (ex: 60s para 1 minuto, ou até o fechamento real da opção)
   antes de apurar WIN ou LOSS!
2. Bloqueia novas entradas automáticas enquanto houver uma operação EM ABERTO (evita empilhar ordens
   ou calcular Martingale antes da operação anterior terminar!).
3. Apura o resultado (WIN/LOSS) comparando o preço real de fechamento da Safirion no segundo exato do vencimento!
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import random

from backend.strategies import StrategyEngine, SignalResult
from backend.mt5_connector import MT5Connector
from backend.safirion_connector import SafirionConnector
from backend.ml_engine import ml_engine


class BotManager:
    def __init__(self):
        self.mt5 = MT5Connector()
        self.safirion = SafirionConnector()

        self.mt5.connect(use_simulation_if_unavailable=True)

        self.is_running = False
        self.config: Dict[str, Any] = {
            "market_type": "SAFIRION_OB",  # "SAFIRION_OB", "MT5_B3", "MT5_FOREX"
            "safirion_sub_type": "BINARY",  # "BINARY", "DIGITAL", "BLITZ"
            "symbol": "EURUSD",
            "timeframe": "M1",
            "strategy_id": "bollinger_rsi_reversal",
            "min_confidence": 75.0,
            "entry_amount": 5.0,
            "expiration_minutes": 1,
            "stop_gain": 500.0,
            "stop_loss": 250.0,
            "use_martingale": False,
            "martingale_factor": 2.0,
            "max_martingale_steps": 1,
            "cooldown_seconds": 10,
            "strategy_params": {
                "ema_fast": 9,
                "ema_slow": 21,
                "rsi_period": 14,
                "rsi_oversold": 38,
                "rsi_overbought": 62,
                "bb_period": 20,
                "bb_std": 2.0,
                "lookback_candles": 10,
                "sl_atr_mult": 1.5,
                "tp_atr_mult": 2.5,
            },
        }

        self.stats: Dict[str, Any] = {
            "daily_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "total_trades": 0,
            "win_rate": 0.0,
            "current_gale_step": 0,
            "open_trade_remaining_sec": 0,
            "status_reason": "Robô em espera (Pausado). Configure seu ativo e clique em LIGAR ROBÔ.",
        }

        self.trade_history: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, str]] = []
        self.last_signal: Optional[Dict[str, Any]] = None
        self._last_trade_timestamp: float = 0.0
        self._has_open_trade: bool = False

        self.add_log("INFO", "Sistema RoboTrader Pro v3.0 inicializado (Expiração Real + Filtro de Gráfico Ativo).")

    def add_log(self, level: str, message: str):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.insert(0, entry)
        self.logs = self.logs[:80]

    def update_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        old_tf = self.config.get("timeframe")
        for k, v in new_cfg.items():
            if k == "strategy_params" and isinstance(v, dict):
                self.config["strategy_params"].update(v)
            elif k in self.config:
                self.config[k] = v
        if self.config.get("timeframe") != old_tf:
            self.safirion.reset_candles()
        self.add_log(
            "CONFIG",
            f"Configuração: {self.config['market_type']} | {self.config['symbol']} ({self.config['timeframe']}) | Exp: {self.config['expiration_minutes']}m",
        )
        return self.config

    def get_market_snapshot(self) -> Dict[str, Any]:
        symbol = self.config["symbol"]
        timeframe = self.config["timeframe"]
        market_type = self.config["market_type"]
        strategy_id = self.config["strategy_id"]

        saf_live_df = (
            self.safirion.get_live_candles_df(timeframe)
            if market_type == "SAFIRION_OB"
            else None
        )
        if saf_live_df is not None and len(saf_live_df) >= 35:
            df = saf_live_df
        else:
            df = self.mt5.get_candles(symbol, timeframe, count=90)

        enriched = StrategyEngine.enrich_dataframe(df, self.config["strategy_params"])
        signal: SignalResult = StrategyEngine.analyze(
            df=df,
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
            strategy_id=strategy_id,
            params={
                **self.config["strategy_params"],
                "expiration_minutes": self.config["expiration_minutes"],
            },
        )
        self.last_signal = signal.to_dict()

        candles_out = []
        for _, row in enriched.tail(60).iterrows():
            candles_out.append(
                {
                    "time": int(row["time"].timestamp()),
                    "time_str": row["time"].strftime("%H:%M"),
                    "open": round(float(row["open"]), 5),
                    "high": round(float(row["high"]), 5),
                    "low": round(float(row["low"]), 5),
                    "close": round(float(row["close"]), 5),
                    "ema_fast": round(float(row["ema_fast"]), 5),
                    "ema_slow": round(float(row["ema_slow"]), 5),
                    "bb_upper": round(float(row["bb_upper"]), 5),
                    "bb_lower": round(float(row["bb_lower"]), 5),
                    "rsi": round(float(row["rsi"]), 2),
                }
            )

        return {
            "is_running": self.is_running,
            "config": self.config,
            "stats": self.stats,
            "signal": self.last_signal,
            "candles": candles_out,
            "trades": self.trade_history[:25],
            "logs": self.logs[:30],
            "connections": {
                "mt5": {
                    "connected": self.mt5.connected,
                    "simulation_mode": self.mt5.simulation_mode,
                    "account": self.mt5.account_info,
                },
                "safirion": {
                    "connected": self.safirion.connected,
                    "simulation_mode": self.safirion.simulation_mode,
                    "broker_url": self.safirion.broker_url,
                    "account_type": self.safirion.account_type,
                    "sub_type": self.config.get("safirion_sub_type", self.safirion.sub_type),
                    "call_bound": self.safirion.call_bound,
                    "put_bound": self.safirion.put_bound,
                    "ws_messages_received": self.safirion.ws_messages_received,
                    "last_live_price": self.safirion.last_live_price,
                    "balance": self.safirion.balance,
                },
            },
        }

    def _calculate_target_expiration_time(self) -> tuple[datetime, int]:
        """
        Calcula o horário exato do relógio (HH:MM:00) em que a opção expira na Safirion:
        - Se for BLITZ: expira em exatos 15 segundos a partir do horário atual.
        - Se for BINÁRIAS / DIGITAL (ex: 1 min):
          Na corretora, a expiração fecha exatamente no segundo :00 do minuto de expiração
          (ex: entrou às 20:37:15 -> fecha às 20:38:00).
          Se entrar nos últimos 30s do minuto (após :30, no dead-time de compra da vela),
          ou se exp_min > 1, respeita a virada do minuto correspondente.
        """
        from datetime import timedelta

        now = datetime.now()
        market_type = self.config["market_type"]
        sub_type = self.config.get("safirion_sub_type", "BINARY")
        exp_min = max(1, int(self.config.get("expiration_minutes", 1)))

        if market_type == "SAFIRION_OB" and sub_type == "BLITZ":
            target_dt = now + timedelta(seconds=15)
            return target_dt, 15

        # Próxima virada de minuto exata (:00)
        next_minute_boundary = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
        seconds_to_next_min = (next_minute_boundary - now).total_seconds()

        if exp_min == 1:
            # Se ainda faltam pelo menos 30s para virar o minuto, fecha na virada deste minuto (ex: 20:37:12 -> 20:38:00)
            # Se já passou de :30 (menos de 30s restantes), a Safirion joga para o fechamento do minuto seguinte
            if seconds_to_next_min >= 30:
                target_dt = next_minute_boundary
            else:
                target_dt = next_minute_boundary + timedelta(minutes=1)
        else:
            # Para M5 / 5 minutos ou mais, alinha ao fechamento do bloco de minutos no relógio
            target_dt = now.replace(second=0, microsecond=0) + timedelta(minutes=exp_min)

        remaining_sec = max(5, int(round((target_dt - now).total_seconds())))
        return target_dt, remaining_sec

    async def execute_trade_from_signal(self, signal: Dict[str, Any], manual: bool = False) -> Dict[str, Any]:
        action = signal["action"]
        if action not in ("BUY", "SELL"):
            return {"success": False, "message": "Sinal neutro, nenhuma ordem enviada."}

        if self._has_open_trade and not manual:
            return {"success": False, "message": "Já existe uma operação em andamento. Aguardando fechamento."}

        symbol = self.config["symbol"]
        market_type = self.config["market_type"]
        base_amount = float(self.config["entry_amount"])

        # Aplica fator de Martingale SOMENTE após o resultado real da operação anterior
        gale_step = self.stats["current_gale_step"]
        if self.config["use_martingale"] and gale_step > 0:
            trade_amount = round(base_amount * (float(self.config["martingale_factor"]) ** gale_step), 2)
        else:
            trade_amount = base_amount

        real_entry_price = (
            float(self.safirion.last_live_price)
            if (market_type == "SAFIRION_OB" and self.safirion.last_live_price is not None)
            else float(signal["price"])
        )

        if market_type == "SAFIRION_OB":
            res = await self.safirion.execute_binary_order(
                symbol=symbol,
                direction=action,
                amount=trade_amount,
                expiration_minutes=int(self.config["expiration_minutes"]),
                entry_price=real_entry_price,
                sub_type=self.config.get("safirion_sub_type", "BINARY"),
            )
        else:
            res = self.mt5.execute_order(
                symbol=symbol,
                action=action,
                volume=trade_amount,
                stop_loss=signal.get("stop_loss"),
                take_profit=signal.get("take_profit"),
            )

        if res.get("success"):
            target_dt, duration_sec = self._calculate_target_expiration_time()
            target_time_str = target_dt.strftime("%H:%M:%S")
            self._has_open_trade = True
            self._last_trade_timestamp = datetime.now().timestamp()
            balance_before = self.safirion.balance

            trade_record = {
                "id": res.get("ticket", f"TRD-{random.randint(1000,9999)}"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "expiration_time": target_time_str,
                "symbol": symbol,
                "market": market_type,
                "action": "CALL" if (market_type == "SAFIRION_OB" and action == "BUY") else (
                    "PUT" if (market_type == "SAFIRION_OB" and action == "SELL") else action
                ),
                "amount": trade_amount,
                "entry_price": round(real_entry_price, 5),
                "confidence": signal["confidence"],
                "strategy": self.config["strategy_id"],
                "status": "OPEN",
                "remaining_sec": duration_sec,
                "pnl": 0.0,
                "gale_step": gale_step,
                "balance_before": balance_before,
                "ml_features": (signal.get("indicators") or {}).get("ml_features", []),
            }
            self.trade_history.insert(0, trade_record)
            origin_tag = "MANUAL" if manual else "AUTO"
            self.add_log(
                "TRADE",
                f"[{origin_tag}] {res.get('message')} -> Expira às {target_time_str} ({duration_sec}s)",
            )

            asyncio.create_task(self._settle_trade_after_real_expiration(trade_record, target_dt))

        return res

    async def _settle_trade_after_real_expiration(self, trade_record: Dict[str, Any], target_dt: datetime):
        # Monitora o relógio em tempo real até bater exatamente o horário de expiração (ex: 20:38:00)!
        while True:
            now = datetime.now()
            rem = int(round((target_dt - now).total_seconds()))
            if rem <= 0:
                break
            trade_record["remaining_sec"] = rem
            self.stats["open_trade_remaining_sec"] = rem
            if self.is_running:
                self.stats["status_reason"] = (
                    f"Operação {trade_record['action']} aberta (@ {trade_record['entry_price']:.5f}) "
                    f"— Fecha às {trade_record['expiration_time']} (faltam {rem}s)..."
                )
            await asyncio.sleep(0.5)

        # Aguarda 1.2s após bater :00 para a Safirion atualizar o saldo no topo direito ("R$ 143.24")
        await asyncio.sleep(1.2)

        # Aciona imediatamente o auto-clique no botão "+ NOVA OPÇÃO" na guia da Safirion (mesmo minimizada)
        if trade_record["market"] == "SAFIRION_OB":
            await self.safirion.unlock_new_option()

        trade_record["remaining_sec"] = 0
        self.stats["open_trade_remaining_sec"] = 0
        self._has_open_trade = False
        self._last_trade_timestamp = datetime.now().timestamp()

        if trade_record["status"] != "OPEN":
            return

        # Obtém o preço de fechamento REAL da Safirion (ou MT5) no segundo exato do vencimento
        if trade_record["market"] == "SAFIRION_OB" and self.safirion.last_live_price is not None:
            close_price = float(self.safirion.last_live_price)
        else:
            df = self.mt5.get_candles(trade_record["symbol"], self.config["timeframe"], count=5)
            close_price = float(df["close"].iloc[-1])

        entry_price = float(trade_record["entry_price"])
        is_buy = trade_record["action"] in ("BUY", "CALL")

        # AUDITORIA REAL DE WIN / LOSS:
        bal_before = float(trade_record.get("balance_before", 0.0))
        bal_now = float(self.safirion.balance)
        if trade_record["market"] == "SAFIRION_OB" and bal_before > 0 and abs(bal_now - bal_before) >= 0.50:
            win = bal_now > bal_before
        else:
            win = (close_price > entry_price) if is_buy else (close_price < entry_price)

        # Alimenta a memória de reforço do Machine Learning com o resultado real desta operação!
        ml_engine.record_trade_feedback(
            features=trade_record.get("ml_features", []),
            action=trade_record["action"],
            won=win,
        )

        if trade_record["market"] == "SAFIRION_OB":
            payout = self.safirion.payout_pct / 100.0  # 89% real da Safirion
            pnl = round(trade_record["amount"] * payout, 2) if win else -round(trade_record["amount"], 2)
        else:
            base_val = max(25.0, trade_record["amount"] * 40.0)
            pnl = round(base_val * 1.4, 2) if win else -round(base_val, 2)

        trade_record["status"] = "WIN" if win else "LOSS"
        trade_record["exit_price"] = round(close_price, 5)
        trade_record["pnl"] = pnl

        self.stats["total_trades"] += 1
        self.stats["daily_pnl"] = round(self.stats["daily_pnl"] + pnl, 2)

        if win:
            self.stats["wins"] += 1
            self.stats["current_gale_step"] = 0
            self.add_log(
                "WIN",
                f"GAIN REAL em {trade_record['symbol']} ({trade_record['action']})! Entrada: {entry_price:.5f} -> Fechamento: {close_price:.5f} | +R$ {pnl:.2f}",
            )
        else:
            self.stats["losses"] += 1
            if (
                self.config["use_martingale"]
                and self.stats["current_gale_step"] < int(self.config["max_martingale_steps"])
            ):
                self.stats["current_gale_step"] += 1
            else:
                self.stats["current_gale_step"] = 0
            self.add_log(
                "LOSS",
                f"LOSS REAL em {trade_record['symbol']} ({trade_record['action']})! Entrada: {entry_price:.5f} -> Fechamento: {close_price:.5f} | -R$ {abs(pnl):.2f}",
            )

        total = self.stats["total_trades"]
        self.stats["win_rate"] = round((self.stats["wins"] / total) * 100.0, 1) if total > 0 else 0.0

        # Verifica Stop Gain e Stop Loss Diário
        if self.stats["daily_pnl"] >= float(self.config["stop_gain"]):
            self.is_running = False
            self.stats["status_reason"] = f"META BATIDA! Stop Gain diário atingido (+R$ {self.stats['daily_pnl']:.2f}). Robô pausado."
            self.add_log("SUCCESS", self.stats["status_reason"])
        elif self.stats["daily_pnl"] <= -abs(float(self.config["stop_loss"])):
            self.is_running = False
            self.stats["status_reason"] = f"STOP LOSS DIÁRIO ATINGIDO (R$ {self.stats['daily_pnl']:.2f}). Proteção de banca acionada."
            self.add_log("ALERT", self.stats["status_reason"])

    async def run_loop(self):
        while True:
            try:
                snapshot = self.get_market_snapshot()
                signal = snapshot["signal"]

                if self.is_running and signal:
                    # Se há uma operação aberta esperando expirar, NÃO envia outra por cima!
                    if self._has_open_trade:
                        await asyncio.sleep(1.5)
                        continue

                    now_ts = datetime.now().timestamp()
                    cooldown = float(self.config.get("cooldown_seconds", 10))
                    in_cooldown = (now_ts - self._last_trade_timestamp) < cooldown

                    if in_cooldown:
                        rem = int(cooldown - (now_ts - self._last_trade_timestamp))
                        self.stats["status_reason"] = f"Aguardando cooldown pós-fechamento ({rem}s restantes)..."
                    elif (
                        signal["action"] in ("BUY", "SELL")
                        and float(signal["confidence"]) >= float(self.config["min_confidence"])
                    ):
                        self.stats["status_reason"] = f"Gatilho confirmado ({signal['action']} - {signal['confidence']}%)! Disparando ordem..."
                        await self.execute_trade_from_signal(signal, manual=False)
                    else:
                        self.stats["status_reason"] = f"Monitorando {self.config['symbol']} ({self.config['timeframe']}): {signal['reason']}"
            except Exception as exc:
                self.add_log("ERROR", f"Erro no ciclo de análise: {str(exc)}")

            await asyncio.sleep(1.5)
