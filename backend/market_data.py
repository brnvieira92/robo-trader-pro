"""Reject inconsistent OHLCV rather than manufacturing or silently repairing bars."""
import numpy as np
import pandas as pd

COLUMNS = ['time', 'open', 'high', 'low', 'close', 'volume']

def validate_candles(frame):
    if frame.empty:
        return
    if any(column not in frame for column in COLUMNS):
        raise ValueError('Histórico OHLCV incompleto.')
    values = frame[['open','high','low','close','volume']].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:,:4] <= 0).any() or (values[:,4] < 0).any():
        raise ValueError('Histórico contém preços/volumes inválidos.')
    times = pd.to_datetime(frame['time'], errors='coerce')
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError('Histórico fora de ordem ou com horários duplicados.')
    if (frame['low'] > frame[['open','close']].min(axis=1)).any() or (frame['high'] < frame[['open','close']].max(axis=1)).any():
        raise ValueError('Máximas/mínimas inconsistentes com abertura e fechamento.')
