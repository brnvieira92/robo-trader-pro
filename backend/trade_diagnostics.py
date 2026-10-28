"""Sampled price excursions. Observations never alter orders or risk limits."""
import math


def observe_exit_quote(trade, price, observed_at, source):
    if trade.get('action') not in ('BUY','SELL'):
        return
    try:
        price=float(price);entry=float(trade['entry_price']);stop=float(trade['stop_loss'])
        observed_at=float(observed_at)
    except (TypeError,ValueError,KeyError):
        return
    if not all(math.isfinite(v) and v>0 for v in (price,entry,stop,observed_at)) or entry==stop:
        return
    move=(price-entry)*(1 if trade['action']=='BUY' else -1)
    previous=trade.get('sampled_excursions') or {}
    if observed_at<previous.get('last_observed_at',0):
        return
    mfe=max(previous.get('mfe_price',0),move,0)
    mae=max(previous.get('mae_price',0),-move,0)
    trade['sampled_excursions']=dict(mfe_price=mfe,mae_price=mae,
        mfe_r=mfe/abs(entry-stop),mae_r=mae/abs(entry-stop),
        samples=previous.get('samples',0)+1,
        first_observed_at=previous.get('first_observed_at',observed_at),last_observed_at=observed_at,
        source=source,basis='SAMPLED_LOWER_BOUND; gaps between observations are not reconstructed')
