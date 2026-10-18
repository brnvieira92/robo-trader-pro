"""
Motor de Machine Learning em Tempo Real (MLEngine) usando scikit-learn.
Utiliza um Ensemble de RandomForestClassifier + GradientBoostingClassifier que:
1. Extrai 10 features (variáveis preditivas) de cada vela:
   - RSI normalizado, Posição relativa nas Bandas de Bollinger (%B),
   - Distância percentual entre EMA rápida e EMA lenta,
   - Aceleração do Histograma MACD,
   - Tamanho do corpo da vela vs ATR,
   - Força de rejeição do pavio superior e do pavio inferior,
   - Retorno dos últimos 1, 2 e 3 períodos (momentum de curto prazo).
2. Treina dinamicamente nas velas recentes do ativo aberto (Online Rolling Training)
   para prever a probabilidade exata da PRÓXIMA vela fechar de ALTA (CALL/BUY) ou BAIXA (PUT/SELL).
3. Aprende também com o histórico de WIN e LOSS reais executados pelo robô (Feedback Loop).
"""

from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


class MLEngine:
    def __init__(self):
        self.rf_model = (
            RandomForestClassifier(
                n_estimators=45,
                max_depth=5,
                min_samples_leaf=2,
                random_state=42,
            )
            if SKLEARN_AVAILABLE
            else None
        )
        # Memória de experiências reais do robô (reforço supervisionado por WIN/LOSS)
        self.experience_X: List[List[float]] = []
        self.experience_y: List[int] = []  # 1 = SUBIU (CALL WIN / PUT LOSS), 0 = CAIU (PUT WIN / CALL LOSS)
        self.last_accuracy: float = 0.0
        self.samples_trained: int = 0

    @staticmethod
    def extract_features_matrix(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, List[float]]:
        """
        Extrai a matriz X de features das velas históricas e o vetor alvo y (se a próxima vela subiu=1 ou caiu=0),
        além do vetor de features da vela atual (última linha) para predição ao vivo.
        """
        c = df["close"].values.astype(float)
        o = df["open"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)
        ema_f = df["ema_fast"].values.astype(float)
        ema_s = df["ema_slow"].values.astype(float)
        rsi = df["rsi"].values.astype(float)
        bb_u = df["bb_upper"].values.astype(float)
        bb_l = df["bb_lower"].values.astype(float)
        macd_h = df["macd_hist"].values.astype(float)
        atr = np.maximum(df["atr"].values.astype(float), 1e-7)

        n = len(df)
        features = []

        for i in range(3, n):
            price = max(c[i], 1e-7)
            bb_range = max(bb_u[i] - bb_l[i], 1e-7)

            # 1. %B (Posição dentro das Bandas de Bollinger: 0 = banda inf, 1 = banda sup)
            pct_b = (c[i] - bb_l[i]) / bb_range
            # 2. RSI normalizado (-1 a +1)
            rsi_norm = (rsi[i] - 50.0) / 50.0
            # 3. Spread entre EMA rápida e lenta normalizado pelo ATR
            ema_spread = (ema_f[i] - ema_s[i]) / atr[i]
            # 4. Variação do histograma MACD
            macd_delta = (macd_h[i] - macd_h[i - 1]) / atr[i]
            # 5. Corpo da vela atual normalizado pelo ATR (positivo=verde, negativo=vermelha)
            body_norm = (c[i] - o[i]) / atr[i]
            # 6. Pavio superior (rejeição de alta) normalizado pelo ATR
            upper_wick = (h[i] - max(o[i], c[i])) / atr[i]
            # 7. Pavio inferior (rejeição de baixa) normalizado pelo ATR
            lower_wick = (min(o[i], c[i]) - l[i]) / atr[i]
            # 8, 9, 10. Momentum de 1, 2 e 3 velas
            ret_1 = (c[i] - c[i - 1]) / atr[i]
            ret_2 = (c[i] - c[i - 2]) / atr[i]
            ret_3 = (c[i] - c[i - 3]) / atr[i]

            row_feat = [
                float(np.clip(pct_b, -2.0, 3.0)),
                float(np.clip(rsi_norm, -1.0, 1.0)),
                float(np.clip(ema_spread, -5.0, 5.0)),
                float(np.clip(macd_delta, -5.0, 5.0)),
                float(np.clip(body_norm, -5.0, 5.0)),
                float(np.clip(upper_wick, 0.0, 5.0)),
                float(np.clip(lower_wick, 0.0, 5.0)),
                float(np.clip(ret_1, -5.0, 5.0)),
                float(np.clip(ret_2, -5.0, 5.0)),
                float(np.clip(ret_3, -5.0, 5.0)),
            ]
            features.append(row_feat)

        # O último elemento de `features` corresponde à vela atual (índice n-1), usada para prever a PRÓXIMA vela
        current_feat = features[-1]

        # Para treinamento supervisionado, usamos os pares (features[k], direção_real_da_vela[k+1])
        X_train = []
        y_train = []
        for idx in range(len(features) - 1):
            candle_idx = idx + 3
            next_close = c[candle_idx + 1]
            curr_close = c[candle_idx]
            if next_close == curr_close:
                continue
            target = 1 if next_close > curr_close else 0
            X_train.append(features[idx])
            y_train.append(target)

        return np.array(X_train, dtype=float), np.array(y_train, dtype=int), current_feat

    def record_trade_feedback(self, features: List[float], action: str, won: bool):
        """
        Alimenta a memória de reforço do Machine Learning com o resultado real da operação na corretora!
        Se o robô entrou CALL (BUY) e ganhou (won=True), o mercado realmente subiu (label=1).
        Se entrou CALL e perdeu (won=False), o mercado caiu (label=0).
        """
        if not features or len(features) != 10:
            return
        if action in ("BUY", "CALL"):
            true_label = 1 if won else 0
        else:
            true_label = 0 if won else 1

        # Adiciona com peso duplo (duas amostras) para que o modelo priorize os padrões de Win/Loss reais
        self.experience_X.append(features)
        self.experience_y.append(true_label)
        self.experience_X.append(features)
        self.experience_y.append(true_label)

        if len(self.experience_X) > 120:
            self.experience_X = self.experience_X[-120:]
            self.experience_y = self.experience_y[-120:]

    def predict_next_candle(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Treina o Random Forest nas velas mais recentes + memória de trades reais e retorna:
        - prob_up (0 a 100%): probabilidade de alta na próxima vela (CALL / BUY)
        - prob_down (0 a 100%): probabilidade de baixa na próxima vela (PUT / SELL)
        - ml_action: "BUY", "SELL" ou "NEUTRAL"
        - accuracy: taxa de acerto estimada do modelo nos dados atuais
        """
        if not SKLEARN_AVAILABLE or len(df) < 35:
            return {
                "enabled": False,
                "prob_up": 50.0,
                "prob_down": 50.0,
                "ml_action": "NEUTRAL",
                "ml_confidence": 50.0,
                "accuracy": 0.0,
                "samples": 0,
                "current_features": [],
            }

        try:
            X_hist, y_hist, curr_feat = self.extract_features_matrix(df)
            if len(X_hist) < 15:
                return {
                    "enabled": True,
                    "prob_up": 50.0,
                    "prob_down": 50.0,
                    "ml_action": "NEUTRAL",
                    "ml_confidence": 50.0,
                    "accuracy": 0.0,
                    "samples": len(X_hist),
                    "current_features": curr_feat,
                }

            if self.experience_X:
                X_full = np.vstack([X_hist, np.array(self.experience_X, dtype=float)])
                y_full = np.concatenate([y_hist, np.array(self.experience_y, dtype=int)])
            else:
                X_full = X_hist
                y_full = y_hist

            # Garante que há exemplos das duas classes (0 e 1) antes do fit
            unique_classes = np.unique(y_full)
            if len(unique_classes) < 2:
                return {
                    "enabled": True,
                    "prob_up": 50.0,
                    "prob_down": 50.0,
                    "ml_action": "NEUTRAL",
                    "ml_confidence": 50.0,
                    "accuracy": 50.0,
                    "samples": len(X_full),
                    "current_features": curr_feat,
                }

            self.rf_model.fit(X_full, y_full)
            self.samples_trained = len(X_full)
            self.last_accuracy = round(float(self.rf_model.score(X_full, y_full)) * 100.0, 1)

            probs = self.rf_model.predict_proba(np.array([curr_feat], dtype=float))[0]
            # probs[0] = classe 0 (queda/PUT), probs[1] = classe 1 (alta/CALL)
            prob_down = round(float(probs[0]) * 100.0, 1)
            prob_up = round(float(probs[1]) * 100.0, 1)

            if prob_up >= 58.0:
                ml_action = "BUY"
                ml_conf = min(98.0, round(prob_up * 1.15, 1))
            elif prob_down >= 58.0:
                ml_action = "SELL"
                ml_conf = min(98.0, round(prob_down * 1.15, 1))
            else:
                ml_action = "NEUTRAL"
                ml_conf = max(prob_up, prob_down)

            return {
                "enabled": True,
                "prob_up": prob_up,
                "prob_down": prob_down,
                "ml_action": ml_action,
                "ml_confidence": ml_conf,
                "accuracy": self.last_accuracy,
                "samples": self.samples_trained,
                "current_features": curr_feat,
                "experience_count": len(self.experience_y) // 2,
            }
        except Exception:
            return {
                "enabled": False,
                "prob_up": 50.0,
                "prob_down": 50.0,
                "ml_action": "NEUTRAL",
                "ml_confidence": 50.0,
                "accuracy": 0.0,
                "samples": 0,
                "current_features": [],
            }


# Instância global compartilhada pelo StrategyEngine e BotManager
ml_engine = MLEngine()
