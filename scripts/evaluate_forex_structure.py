"""Reproduce frozen structural rules on known days; not independent validation.

Usage: PYTHONPATH=. .venv/bin/python scripts/evaluate_forex_structure.py BASE OUTPUT_NEW_DIR
Never connects to a broker; no optimizer or strategy selection.
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


def run(source,output):
    output.mkdir(parents=True,exist_ok=False)
    raw=source.read_bytes();base=json.loads(raw)
    protocol=json.loads(Path('research/forex_structure_retest_protocol.json').read_text())
    report=dict(protocol=protocol,dataset_sha256=hashlib.sha256(raw).hexdigest(),results=[],
        status='RUNNING',evidence='KNOWN_PERIOD_EXPLORATORY_ONLY',operational_configuration_changed=False)
    original=StrategyEngine.analyze;descriptor=StrategyEngine.__dict__['analyze'];cache={}
    def cached(df,symbol,market,timeframe,strategy,params,**kwargs):
        key=(symbol,strategy,str(df.iloc[0]['time']),str(df.iloc[-1]['time']),len(df),json.dumps(params,sort_keys=True))
        if key not in cache:cache[key]=original(df,symbol,market,timeframe,strategy,params,**kwargs)
        return cache[key]
    StrategyEngine.analyze=staticmethod(cached)
    def save():(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    try:
        save()
        for date in ['2026-10-20','2026-10-21','2026-10-22','2026-10-23']:
            z=ZoneInfo('America/Sao_Paulo')
            begin=int(datetime.fromisoformat(date+'T03:00').replace(tzinfo=z).timestamp())
            last=int(datetime.fromisoformat(date+'T17:56').replace(tzinfo=z).timestamp())
            for cost in [2,5]:
                req=copy.deepcopy(base)
                req['config']['strategy_id']='forex_structure_retest'
                req.update(initial_balance=10000,evaluated_bars=(last-begin)//60+1,assumptions_acknowledged=True,
                    adverse_slippage_points=cost,commission_per_lot_side=0,swap_cost_per_lot_utc_day=0,exit_management=dict(mode='FIXED'))
                for m in req['markets']:m['candles']=[b for b in m['candles'] if b['time']<=last]
                print('START',date,cost,flush=True)
                row=dict(date=date,slippage_points=cost)
                try:
                    r=run_forex_backtest(ForexBacktestRequest.model_validate(req))
                    if r['start']!=begin or r['end']!=last+60:raise ValueError('Unexpected evaluation window')
                    name=f'{date}-slip-{cost}.json';(output/name).write_text(json.dumps(r,ensure_ascii=False,allow_nan=False))
                    row.update(result_file=name,realized_pnl=r['realized_pnl'],equity_change=r['ending_equity']-10000,
                        closed_trades=r['closed_trades'],wins=r['wins'],losses=r['losses'],open_positions=len(r['open_positions']),
                        max_drawdown=r['max_drawdown'],per_pair=r['per_pair'])
                except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
                report['results'].append(row);save()
                print('RESULT',json.dumps({k:v for k,v in row.items() if k!='per_pair'}),flush=True)
        report['status']='FINISHED' if not any('error' in r for r in report['results']) else 'FINISHED_WITH_ERRORS';save()
    finally:StrategyEngine.analyze=descriptor


if __name__=='__main__':run(Path(sys.argv[1]),Path(sys.argv[2]))
