"""One-bar chronological binary research, not broker execution validation."""
import math
from typing import Literal
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.config import STRATEGIES, StrategyParams
from backend.strategies import StrategyEngine

class Candle(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    time: int = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(0, ge=0)

    @model_validator(mode='after')
    def ohlc(self):
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError('OHLC inconsistente.')
        return self

class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    candles: list[Candle] = Field(min_length=150, max_length=2000)
    symbol: str = Field('DATASET', min_length=1, max_length=30)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1'] = 'M1'
    strategy_id: STRATEGIES = 'multi_confluence'
    min_confidence: float = Field(78, ge=50, le=100)
    payout_pct: float = Field(80, ge=1, le=100)
    cost_per_trade: float = Field(0, ge=0, le=10)
    stake: float = Field(5, gt=0, le=10000)
    strategy_params: StrategyParams = Field(default_factory=StrategyParams)

    @model_validator(mode='after')
    def chronology(self):
        step = {'M1':60, 'M5':300, 'M15':900, 'M30':1800, 'H1':3600}[self.timeframe]
        if any(b.time-a.time != step for a,b in zip(self.candles,self.candles[1:])):
            raise ValueError('Informe velas fechadas, em ordem, únicas e contínuas no timeframe selecionado.')
        return self

def run_backtest(request: BacktestRequest):
    df = pd.DataFrame([c.model_dump() for c in request.candles])
    df['time'] = pd.to_datetime(df['time'], unit='s')
    pnl, equity, peak, drawdown = [], 0., 0., 0.
    wins = losses = draws = 0
    # Bound CPU: at most the latest 200 outcomes; every prediction uses past rows only.
    start = max(120, len(df)-200)
    for end in range(start, len(df)):
        history = df.iloc[max(0,end-300):end]
        signal = StrategyEngine.analyze(history, request.symbol, 'SAFIRION_OB', request.timeframe,
                                        request.strategy_id, {**request.strategy_params.model_dump(), 'data_source':'RESEARCH'})
        if signal.action not in ('BUY','SELL') or signal.confidence < request.min_confidence:
            continue
        # Enter at next open; label based on that bar's close. No future features.
        next_bar = df.iloc[end]
        movement = (float(next_bar['close'])-float(next_bar['open']))*(1 if signal.action=='BUY' else -1)
        win = movement > 0
        wins += int(win); losses += int(movement<0); draws += int(movement==0)
        profit = (request.stake * request.payout_pct/100 if win else (-request.stake if movement<0 else 0))-request.cost_per_trade
        pnl.append(profit); equity += profit; peak=max(peak,equity); drawdown=max(drawdown,peak-equity)
    count = len(pnl)
    resolved = wins+losses
    rate = wins/resolved if resolved else 0
    z=1.96
    center=(rate+z*z/(2*resolved))/(1+z*z/resolved) if resolved else 0
    margin=z*math.sqrt(rate*(1-rate)/resolved+z*z/(4*resolved*resolved))/(1+z*z/resolved) if resolved else 0
    positive=sum(x for x in pnl if x>0); negative=-sum(x for x in pnl if x<0)
    return dict(evaluated_bars=len(df)-start, trades=count, wins=wins, losses=losses, draws=draws,
                win_rate=round(rate*100,2), win_rate_ci95=[round(max(0,center-margin)*100,2),round(min(1,center+margin)*100,2)] if resolved else None,
                net_pnl=round(equity,2), max_drawdown=round(drawdown,2),
                profit_factor=round(positive/negative,3) if negative else None,
                expectancy=round(equity/count,4) if count else None,
                break_even_win_rate=round((1+request.cost_per_trade/request.stake)/(1+request.payout_pct/100)*100,2),
                evidence='INSUFFICIENT' if count<100 else 'RESEARCH_ONLY',
                limitations=['Horizonte de uma vela; não equivale à expiração da corretora.',
                             'Payout e custo constantes informados pelo usuário; sem latência ou slippage.',
                             'Não valida execução real nem identifica dados sintéticos enviados.',
                             'Exploração repetida do mesmo conjunto cria viés de seleção. Reserve outro período para confirmação.'])
