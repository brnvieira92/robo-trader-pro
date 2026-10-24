"""Read-only MT5 research dataset capture, under the application's SDK guard."""
import time
import pandas as pd
from backend.forex_backtest import SymbolSpecification
from backend.mt5_execution import MT5ExecutionError


def capture_forex_history(bot, count):
    c=bot.config
    if c['market_type']!='MT5_FOREX':raise ValueError('Selecione MT5 Forex antes de carregar histórico.')
    account=bot.live.account(c.get('mt5_account_login'),c.get('mt5_account_server'))
    sdk=bot.live.sdk
    tf=getattr(sdk,'TIMEFRAME_'+c['timeframe'])
    symbols=[p['symbol'] for p in c['forex_pairs'] if p['enabled']] if c.get('forex_multi_enabled') else [c['symbol']]
    result=[]
    for symbol in symbols:
        if not sdk.symbol_select(symbol,True):raise MT5ExecutionError('Símbolo indisponível: '+symbol)
        info=sdk.symbol_info(symbol);tick=sdk.symbol_info_tick(symbol)
        if info is None or tick is None:raise MT5ExecutionError('Especificação/cotação indisponível: '+symbol)
        rates=sdk.copy_rates_from_pos(symbol,tf,0,count+1)
        if rates is None:raise MT5ExecutionError('Histórico indisponível: '+symbol)
        frame=pd.DataFrame(rates)
        if len(frame)<151 or 'spread' not in frame:raise MT5ExecutionError('Histórico insuficiente ou sem spread por vela: '+symbol)
        # Remove the forming candle; normalize only bar timestamps, never deal times.
        frame=frame.iloc[:-1].tail(count).copy()
        spec=SymbolSpecification(point=info.point,tick_size=info.trade_tick_size,digits=info.digits,
            tick_value_profit=info.trade_tick_value_profit,tick_value_loss=info.trade_tick_value_loss,
            volume_min=info.volume_min,volume_max=info.volume_max,volume_step=info.volume_step,
            stops_level=info.trade_stops_level,trade_mode=info.trade_mode,
            margin_per_lot=sdk.order_calc_margin(sdk.ORDER_TYPE_BUY,symbol,1.,float(tick.ask)))
        candles=[dict(time=int(row['time'])-c['mt5_bar_time_offset_minutes']*60,
            open=float(row['open']),high=float(row['high']),low=float(row['low']),close=float(row['close']),
            volume=float(row.get('tick_volume',row.get('volume',0))),spread_points=float(row['spread'])) for _,row in frame.iterrows()]
        result.append(dict(symbol=symbol,candles=candles,specification=spec.model_dump(),source='MT5',
            specification_basis='Snapshot atual do terminal; valores de tick, margem e permissões assumidos constantes no período histórico.'))
    return dict(config=dict(c),markets=result,account_currency=account['currency'],initial_balance=max(1,account['equity']),
        evaluated_bars=100,commission_per_lot_side=0,adverse_slippage_points=0,swap_cost_per_lot_utc_day=0,
        goal_fx_account_per_brl=None,assumptions_acknowledged=False,captured_at=time.time())
