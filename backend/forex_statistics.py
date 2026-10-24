"""Descriptive financial diagnostics, never an automatic strategy approval."""
from collections import defaultdict
from datetime import datetime, timezone
from math import sqrt


def summarize(trades):
    values=[t['net_pnl'] for t in trades]
    n=len(values);wins=sum(x>1e-8 for x in values)
    gain=sum(max(0,x) for x in values);loss=-sum(min(0,x) for x in values)
    # Wilson interval describes hit rate only. Serial dependence and repeated
    # strategy selection invalidate an interpretation as proof of an edge.
    interval=None
    if n:
        z=1.959963984540054;p=wins/n;den=1+z*z/n
        center=(p+z*z/(2*n))/den
        radius=z*sqrt(p*(1-p)/n+z*z/(4*n*n))/den
        interval=[round(100*(center-radius),4),round(100*(center+radius),4)]
    return dict(closed_trades=n,net_pnl=round(sum(values),8),
        win_rate=round(100*wins/n,4) if n else None,win_rate_ci95=interval,
        expectancy=round(sum(values)/n,8) if n else None,
        profit_factor=round(gain/loss,6) if loss else None,
        mean_sampled_mae_r=sum(t['sampled_mae_r'] for t in trades)/n if n else None,
        mean_sampled_mfe_r=sum(t['sampled_mfe_r'] for t in trades)/n if n else None)


def financial_breakdown(trades):
    dimensions={
        'symbol':lambda t:t['symbol'],
        'entry_hour_utc':lambda t:f"{datetime.fromtimestamp(t['entry_time'],timezone.utc).hour:02d}",
        'trigger':lambda t:(t.get('entry_confirmation') or {}).get('trigger') or 'UNSPECIFIED',
        'policy':lambda t:t.get('entry_policy_version','legacy'),
    }
    result=dict(overall=summarize(trades),evidence='DESCRIPTIVE_ONLY',
        note='Grupos são descritivos, não testes independentes. IC de acerto pressupõe independência; não mede rentabilidade nem corrige seleção múltipla.')
    for name,key in dimensions.items():
        groups=defaultdict(list)
        for trade in trades:groups[key(trade)].append(trade)
        result[name]=[dict(group=group,**summarize(items)) for group,items in sorted(groups.items())]
    return result
