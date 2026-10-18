"""
Orquestrador Central (BotManager) e Motor Inteligente de Gestão de Banca & Martingale IA (v4.5).
Destaques:
1. Gestão Inteligente de Banca (Smart AI Bankroll):
   - Piso mínimo obrigatório de R$ 5,00 na Safirion.
   - Escalonamento dinâmico (1x, 2x, 3x, 4x) baseado em Critério de Kelly Fracionado + Soros + Confluência da IA (RandomForest).
   - Martingale Seletivo IA: calcula a recuperação exata da perda no campo "Invest (R$)" da Safirion somente quando o sinal tem alta probabilidade e respeita a trava de segurança da banca.
2. Aciona automaticamente o clique em "+ NOVA OPÇÃO" e reseta o campo "Invest (R$)" para o valor base assim que um ciclo de Gale é vencido.
"""

import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import math
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
            "min_confidence": 78.0,
            "entry_amount": 5.0,  # Mínimo R$ 5,00 na Safirion
            "bankroll_mode": "SMART_AI",  # "SMART_AI", "SOROS", "PERCENT", "FIXED"
            "expiration_minutes": 1,
            "stop_gain": 500.0,
            "stop_loss": 250.0,
            "use_martingale": True,
            "martingale_factor": 2.0,
            "max_martingale_steps": 2,
            "cooldown_seconds": 15,
            "strategy_params": {
                "ema_fast": 9,
                "ema_slow": 21,
                "rsi_period": 14,
                "rsi_oversold": 35,
                "rsi_overbought": 65,
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
            "win_streak": 0,
            "loss_streak": 0,
            "cycle_loss_accumulated": 0.0,
            "last_win_profit": 0.0,
            "next_suggested_amount": 5.0,
            "sizing_reason": "1x Base Analítica (R$ 5.00)",
            "open_trade_remaining_sec": 0,
            "status_reason": "Robô em espera (Pausado). Configure seu ativo e clique em LIGAR ROBÔ.",
        }

        self.trade_history: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, str]] = []
        self.last_signal: Optional[Dict[str, Any]] = None
        self._last_trade_timestamp: float = 0.0
        self._last_loss_timestamp: float = 0.0
        self._last_loss_action: Optional[str] = None
        self._has_open_trade: bool = False

        self.add_log(
            "INFO",
            "ProspeTraderBot v4.7 inicializado (Modo Analítico Rigoroso + Gestão Conservadora IA + Auto Nova Opção).",
        )

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
        if "custom_balance" in new_cfg:
            try:
                cb = float(new_cfg["custom_balance"])
                if cb >= 5.0:
                    self.safirion.balance = round(cb, 2)
            except Exception:
                pass

        for k, v in new_cfg.items():
            if k == "strategy_params" and isinstance(v, dict):
                self.config["strategy_params"].update(v)
            elif k in self.config:
                self.config[k] = v

        # Garante piso mínimo de R$ 5,00 na Safirion
        if self.config["market_type"] == "SAFIRION_OB":
            self.config["entry_amount"] = max(5.0, float(self.config.get("entry_amount", 5.0)))

        if self.config.get("timeframe") != old_tf:
            self.safirion.reset_candles()

        self.add_log(
            "CONFIG",
            f"Config: {self.config['market_type']} | {self.config['symbol']} ({self.config['timeframe']}) | Gestão: {self.config['bankroll_mode']} | Base: R$ {self.config['entry_amount']:.2f}",
        )
        return self.config

    def _calculate_smart_entry_amount(self, signal: Optional[Dict[str, Any]]) -> tuple[float, str]:
        """
        Motor Analítico e Conservador de Gestão de Banca:
        - Valor base padrão na Safirion: R$ 5,00 (ou o valor definido em entry_amount).
        - Quando em recuperação (Martingale): calcula a recuperação exata da perda do ciclo no campo "Invest",
          respeitando a trava máxima de proteção de banca.
        - Quando sem perdas pendentes (Gale 0): mantém a mão base segura de R$ 5,00 por padrão e só dobra/triplica/quadruplica
          quando a sessão está positiva (lucro acumulado > 0), com sequência real de vitórias (Win Streak >= 2) e alta confirmação da IA.
        """
        market_type = self.config["market_type"]
        base_amount = max(5.0, float(self.config.get("entry_amount", 5.0))) if market_type == "SAFIRION_OB" else float(self.config.get("entry_amount", 1.0))
        if market_type != "SAFIRION_OB":
            return base_amount, f"Lote MT5 ({base_amount})"

        banca = max(5.0, float(self.safirion.balance))
        mode = self.config.get("bankroll_mode", "SMART_AI")
        gale_step = int(self.stats.get("current_gale_step", 0))
        use_gale = bool(self.config.get("use_martingale", True))
        gale_factor = max(1.2, float(self.config.get("martingale_factor", 2.0)))
        payout_ratio = max(0.70, float(self.safirion.payout_pct) / 100.0)  # 0.89

        conf = float((signal or {}).get("confidence", 78.0))
        ind = (signal or {}).get("indicators") or {}
        action = (signal or {}).get("action", "HOLD")
        ml_prob = float(ind.get("ml_prob_up", 50.0) if action == "BUY" else ind.get("ml_prob_down", 50.0))

        # Limite máximo seguro por operação (não ultrapassa Stop Loss restante nem 25% da banca)
        remaining_sl = max(5.0, float(self.config.get("stop_loss", 250.0)) + float(self.stats.get("daily_pnl", 0.0)))
        max_safe_stake = max(5.0, min(banca * 0.25, remaining_sl))

        # 1. MODO RECUPERAÇÃO (MARTINGALE INTELIGENTE)
        if use_gale and gale_step > 0:
            loss_accum = float(self.stats.get("cycle_loss_accumulated", base_amount))
            by_factor = round(base_amount * (gale_factor ** gale_step))
            by_exact_recovery = math.ceil((loss_accum * 1.12) / payout_ratio)
            raw_gale_amount = float(max(by_factor, by_exact_recovery))

            # Se o Gale estourar a proteção de banca, aborta o Gale para proteger a conta!
            if raw_gale_amount > max_safe_stake or raw_gale_amount > banca:
                self.stats["current_gale_step"] = 0
                self.stats["cycle_loss_accumulated"] = 0.0
                return base_amount, f"Proteção de Banca (Gale abortado p/ preservar R$ {banca:.2f})"

            final_gale = max(5.0, float(round(raw_gale_amount)))
            return final_gale, f"Recuperação Gale {gale_step} (R$ {final_gale:.0f} p/ recuperar R$ {loss_accum:.0f})"

        # 2. MODO MÃO FIXA
        if mode == "FIXED":
            return base_amount, f"Mão Fixa (R$ {base_amount:.0f})"

        # 3. MODO SOROS NÍVEL 1 (Reinveste o lucro da vitória anterior)
        if mode == "SOROS":
            win_streak = int(self.stats.get("win_streak", 0))
            last_profit = float(self.stats.get("last_win_profit", 0.0))
            if win_streak == 1 and last_profit > 0:
                soros_amt = max(5.0, float(round(base_amount + last_profit)))
                if soros_amt <= max_safe_stake:
                    return soros_amt, f"Soros Nível 1 (Base R$ {base_amount:.0f} + Lucro R$ {last_profit:.0f} = R$ {soros_amt:.0f})"
            return base_amount, f"Mão Base Soros (R$ {base_amount:.0f})"

        # 4. MODO % DA BANCA (Juros Compostos)
        if mode == "PERCENT":
            pct_amt = max(5.0, float(round(banca * 0.025)))
            pct_amt = min(pct_amt, max_safe_stake)
            return pct_amt, f"2.5% da Banca (R$ {pct_amt:.0f} sobre R$ {banca:.2f})"

        # 5. MODO SMART_AI CONSERVADOR (Só aumenta a mão com lucro acumulado + sequência de vitórias + IA forte)
        win_streak = int(self.stats.get("win_streak", 0))
        daily_pnl = float(self.stats.get("daily_pnl", 0.0))

        smart_unit = base_amount
        mult = 1
        reason_label = f"1x Base Segura IA (R$ {smart_unit:.0f})"

        # Quadruplicar (4x): exige sessão no lucro, 4+ vitórias seguidas, Confiança >= 92% e IA >= 66%
        if (
            daily_pnl >= 15.0
            and win_streak >= 4
            and conf >= 92.0
            and ml_prob >= 66.0
            and (smart_unit * 4) <= banca * 0.12
            and (smart_unit * 4) <= max_safe_stake
        ):
            mult = 4
            reason_label = f"4x Quadruplicado IA (Streak {win_streak} + Confluência {conf:.0f}%)"

        # Triplicar (3x): exige sessão no lucro, 3+ vitórias seguidas, Confiança >= 89% e IA >= 63%
        elif (
            daily_pnl >= 10.0
            and win_streak >= 3
            and conf >= 89.0
            and ml_prob >= 63.0
            and (smart_unit * 3) <= banca * 0.10
            and (smart_unit * 3) <= max_safe_stake
        ):
            mult = 3
            reason_label = f"3x Triplicado IA (Streak {win_streak} + Confluência {conf:.0f}%)"

        # Dobrar (2x): exige sessão no lucro, 2+ vitórias seguidas, Confiança >= 86% e IA >= 60%
        elif (
            daily_pnl >= 4.0
            and win_streak >= 2
            and conf >= 86.0
            and ml_prob >= 60.0
            and (smart_unit * 2) <= banca * 0.08
            and (smart_unit * 2) <= max_safe_stake
        ):
            mult = 2
            reason_label = f"2x Dobrado IA (Streak {win_streak} + Confluência {conf:.0f}%)"

        final_amount = max(5.0, float(round(smart_unit * mult)))
        return final_amount, reason_label

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

        # Atualiza sugestão de lote em tempo real pela Gestão de Banca IA
        suggested_amt, sizing_reason = self._calculate_smart_entry_amount(self.last_signal)
        self.stats["next_suggested_amount"] = suggested_amt
        self.stats["sizing_reason"] = sizing_reason

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
                    "balance": round(float(self.safirion.balance), 2),
                },
            },
        }

    def _calculate_target_expiration_time(self) -> tuple[datetime, int]:
        from datetime import timedelta

        now = datetime.now()
        market_type = self.config["market_type"]
        sub_type = self.config.get("safirion_sub_type", "BINARY")
        exp_min = max(1, int(self.config.get("expiration_minutes", 1)))

        if market_type == "SAFIRION_OB" and sub_type == "BLITZ":
            target_dt = now + timedelta(seconds=15)
            return target_dt, 15

        next_minute_boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        seconds_to_next_min = (next_minute_boundary - now).total_seconds()

        if exp_min == 1:
            if seconds_to_next_min >= 30:
                target_dt = next_minute_boundary
            else:
                target_dt = next_minute_boundary + timedelta(minutes=1)
        else:
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

        # Calcula o valor inteligente da entrada (1x a 4x ou Recuperação de Martingale)
        trade_amount, sizing_reason = self._calculate_smart_entry_amount(signal)
        gale_step = int(self.stats.get("current_gale_step", 0))

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
            balance_before = float(self.safirion.balance)

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
                "sizing_reason": sizing_reason,
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
                f"[{origin_tag}] {res.get('message')} | Gestão: {sizing_reason} -> Expira às {target_time_str}",
            )

            asyncio.create_task(self._settle_trade_after_real_expiration(trade_record, target_dt))

        return res

    async def _settle_trade_after_real_expiration(self, trade_record: Dict[str, Any], target_dt: datetime):
        try:
            while True:
                now = datetime.now()
                rem = int(round((target_dt - now).total_seconds()))
                if rem <= 0:
                    break
                trade_record["remaining_sec"] = rem
                self.stats["open_trade_remaining_sec"] = rem
                if self.is_running:
                    self.stats["status_reason"] = (
                        f"Operação {trade_record['action']} de R$ {trade_record['amount']:.0f} (@ {trade_record['entry_price']:.5f}) "
                        f"— Fecha às {trade_record['expiration_time']} (faltam {rem}s)..."
                    )
                await asyncio.sleep(0.5)

            # Aguarda 1.2s após bater :00 para apurar o fechamento exato e acionar "+ NOVA OPÇÃO"
            await asyncio.sleep(1.2)

            if trade_record["market"] == "SAFIRION_OB":
                await self.safirion.unlock_new_option()

            trade_record["remaining_sec"] = 0
            self.stats["open_trade_remaining_sec"] = 0
            self._has_open_trade = False
            self._last_trade_timestamp = datetime.now().timestamp()

            if trade_record["status"] != "OPEN":
                return

            if trade_record["market"] == "SAFIRION_OB" and self.safirion.last_live_price is not None:
                close_price = float(self.safirion.last_live_price)
            else:
                df = self.mt5.get_candles(trade_record["symbol"], self.config["timeframe"], count=5)
                close_price = float(df["close"].iloc[-1])

            entry_price = float(trade_record["entry_price"])
            is_buy = trade_record["action"] in ("BUY", "CALL")

            bal_before = float(trade_record.get("balance_before", 0.0))
            bal_now = float(self.safirion.balance)
            if trade_record["market"] == "SAFIRION_OB" and bal_before > 0 and abs(bal_now - bal_before) >= 0.50:
                win = bal_now > bal_before
            else:
                win = (close_price > entry_price) if is_buy else (close_price < entry_price)

            ml_engine.record_trade_feedback(
                features=trade_record.get("ml_features", []),
                action=trade_record["action"],
                won=win,
            )

            if trade_record["market"] == "SAFIRION_OB":
                payout = self.safirion.payout_pct / 100.0  # 89%
                pnl = round(trade_record["amount"] * payout, 2) if win else -round(trade_record["amount"], 2)
                if abs(bal_now - bal_before) < 0.50:
                    self.safirion.balance = round(max(0.0, self.safirion.balance + pnl), 2)
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
                self.stats["win_streak"] = int(self.stats.get("win_streak", 0)) + 1
                self.stats["loss_streak"] = 0
                self.stats["current_gale_step"] = 0
                self.stats["cycle_loss_accumulated"] = 0.0
                self.stats["last_win_profit"] = pnl
                self._last_loss_action = None
                self._last_loss_timestamp = 0.0

                self.add_log(
                    "WIN",
                    f"GAIN REAL em {trade_record['symbol']} ({trade_record['action']})! R$ {trade_record['amount']:.0f} -> +R$ {pnl:.2f} | Banca: R$ {self.safirion.balance:.2f}",
                )
            else:
                self.stats["losses"] += 1
                self.stats["loss_streak"] = int(self.stats.get("loss_streak", 0)) + 1
                self.stats["win_streak"] = 0
                self.stats["last_win_profit"] = 0.0
                self._last_loss_action = "BUY" if trade_record["action"] in ("BUY", "CALL") else "SELL"
                self._last_loss_timestamp = datetime.now().timestamp()
                self.stats["cycle_loss_accumulated"] = round(
                    float(self.stats.get("cycle_loss_accumulated", 0.0)) + float(trade_record["amount"]), 2
                )

                if (
                    self.config["use_martingale"]
                    and self.stats["current_gale_step"] < int(self.config["max_martingale_steps"])
                ):
                    self.stats["current_gale_step"] += 1
                else:
                    self.stats["current_gale_step"] = 0
                    self.stats["cycle_loss_accumulated"] = 0.0

                self.add_log(
                    "LOSS",
                    f"LOSS REAL em {trade_record['symbol']} ({trade_record['action']})! -R$ {abs(pnl):.2f} | Banca: R$ {self.safirion.balance:.2f} (Próx Gale: {self.stats['current_gale_step']})",
                )

            # Pré-ajusta o campo "Invest (R$)" da Safirion imediatamente após abrir a Nova Opção,
            # para que no próximo sinal o clique em CALL/PUT seja instantâneo (0ms de atraso)!
            if trade_record["market"] == "SAFIRION_OB":
                next_amt, _ = self._calculate_smart_entry_amount(self.last_signal)
                await asyncio.sleep(0.55)
                await self.safirion.set_investment_amount(next_amt)

            total = self.stats["total_trades"]
            self.stats["win_rate"] = round((self.stats["wins"] / total) * 100.0, 1) if total > 0 else 0.0

            if self.stats["daily_pnl"] >= float(self.config["stop_gain"]):
                self.is_running = False
                self.stats["status_reason"] = f"META BATIDA! Stop Gain atingido (+R$ {self.stats['daily_pnl']:.2f}). Robô pausado."
                self.add_log("SUCCESS", self.stats["status_reason"])
            elif self.stats["daily_pnl"] <= -abs(float(self.config["stop_loss"])):
                self.is_running = False
                self.stats["status_reason"] = f"STOP LOSS ATINGIDO (R$ {self.stats['daily_pnl']:.2f}). Proteção de banca acionada."
                self.add_log("ALERT", self.stats["status_reason"])
        except Exception as exc:
            self.add_log("ERROR", f"Erro na liquidação da operação (destravando automático): {str(exc)}")
        finally:
            trade_record["remaining_sec"] = 0
            self.stats["open_trade_remaining_sec"] = 0
            self._has_open_trade = False

    async def run_loop(self):
        last_resync_attempt_ts = 0.0
        while True:
            try:
                now_dt = datetime.now()
                now_ts = now_dt.timestamp()

                # 1. WATCHDOG ANTI-TRAVAMENTO DE OPERAÇÃO:
                if self._has_open_trade:
                    max_open_sec = max(1, int(self.config.get("expiration_minutes", 1))) * 60 + 25
                    if (now_ts - self._last_trade_timestamp) > max_open_sec:
                        self._has_open_trade = False
                        self.stats["open_trade_remaining_sec"] = 0
                        self.add_log("INFO", "🔄 Watchdog destravou operação expirada automaticamente.")
                        if self.config["market_type"] == "SAFIRION_OB":
                            await self.safirion.unlock_new_option()

                # 2. ROTINA DE AUTO-SINCRONIZAÇÃO COM A SAFIRION:
                if (
                    self.config["market_type"] == "SAFIRION_OB"
                    and self.safirion.bridge_ws is not None
                    and self.safirion.last_tick_timestamp > 0
                    and (now_ts - self.safirion.last_tick_timestamp) >= 14.0
                    and (now_ts - last_resync_attempt_ts) >= 10.0
                ):
                    last_resync_attempt_ts = now_ts
                    await self.safirion.request_auto_resync()
                    self.add_log("SAFIRION", "🔄 Auto-Sync acionado para manter sincronia contínua com a Safirion.")

                snapshot = self.get_market_snapshot()
                signal = snapshot["signal"]

                if self.is_running and signal:
                    if self._has_open_trade:
                        await asyncio.sleep(0.6)
                        continue

                    # Cooldown enxuto (5s) para não atrasar a entrada na vela atual nem perder a janela de :30s!
                    effective_cooldown = 5.0
                    in_cooldown = (now_ts - self._last_trade_timestamp) < effective_cooldown

                    # Evita apenas os 4 segundos exatos da virada do relógio de compra da Safirion (:28s a :31s)
                    sub_type = self.config.get("safirion_sub_type", "BINARY")
                    in_rollover_boundary = (
                        self.config["market_type"] == "SAFIRION_OB"
                        and sub_type != "BLITZ"
                        and (28 <= now_dt.second <= 31)
                    )

                    if in_cooldown:
                        rem = max(1, int(effective_cooldown - (now_ts - self._last_trade_timestamp)))
                        self.stats["status_reason"] = f"Preparando próxima entrada ({rem}s)..."
                    elif in_rollover_boundary:
                        self.stats["status_reason"] = (
                            f"Sincronizando virada do relógio de expiração ({now_dt.second}s)..."
                        )
                    elif (
                        signal["action"] in ("BUY", "SELL")
                        and float(signal["confidence"]) >= float(self.config["min_confidence"])
                    ):
                        # Bloqueio Anti-Teimosia: se acabou de tomar LOSS na mesma direção há menos de 45s, exige confirmação >= 84%
                        same_dir_after_loss = (
                            self._last_loss_action == signal["action"]
                            and (now_ts - self._last_loss_timestamp) < 45.0
                        )
                        if same_dir_after_loss and float(signal["confidence"]) < 84.0:
                            self.stats["status_reason"] = (
                                f"🛡️ [PROTEÇÃO ANTI-SEQ] Evitando repetir {signal['action']} após LOSS sem confirmação forte (atual: {signal['confidence']}%)..."
                            )
                        elif self.stats["current_gale_step"] > 0 and float(signal["confidence"]) < max(79.0, float(self.config["min_confidence"])):
                            self.stats["status_reason"] = (
                                f"Gale {self.stats['current_gale_step']} aguardando gatilho analítico >= 79% (atual: {signal['confidence']}%)..."
                            )
                        else:
                            self.stats["status_reason"] = (
                                f"⚡ Entrada Imediata ({signal['action']} - {signal['confidence']}%) | {self.stats['sizing_reason']}!"
                            )
                            await self.execute_trade_from_signal(signal, manual=False)
                    else:
                        self.stats["status_reason"] = f"Monitorando {self.config['symbol']} ({self.config['timeframe']}): {signal['reason']}"
            except Exception as exc:
                self.add_log("ERROR", f"Erro no ciclo de análise: {str(exc)}")

            await asyncio.sleep(0.6)
