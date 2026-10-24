"""Reproducible offline comparison. No terminal connection or order methods.

Run from repository root: python tools/compare_forex.py --dataset base.json
  --history historico.json --output /tmp/forex-study
Dates/parameters are declared before simulation; no optimizer selects a winner.
"""
import argparse
import copy
from datetime import datetime,timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from backend.config import FOREX_TECHNICAL_STRATEGIES
from backend.forex_backtest import ForexBacktestRequest,HistoricalBar,HistoricalMarket,HistoricalExecutor,exit_on_bar,run_forex_backtest
from backend.strategies import StrategyEngine


def study(dataset,history,output,days,slippages):
    if not slippages or any(not math.isfinite(s) or not 0<=s<=10000 for s in slippages):
        raise ValueError('Slippage precisa ser finito e entre 0 e 10.000 pontos por preenchimento.')
    for day in days:datetime.fromisoformat(day)
    source=Path(dataset);base=json.loads(source.read_text(encoding='utf-8-sig'))
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    tz=ZoneInfo('America/Sao_Paulo');strategies=('forex_trend_continuation','forex_triple_atr')
    protocol=dict(dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),days=days,
        timezone='America/Sao_Paulo',start='03:00',last_bar='17:56',strategies=strategies,
        initial_balance=10000,slippage_points_per_fill=slippages,
        commission_per_lot_side=base.get('commission_per_lot_side',0),swap_per_lot_day=base.get('swap_cost_per_lot_utc_day',0),
        atr_period=10,atr_multipliers=[1.5,2,3],exits='Original protective SL/TP, no reversal or trailing.',
        limitations=['Fixed diagnostic window chosen for continuous four-pair data, not a time filter for execution.',
          'Previously inspected data; no independent holdout. No parameter search.',
          'Synthetic daily starting state; not a reconstruction of actual runtime or prior UTC limits.',
          'OHLC Bid plus per-bar spread approximation; present-day tick/margin specifications frozen.',
          'Costs are explicit research hypotheses, not confirmed historical tariffs.',
          'Different entry times are a different strategy; skipped entries are not wins.'])
    protocol_path=output/'protocol.json'
    if protocol_path.exists() and json.loads(protocol_path.read_text())!=json.loads(json.dumps(protocol)):
        raise ValueError('A pasta contém outro protocolo. Use outra pasta de saída para preservar a comparação anterior.')
    protocol_path.write_text(json.dumps(protocol,ensure_ascii=False,indent=2,allow_nan=False))
    original=StrategyEngine.analyze;cache={};results=[]
    def cached(frame,symbol,market,timeframe,strategy,params,**kwargs):
        assert strategy in FOREX_TECHNICAL_STRATEGIES
        key=(symbol,strategy,str(frame.iloc[0]['time']) if len(frame) else '',
             str(frame.iloc[-1]['time']) if len(frame) else '',len(frame),json.dumps(params,sort_keys=True))
        # Immutable uploaded source and no ML: exit/cost variants share exactly
        # the same pure input signal. to_dict() deep-copies the result per call.
        if key not in cache:cache[key]=original(frame,symbol,market,timeframe,strategy,params,**kwargs)
        return cache[key]
    StrategyEngine.analyze=staticmethod(cached)
    try:
        for day in days:
            start=int(datetime.fromisoformat(day+'T03:00').replace(tzinfo=tz).timestamp())
            last=int(datetime.fromisoformat(day+'T17:56').replace(tzinfo=tz).timestamp())
            for strategy in strategies:
                for slip in slippages:
                    request=copy.deepcopy(base)
                    for m in request['markets']:m['candles']=[b for b in m['candles'] if b['time']<=last]
                    request['config']['strategy_id']=strategy
                    request.update(initial_balance=10000,evaluated_bars=(last-start)//60+1,
                        adverse_slippage_points=slip,
                        assumptions_acknowledged=True,exit_management={'mode':'FIXED'})
                    began=time.monotonic();print('START',day,strategy,'slippage',slip,flush=True)
                    try:
                        r=run_forex_backtest(ForexBacktestRequest.model_validate(request))
                    except ValueError as error:
                        results.append(dict(day=day,strategy=strategy,slippage=slip,state='FAILED',error=str(error)))
                    else:
                        r['study_protocol']=protocol
                        filename=f'{day}-{strategy}-slip-{slip}.json'
                        (output/filename).write_text(json.dumps(r,ensure_ascii=False,allow_nan=False))
                        results.append(dict(day=day,strategy=strategy,slippage=slip,state='DONE',
                            entries=sum(p['entries'] for p in r['per_pair']),closed=r['closed_trades'],wins=r['wins'],losses=r['losses'],
                            draws=r['draws'],net=r['realized_pnl'],equity=r['ending_equity'],drawdown=r['max_drawdown'],
                            profit_factor=r['profit_factor'],open_positions=len(r['open_positions']),
                            elapsed_seconds=round(time.monotonic()-began,2)))
                    print('RESULT',json.dumps(results[-1]),flush=True)
                    (output/'comparison.json').write_text(json.dumps(dict(protocol=protocol,results=results),ensure_ascii=False,indent=2))
    finally:
        StrategyEngine.analyze=original
    if history:
        counterfactual_entries(base,history,output)
    return results


def counterfactual_entries(base,history,output):
    rows=[];tz=ZoneInfo('America/Sao_Paulo')
    trades=json.loads(Path(history).read_text(encoding='utf-8-sig'))['trades']
    markets={m['symbol']:HistoricalMarket.model_validate(m) for m in base['markets']}
    for trade in sorted(trades,key=lambda t:t['created_at']):
        if datetime.fromtimestamp(trade['created_at'],tz).date().isoformat()!='2026-10-23':continue
        market=markets[trade['symbol']];ts=int(trade['created_at']//60)*60
        frame=pd.DataFrame([b.model_dump() for b in market.candles if b.time<ts][-239:])
        frame['time']=pd.to_datetime(frame['time'],unit='s')
        cfg=copy.deepcopy(trade['portfolio_config']);cfg['strategy_id']='forex_triple_atr'
        signal=StrategyEngine.analyze(frame,trade['symbol'],'MT5_FOREX','M1',cfg['strategy_id'],cfg['strategy_params']).to_dict()
        row=dict(symbol=trade['symbol'],entry_brt=datetime.fromtimestamp(trade['created_at'],tz).isoformat(),
                 original_side=trade['action'],original_pnl=trade['pnl'],new_action=signal['action'],score=signal['confidence'],
                 reason=signal['reason'],confirmation=signal['indicators'].get('entry_confirmation'),outcomes=[])
        row['decision']='PROPOSED_REQUIRES_PREFLIGHT'
        if signal['action']=='NEUTRAL':
            row['decision']='SKIPPED_NOT_A_WIN';rows.append(row);continue
        quote=trade['entry_diagnostics']
        # Reuse the ACTUAL entry-time quote, rather than pretending the minute
        # opening price was executable seconds later. Future Ask remains OHLC.
        actual_bar=HistoricalBar(time=ts,open=quote['bid'],high=quote['bid'],low=quote['bid'],close=quote['bid'],
                                 spread_points=quote['spread_points'])
        for slip in (0,2,5):
            equity=trade['portfolio']['risk_limit']/(cfg['risk_per_trade_pct']/100)
            commission=base.get('commission_per_lot_side',0)
            adapter=HistoricalExecutor(cfg,market,actual_bar,equity,base['account_currency'],
                commission_per_lot_side=commission,adverse_slippage_points=slip)
            try:
                p=adapter.prepare(cfg,signal,trade['remaining_loss_budget'],'COUNTERFACTUAL',None)
            except ValueError as error:
                row['outcomes'].append(dict(slippage=slip,decision='PREFLIGHT_REJECTED',reason=str(error)));continue
            direction=1 if signal['action']=='BUY' else -1
            entry=p['price']+direction*slip*market.specification.point
            new=dict(action=signal['action'],stop_loss=p['stop_loss'],take_profit=p['take_profit'])
            result=dict(slippage=slip,decision='OPEN_AT_120_MIN',entry=entry,stop=p['stop_loss'],target=p['take_profit'],volume=p['volume'])
            for bar in market.candles:
                if not ts<=bar.time<ts+7200:continue
                exit_result=exit_on_bar(new,bar,market.specification,slip)
                if exit_result:
                    price,reason,ambiguous=exit_result
                    pnl=adapter.profit(0 if direction==1 else 1,trade['symbol'],p['volume'],entry,price)-2*commission*p['volume']
                    result.update(decision='WIN' if pnl>1e-8 else 'LOSS' if pnl< -1e-8 else 'DRAW',pnl=pnl,
                                  exit=price,reason=reason,ambiguous=ambiguous,
                                  event_bar_brt=datetime.fromtimestamp(bar.time,tz).isoformat());break
            row['outcomes'].append(result)
        rows.append(row)
    (Path(output)/'previous_entries.json').write_text(json.dumps(dict(
        note='Actual-time quotes, hypothetical new plans; 120-minute horizon, approximate future Ask, no portfolio path replay. Skipped/rejected is not a win.',
        commission_per_lot_side=base.get('commission_per_lot_side',0),slippage_profiles=[0,2,5],
        trades=rows),ensure_ascii=False,indent=2,allow_nan=False))
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True);parser.add_argument('--history')
    parser.add_argument('--output',required=True)
    parser.add_argument('--days',nargs='+',default=['2026-10-20','2026-10-21','2026-10-22','2026-10-23'])
    parser.add_argument('--slippages',nargs='+',type=float,default=[0,2,5])
    args=parser.parse_args()
    study(args.dataset,args.history,args.output,args.days,args.slippages)
