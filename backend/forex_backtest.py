"""Causal, bid/ask OHLC Forex portfolio research with no SDK IO.

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
from backend.config import TradingConfig, strategy_requires_ml, FOREX_TECHNICAL_STRATEGIES
from backend.diagnostics import digest, preflight_code
from backend.ml_engine import MLEngine
from backend.mt5_execution import MT5Executor, MT5ExecutionError
from backend.strategies import StrategyEngine
from backend.portfolio_risk import position_limit
from backend.forex_statistics import financial_breakdown
from backend.forex_exit_research import ResearchExitManagement, update_research_stop
from backend.profit_exit import closed_observation, decision as profit_exit_decision
from backend.trade_plan import PRICING_VERSION


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
    candles: list[HistoricalBar] = Field(min_length=150,max_length=10000)
    source: Literal['MT5','UPLOADED']
    specification_basis: str = Field(min_length=3,max_length=300)


class ForexBacktestRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    config: TradingConfig
    markets: list[HistoricalMarket] = Field(min_length=1,max_length=4)
    account_currency: str = Field(min_length=3,max_length=8,pattern=r'^[A-Z]+$')
    initial_balance: float = Field(10000,gt=0,le=10000000)
    evaluated_bars: int = Field(100,ge=1,le=2000)
    commission_per_lot_side: float = Field(0,ge=0,le=10000)
    adverse_slippage_points: float = Field(0,ge=0,le=10000)
    swap_cost_per_lot_utc_day: float = Field(0,ge=0,le=10000)
    goal_fx_account_per_brl: float | None = Field(None,gt=0,le=1000000)
    captured_at: float | None = Field(None,ge=0)
    exit_management: ResearchExitManagement = Field(default_factory=ResearchExitManagement)
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
        if c.market_type!='MT5_FOREX': raise ValueError('Este laboratório aceita somente Forex.')
        if c.strategy_id not in ('ml_random_forest',*FOREX_TECHNICAL_STRATEGIES) or c.min_confidence<78 or (c.strategy_id=='ml_random_forest' and not c.strategy_params.use_ml_filter):
            raise ValueError('Use RF com filtro ML, ou estratégia técnica Forex, e score mínimo de 78.')
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
    historical_research=True
    def __init__(self, config, market, bar, balance, currency, portfolio=(), markets=None, equity=None,
                 commission_per_lot_side=0.,adverse_slippage_points=0.):
        self.spec=market.specification
        self.bar=bar;self.config=config;self.balance=balance;self.currency=currency
        self.markets=markets or {market.symbol:market}
        self.equity=balance if equity is None else equity
        self.commission_per_lot_side=commission_per_lot_side
        self.adverse_slippage_points=adverse_slippage_points
        self.margin=sum(t['volume']*self.markets[t['symbol']].specification.margin_per_lot for t in portfolio)
        positions=[NS(identifier=t['position_id'],ticket=t['position_id'],symbol=t['symbol'],
            type=0 if t['action']=='BUY' else 1,sl=t['stop_loss'],tp=t['take_profit'],
            volume=t['volume'],price_open=t['entry_price']) for t in portfolio]
        spec=self.spec
        info=NS(point=spec.point,trade_tick_size=spec.tick_size,digits=spec.digits,
                trade_stops_level=spec.stops_level,volume_min=spec.volume_min,volume_step=spec.volume_step,
                volume_max=spec.volume_max,trade_mode=spec.trade_mode,filling_mode=3,trade_exemode=2)
        self.sdk=NS(SYMBOL_TRADE_MODE_FULL=4,SYMBOL_TRADE_MODE_LONGONLY=1,SYMBOL_TRADE_MODE_SHORTONLY=2,
            ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1,SYMBOL_FILLING_FOK=1,SYMBOL_FILLING_IOC=2,
            ORDER_FILLING_FOK=0,ORDER_FILLING_IOC=1,TRADE_ACTION_DEAL=1,ORDER_TIME_GTC=0,
            symbol_select=lambda *a:True,symbol_info=lambda *a:info,
            positions_get=lambda **k:tuple(p for p in positions if not k.get('symbol') or p.symbol==k['symbol']),orders_get=lambda **k:(),
            order_calc_profit=self.planning_profit,order_calc_margin=lambda kind,symbol,volume,price:volume*spec.margin_per_lot,
            order_check=lambda request:NS(retcode=0))

    def account(self,*args,**kwargs):
        return dict(login=self.config.get('mt5_account_login') or 1,server=self.config.get('mt5_account_server') or 'HISTORICAL',
                    equity=self.equity,balance=self.balance,margin_free=max(0,self.equity-self.margin),currency=self.currency,account_type='RESEARCH')

    def _tick(self,symbol,config):
        return NS(bid=self.bar.open,ask=self.bar.open+self.bar.spread_points*self.spec.point)

    def _quote_timestamp(self, tick):
        return self.bar.time

    def profit(self,kind,symbol,volume,entry,exit):
        movement=(Decimal(str(exit))-Decimal(str(entry)))*(1 if kind==0 else -1)
        spec=self.markets[symbol].specification
        value=spec.tick_value_profit if movement>=0 else spec.tick_value_loss
        result=float(movement/Decimal(str(spec.tick_size))*Decimal(str(value))*Decimal(str(volume)))
        if not math.isfinite(result):raise MT5ExecutionError('Resultado monetário não finito; confira especificação e preços históricos.')
        return result

    def planning_profit(self,kind,symbol,volume,entry,exit):
        """Price known research friction BEFORE sizing, without altering fills.

        Existing positions have already reserved their own costs. Only the new
        symbol is surcharged here; portfolio verification retains gross reserves.
        Settlement always uses raw profit() and actual assumed fills/fees once.
        """
        raw=self.profit(kind,symbol,volume,entry,exit)
        if symbol!=self.config['symbol']:return raw
        value=self.spec.tick_value_profit if raw>=0 else self.spec.tick_value_loss
        friction=volume*(2*self.commission_per_lot_side+
            2*self.adverse_slippage_points*self.spec.point/self.spec.tick_size*value)
        if raw>0 and raw-friction<=0:
            raise MT5ExecutionError('Retorno/risco inviável após custos históricos assumidos.')
        return raw-friction


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
    model_context='HISTORICAL:'+digest(config)
    start=max(m.candles[0].time for m in request.markets)
    finish=min(m.candles[-1].time for m in request.markets)
    timeline=sorted({b.time for m in request.markets for b in m.candles if start<=b.time<=finish})[-request.evaluated_bars:]
    if not timeline:raise ValueError('Os períodos dos pares não se sobrepõem.')
    balance=request.initial_balance;positions=[];trades=[];analyses=[];curve=[]
    slots=position_limit(config);sequence=0
    daily=Counter();daily_entries=Counter();loss_streak=0;session=None;last_trade=-math.inf;cursor=0
    ambiguities=0;last_used={};peak=balance;drawdown=0;drawdown_pct=0
    goal=config['stop_gain']*(request.goal_fx_account_per_brl if config['goal_currency']=='BRL' and request.account_currency!='BRL' else 1)

    def day_key(ts):return datetime.fromtimestamp(ts,timezone.utc).date().isoformat()
    def post_cash(amount,ts):
        nonlocal balance
        balance=round(balance+amount,8)
        key=day_key(ts);daily[key]=round(daily[key]+amount,8)

    def observe(trade, prices):
        # Only full bars known to have remained open are used. On exit bars,
        # extremes could occur AFTER exit, so record only open/fill prices.
        direction=1 if trade['action']=='BUY' else -1
        moves=[direction*(price-trade['entry_price']) for price in prices]
        trade['sampled_mfe_price']=max(trade['sampled_mfe_price'],max(moves))
        trade['sampled_mae_price']=max(trade['sampled_mae_price'],-min(moves))
        risk=trade['initial_risk_distance']
        trade['sampled_mfe_r']=trade['sampled_mfe_price']/risk
        trade['sampled_mae_r']=trade['sampled_mae_price']/risk

    def settle(trade,exit_price,reason,ambiguous,ts):
        nonlocal balance,loss_streak,last_trade,ambiguities
        observe(trade,[exit_price])
        model=HistoricalExecutor(config,markets[trade['symbol']],bars[trade['symbol']][trade['entry_bar']],balance,request.account_currency)
        gross=model.profit(0 if trade['action']=='BUY' else 1,trade['symbol'],trade['volume'],trade['entry_price'],exit_price)
        commission=request.commission_per_lot_side*trade['volume']
        delta=gross-commission
        post_cash(delta,ts)
        net=round(gross-trade['entry_commission']-commission-trade['swap_cost'],8)
        trade.update(exit_price=exit_price,exit_time=ts,exit_reason=reason,ambiguous=ambiguous,
                     gross_pnl=gross,exit_commission=commission,net_pnl=net,status='WIN' if net>1e-8 else 'LOSS' if net< -1e-8 else 'DRAW')
        loss_streak=loss_streak+1 if net< -1e-8 else 0
        trades.append(trade);ambiguities+=int(ambiguous);last_trade=ts;positions.remove(trade)

    def equity_at_open(ts):
        floating=0
        for trade in positions:
            symbol=trade['symbol'];bar=bars[symbol][ts]
            mark=bar.open+(bar.spread_points*markets[symbol].specification.point if trade['action']=='SELL' else 0)
            model=HistoricalExecutor(config,markets[symbol],bar,balance,request.account_currency)
            floating+=model.profit(0 if trade['action']=='BUY' else 1,symbol,trade['volume'],trade['entry_price'],mark)
        return balance+floating

    for index,ts in enumerate(timeline):
        day=day_key(ts)
        if day!=session:
            session=day
            # A UTC date change resets session limits, not the balance or position.
            loss_streak=0
            for trade in reversed(trades):
                if day_key(trade['exit_time'])!=day or trade['status']!='LOSS':break
                loss_streak+=1
        for position in list(positions):
            current=bars[position['symbol']].get(ts)
            if current is None:
                raise ValueError('Lacuna no histórico de '+position['symbol']+' com posição aberta; não é possível reconstruir SL/TP. Revise a base antes de concluir rentabilidade.')
            if current:
                elapsed_days=(ts//86400)-(position['last_swap_time']//86400)
                cost=max(0,elapsed_days)*request.swap_cost_per_lot_utc_day*position['volume']
                position['swap_cost']+=cost;post_cash(-cost,ts);position['last_swap_time']=ts
                mark=current.open+(current.spread_points*markets[position['symbol']].specification.point if position['action']=='SELL' else 0)
                observe(position,[mark])
                result=exit_on_bar(position,current,markets[position['symbol']].specification,request.adverse_slippage_points)
                if result and result[1].startswith('GAP_'):settle(position,*result,ts)
        if config['profit_exit']['enabled']:
            # Evaluate only last CLOSED bars and executable next-open Bid/Ask.
            # Current candle high/low/close cannot influence this decision.
            for position in list(positions):
                symbol=position['symbol'];bar=bars[symbol][ts];spec=markets[symbol].specification
                policy=config['profit_exit'];buy=position['action']=='BUY'
                frame=frames[symbol];history=frame[frame['time']<pd.Timestamp(ts,unit='s')].tail(60)
                try:observation=closed_observation(history,ts,position['entry_time'],config['timeframe'])
                except ValueError:continue
                slip=max(request.adverse_slippage_points,min(policy['slippage_points'],config['mt5_deviation_points']))
                quote=bar.open+(0 if buy else bar.spread_points*spec.point)
                fill=quote+(-1 if buy else 1)*slip*spec.point
                adapter=HistoricalExecutor(config,markets[symbol],bar,balance,request.account_currency)
                gross=adapter.profit(0 if buy else 1,symbol,position['volume'],position['entry_price'],fill)
                net=gross-position['entry_commission']-request.commission_per_lot_side*position['volume']-position['swap_cost']-policy['cost_buffer']
                profit_peak=max(position.get('peak_exit_net',0),net);position['peak_exit_net']=profit_peak
                gate=profit_exit_decision(position['action'],observation,net,profit_peak,bar.open,policy)
                position['last_profit_exit_decision']=gate
                if gate['eligible']:settle(position,fill,'EARLY_PROFIT_WEAKNESS',False,ts)
        candidates=[];available_atr={}
        for symbol in order:
            bar=bars[symbol].get(ts)
            history=frames[symbol]
            history=history[history['time']<pd.Timestamp(ts,unit='s')].tail(239).copy()
            gaps=history['time'].diff().dt.total_seconds().iloc[1:]!=step
            if gaps.any():history=history.iloc[int(gaps.to_numpy().nonzero()[0][-1])+1:].copy()
            signal=StrategyEngine.analyze(history,symbol,'MT5_FOREX',config['timeframe'],config['strategy_id'],
                {**config['strategy_params'],'data_source':'HISTORICAL_FOREX'},model_engine=engines[symbol],
                model_context=model_context).to_dict()
            ml=signal.get('indicators',{}).get('ml_validation',{})
            available_atr[symbol]=float(signal.get('indicators',{}).get('atr',0))
            blocks=[]
            if not bar:blocks.append('BAR_MISSING')
            if len(history)<50:blocks.append('HISTORY')
            if strategy_requires_ml(config['strategy_id'],config['strategy_params']):
                if len(history)<100:blocks.append('ML_HISTORY')
                if not ml.get('validated'):blocks.append('ML_VALIDATED')
            if ml.get('validation_method')=='walk_forward_v2' and not (ml.get('walk_forward') or {}).get('validated'):
                blocks.append('ML_WALK_FORWARD')
            blocks.extend(c['code'] for c in (signal.get('indicators',{}).get('entry_confirmation') or {}).get('checks',[]) if not c['passed'])
            if signal['action'] not in ('BUY','SELL'):blocks.append('SIGNAL_DIRECTION')
            if signal['confidence']<config['min_confidence']:blocks.append('MIN_CONFIDENCE')
            if (signal.get('diagnostic') or {}).get('extreme_volatility'):blocks.append('VOLATILITY')
            if len(positions)>=slots:blocks.append('ONE_POSITION' if slots==1 else 'POSITION_LIMIT')
            if any(t['symbol']==symbol for t in positions):blocks.append('SYMBOL_POSITION')
            if daily[day]<=-config['stop_loss']:blocks.append('DAILY_LOSS')
            if daily[day]>=goal:blocks.append('DAILY_GOAL')
            if daily_entries[day]>=config['max_daily_trades']:blocks.append('DAILY_ENTRIES')
            if loss_streak>=config['max_consecutive_losses']:blocks.append('LOSS_STREAK')
            if ts-last_trade<config['cooldown_seconds']:blocks.append('COOLDOWN')
            bar_time=str(history.iloc[-1]['time']) if len(history) else None
            if bar_time and last_used.get(symbol)==bar_time:blocks.append('UNUSED_BAR')
            record=dict(symbol=symbol,time=ts,signal=signal['action'],score=signal['confidence'],
                entry_confirmation=signal.get('indicators',{}).get('entry_confirmation'),
                entry_policy_version=signal.get('indicators',{}).get('entry_policy_version','legacy'),
                ml_confidence=ml.get('ml_confidence'),validation=ml,bar_time=bar_time,blocks=blocks,
                action='REJECTED' if blocks else 'CANDIDATE',unavailable_checks=['HISTORICAL_TICK_AGE','HISTORICAL_ACCOUNT_PERMISSIONS','BROKER_ORDER_CHECK'])
            analyses.append(record)
            if not blocks:
                prepared_signal={**signal,'bar_time':bar_time}
                candidates.append((symbol,bar,prepared_signal,record))
        if len(positions)<slots:
            rank={symbol:(order.index(symbol)-cursor)%len(order) for symbol in order}
            for symbol,bar,signal,record in sorted(candidates,key=lambda item:rank[item[0]]):
                # Recheck after each reservation: candidates share limits and
                # cooldown, even when their initial projections were eligible.
                if len(positions)>=slots or any(t['symbol']==symbol for t in positions):
                    record.update(action='REJECTED');record['blocks'].append('ONE_POSITION' if slots==1 else 'POSITION_LIMIT')
                    continue
                if ts-last_trade<config['cooldown_seconds']:
                    record.update(action='REJECTED');record['blocks'].append('COOLDOWN');continue
                if daily_entries[day]>=config['max_daily_trades']:
                    record.update(action='REJECTED');record['blocks'].append('DAILY_ENTRIES');continue
                cfg={**config,'symbol':symbol}
                adapter=HistoricalExecutor(cfg,markets[symbol],bar,balance,request.account_currency,
                    portfolio=positions,markets=markets,equity=equity_at_open(ts),
                    commission_per_lot_side=request.commission_per_lot_side,
                    adverse_slippage_points=request.adverse_slippage_points)
                try:
                    prepared=adapter.prepare(cfg,signal,max(0,config['stop_loss']+daily[day]),'HISTORICAL',max(0,goal-daily[day]),portfolio_trades=positions)
                except MT5ExecutionError as exc:
                    record.update(action='REJECTED',preflight_reason=str(exc));record['blocks'].append(preflight_code(str(exc)))
                    continue
                spec=markets[symbol].specification;buy=signal['action']=='BUY'
                entry=prepared['price']+(1 if buy else -1)*request.adverse_slippage_points*spec.point
                fee=request.commission_per_lot_side*prepared['volume']
                # Slippage/cost assumptions must not silently invalidate the
                # same per-position and global reservations used in production.
                stop_fill=prepared['stop_loss']-(1 if buy else -1)*request.adverse_slippage_points*spec.point
                risk=max(prepared['estimated_risk'],-adapter.profit(0 if buy else 1,symbol,prepared['volume'],entry,stop_fill)+config['mt5_cost_buffer']+2*fee)
                record['cost_preflight']=dict(assumed_risk=risk,risk_budget=prepared['risk_budget'],
                    round_trip_commission=2*fee,slippage_points_per_fill=request.adverse_slippage_points)
                if risk>prepared['risk_budget']+1e-8 or not (prepared['stop_loss']<entry<prepared['take_profit'] if buy else prepared['take_profit']<entry<prepared['stop_loss']):
                    record.update(action='REJECTED',preflight_reason='Slippage/custos excedem a reserva de risco ou o plano de preços.');record['blocks'].append('RISK_BUDGET');continue
                target_fill=prepared['take_profit']-(1 if buy else -1)*request.adverse_slippage_points*spec.point
                reward=adapter.profit(0 if buy else 1,symbol,prepared['volume'],entry,target_fill)-config['mt5_cost_buffer']-2*fee
                record['cost_preflight'].update(assumed_reward=reward,net_reward_risk=reward/risk,
                    required_reward_risk=config['strategy_params']['min_reward_risk'])
                if reward/risk+1e-8<config['strategy_params']['min_reward_risk']:
                    record.update(action='REJECTED',preflight_reason='Retorno/risco líquido insuficiente após custos e slippage assumidos.');record['blocks'].append('REWARD_RISK');continue
                sequence+=1
                position=dict(symbol=symbol,action=signal['action'],volume=prepared['volume'],entry_price=entry,
                    id='HISTORICAL-'+str(sequence),source='HISTORICAL_FOREX',simulated=True,
                    mode='LIVE_MT5',protected=True,position_id=sequence,
                    account_login=prepared['account']['login'],account_server=prepared['account']['server'],
                    filled_volume=prepared['volume'],reserved_risk=risk,cost_buffer=config['mt5_cost_buffer']+2*fee,
                    quoted_entry=prepared['price'],entry_time=ts,entry_bar=ts,stop_loss=prepared['stop_loss'],take_profit=prepared['take_profit'],
                    initial_stop_loss=prepared['stop_loss'],initial_risk_distance=abs(entry-prepared['stop_loss']),
                    cost_buffer_configured=config['mt5_cost_buffer'],stop_updates=[],exit_management_activated=False,
                    entry_commission=fee,swap_cost=0,last_swap_time=ts,risk_budget=prepared['risk_budget'],
                    estimated_risk=risk,plan=prepared['plan'],entry_diagnostics=prepared['entry_diagnostics'],status='OPEN',signal_bar=signal['bar_time'],
                    entry_policy_version=record['entry_policy_version'],entry_confirmation=record['entry_confirmation'],
                    signal_score=signal['confidence'],sampled_mfe_price=0.,sampled_mae_price=0.,sampled_mfe_r=0.,sampled_mae_r=0.,
                    assumed_net_reward_risk=reward/risk,
                    excursion_basis='LOWER_BOUND_CLOSED_OHLC; exit bars use open/fill only, not unknown extrema')
                positions.append(position)
                post_cash(-fee,ts);daily_entries[day]+=1;last_trade=ts;last_used[symbol]=signal['bar_time']
                record.update(action='ENTERED',plan=prepared['plan'],volume=prepared['volume'])
                cursor=(order.index(symbol)+1)%len(order)
        floating=0
        for position in list(positions):
            symbol=position['symbol'];bar=bars[symbol].get(ts)
            if bar:
                result=exit_on_bar(position,bar,markets[symbol].specification,request.adverse_slippage_points)
                if result:settle(position,*result,ts+step)
                else:
                    shift=bar.spread_points*markets[symbol].specification.point if position['action']=='SELL' else 0
                    observe(position,[bar.high+shift,bar.low+shift])
                    mark=bar.close+(bar.spread_points*markets[symbol].specification.point if position['action']=='SELL' else 0)
                    model=HistoricalExecutor(config,markets[symbol],bar,balance,request.account_currency)
                    pnl=model.profit(0 if position['action']=='BUY' else 1,symbol,position['volume'],position['entry_price'],mark)
                    floating+=pnl
                    position['mark_price']=mark;position['floating_pnl']=pnl
                    update_research_stop(position,bar,markets[symbol].specification,available_atr.get(symbol,0),
                        request.exit_management,request.commission_per_lot_side,request.adverse_slippage_points,ts+step)
            else:floating+=position.get('floating_pnl',0)
        equity=balance+floating;peak=max(peak,equity);drawdown=max(drawdown,peak-equity);drawdown_pct=max(drawdown_pct,(peak-equity)/peak*100)
        curve.append(dict(time=ts+step,balance=round(balance,8),equity=round(equity,8),open_positions=len(positions),
            reserved_risk=sum(t['reserved_risk'] for t in positions)))
        if progress:progress(index+1,len(timeline))
    positives=sum(t['net_pnl'] for t in trades if t['net_pnl']>0)
    negatives=-sum(t['net_pnl'] for t in trades if t['net_pnl']<0)
    per_pair=[]
    for symbol in order:
        observations=[a for a in analyses if a['symbol']==symbol];closed=[t for t in trades if t['symbol']==symbol]
        per_pair.append(dict(symbol=symbol,analyses=len(observations),validated=sum(a['validation'].get('validated',False) for a in observations),
            validation_kind='TECHNICAL_RULES' if config['strategy_id'] in FOREX_TECHNICAL_STRATEGIES else 'ML',
            technical_qualified=sum(a['signal'] in ('BUY','SELL') and a['score']>=config['min_confidence'] for a in observations),
            entries=sum(a['action']=='ENTERED' for a in observations),closed_trades=len(closed),net_pnl=sum(t['net_pnl'] for t in closed),
            blocks=dict(Counter(code for a in observations for code in a['blocks']))))
    # Production-shaped identifiers exist only inside the historical adapter.
    # Exports must not label simulated positions as terminal fills/protections.
    for trade in [*trades,*positions]:
        for key in ('mode','protected','position_id','account_login','account_server'):
            trade.pop(key,None)
    return dict(kind='FOREX_SL_TP',version=6,pricing_version=PRICING_VERSION,cost_sizing_version='known-friction-before-lot-v1',
        request_hash=digest(request.model_dump()),currency=request.account_currency,
        config=config,assumptions=request.model_dump(exclude={'markets'}),
        datasets=[dict(symbol=m.symbol,source=m.source,first_time=m.candles[0].time,last_time=m.candles[-1].time,
            candles=len(m.candles),hash=digest(m.model_dump()),specification=m.specification.model_dump(),specification_basis=m.specification_basis) for m in request.markets],
        evaluated_bars=len(timeline),start=timeline[0],end=timeline[-1]+step,closed_trades=len(trades),
        wins=sum(t['status']=='WIN' for t in trades),losses=sum(t['status']=='LOSS' for t in trades),
        draws=sum(t['status']=='DRAW' for t in trades),
        realized_pnl=round(balance-request.initial_balance,8),closed_trade_net_pnl=round(sum(t['net_pnl'] for t in trades),8),
        ending_balance=round(balance,8),ending_equity=curve[-1]['equity'],max_drawdown=round(drawdown,8),max_drawdown_pct=round(drawdown_pct,6),
        win_rate=round(sum(t['status']=='WIN' for t in trades)/len(trades)*100,4) if trades else None,
        profit_factor=round(positives/negatives,6) if negatives else None,
        expectancy=round(sum(t['net_pnl'] for t in trades)/len(trades),8) if trades else None,
        ambiguous_bars=ambiguities,open_position=positions[0] if len(positions)==1 else None,open_positions=positions,
        max_open_positions=slots,financial_breakdown=financial_breakdown(trades),
        trades=trades,analyses=analyses,equity_curve=curve,per_pair=per_pair,
        evidence='EXPLORATORY_ONLY' if trades else 'NO_CLOSED_TRADES' if positions else 'NO_TRADES',
        limitations=[
            'Saída antecipada por fraqueza, se habilitada: usa somente velas fechadas e a abertura seguinte, com custos/slippage. Não equivale ao monitoramento de ticks do MT5.',
            'Custos/slippage assumidos entram no cálculo do lote antes da reserva; isso pode alterar resultados de versões anteriores que rejeitavam o lote sem recalculá-lo.',
            'Saídas dinâmicas são pesquisa: ativação pelo fechamento, ajuste válido na próxima vela, reserva original mantida. Não alteram stops do MT5.',
            'OHLC Bid; Ask aproximado pelo spread de cada vela. Sem sequência intravela nem ticks históricos.',
            'SL e TP na mesma vela: stop primeiro. Gaps no stop executam na abertura adversa; gaps favoráveis no TP limitados ao alvo.',
            'Sem expiração. Posição aberta no fim permanece aberta; não vira lucro realizado.',
            f'Até {slots} posições em símbolos distintos, risco/margem compartilhados e cooldown global; saídas intravela registradas no fechamento da vela.',
            'Excursões favoráveis/adversas são limites inferiores observados; extremos da vela de saída são excluídos porque podem ocorrer após o fechamento.',
            'Valores de tick, margem por lote e permissões são constantes assumidas; não reproduzem câmbio/margem históricos ou order_check da corretora.',
            'Comissão por lado, slippage adverso por preenchimento e swap por dia UTC são hipóteses; swap não reproduz rollover triplo/calendário da XM.',
            'Curva e drawdown são amostrados em fechamento de vela; não capturam toda excursão intravela.',
            'Custos e gaps podem exceder risco estimado. Saldo inicial e meta em BRL usam as hipóteses informadas.',
            'Não demonstra rentabilidade futura. Confirme em período independente e depois em demo.'])
