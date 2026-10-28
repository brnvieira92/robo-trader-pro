"""Run frozen protocol; never open confirmation unless development passes.

Usage: PYTHONPATH=. .venv/bin/python scripts/research_forex_batch02.py BASE_JSON OUTPUT_DIR
Output directory must be new: accidental repeat/overwrite is refused.
"""
import copy
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
from backend.strategies import StrategyEngine


def qualifies(rows,costs,min_total):
    return bool(rows) and all(r.get('error') is None and r['realized_pnl']>0 and r['equity_change']>0 and r['closed_trades']>0 for r in rows) and all(sum(r['closed_trades'] for r in rows if r['slippage']==cost)>=min_total for cost in costs)


def run(source,output):
    protocol=json.loads(Path('research/forex_batch_02_protocol.json').read_text())
    raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=protocol['dataset_sha256']:raise ValueError('Dataset differs from registered protocol')
    output.mkdir(parents=True,exist_ok=False)
    base=json.loads(raw);results=[];selected=None;confirmation_opened=False
    original=StrategyEngine.analyze;original_descriptor=StrategyEngine.__dict__['analyze'];cache={}
    def cached(df,symbol,market,timeframe,strategy,params,**kwargs):
        key=(symbol,strategy,str(df.iloc[0]['time']),str(df.iloc[-1]['time']),len(df),json.dumps(params,sort_keys=True))
        if key not in cache:cache[key]=original(df,symbol,market,timeframe,strategy,params,**kwargs)
        return cache[key]
    StrategyEngine.analyze=staticmethod(cached)
    def save():
        report=dict(protocol=protocol,results=results,selected=selected,confirmation_opened=confirmation_opened,
                    status='RUNNING',operational_strategy_changed=False)
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return report
    def evaluate(date,strategy,cost,phase):
        z=ZoneInfo(protocol['timezone'])
        begin=int(datetime.fromisoformat(date+'T'+protocol['window_start']).replace(tzinfo=z).timestamp())
        last=int(datetime.fromisoformat(date+'T'+protocol['window_last_bar']).replace(tzinfo=z).timestamp())
        request=copy.deepcopy(base)
        for market in request['markets']:market['candles']=[b for b in market['candles'] if b['time']<=last]
        request['config']['strategy_id']=strategy
        request.update(initial_balance=protocol['initial_balance_each_case'],evaluated_bars=(last-begin)//60+1,
            assumptions_acknowledged=True,adverse_slippage_points=cost,commission_per_lot_side=0,
            swap_cost_per_lot_utc_day=0,exit_management=dict(mode='FIXED'))
        row=dict(date=date,strategy=strategy,slippage=cost,phase=phase)
        print('START',date,strategy,cost,flush=True)
        try:
            r=run_forex_backtest(ForexBacktestRequest.model_validate(request))
            if r['start']!=begin or r['end']!=last+60:raise ValueError('Evaluation window mismatch')
            row.update(realized_pnl=r['realized_pnl'],equity_change=r['ending_equity']-request['initial_balance'],
                closed_trades=r['closed_trades'],wins=r['wins'],losses=r['losses'],open_positions=len(r['open_positions']),
                max_drawdown=r['max_drawdown'],per_pair=r['per_pair'])
            path=output/f'{date}-{strategy}-slip-{cost}.json'
            path.write_text(json.dumps(r,ensure_ascii=False,allow_nan=False))
            row['result_file']=path.name
        except Exception as exc:row.update(error=f'{type(exc).__name__}: {exc}')
        results.append(row);save();print('RESULT',json.dumps(row),flush=True)
    try:
        save()
        for strategy in protocol['candidates']:
            for date in protocol['development_dates']:
                for cost in protocol['slippage_points_per_fill']:evaluate(date,strategy,cost,'development')
        eligible=[s for s in protocol['candidates'] if qualifies([r for r in results if r['strategy']==s],protocol['slippage_points_per_fill'],20)]
        if eligible:
            def rank(s):
                rows=[r for r in results if r['strategy']==s]
                return (-min(r['equity_change'] for r in rows),-sum(r['equity_change'] for r in rows)/len(rows),max(r['max_drawdown'] for r in rows),s)
            selected=sorted(eligible,key=rank)[0]
            confirmation_opened=True;save() # Freeze selected identity before loading holdout dates.
            for date in protocol['confirmation_dates']:
                for cost in protocol['slippage_points_per_fill']:evaluate(date,selected,cost,'confirmation')
        report=save()
        report['status']='SCREENING_REJECTED' if not selected else 'EXPLORATORY_PASS' if qualifies([r for r in results if r['phase']=='confirmation'],protocol['slippage_points_per_fill'],10) else 'CONFIRMATION_REJECTED'
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('FINAL',report['status'],'selected=',selected,'confirmation_opened=',confirmation_opened,flush=True)
    finally:StrategyEngine.analyze=original_descriptor


if __name__=='__main__':run(Path(sys.argv[1]),Path(sys.argv[2]))
