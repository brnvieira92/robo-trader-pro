"""
Motor de Indicadores Técnicos e Estratégias para Forex, Mini Índice/Dólar (B3) e Opções Binárias.
Implementa as 4 estratégias selecionadas:
1. Cruzamento de Médias Móveis (EMA) + Filtro de RSI (IFR)
2. Bandas de Bollinger + RSI (Reversão à Média / Exaustão)
3. Price Action / Rompimento de Máxima e Mínima com Filtro de Volume
4. Confluência Multi-Indicadores (EMA + RSI + MACD)
"""

from dataclasses import dataclass, asdict
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd
from backend.ml_engine import ml_engine
from backend.market_data import validate_candles
from backend.fibonacci import fibonacci_context


@dataclass
class SignalResult:
    symbol: str
    market_type: str  # "MT5_B3", "MT5_FOREX", "SAFIRION_OB"
    timeframe: str
    strategy_id: str
    action: str  # "BUY" (CALL), "SELL" (PUT), "NEUTRAL"
    confidence: float  # 0 to 100
    price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    expiration_minutes: Optional[int]
    indicators: Dict[str, Any]
    reason: str
    diagnostic: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StrategyEngine:
    """Calcula indicadores técnicos usando pandas/numpy puro (alta velocidade e zero dependências externas C++)."""

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(window=period).mean()

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0).fillna(50.0)

    @staticmethod
    def calculate_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
        sma = series.rolling(window=period, min_periods=1).mean()
        std = series.rolling(window=period, min_periods=1).std().fillna(0.0)
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return upper, sma, lower

    @staticmethod
    def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = StrategyEngine.calculate_ema(series, fast)
        ema_slow = StrategyEngine.calculate_ema(series, slow)
        macd_line = ema_fast - ema_slow
        signal_line = StrategyEngine.calculate_ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period, min_periods=1).mean().fillna(0.0)

    @classmethod
    def enrich_dataframe(cls, df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
        df = df.copy()
        ema_fast_p = int(params.get("ema_fast", 9))
        ema_slow_p = int(params.get("ema_slow", 21))
        rsi_p = int(params.get("rsi_period", 14))
        bb_p = int(params.get("bb_period", 20))
        bb_std = float(params.get("bb_std", 2.0))

        df["ema_fast"] = cls.calculate_ema(df["close"], ema_fast_p)
        df["ema_slow"] = cls.calculate_ema(df["close"], ema_slow_p)
        df["rsi"] = cls.calculate_rsi(df["close"], rsi_p)
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = cls.calculate_bollinger_bands(
            df["close"], bb_p, bb_std
        )
        df["macd"], df["macd_signal"], df["macd_hist"] = cls.calculate_macd(df["close"])
        df["atr"] = cls.calculate_atr(df, 14)
        df["vol_sma"] = df["volume"].rolling(window=20, min_periods=1).mean().fillna(1.0)
        df = df.replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)
        return df

    @classmethod
    def analyze(
        cls,
        df: pd.DataFrame,
        symbol: str,
        market_type: str,
        timeframe: str,
        strategy_id: str,
        params: Optional[Dict[str, Any]] = None,
        model_engine=None,
        model_context=None,
    ) -> SignalResult:
        params = params or {}
        validate_candles(df)
        if len(df) < 50:
            return SignalResult(
                symbol=symbol,
                market_type=market_type,
                timeframe=timeframe,
                strategy_id=strategy_id,
                action="NEUTRAL",
                confidence=0.0,
                price=float(df["close"].iloc[-1]) if not df.empty else 0.0,
                stop_loss=None,
                take_profit=None,
                expiration_minutes=None,
                indicators={},
                reason="Aguardando histórico suficiente de candles (mínimo 50 velas fechadas).",
            )

        df = cls.enrich_dataframe(df, params)
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        price = float(curr["close"])
        atr = float(curr["atr"]) if float(curr["atr"]) > 0 else price * 0.0015

        # Executa o motor de Machine Learning (Random Forest scikit-learn) nas velas enriquecidas
        ml_pred = (model_engine or ml_engine).predict_next_candle(df, context=f"{model_context or market_type}:{symbol}:{timeframe}:{params.get('data_source', 'unknown')}")

        indicators_snapshot = {
            "fibonacci": fibonacci_context(df, atr) if market_type == 'MT5_FOREX' else None,
            "support": float(df['low'].tail(params.get('structure_lookback',20)).min()),
            "resistance": float(df['high'].tail(params.get('structure_lookback',20)).max()),
            "price": round(price, 5),
            "ema_fast": round(float(curr["ema_fast"]), 5),
            "ema_slow": round(float(curr["ema_slow"]), 5),
            "rsi": round(float(curr["rsi"]), 2),
            "bb_upper": round(float(curr["bb_upper"]), 5),
            "bb_lower": round(float(curr["bb_lower"]), 5),
            "macd_hist": round(float(curr["macd_hist"]), 6),
            "atr": round(atr, 5),
            "volume": float(curr["volume"]),
            "ml_prob_up": ml_pred["prob_up"],
            "ml_prob_down": ml_pred["prob_down"],
            "ml_accuracy": ml_pred["accuracy"],
            "ml_samples": ml_pred["samples"],
            "ml_features": ml_pred["current_features"],
            "ml_validation": {k: v for k, v in ml_pred.items() if k != "current_features"},
        }

        action = "NEUTRAL"
        confidence = 30.0
        reason = "Sem gatilho claro no candle atual."

        # -------------------------------------------------------------------------
        # ESTRATÉGIA 1: Cruzamento de Médias Móveis (EMA) + Filtro de RSI
        # -------------------------------------------------------------------------
        if strategy_id == "ema_rsi_crossover":
            cross_up = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
            cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
            trend_up = curr["ema_fast"] > curr["ema_slow"] and curr["close"] > curr["ema_fast"]
            trend_down = curr["ema_fast"] < curr["ema_slow"] and curr["close"] < curr["ema_fast"]

            if cross_up and 48 <= curr["rsi"] <= 72:
                action = "BUY"
                confidence = 86.0
                reason = f"Cruzamento de Alta EMA({params.get('ema_fast',9)}/{params.get('ema_slow',21)}) confirmado com RSI em {curr['rsi']:.1f}."
            elif cross_down and 28 <= curr["rsi"] <= 52:
                action = "SELL"
                confidence = 86.0
                reason = f"Cruzamento de Baixa EMA({params.get('ema_fast',9)}/{params.get('ema_slow',21)}) confirmado com RSI em {curr['rsi']:.1f}."
            elif trend_up and 55 <= curr["rsi"] <= 68 and prev["close"] <= prev["ema_fast"]:
                action = "BUY"
                confidence = 75.0
                reason = f"Pullback na EMA rápida a favor da tendência de alta (RSI {curr['rsi']:.1f})."
            elif trend_down and 32 <= curr["rsi"] <= 45 and prev["close"] >= prev["ema_fast"]:
                action = "SELL"
                confidence = 75.0
                reason = f"Pullback na EMA rápida a favor da tendência de baixa (RSI {curr['rsi']:.1f})."
            else:
                reason = f"Aguardando cruzamento ou pullback EMA. RSI atual: {curr['rsi']:.1f}."

        # -------------------------------------------------------------------------
        # ESTRATÉGIA 2: Bandas de Bollinger + RSI (Alta Precisão Analítica)
        # Exige confirmação real de rejeição (pavio/exaustão) na Banda e bloqueia
        # entradas contra rompimentos de força (evita tomar sequência de LOSS em tendências fortes).
        # Também opera a favor da tendência somente quando EMAs, MACD, RSI e IA estão 100% alinhados.
        # -------------------------------------------------------------------------
        elif strategy_id == "bollinger_rsi_reversal":
            rsi_oversold = float(params.get("rsi_oversold", 35))
            rsi_overbought = float(params.get("rsi_overbought", 65))

            touched_lower = curr["low"] <= curr["bb_lower"] or prev["low"] <= prev["bb_lower"]
            touched_upper = curr["high"] >= curr["bb_upper"] or prev["high"] >= prev["bb_upper"]

            candle_range = max(float(curr["high"] - curr["low"]), 1e-7)
            body_size = abs(float(curr["close"] - curr["open"]))
            lower_wick = float(min(curr["open"], curr["close"]) - curr["low"])
            upper_wick = float(curr["high"] - max(curr["open"], curr["close"]))
            atr_val = max(float(curr["atr"]), 1e-6)
            three_bar_move = float(curr["close"] - df["close"].iloc[-4]) if len(df) >= 4 else 0.0

            # Detecta se o mercado está em rompimento explosivo (proíbe operar contra a força!)
            violent_bull_breakout = (
                (curr["close"] > curr["bb_upper"] and upper_wick < (candle_range * 0.22) and curr["close"] > curr["open"])
                or (three_bar_move > (atr_val * 1.65) and curr["macd_hist"] > prev["macd_hist"] and curr["ema_fast"] > curr["ema_slow"])
            )
            violent_bear_breakout = (
                (curr["close"] < curr["bb_lower"] and lower_wick < (candle_range * 0.22) and curr["close"] < curr["open"])
                or (three_bar_move < -(atr_val * 1.65) and curr["macd_hist"] < prev["macd_hist"] and curr["ema_fast"] < curr["ema_slow"])
            )

            # Confirmação real de rejeição de fundo (pavio inferior visível ou virada compradora após toque na banda)
            bull_rejection_confirmed = (
                (lower_wick >= candle_range * 0.25)
                or (prev["low"] <= prev["bb_lower"] and curr["close"] > curr["open"] and curr["close"] > prev["close"])
            )
            # Confirmação real de rejeição de topo (pavio superior visível ou virada vendedora após toque na banda)
            bear_rejection_confirmed = (
                (upper_wick >= candle_range * 0.25)
                or (prev["high"] >= prev["bb_upper"] and curr["close"] < curr["open"] and curr["close"] < prev["close"])
            )

            # Confluência Analítica Estrita de Tendência (somente quando todos os indicadores + IA convergem)
            confirmed_bull_trend = (
                curr["ema_fast"] > curr["ema_slow"]
                and prev["ema_fast"] > prev["ema_slow"]
                and curr["macd_hist"] > 0
                and curr["macd_hist"] > prev["macd_hist"]
                and 53.0 <= curr["rsi"] <= 64.0
                and prev["close"] > prev["open"]
                and curr["close"] > curr["open"]
                and curr["close"] > curr["ema_fast"]
                and curr["high"] < curr["bb_upper"]
                and upper_wick <= (candle_range * 0.25)
                and ml_pred["prob_up"] >= 56.0
            )
            confirmed_bear_trend = (
                curr["ema_fast"] < curr["ema_slow"]
                and prev["ema_fast"] < prev["ema_slow"]
                and curr["macd_hist"] < 0
                and curr["macd_hist"] < prev["macd_hist"]
                and 36.0 <= curr["rsi"] <= 47.0
                and prev["close"] < prev["open"]
                and curr["close"] < curr["open"]
                and curr["close"] < curr["ema_fast"]
                and curr["low"] > curr["bb_lower"]
                and lower_wick <= (candle_range * 0.25)
                and ml_pred["prob_down"] >= 56.0
            )

            if touched_lower and curr["rsi"] <= rsi_oversold:
                if violent_bear_breakout:
                    action = "NEUTRAL"
                    confidence = 38.0
                    reason = (
                        f"🛡️ [FILTRO ANALÍTICO] Toque na Banda Inferior ({curr['bb_lower']:.5f}), mas vela sem rejeição "
                        f"em queda forte. Aguardando exaustão real para não entrar contra o rompimento."
                    )
                elif bull_rejection_confirmed:
                    action = "BUY"
                    confidence = min(94.0, 80.0 + (rsi_oversold - curr["rsi"]) * 0.9)
                    reason = (
                        f"Reversão de Fundo Confirmada (CALL ▲): Banda Inferior ({curr['bb_lower']:.5f}) "
                        f"+ Rejeição de preço + RSI sobrevendido ({curr['rsi']:.1f})."
                    )
                else:
                    reason = (
                        f"Preço na Banda Inferior ({curr['bb_lower']:.5f}) com RSI {curr['rsi']:.1f}, "
                        f"aguardando pavio/rejeição compradora para confirmar CALL."
                    )

            elif touched_upper and curr["rsi"] >= rsi_overbought:
                if violent_bull_breakout:
                    action = "NEUTRAL"
                    confidence = 38.0
                    reason = (
                        f"🛡️ [FILTRO ANALÍTICO] Toque na Banda Superior ({curr['bb_upper']:.5f}), mas vela de força compradora "
                        f"sem pavio. Entrada PUT bloqueada para proteger contra rompimento de alta!"
                    )
                elif bear_rejection_confirmed:
                    action = "SELL"
                    confidence = min(94.0, 80.0 + (curr["rsi"] - rsi_overbought) * 0.9)
                    reason = (
                        f"Reversão de Topo Confirmada (PUT ▼): Banda Superior ({curr['bb_upper']:.5f}) "
                        f"+ Rejeição de preço + RSI sobrecomprado ({curr['rsi']:.1f})."
                    )
                else:
                    reason = (
                        f"Preço na Banda Superior ({curr['bb_upper']:.5f}) com RSI {curr['rsi']:.1f}, "
                        f"aguardando pavio/rejeição vendedora para confirmar PUT."
                    )

            elif confirmed_bull_trend:
                action = "BUY"
                confidence = 83.0
                reason = (
                    f"Confluência Analítica de Alta (CALL ▲): EMA 9 > EMA 21 + MACD crescente "
                    f"+ RSI {curr['rsi']:.1f} + IA {ml_pred['prob_up']}%."
                )

            elif confirmed_bear_trend:
                action = "SELL"
                confidence = 83.0
                reason = (
                    f"Confluência Analítica de Baixa (PUT ▼): EMA 9 < EMA 21 + MACD decrescente "
                    f"+ RSI {curr['rsi']:.1f} + IA {ml_pred['prob_down']}%."
                )

            else:
                reason = (
                    f"Análise Rigorosa: Bandas ({curr['bb_lower']:.5f} - {curr['bb_upper']:.5f}) | "
                    f"RSI: {curr['rsi']:.1f}. Aguardando critérios técnicos completos."
                )

        # -------------------------------------------------------------------------
        # ESTRATÉGIA 3: Price Action / Rompimento de Máxima/Mínima + Volume
        # Excelente para Mini Índice (WIN), Mini Dólar (WDO) e Forex
        # -------------------------------------------------------------------------
        elif strategy_id == "price_action_breakout":
            lookback = int(params.get("lookback_candles", 10))
            recent_window = df.iloc[-(lookback + 1) : -1]
            highest_high = float(recent_window["high"].max())
            lowest_low = float(recent_window["low"].min())
            vol_multiplier = float(curr["volume"]) / max(1.0, float(curr["vol_sma"]))

            if curr["close"] > highest_high and vol_multiplier >= 1.1:
                action = "BUY"
                confidence = min(92.0, 74.0 + (vol_multiplier - 1.0) * 20)
                reason = f"Rompimento da máxima de {lookback} períodos ({highest_high:.5f}) com Volume {vol_multiplier:.2f}x acima da média."
            elif curr["close"] < lowest_low and vol_multiplier >= 1.1:
                action = "SELL"
                confidence = min(92.0, 74.0 + (vol_multiplier - 1.0) * 20)
                reason = f"Rompimento da mínima de {lookback} períodos ({lowest_low:.5f}) com Volume {vol_multiplier:.2f}x acima da média."
            else:
                reason = f"Consolidado entre mínima ({lowest_low:.5f}) e máxima ({highest_high:.5f}). Vol: {vol_multiplier:.2f}x."

        # -------------------------------------------------------------------------
        # ESTRATÉGIA 4: Confluência Multi-Indicadores (EMA + RSI + MACD)
        # -------------------------------------------------------------------------
        elif strategy_id == "multi_confluence":
            score_buy = 0
            score_sell = 0

            if curr["ema_fast"] > curr["ema_slow"] and curr["close"] > curr["ema_fast"]:
                score_buy += 35
            elif curr["ema_fast"] < curr["ema_slow"] and curr["close"] < curr["ema_fast"]:
                score_sell += 35

            if curr["macd_hist"] > 0 and curr["macd_hist"] > prev["macd_hist"]:
                score_buy += 35
            elif curr["macd_hist"] < 0 and curr["macd_hist"] < prev["macd_hist"]:
                score_sell += 35

            if 50 <= curr["rsi"] <= 68:
                score_buy += 30
            elif 32 <= curr["rsi"] <= 50:
                score_sell += 30

            if score_buy >= 70:
                action = "BUY"
                confidence = float(score_buy)
                reason = f"Confluência de Alta ({score_buy}%): EMA alinhada + Histograma MACD crescente + RSI em {curr['rsi']:.1f}."
            elif score_sell >= 70:
                action = "SELL"
                confidence = float(score_sell)
                reason = f"Confluência de Baixa ({score_sell}%): EMA alinhada + Histograma MACD decrescente + RSI em {curr['rsi']:.1f}."
            else:
                reason = f"Confluência parcial (Compra: {score_buy}% | Venda: {score_sell}%). Aguardando alinhamento >= 70%."

        # -------------------------------------------------------------------------
        # ESTRATÉGIA 5: Inteligência Artificial Pura (Machine Learning Random Forest)
        # -------------------------------------------------------------------------
        elif strategy_id == "ml_random_forest":
            p_up = ml_pred["prob_up"]
            p_down = ml_pred["prob_down"]
            if ml_pred["ml_action"] == "BUY":
                action = "BUY"
                confidence = ml_pred["ml_confidence"]
                reason = (
                    f"🧠 IA Machine Learning prevê ALTA (CALL ▲) com {p_up}% de probabilidade "
                    f"(Modelo Random Forest: {ml_pred['samples']} amostras, Acurácia temporal {ml_pred['accuracy']}%)."
                )
            elif ml_pred["ml_action"] == "SELL":
                action = "SELL"
                confidence = ml_pred["ml_confidence"]
                reason = (
                    f"🧠 IA Machine Learning prevê QUEDA (PUT ▼) com {p_down}% de probabilidade "
                    f"(Modelo Random Forest: {ml_pred['samples']} amostras, Acurácia temporal {ml_pred['accuracy']}%)."
                )
            else:
                reason = (
                    f"🧠 IA analisando padrões: Prob. Alta {p_up}% | Prob. Queda {p_down}% "
                    f"(Aguardando validação temporal aprovada e força direcional mínima do modelo; "
                    f"o score mínimo configurado também será exigido na entrada)."
                )

        # Model estimates never inflate a heuristic technical score.
        # Refuse automated signals without independent chronological evidence.
        raw_action, raw_confidence = action, confidence
        directional_estimate = ml_pred['prob_up'] if action=='BUY' else (ml_pred['prob_down'] if action=='SELL' else ml_pred.get('ml_confidence',50.))
        if action in ("BUY", "SELL") and bool(params.get("use_ml_filter", True)):
            directional = ml_pred["prob_up"] if action == "BUY" else ml_pred["prob_down"]
            if not ml_pred.get("validated") or directional < 60.0:
                action, confidence = "NEUTRAL", 0.0
                reason = "Filtro temporal: " + (ml_pred["reason"] if not ml_pred.get('validated') else f'Estimativa direcional {directional:.1f}% abaixo de 60%.')

        indicators_snapshot["score_kind"] = "technical_confluence_not_win_probability"
        indicators_snapshot["regime"] = ("TENDÊNCIA" if abs(float(curr["ema_fast"])-float(curr["ema_slow"])) / atr > .7 else "LATERAL")
        indicators_snapshot["atr_pct"] = round(atr / max(price, 1e-9) * 100, 4)
        if float(curr["high"])-float(curr["low"]) > atr * 3:
            action, confidence, reason = "NEUTRAL", 0.0, "Volatilidade extrema: candle acima de 3 ATR."

        # Cálculo de Stop Loss, Take Profit (para MT5) ou Expiração (para Safirion OB)
        stop_loss = None
        take_profit = None
        expiration_minutes = None

        if market_type == "SAFIRION_OB":
            expiration_minutes = int(params.get("expiration_minutes", 1))
        else:
            sl_atr_mult = float(params.get("sl_atr_mult", 1.5))
            tp_atr_mult = float(params.get("tp_atr_mult", 2.5))
            if action == "BUY":
                stop_loss = round(price - (atr * sl_atr_mult), 5)
                take_profit = round(price + (atr * tp_atr_mult), 5)
            elif action == "SELL":
                stop_loss = round(price + (atr * sl_atr_mult), 5)
                take_profit = round(price - (atr * tp_atr_mult), 5)

        diagnostic=dict(candidate_action=raw_action,candidate_confidence=round(raw_confidence,1),
                        directional_estimate=directional_estimate,ml_estimated_direction=('BUY' if ml_pred['prob_up']>ml_pred['prob_down'] else ('SELL' if ml_pred['prob_down']>ml_pred['prob_up'] else 'NEUTRAL')),
                        extreme_volatility=bool(float(curr['high'])-float(curr['low'])>atr*3),
                        candle_range=float(curr['high'])-float(curr['low']), atr=atr)
        result = SignalResult(
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
            strategy_id=strategy_id,
            action=action,
            confidence=round(confidence, 1),
            price=round(price, 5),
            stop_loss=stop_loss,
            take_profit=take_profit,
            expiration_minutes=expiration_minutes,
            indicators=indicators_snapshot,
            reason=reason,
        )
        result.diagnostic=diagnostic
        return result
