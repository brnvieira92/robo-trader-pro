"""Validated research configuration; explicit paper or native MT5 execution."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

STRATEGIES = Literal['bollinger_rsi_reversal', 'ema_rsi_crossover', 'price_action_breakout', 'multi_confluence', 'ml_random_forest', 'forex_trend_continuation', 'forex_pullback_retest','forex_triple_atr','forex_range_reentry','forex_squeeze_breakout']

FOREX_TECHNICAL_STRATEGIES=('forex_trend_continuation','forex_pullback_retest','forex_triple_atr','forex_range_reentry','forex_squeeze_breakout')
RESEARCH_ONLY_STRATEGIES=('forex_pullback_retest','forex_range_reentry','forex_squeeze_breakout')
DEMO_ONLY_STRATEGIES=('forex_triple_atr',)

def strategy_requires_ml(strategy_id,params):
    return strategy_id not in FOREX_TECHNICAL_STRATEGIES and (strategy_id=='ml_random_forest' or params.get('use_ml_filter',True))

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
    use_structure_stops: bool = True
    use_structure_targets: bool = True
    structure_lookback: int = Field(20, ge=5, le=100)
    structure_buffer_atr: float = Field(.15, ge=0, le=1)
    target_buffer_atr: float = Field(.1, ge=0, le=1)
    min_reward_risk: float = Field(1.5, ge=1, le=5)
    max_stop_atr: float = Field(4, ge=1, le=10)
    max_entry_drift_atr: float = Field(1, gt=0, le=5)
    trend_breakout_lookback: int = Field(5,ge=3,le=20)
    trend_stop_lookback: int = Field(3,ge=2,le=10)
    trend_max_extension_atr: float = Field(1.5,gt=0,le=3)
    trend_target_r: float = Field(2.,ge=1.5,le=3)

    @model_validator(mode='after')
    def ordered_emas(self):
        if self.ema_fast >= self.ema_slow:
            raise ValueError('EMA rápida deve ser menor que a lenta.')
        return self

class ForexPair(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pair: Literal['EURUSD', 'USDJPY', 'GBPUSD', 'USDCHF']
    symbol: str = Field(min_length=2, max_length=30, pattern=r'^[A-Za-z0-9_$./#-]+$')
    enabled: bool = False


class TradingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    execution_mode: Literal['PAPER', 'LIVE_MT5'] = 'PAPER'
    mt5_account_login: int | None = Field(None, gt=0)
    mt5_account_server: str | None = None
    mt5_tick_time_offset_minutes: int = Field(0, ge=-840, le=840)
    mt5_bar_time_offset_minutes: int = Field(0, ge=-840, le=840)
    mt5_deal_time_offset_minutes: int | None = Field(None, ge=-840, le=840)
    mt5_max_spread_points: float = Field(30, gt=0, le=100000)
    mt5_max_spread_stop_ratio: float = Field(.30, gt=0, le=1)
    mt5_deviation_points: int = Field(20, ge=0, le=1000)
    mt5_volume_cap: float = Field(1, gt=0, le=10000)
    mt5_cost_buffer: float = Field(0, ge=0, le=100000)
    mt5_magic: int = Field(202610, gt=0, le=2147483647)
    market_type: Literal['SAFIRION_OB', 'MT5_B3', 'MT5_FOREX'] = 'SAFIRION_OB'
    safirion_sub_type: Literal['BINARY', 'DIGITAL', 'BLITZ'] = 'BINARY'
    symbol: str = Field('EURUSD', min_length=2, max_length=30, pattern=r'^[A-Za-z0-9_$./#-]+$')
    forex_multi_enabled: bool = False
    forex_max_open_positions: int = Field(2, ge=1, le=2)
    forex_pairs: list[ForexPair] = Field(default_factory=list, max_length=4)
    timeframe: Literal['M1', 'M5', 'M15', 'M30', 'H1'] = 'M1'
    strategy_id: STRATEGIES = 'bollinger_rsi_reversal'
    min_confidence: float = Field(78, ge=50, le=100)
    entry_amount: float = Field(5, ge=1, le=100000)
    bankroll_mode: Literal['FIXED', 'PERCENT', 'SMART_AI', 'SOROS'] = 'FIXED'
    expiration_minutes: int = Field(1, ge=1, le=60)
    stop_gain: float = Field(150, gt=0, le=1000000)
    goal_currency: Literal['ACCOUNT','BRL'] = 'ACCOUNT'
    goal_fx_symbol: str = Field('', max_length=40, pattern=r'^[A-Za-z0-9_$.+/-]*$')
    profit_per_trade_cap: float = Field(15, gt=0, le=100000)
    paper_cost_per_trade: float = Field(0, ge=0, le=100000)
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

    @model_validator(mode='after')
    def native_execution_market(self):
        if self.strategy_id in FOREX_TECHNICAL_STRATEGIES and (self.market_type!='MT5_FOREX' or self.timeframe!='M1' or self.min_confidence<78):
            raise ValueError('Estratégias técnicas Forex exigem MT5 Forex, M1 e score técnico mínimo de 78.')
        if len({p.pair for p in self.forex_pairs}) != len(self.forex_pairs) or len({p.symbol for p in self.forex_pairs}) != len(self.forex_pairs):
            raise ValueError('Pares e símbolos exatos devem ser únicos.')
        if self.forex_multi_enabled:
            if self.market_type != 'MT5_FOREX' or self.execution_mode != 'LIVE_MT5':
                raise ValueError('Motor multipares exige MT5 Forex com conta vinculada; PAPER continua no modo individual.')
            if self.strategy_id not in ('ml_random_forest',*FOREX_TECHNICAL_STRATEGIES) or self.min_confidence < 78 or (self.strategy_id=='ml_random_forest' and not self.strategy_params.use_ml_filter):
                raise ValueError('Multipares exige RF com filtro ML, ou estratégia técnica Forex, e score mínimo de 78.')
            if not any(p.enabled for p in self.forex_pairs):
                raise ValueError('Selecione pelo menos um par para monitorar.')
            if self.symbol not in [p.symbol for p in self.forex_pairs if p.enabled]:
                raise ValueError('O símbolo do gráfico deve estar entre os pares selecionados.')
        if self.execution_mode == 'LIVE_MT5' and (self.mt5_account_login is None or not self.mt5_account_server):
            raise ValueError('Execução MT5 exige conta e servidor vinculados pela conexão do terminal.')
        if self.execution_mode == 'LIVE_MT5' and self.market_type == 'SAFIRION_OB':
            raise ValueError('Execução nativa MT5 exige mercado MT5 B3 ou MT5 Forex.')
        return self
