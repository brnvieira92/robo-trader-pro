"""Informational Fibonacci from confirmed pivots of already CLOSED Bid bars.

Two bars on either side confirm a strict pivot. No future data, ML feature,
entry gate or execution parameter is introduced by this module.
"""
import math


def fibonacci_context(df, atr, lookback=60, pivot_bars=2):
    result = dict(version='confirmed-pivots-v1', informational=True, available=False,
                  lookback=lookback, pivot_bars=pivot_bars, quote='BID',
                  reason='Aguardando dois pivôs opostos confirmados.')
    window = df.tail(lookback)
    highs = window['high'].tolist()
    lows = window['low'].tolist()
    pivots = []
    for i in range(pivot_bars, len(window)-pivot_bars):
        row = window.iloc[i]
        high = highs[i] > max(highs[i-pivot_bars:i]+highs[i+1:i+pivot_bars+1])
        low = lows[i] < min(lows[i-pivot_bars:i]+lows[i+1:i+pivot_bars+1])
        # An outside bar with both extremes has unknown intrabar ordering.
        if high == low:
            continue
        pivots.append(dict(kind='HIGH' if high else 'LOW',
            price=float(row['high'] if high else row['low']),
            time=str(row['time']), confirmed_at=str(window.iloc[i+pivot_bars]['time'])))
    if len(pivots) < 2:
        return result
    end = pivots[-1]
    start = next((p for p in reversed(pivots[:-1]) if p['kind'] != end['kind']), None)
    if start is None:
        return result
    up = end['kind'] == 'HIGH'
    span = (end['price']-start['price'])*(1 if up else -1)
    if not math.isfinite(span) or span <= 0:
        return result
    price = float(window.iloc[-1]['close'])
    sign = 1 if up else -1
    levels = [dict(ratio=r, price=end['price']-sign*span*r)
              for r in (0., .236, .382, .5, .618, .786, 1.)]
    extensions = [dict(ratio=r, price=start['price']+sign*span*r) for r in (1.272, 1.618)]
    fraction = (end['price']-price)*sign/span
    nearest = min(levels, key=lambda level: abs(price-level['price']))
    distance_atr = abs(price-nearest['price'])/atr if math.isfinite(atr) and atr > 0 else None
    result.update(available=True, reason='', direction='UP' if up else 'DOWN',
        start=start, end=end, levels=levels, extensions=extensions, reference_price=price,
        retracement_fraction=fraction, within_swing=0 <= fraction <= 1,
        nearest={**nearest, 'distance_atr':distance_atr},
        in_382_618_zone=.382 <= fraction <= .618)
    return result
