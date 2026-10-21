"""Causal, bid/ask OHLC Forex research with one global position and no SDK IO.

Uses the production signal and price/risk preparation code through a closed
historical adapter. No send/reconcile methods are called by this simulator.
"""
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from typing import Literal
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.backtest import Candle
from backend.config import TradingConfig
from backend.diagnostics import digest, preflight_code
from backend.ml_engine import MLEngine
from backend.mt5_execution import MT5Executor, MT5ExecutionError
from backend.strategies import StrategyEngine


class HistoricalBar(Candle):
    spread_points: float = Field(ge=0, le=100000)


class SymbolSpecification(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    point: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    digits: int = Field(ge=0,le=10)
    tick_value_profit: float = Field(gt=0)
    tick_value_loss: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    stops_level: int = Field(ge=0)
    margin_per_lot: float = Field(gt=0)
    trade_mode: Literal[0,1,2,3,4]

    @model_validator(mode='after')
    def bounds(self):
        if self.volume_min>self.volume_max: raise ValueError('Volume mínimo maior que o máximo.')
        return self


class HistoricalMarket(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    symbol: str = Field(min_length=2,max_length=30,pattern=r'^[A-Za-z0-9_$./#-]+$')
    specification: SymbolSpecification
    candles: list[HistoricalBar] = Field(min_length=150,max_length=2000)
    source: Literal['MT5','UPLOADED']
    specification_basis: str = Field(min_length=3,max_length=300)


class ForexBacktestRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    config: TradingConfig
    markets: list[HistoricalMarket] = Field(min_length=1,max_length=4)
    account_currency: str = Field(min_length=3,max_length=8,pattern=r'^[A-Z]+$')
    initial_balance: float = Field(10000,gt=0,le=10000000)
    evaluated_bars: int = Field(100,ge=1,le=200)
    commission_per_lot_side: float = Field(0,ge=0,le=10000)
    adverse_slippage_points: float = Field(0,ge=0,le=10000)
    swap_cost_per_lot_utc_day: float = Field(0,ge=0,le=10000)
    goal_fx_account_per_brl: float | None = Field(None,gt=0,le=1000000)
    captured_at: float | None = Field(None,ge=0)
    assumptions_acknowledged: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def legacy_single_position_dataset(cls, values):
        # Bases captured before parallel execution described a one-position
        # strategy. Preserve that meaning without changing live defaults.
        if isinstance(values, dict) and isinstance(values.get('config'), dict) and 'forex_max_open_positions' not in values['config']:
            values={**values,'config':{**values['config'],'forex_max_open_positions':1}}
        return values

    @model_validator(mode='after')
    def consistency(self):
        c=self.config
        if c.forex_multi_enabled and c.forex_max_open_positions>1:
            raise ValueError('Este laboratório histórico avalia uma posição global. Não valida a carteira simultânea de duas posições. Use uma configuração de pesquisa com limite 1; a execução ativa não será alterada.')
        if c.market_type!='MT5_FOREX': raise ValueError('Este laboratório aceita somente Forex.')
        if c.strategy_id!='ml_random_forest' or c.min_confidence<78 or not c.strategy_params.use_ml_filter:
            raise ValueError('Mantenha Random Forest, score mínimo de 78% e filtro ML ativo.')
        symbols=[m.symbol for m in self.markets]
        if len(set(symbols))!=len(symbols): raise ValueError('Símbolos duplicados no histórico.')
        selected=[p.symbol for p in c.forex_pairs if p.enabled] if c.forex_multi_enabled else [c.symbol]
        if set(symbols)!=set(selected): raise ValueError('Histórico deve corresponder exatamente aos pares selecionados na configuração.')
        if c.goal_currency=='BRL' and self.account_currency!='BRL' and self.goal_fx_account_per_brl is None:
            raise ValueError('Informe a taxa histórica assumida em moeda da conta por BRL para a meta em reais.')
        step={'M1':60,'M5':300,'M15':900,'M30':1800,'H1':3600}[c.timeframe]
        for market in self.markets:
            if any(b.time<=a.time or (b.time-a.time)%step for a,b in zip(market.candles,market.candles[1:])):
                raise ValueError('Velas devem ser únicas, crescentes e alinhadas ao timeframe; lacunas são permitidas e diagnosticadas.')
            if any(bar.time>datetime.now(timezone.utc).timestamp()-step for bar in market.candles):
                raise ValueError('Inclua somente velas históricas fechadas, sem timestamps futuros.')
        return self


class HistoricalExecutor(MT5Executor):
    """Only prepare() is used. Frozen symbol money values are explicit assumptions."""
    def __init__(self, config, market, bar, balance, currency):
        self.spec=market.specification
        self.bar=bar;self.config=config;self.balance=balance;self.currency=currency
        spec=self.spec
        info=NS(point=spec.point,trade_tick_size=spec.tick_size,digits=spec.digits,
                trade_stops_level=spec.stops_level,volume_min=spec.volume_min,volume_step=spec.volume_step,
                volume_max=spec.volume_max,trade_mode=spec.trade_mode,filling_mode=3,trade_exemode=2)
        self.sdk=NS(SYMBOL_TRADE_MODE_FULL=4,SYMBOL_TRADE_MODE_LONGONLY=1,SYMBOL_TRADE_MODE_SHORTONLY=2,
            ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1,SYMBOL_FILLING_FOK=1,SYMBOL_FILLING_IOC=2,
            ORDER_FILLING_FOK=0,ORDER_FILLING_IOC=1,TRADE_ACTION_DEAL=1,ORDER_TIME_GTC=0,
            symbol_select=lambda *a:True,symbol_info=lambda *a:info,positions_get=lambda **k:(),orders_get=lambda **k:(),
            order_calc_profit=self.profit,order_calc_margin=lambda kind,symbol,volume,price:volume*spec.margin_per_lot,
            order_check=lambda request:NS(retcode=0))

    def account(self,*args,**kwargs):
        return dict(login=self.config.get('mt5_account_login') or 1,server=self.config.get('mt5_account_server') or 'HISTORICAL',
                    equity=self.balance,balance=self.balance,margin_free=max(0,self.balance),currency=self.currency,account_type='RESEARCH')

    def _tick(self,symbol,config):
        return NS(bid=self.bar.open,ask=self.bar.open+self.bar.spread_points*self.spec.point)

    def profit(self,kind,symbol,volume,entry,exit):
        movement=(Decimal(str(exit))-Decimal(str(entry)))*(1 if kind==0 else -1)
        value=self.spec.tick_value_profit if movement>=0 else self.spec.tick_value_loss
        result=float(movement/Decimal(str(self.spec.tick_size))*Decimal(str(value))*Decimal(str(volume)))
        if not math.isfinite(result):raise MT5ExecutionError('Resultado monetário não finito; confira especificação e preços históricos.')
        return result


def exit_on_bar(trade, bar, spec, slippage_points):
    """Bid bars for longs, ask approximated by bid + bar spread for shorts."""
    buy=trade['action']=='BUY';spread=0 if buy else bar.spread_points*spec.point
    op,hi,lo=bar.open+spread,bar.high+spread,bar.low+spread
    sl,tp=trade['stop_loss'],trade['take_profit'];direction=1 if buy else -1
    slip=slippage_points*spec.point
    gap_stop=(op<=sl if buy else op>=sl)
    gap_target=(op>=tp if buy else op<=tp)
    if gap_stop:return op-direction*slip,'GAP_SL',False
    if gap_target:return tp-direction*slip,'GAP_TP',False
    hit_sl=lo<=sl if buy else hi>=sl
    hit_tp=hi>=tp if buy else lo<=tp
    if hit_sl:return sl-direction*slip,'SL',bool(hit_tp)
    if hit_tp:return tp-direction*slip,'TP',False
    return None


def run_forex_backtest(request: ForexBacktestRequest, progress=None):
    config=request.config.model_dump();step={'M1':60,'M5':300,'M15':900,'M30':1800,'H1':3600}[config['timeframe']]
    markets={m.symbol:m for m in request.markets}
    order=[p['symbol'] for p in config['forex_pairs'] if p['enabled']] if config['forex_multi_enabled'] else [config['symbol']]
    bars={s:{b.time:b for b in m.candles} for s,m in markets.items()}
    frames={s:pd.DataFrame([b.model_dump() for b in m.candles]) for s,m in markets.items()}
    for frame in frames.values():frame['time']=pd.to_datetime(frame['time'],unit='s')
    engines={s:MLEngine() for s in markets}
    start=max(m.candles[0].time for m in request.markets)
    finish=min(m.candles[-1].time for m in request.markets)
    timeline=sorted({b.time for m in request.markets for b in m.candles if start<=b.time<=finish})[-request.evaluated_bars:]
    if not timeline:raise ValueError('Os períodos dos pares não se sobrepõem.')
    balance=request.initial_balance;position=None;trades=[];analyses=[];curve=[]
    daily=Counter();daily_entries=Counter();loss_streak=0;session=None;last_trade=-math.inf;cursor=0
    ambiguities=0;last_used={};peak=balance;drawdown=0;drawdown_pct=0
    goal=config['stop_gain']*(request.goal_fx_account_per_brl if config['goal_currency']=='BRL' and request.account_currency!='BRL' else 1)

    def day_key(ts):return datetime.fromtimestamp(ts,timezone.utc).date().isoformat()
    def post_cash(amount,ts):
        nonlocal balance
        balance=round(balance+amount,8)
        key=day_key(ts);daily[key]=round(daily[key]+amount,8)

    def settle(exit_price,reason,ambiguous,ts):
        nonlocal balance,position,loss_streak,last_trade,ambiguities
        trade=position
        model=HistoricalExecutor(config,markets[trade['symbol']],bars[trade['symbol']][trade['entry_bar']],balance,request.account_currency)
        gross=model.profit(0 if trade['action']=='BUY' else 1,trade['symbol'],trade['volume'],trade['entry_price'],exit_price)
        commission=request.commission_per_lot_side*trade['volume']
        delta=gross-commission
        post_cash(delta,ts)
        net=round(gross-trade['entry_commission']-commission-trade['swap_cost'],8)
        trade.update(exit_price=exit_price,exit_time=ts,exit_reason=reason,ambiguous=ambiguous,
                     gross_pnl=gross,exit_commission=commission,net_pnl=net,status='WIN' if net>1e-8 else 'LOSS' if net< -1e-8 else 'DRAW')
        loss_streak=loss_streak+1 if net< -1e-8 else 0
        trades.append(trade);ambiguities+=int(ambiguous);last_trade=ts;position=None

    for index,ts in enumerate(timeline):
        day=day_key(ts)
        if day!=session:
            session=day
            # A UTC date change resets session limits, not the balance or position.
            loss_streak=0
            for trade in reversed(trades):
                if day_key(trade['exit_time'])!=day or trade['status']!='LOSS':break
                loss_streak+=1
        if position:
            current=bars[position['symbol']].get(ts)
            if current is None:
                raise ValueError('Lacuna no histórico de '+position['symbol']+' com posição aberta; não é possível reconstruir SL/TP. Revise a base antes de concluir rentabilidade.')
            if current:
                elapsed_days=(ts//86400)-(position['last_swap_time']//86400)
                cost=max(0,elapsed_days)*request.swap_cost_per_lot_utc_day*position['volume']
                position['swap_cost']+=cost;post_cash(-cost,ts);position['last_swap_time']=ts
                result=exit_on_bar(position,current,markets[position['symbol']].specification,request.adverse_slippage_points)
                if result and result[1].startswith('GAP_'):settle(*result,ts)
        candidates=[]
        for symbol in order:
            bar=bars[symbol].get(ts)
            history=frames[symbol]
            history=history[history['time']<pd.Timestamp(ts,unit='s')].tail(239).copy()
            gaps=history['time'].diff().dt.total_seconds().iloc[1:]!=step
            if gaps.any():history=history.iloc[int(gaps.to_numpy().nonzero()[0][-1])+1:].copy()
            signal=StrategyEngine.analyze(history,symbol,'MT5_FOREX',config['timeframe'],config['strategy_id'],
                {**config['strategy_params'],'data_source':'HISTORICAL_FOREX'},model_engine=engines[symbol],
                model_context='HISTORICAL:'+digest(config)).to_dict()
            ml=signal.get('indicators',{}).get('ml_validation',{})
            blocks=[]
            if not bar:blocks.append('BAR_MISSING')
            if len(history)<100:blocks.append('ML_HISTORY')
            if not ml.get('validated'):blocks.append('ML_VALIDATED')
            if signal['action'] not in ('BUY','SELL'):blocks.append('SIGNAL_DIRECTION')
            if signal['confidence']<config['min_confidence']:blocks.append('MIN_CONFIDENCE')
            if (signal.get('diagnostic') or {}).get('extreme_volatility'):blocks.append('VOLATILITY')
            if position:blocks.append('ONE_POSITION')
            if daily[day]<=-config['stop_loss']:blocks.append('DAILY_LOSS')
            if daily[day]>=goal:blocks.append('DAILY_GOAL')
            if daily_entries[day]>=config['max_daily_trades']:blocks.append('DAILY_ENTRIES')
            if loss_streak>=config['max_consecutive_losses']:blocks.append('LOSS_STREAK')
            if ts-last_trade<config['cooldown_seconds']:blocks.append('COOLDOWN')
            bar_time=str(history.iloc[-1]['time']) if len(history) else None
            if bar_time and last_used.get(symbol)==bar_time:blocks.append('UNUSED_BAR')
            record=dict(symbol=symbol,time=ts,signal=signal['action'],score=signal['confidence'],
                ml_confidence=ml.get('ml_confidence'),validation=ml,bar_time=bar_time,blocks=blocks,
                action='REJECTED' if blocks else 'CANDIDATE',unavailable_checks=['HISTORICAL_TICK_AGE','HISTORICAL_ACCOUNT_PERMISSIONS','BROKER_ORDER_CHECK'])
            analyses.append(record)
            if not blocks:
                prepared_signal={**signal,'bar_time':bar_time}
                candidates.append((symbol,bar,prepared_signal,record))
        if not position:
            rank={symbol:(order.index(symbol)-cursor)%len(order) for symbol in order}
            for symbol,bar,signal,record in sorted(candidates,key=lambda item:rank[item[0]]):
                cfg={**config,'symbol':symbol}
                adapter=HistoricalExecutor(cfg,markets[symbol],bar,balance,request.account_currency)
                try:
                    prepared=adapter.prepare(cfg,signal,max(0,config['stop_loss']+daily[day]),'HISTORICAL',max(0,goal-daily[day]))
                except MT5ExecutionError as exc:
                    record.update(action='REJECTED',preflight_reason=str(exc));record['blocks'].append(preflight_code(str(exc)))
                    continue
                spec=markets[symbol].specification;buy=signal['action']=='BUY'
                entry=prepared['price']+(1 if buy else -1)*request.adverse_slippage_points*spec.point
                fee=request.commission_per_lot_side*prepared['volume']
                position=dict(symbol=symbol,action=signal['action'],volume=prepared['volume'],entry_price=entry,
                    quoted_entry=prepared['price'],entry_time=ts,entry_bar=ts,stop_loss=prepared['stop_loss'],take_profit=prepared['take_profit'],
                    entry_commission=fee,swap_cost=0,last_swap_time=ts,risk_budget=prepared['risk_budget'],
                    estimated_risk=prepared['estimated_risk'],plan=prepared['plan'],status='OPEN',signal_bar=signal['bar_time'])
                post_cash(-fee,ts);daily_entries[day]+=1;last_trade=ts;last_used[symbol]=signal['bar_time']
                record.update(action='ENTERED',plan=prepared['plan'],volume=prepared['volume'])
                cursor=(order.index(symbol)+1)%len(order)
                for other_symbol,_,_,other in candidates:
                    if other is not record and other['action']=='CANDIDATE':
                        other.update(action='REJECTED');other['blocks'].append('ONE_POSITION')
                break
        floating=0
        if position:
            symbol=position['symbol'];bar=bars[symbol].get(ts)
            if bar:
                result=exit_on_bar(position,bar,markets[symbol].specification,request.adverse_slippage_points)
                if result:settle(*result,ts+step)
                else:
                    mark=bar.close+(bar.spread_points*markets[symbol].specification.point if position['action']=='SELL' else 0)
                    model=HistoricalExecutor(config,markets[symbol],bar,balance,request.account_currency)
                    floating=model.profit(0 if position['action']=='BUY' else 1,symbol,position['volume'],position['entry_price'],mark)
                    position['mark_price']=mark;position['floating_pnl']=floating
            else:floating=position.get('floating_pnl',0)
        equity=balance+floating;peak=max(peak,equity);drawdown=max(drawdown,peak-equity);drawdown_pct=max(drawdown_pct,(peak-equity)/peak*100)
        curve.append(dict(time=ts+step,balance=round(balance,8),equity=round(equity,8),open_positions=int(position is not None)))
        if progress:progress(index+1,len(timeline))
    positives=sum(t['net_pnl'] for t in trades if t['net_pnl']>0)
    negatives=-sum(t['net_pnl'] for t in trades if t['net_pnl']<0)
    per_pair=[]
    for symbol in order:
        observations=[a for a in analyses if a['symbol']==symbol];closed=[t for t in trades if t['symbol']==symbol]
        per_pair.append(dict(symbol=symbol,analyses=len(observations),validated=sum(a['validation'].get('validated',False) for a in observations),
            entries=sum(a['action']=='ENTERED' for a in observations),closed_trades=len(closed),net_pnl=sum(t['net_pnl'] for t in closed),
            blocks=dict(Counter(code for a in observations for code in a['blocks']))))
    return dict(kind='FOREX_SL_TP',version=1,request_hash=digest(request.model_dump()),currency=request.account_currency,
        config=config,assumptions=request.model_dump(exclude={'markets'}),
        datasets=[dict(symbol=m.symbol,source=m.source,first_time=m.candles[0].time,last_time=m.candles[-1].time,
            candles=len(m.candles),hash=digest(m.model_dump()),specification=m.specification.model_dump(),specification_basis=m.specification_basis) for m in request.markets],
        evaluated_bars=len(timeline),start=timeline[0],end=timeline[-1]+step,closed_trades=len(trades),
        wins=sum(t['status']=='WIN' for t in trades),losses=sum(t['status']=='LOSS' for t in trades),
        realized_pnl=round(balance-request.initial_balance,8),closed_trade_net_pnl=round(sum(t['net_pnl'] for t in trades),8),
        ending_balance=round(balance,8),ending_equity=curve[-1]['equity'],max_drawdown=round(drawdown,8),max_drawdown_pct=round(drawdown_pct,6),
        win_rate=round(sum(t['status']=='WIN' for t in trades)/len(trades)*100,4) if trades else None,
        profit_factor=round(positives/negatives,6) if negatives else None,
        expectancy=round(sum(t['net_pnl'] for t in trades)/len(trades),8) if trades else None,
        ambiguous_bars=ambiguities,open_position=position,trades=trades,analyses=analyses,equity_curve=curve,per_pair=per_pair,
        evidence='NO_TRADES' if not trades else 'EXPLORATORY_ONLY',
        limitations=[
            'OHLC Bid; Ask aproximado pelo spread de cada vela. Sem sequência intravela nem ticks históricos.',
            'SL e TP na mesma vela: stop primeiro. Gaps no stop executam na abertura adversa; gaps favoráveis no TP limitados ao alvo.',
            'Sem expiração. Posição aberta no fim permanece aberta; não vira lucro realizado.',
            'Uma posição global; saída intravela registrada no fechamento da vela, sem reentrada na mesma vela.',
            'Valores de tick, margem por lote e permissões são constantes assumidas; não reproduzem câmbio/margem históricos ou order_check da corretora.',
            'Comissão por lado, slippage adverso por preenchimento e swap por dia UTC são hipóteses; swap não reproduz rollover triplo/calendário da XM.',
            'Curva e drawdown são amostrados em fechamento de vela; não capturam toda excursão intravela.',
            'Custos e gaps podem exceder risco estimado. Saldo inicial e meta em BRL usam as hipóteses informadas.',
            'Não demonstra rentabilidade futura. Confirme em período independente e depois em demo.'])
