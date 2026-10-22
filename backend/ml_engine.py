"""Directional research model with chronological validation, never training accuracy.

Scores are model estimates, not calibrated probabilities of broker profit. Each
instrument/timeframe/source has an isolated bounded cache. Only closed candles
may be supplied by callers. Trade outcomes are not interchangeable with one-bar
labels and are deliberately excluded from training.
"""
from typing import Any, Dict, List, Tuple
from collections import OrderedDict
import hashlib
import logging
import threading
import numpy as np
import pandas as pd
try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import balanced_accuracy_score, brier_score_loss
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

logger = logging.getLogger(__name__)

class MLEngine:
    def __init__(self):
        self.samples_trained = 0
        self.last_accuracy = 0.0
        self._cache = OrderedDict()
        self._cache_lock = threading.RLock()

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
            curr_close = o[candle_idx + 1]
            if next_close == curr_close:
                continue
            target = 1 if next_close > curr_close else 0
            X_train.append(features[idx])
            y_train.append(target)

        return np.array(X_train, dtype=float), np.array(y_train, dtype=int), current_feat

    def record_trade_feedback(self, features: List[float], action: str, won: bool):
        # Broker expiry, fees and fills have a different target from next-bar direction.
        # Do not contaminate one-bar training labels with trade PnL.
        return

    def predict_next_candle(self, df: pd.DataFrame, context: str = "default", validation_mode="single_split") -> Dict[str, Any]:
        if validation_mode not in ('single_split','walk_forward_v2'):
            raise ValueError('Modo de validação ML desconhecido.')
        result = dict(enabled=False, validated=False, prob_up=50.0, prob_down=50.0,
                      ml_action="NEUTRAL", ml_confidence=50.0, accuracy=0.0,
                      samples=0, validation_samples=0, baseline_accuracy=0.0,
                      balanced_accuracy=0.0, brier_score=None, current_features=[],
                      experience_count=0, reason="Histórico insuficiente: mínimo 100 velas fechadas.",
                      probability_kind="uncalibrated_model_estimate",validation_method=validation_mode)
        if not SKLEARN_AVAILABLE:
            result["reason"] = "scikit-learn indisponível."
            return result
        if len(df) < 100:
            return result
        signature = hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()
        result.update(dataset_version=signature,model_version=hashlib.sha256((context+signature+'RF80-depth4-leaf6-seed42-v1'+validation_mode).encode()).hexdigest())
        key = (context, signature, validation_mode)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return dict(self._cache[key])
        try:
            # Indicators need warm-up; drop the early rows after enrichment.
            X, y, features = self.extract_features_matrix(df.iloc[25:])
            split = int(len(X) * .70)
            # Purge the boundary label: its next close must not enter validation.
            X_train, y_train = X[:split-1], y[:split-1]
            X_test, y_test = X[split:], y[split:]
            result.update(samples=len(X), training_samples=len(X_train), validation_samples=len(X_test),
                          training_classes=[int(x) for x in np.unique(y_train)],validation_classes=[int(x) for x in np.unique(y_test)],current_features=features)
            if len(X_train) < 40 or len(X_test) < 20 or len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
                result["reason"] = "Amostra temporal insuficiente ou sem as duas direções."
                return result
            def model():
                return RandomForestClassifier(n_estimators=80, max_depth=4,
                                              min_samples_leaf=6, random_state=42, n_jobs=1)
            validation_model = model().fit(X_train, y_train)
            predictions = validation_model.predict(X_test)
            probabilities = validation_model.predict_proba(X_test)[:, list(validation_model.classes_).index(1)]
            accuracy = float(np.mean(predictions == y_test))
            baseline = float(np.mean(y_test == int(np.mean(y_train) >= .5)))
            balanced = float(balanced_accuracy_score(y_test, predictions))
            brier = float(brier_score_loss(y_test, probabilities))
            validated = accuracy > baseline and balanced >= .55 and brier < .25
            if validation_mode == 'walk_forward_v2':
                walk = self.walk_forward(X,y,model)
                result['walk_forward']=walk
                validated = validated and walk['validated']
            fitted = model().fit(X, y)
            probs = fitted.predict_proba([features])[0]
            up = float(probs[list(fitted.classes_).index(1)]) * 100
            down = 100-up
            confidence = max(up, down)
            action = ("BUY" if up > down else "SELL") if validated and confidence >= 60 else "NEUTRAL"
            self.samples_trained = len(X)
            self.last_accuracy = round(accuracy * 100, 1)
            result.update(validation_checks=dict(accuracy=accuracy>baseline,balanced=balanced>=.55,brier=brier<.25,
                          **({'walk_forward':walk['validated']} if validation_mode=='walk_forward_v2' else {})),enabled=True, validated=validated, prob_up=round(up, 1), prob_down=round(down, 1),
                          ml_action=action, ml_confidence=round(confidence, 1), accuracy=self.last_accuracy,
                          baseline_accuracy=round(baseline*100, 1), balanced_accuracy=round(balanced*100, 1),
                          brier_score=round(brier, 4),
                          reason="Validação temporal favorável; estimativas não calibradas." if validated else
                          ("Validação walk-forward reprovada: consistência insuficiente entre janelas." if validation_mode=='walk_forward_v2' and not walk['validated'] else
                           "Sem vantagem demonstrada sobre a referência no período de validação."))
            with self._cache_lock:
                self._cache[key] = dict(result)
                while len(self._cache) > 16:
                    self._cache.popitem(last=False)
        except (ValueError, IndexError, TypeError) as exc:
            logger.exception("Falha no modelo direcional")
            result["reason"] = "Modelo indisponível: dados inconsistentes."
        return result

    @staticmethod
    def walk_forward(X,y,factory):
        """Three disjoint test blocks, expanding training, one purged label.

        These are directional tests, not evidence of profitable SL/TP execution.
        """
        bounds=[int(len(X)*r) for r in (.4,.6,.8,1.)]
        folds=[];truth=[];predicted=[];probabilities=[];baseline_hits=[]
        for start,end in zip(bounds,bounds[1:]):
            train_end=start-1
            fold=dict(train_end_exclusive=train_end,test_start=start,test_end_exclusive=end,
                      training_samples=train_end,validation_samples=end-start,passed=False)
            if train_end<40 or end-start<20 or len(np.unique(y[:train_end]))<2 or len(np.unique(y[start:end]))<2:
                fold['reason']='Amostras/classes insuficientes nesta janela.'
                folds.append(fold);continue
            fitted=factory().fit(X[:train_end],y[:train_end])
            target=y[start:end];prediction=fitted.predict(X[start:end])
            probability=fitted.predict_proba(X[start:end])[:,list(fitted.classes_).index(1)]
            base=(target==int(np.mean(y[:train_end])>=.5))
            acc=float(np.mean(prediction==target));ref=float(np.mean(base))
            bal=float(balanced_accuracy_score(target,prediction));brier=float(brier_score_loss(target,probability))
            fold.update(accuracy=acc*100,baseline_accuracy=ref*100,balanced_accuracy=bal*100,brier_score=brier,
                        passed=acc>ref and bal>=.55 and brier<.25)
            folds.append(fold);truth.extend(target);predicted.extend(prediction);probabilities.extend(probability);baseline_hits.extend(base)
        passed=sum(f['passed'] for f in folds)
        complete=len(truth)==bounds[-1]-bounds[0]
        accuracy=float(np.mean(np.array(predicted)==truth)) if truth else 0.
        baseline=float(np.mean(baseline_hits)) if truth else 0.
        balanced=float(balanced_accuracy_score(truth,predicted)) if truth and len(set(truth))==2 else 0.
        brier=float(brier_score_loss(truth,probabilities)) if truth else None
        valid=complete and passed>=2 and accuracy>baseline and balanced>=.55 and brier is not None and brier<.25
        return dict(validated=bool(valid),folds=folds,passed_folds=passed,required_folds=2,
                    complete=complete,validation_samples=len(truth),accuracy=accuracy*100,
                    baseline_accuracy=baseline*100,balanced_accuracy=balanced*100,brier_score=brier)

ml_engine = MLEngine()
