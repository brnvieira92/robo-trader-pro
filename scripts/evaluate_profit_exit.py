"""Known-period full early-profit comparison. No SDK, no optimizer, no shadow."""
import copy,json,sys,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from backend.config import TradingConfig
from backend.strategies import StrategyEngine
from backend.forex_backtest import ForexBacktestRequest,run_forex_backtest
from backend.trade_plan import PRICING_VERSION


def run(source,output):
    raw=source.read_bytes();base=json.loads(raw);output.mkdir(parents=True,exist_ok=False)
    report=dict(protocol=json.loads(Path('research/profit_exit_protocol.json').read_text()),dataset_sha256=hashlib.sha256(raw).hexdigest(),results=[],status='RUNNING')
    original=StrategyEngine.analyze;descriptor=StrategyEngine.__dict__['analyze'];cache={}
    def cached(df,symbol,market,timeframe,strategy,params,**kwargs):
        key=(symbol,strategy,str(df.iloc[0]['time']),str(df.iloc[-1]['time']),len(df),json.dumps(params,sort_keys=True))
        if key not in cache:cache[key]=original(df,symbol,market,timeframe,strategy,params,**kwargs)
        return cache[key]
    StrategyEngine.analyze=staticmethod(cached)
    def save():(output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    try:
        for date in ['2026-10-20','2026-10-21','2026-10-22','2026-10-23']:
            z=ZoneInfo('America/Sao_Paulo');begin=int(datetime.fromisoformat(date+'T03:00').replace(tzinfo=z).timestamp());end=int(datetime.fromisoformat(date+'T17:56').replace(tzinfo=z).timestamp())
            for cost in [2,5]:
                for enabled in [False,True]:
                    req=copy.deepcopy(base);req['config']['strategy_id']='forex_trend_continuation'
                    req['config']['profit_exit']=dict(report['protocol']['defaults'],enabled=enabled)
                    req.update(initial_balance=10000,evaluated_bars=(end-begin)//60+1,adverse_slippage_points=cost,
                        commission_per_lot_side=0,swap_cost_per_lot_utc_day=0,assumptions_acknowledged=True,exit_management=dict(mode='FIXED'))
                    for market in req['markets']:market['candles']=[b for b in market['candles'] if b['time']<=end]
                    request=ForexBacktestRequest.model_validate(req);reuse=None
                    name=f'{date}-slip-{cost}-early-{enabled}.json'
                    print('START',name,flush=True)
                    # Reuse an immutable prior result only after config, window,
                    # balances, costs and dataset identities are all verified.
                    prior=Path('/workspace/artifacts/triple-atr-study')/f'{date}-forex_trend_continuation-slip-{cost}.json'
                    if not enabled and prior.exists():
                        old=json.loads(prior.read_text())
                        from backend.diagnostics import digest
                        hashes=[digest(m.model_dump()) for m in request.markets]
                        if (old.get('version')==6 and old.get('pricing_version')==PRICING_VERSION and
                            TradingConfig.model_validate(old['config']).model_dump()==request.config.model_dump() and
                            old['start']==begin and old['end']==end+60 and old['currency']==request.account_currency and
                            old['assumptions'].get('goal_fx_account_per_brl')==request.goal_fx_account_per_brl and old['assumptions']['initial_balance']==10000 and
                            old['assumptions']['adverse_slippage_points']==cost and old['assumptions']['commission_per_lot_side']==0 and
                            old['assumptions']['swap_cost_per_lot_utc_day']==0 and old['assumptions']['exit_management']['mode']=='FIXED' and
                            [m['hash'] for m in old['datasets']]==hashes):reuse=prior;r=old
                    if reuse is None:r=run_forex_backtest(request)
                    if r['start']!=begin or r['end']!=end+60:raise ValueError('Window mismatch')
                    (output/name).write_text(json.dumps(r,ensure_ascii=False,allow_nan=False))
                    row=dict(date=date,slippage=cost,early_exit=enabled,baseline_reused_from=str(reuse) if reuse else None,
                        realized_pnl=r['realized_pnl'],equity_change=r['ending_equity']-10000,closed=r['closed_trades'],wins=r['wins'],losses=r['losses'],
                        open_positions=len(r['open_positions']),early_exits=sum(t.get('exit_reason')=='EARLY_PROFIT_WEAKNESS' for t in r['trades']),
                        drawdown=r['max_drawdown'],result_file=name)
                    report['results'].append(row);save();print('RESULT',json.dumps(row),flush=True)
        report['status']='FINISHED';save()
    finally:StrategyEngine.analyze=descriptor


if __name__=='__main__':run(Path(sys.argv[1]),Path(sys.argv[2]))
