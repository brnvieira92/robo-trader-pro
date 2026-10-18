"""Validated research configuration; execution stays in paper mode."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

STRATEGIES = Literal['bollinger_rsi_reversal', 'ema_rsi_crossover', 'price_action_breakout', 'multi_confluence', 'ml_random_forest']

class StrategyParams(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    ema_fast: int = Field(9, ge=2, le=100)
    ema_slow: int = Field(21, ge=3, le=200)
    rsi_period: int = Field(14, ge=2, le=100)
    rsi_oversold: float = Field(35, ge=5, le=49)
    rsi_overbought: float = Field(65, ge=51, le=95)
    bb_period: int = Field(20, ge=5, le=100)
    bb_std: float = Field(2, ge=.5, le=4)
    lookback_candles: int = Field(10, ge=3, le=100)
    sl_atr_mult: float = Field(1.5, ge=.5, le=5)
    tp_atr_mult: float = Field(2.5, ge=.5, le=10)
    use_ml_filter: bool = True

    @model_validator(mode='after')
    def ordered_emas(self):
        if self.ema_fast >= self.ema_slow:
            raise ValueError('EMA rápida deve ser menor que a lenta.')
        return self

class TradingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    execution_mode: Literal['PAPER'] = 'PAPER'
    market_type: Literal['SAFIRION_OB', 'MT5_B3', 'MT5_FOREX'] = 'SAFIRION_OB'
    safirion_sub_type: Literal['BINARY', 'DIGITAL', 'BLITZ'] = 'BINARY'
    symbol: str = Field('EURUSD', min_length=2, max_length=30, pattern=r'^[A-Za-z0-9_$./-]+$')
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1'] = 'M1'
    strategy_id: STRATEGIES = 'bollinger_rsi_reversal'
    min_confidence: float = Field(78, ge=50, le=100)
    entry_amount: float = Field(5, ge=1, le=100000)
    bankroll_mode: Literal['FIXED', 'PERCENT', 'SMART_AI', 'SOROS'] = 'FIXED'
    expiration_minutes: int = Field(1, ge=1, le=60)
    stop_gain: float = Field(500, gt=0, le=1000000)
    stop_loss: float = Field(250, gt=0, le=1000000)
    risk_per_trade_pct: float = Field(1, gt=0, le=2)
    max_consecutive_losses: int = Field(3, ge=1, le=10)
    max_daily_trades: int = Field(20, ge=1, le=200)
    max_tick_age_seconds: float = Field(5, ge=1, le=30)
    paper_balance: float = Field(1000, ge=0, le=10000000)
    payout_pct: float = Field(80, ge=1, le=100)
    use_martingale: Literal[False] = False
    martingale_factor: float = Field(2, ge=1, le=3)
    max_martingale_steps: Literal[0] = 0
    cooldown_seconds: int = Field(60, ge=5, le=3600)
    strategy_params: StrategyParams = Field(default_factory=StrategyParams)
