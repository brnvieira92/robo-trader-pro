"""Paper research orchestration, risk gates and auditable virtual settlements.
Live market feeds may inform research; this manager never sends broker orders.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import math
import uuid
import pandas as pd
from backend.config import TradingConfig
from backend.journal import PaperJournal
from backend.market_data import validate_candles

from backend.strategies import StrategyEngine, SignalResult
from backend.mt5_connector import MT5Connector
from backend.safirion_connector import SafirionConnector


class BotManager:
    def __init__(self):
        self.mt5 = MT5Connector()
        self.safirion = SafirionConnector()

        self.mt5.connect(use_simulation_if_unavailable=True)

        self.is_running = False
        self.journal = PaperJournal()
        self.config = TradingConfig.model_validate(self.journal.load_config() or {}).model_dump()
        self._session_day = datetime.now(timezone.utc).date().isoformat()
        self._order_lock = asyncio.Lock()
        self._last_signal_bar = None
        self._settlement_tasks = set()

        self.stats: Dict[str, Any] = {
            "daily_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "total_trades": 0,
            "win_rate": 0.0,
            "current_gale_step": 0,
            "win_streak": 0,
            "loss_streak": 0,
            "cycle_loss_accumulated": 0.0,
            "last_win_profit": 0.0,
            "next_suggested_amount": 5.0,
            "sizing_reason": "1x Base Analítica (R$ 5.00)",
            "open_trade_remaining_sec": 0,
            "status_reason": "Robô em espera (Pausado). Configure seu ativo e clique em LIGAR ROBÔ.",
        }

        self.trade_history: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, str]] = []
        self.last_signal: Optional[Dict[str, Any]] = None
        self._last_trade_timestamp: float = 0.0
        self._last_loss_timestamp: float = 0.0
        self._last_loss_action: Optional[str] = None
        self._has_open_trade: bool = False

        # Restarts retain session risk and never turn pending paper trades into wins.
        self.trade_history = self.journal.today()
        for trade in self.trade_history:
            if trade['status'] == 'OPEN':
                trade.update(status='INTERRUPTED', pnl=-float(trade['amount']), remaining_sec=0,
                             settlement_note='Simulação interrompida: reserva de risco integral debitada; não é resultado de mercado.')
                self.journal.save(trade)
        self._refresh_stats()
        self._last_signal_bar = self.trade_history[0].get('bar_time') if self.trade_history else None
        self._last_trade_timestamp = max((t.get('settled_at', t.get('created_at', 0)) for t in self.trade_history), default=0)
        self.add_log("INFO", "Modo PAPER: ordens virtuais, sem envio à corretora. Resultados não comprovam rentabilidade real.")

    def _refresh_stats(self):
        settled = [t for t in self.trade_history if t['status'] != 'OPEN']
        self.stats['daily_pnl'] = round(sum(t['pnl'] for t in settled), 2)
        self.stats['wins'] = sum(t['status'] == 'WIN' for t in settled)
        self.stats['losses'] = sum(t['status'] in ('LOSS', 'INTERRUPTED') for t in settled)
        self.stats['total_trades'] = len(settled)
        self.stats['win_rate'] = round(100 * self.stats['wins'] / len(settled), 1) if settled else 0
        streak = 0
        for t in settled:
            if t['status'] not in ('LOSS', 'INTERRUPTED'):
                break
            streak += 1
        self.stats['loss_streak'] = streak

    def add_log(self, level: str, message: str):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.insert(0, entry)
        self.logs = self.logs[:80]

    def update_config(self, new_cfg: Dict[str, Any]) -> Dict[str, Any]:
        if self.is_running or self._has_open_trade:
            raise ValueError("Pause o robô e aguarde a operação encerrar antes de alterar parâmetros.")
        payload = dict(new_cfg)
        # Legacy dashboard balance is virtual only, never a broker balance override.
        if 'custom_balance' in payload:
            payload['paper_balance'] = payload.pop('custom_balance')
        merged = {**self.config, **payload}
        if 'strategy_params' in payload:
            merged['strategy_params'] = {**self.config['strategy_params'], **payload['strategy_params']}
        validated = TradingConfig.model_validate(merged).model_dump()
        if self.trade_history and validated['paper_balance'] != self.config['paper_balance']:
            raise ValueError("Banca inicial não pode mudar após a primeira operação da sessão.")
        changed_stream = any(validated[k] != self.config[k] for k in ('symbol', 'timeframe', 'market_type'))
        self.journal.save_config(validated)
        self.config = validated
        if changed_stream:
            self.safirion.reset_candles()
            self._last_signal_bar = None
        self.add_log("CONFIG", f"Parâmetros validados: {self.config['symbol']} / {self.config['timeframe']} / PAPER")
        return self.config

    def _calculate_smart_entry_amount(self, signal=None):
        balance = max(0.0, self.config['paper_balance'] + self.stats['daily_pnl'])
        cap = min(balance * self.config['risk_per_trade_pct'] / 100,
                  max(0.0, self.config['stop_loss'] + self.stats['daily_pnl']))
        amount = balance * self.config['risk_per_trade_pct'] / 100 if self.config['bankroll_mode'] == 'PERCENT' else self.config['entry_amount']
        amount = math.floor(min(amount, cap) * 100) / 100
        minimum = 5 if self.config['market_type'] == 'SAFIRION_OB' else 1
        if amount < minimum:
            return 0.0, "Bloqueado: mínimo excede o orçamento de risco."
        return amount, f"PAPER R$ {amount:.2f} • limite {self.config['risk_per_trade_pct']}% da banca • sem progressão"

    def _risk_block(self, signal=None, manual=False):
        if self._has_open_trade:
            return "Já existe uma operação em andamento."
        if self.stats['daily_pnl'] <= -self.config['stop_loss']:
            return "Stop loss da sessão atingido."
        if self.stats['daily_pnl'] >= self.config['stop_gain']:
            return "Stop gain da sessão atingido."
        if self.stats['loss_streak'] >= self.config['max_consecutive_losses']:
            return "Limite de perdas consecutivas atingido."
        if len(self.trade_history) >= self.config['max_daily_trades']:
            return "Limite de operações da sessão atingido."
        if datetime.now().timestamp()-self._last_trade_timestamp < self.config['cooldown_seconds']:
            return "Intervalo mínimo entre operações ainda não cumprido."
        amount, reason = self._calculate_smart_entry_amount(signal)
        if amount <= 0:
            return reason
        if not manual and signal:
            if signal['action'] not in ('BUY', 'SELL') or signal['confidence'] < self.config['min_confidence']:
                return "Sem confluência técnica suficiente."
            if signal.get('bar_time') == self._last_signal_bar:
                return "Este candle já originou uma operação."
            validation = signal.get('indicators', {}).get('ml_validation', {})
            if self.config['strategy_params']['use_ml_filter'] and not validation.get('validated'):
                return "IA sem vantagem demonstrada na validação temporal."
        return None

    def get_market_snapshot(self) -> Dict[str, Any]:
        day = datetime.now(timezone.utc).date().isoformat()
        if day != self._session_day and not self._has_open_trade:
            self._session_day = day
            self.trade_history = self.journal.today()
            self._refresh_stats()
            self._last_signal_bar = None
            self.add_log('INFO', 'Nova sessão PAPER (dia UTC); diário anterior preservado no SQLite.')
        symbol = self.config["symbol"]
        timeframe = self.config["timeframe"]
        market_type = self.config["market_type"]
        strategy_id = self.config["strategy_id"]

        saf_live_df = (
            self.safirion.get_live_candles_df(timeframe)
            if market_type == "SAFIRION_OB"
            else None
        )
        live_safirion = market_type == 'SAFIRION_OB' and (self.safirion.bridge_ws is not None or bool(self.safirion._live_candles))
        if live_safirion:
            df = saf_live_df if saf_live_df is not None else pd.DataFrame(columns=['time','open','high','low','close','volume'])
            source = 'SAFIRION_LIVE'
        else:
            df = self.mt5.get_candles(symbol, timeframe, count=240)
            source = 'SIMULATED' if self.mt5.simulation_mode else 'MT5_LIVE'
        validate_candles(df)
        # The last candle is forming, so neither its signal nor label is usable.
        closed = df.iloc[:-1].copy()
        if df.empty:
            df = pd.DataFrame(columns=['time','open','high','low','close','volume'])
            closed = df.copy()

        # A market gap invalidates the old prefix, not every future candle.
        dropped_gap_candles = 0
        if len(closed) > 1:
            expected = self.mt5.TIMEFRAME_MAP.get(timeframe, 1)*60
            gaps = closed['time'].diff().dt.total_seconds().iloc[1:] != expected
            if gaps.any():
                dropped_gap_candles = int(gaps.to_numpy().nonzero()[0][-1]) + 1
                closed = closed.iloc[dropped_gap_candles:].copy()
        enriched = StrategyEngine.enrich_dataframe(df, self.config["strategy_params"])
        signal: SignalResult = StrategyEngine.analyze(
            df=closed,
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
            strategy_id=strategy_id,
            params={
                **self.config["strategy_params"],
                "expiration_minutes": self.config["expiration_minutes"],
                "data_source": source,
            },
        )
        self.last_signal = signal.to_dict()
        self.last_signal['bar_time'] = str(closed.iloc[-1]['time']) if len(closed) else None
        self.last_signal['price'] = float(df.iloc[-1]['close']) if len(df) else 0.0
        tick_age = max(0, datetime.now().timestamp()-self.safirion.last_tick_timestamp) if live_safirion else None
        if live_safirion and (tick_age is None or tick_age > self.config['max_tick_age_seconds']):
            self.last_signal.update(action='NEUTRAL', confidence=0, reason='Dados Safirion desatualizados: aguardando cotação válida.')
        data_quality = dict(source=source, closed_candles=len(closed), tick_age_seconds=round(tick_age, 1) if tick_age is not None else None, symbol_verified=(not live_safirion or self.safirion.symbol == symbol.upper().replace('/', '').replace('-', '')),
                            forming_candle_excluded=True, dropped_gap_candles=dropped_gap_candles, synthetic=source == 'SIMULATED')


        if not data_quality['symbol_verified']:
            self.last_signal.update(action='NEUTRAL', confidence=0, reason='Ativo da ponte não corresponde ao ativo selecionado.')
        # Atualiza sugestão de lote em tempo real pela Gestão de Banca IA
        suggested_amt, sizing_reason = self._calculate_smart_entry_amount(self.last_signal)
        self.stats["next_suggested_amount"] = suggested_amt
        self.stats["sizing_reason"] = sizing_reason

        candles_out = []
        for _, row in enriched.tail(60).iterrows():
            candles_out.append(
                {
                    "time": int(row["time"].timestamp()),
                    "time_str": row["time"].strftime("%H:%M"),
                    "open": round(float(row["open"]), 5),
                    "high": round(float(row["high"]), 5),
                    "low": round(float(row["low"]), 5),
                    "close": round(float(row["close"]), 5),
                    "ema_fast": round(float(row["ema_fast"]), 5),
                    "ema_slow": round(float(row["ema_slow"]), 5),
                    "bb_upper": round(float(row["bb_upper"]), 5),
                    "bb_lower": round(float(row["bb_lower"]), 5),
                    "rsi": round(float(row["rsi"]), 2),
                }
            )

        return {
            "execution_mode": "PAPER",
            "paper_balance": round(self.config['paper_balance'] + self.stats['daily_pnl'], 2),
            "data_quality": data_quality,
            "risk": {"blocked_reason": self._risk_block(self.last_signal), "max_risk_pct": self.config['risk_per_trade_pct'],
                     "remaining_loss_budget": max(0, self.config['stop_loss']+self.stats['daily_pnl']),
                     "next_amount": suggested_amt},
            "is_running": self.is_running,
            "config": self.config,
            "stats": self.stats,
            "signal": self.last_signal,
            "candles": candles_out,
            "trades": self.trade_history[:25],
            "logs": self.logs[:30],
            "connections": {
                "mt5": {
                    "connected": self.mt5.connected,
                    "simulation_mode": self.mt5.simulation_mode,
                    "account": self.mt5.account_info,
                },
                "safirion": {
                    "connected": self.safirion.connected,
                    "simulation_mode": self.safirion.simulation_mode,
                    "broker_url": self.safirion.broker_url,
                    "account_type": self.safirion.account_type,
                    "sub_type": self.config.get("safirion_sub_type", self.safirion.sub_type),
                    "call_bound": self.safirion.call_bound,
                    "put_bound": self.safirion.put_bound,
                    "ws_messages_received": self.safirion.ws_messages_received,
                    "last_live_price": self.safirion.last_live_price,
                    "balance": round(float(self.safirion.balance), 2),
                },
            },
        }

    def _calculate_target_expiration_time(self) -> tuple[datetime, int]:
        from datetime import timedelta

        now = datetime.now()
        market_type = self.config["market_type"]
        sub_type = self.config.get("safirion_sub_type", "BINARY")
        exp_min = max(1, int(self.config.get("expiration_minutes", 1)))

        if market_type == "SAFIRION_OB" and sub_type == "BLITZ":
            target_dt = now + timedelta(seconds=15)
            return target_dt, 15

        next_minute_boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        seconds_to_next_min = (next_minute_boundary - now).total_seconds()

        if exp_min == 1:
            if seconds_to_next_min >= 30:
                target_dt = next_minute_boundary
            else:
                target_dt = next_minute_boundary + timedelta(minutes=1)
        else:
            target_dt = now.replace(second=0, microsecond=0) + timedelta(minutes=exp_min)

        remaining_sec = max(5, int(round((target_dt - now).total_seconds())))
        return target_dt, remaining_sec

    async def execute_trade_from_signal(self, signal: Dict[str, Any], manual: bool = False) -> Dict[str, Any]:
        async with self._order_lock:
            if signal.get('action') not in ('BUY', 'SELL'):
                return {"success": False, "message": "Direção inválida ou neutra."}
            blocked = self._risk_block(signal, manual)
            if blocked:
                return {"success": False, "message": blocked}
            snapshot = self.get_market_snapshot()
            quality = snapshot['data_quality']
            if quality['source'] == 'SAFIRION_LIVE':
                if not quality['symbol_verified'] or quality['closed_candles'] < 50 or quality['tick_age_seconds'] > self.config['max_tick_age_seconds']:
                    return {"success": False, "message": "Histórico real insuficiente ou cotação desatualizada."}
            price = snapshot['signal']['price']
            if not math.isfinite(price) or price <= 0:
                return {"success": False, "message": "Preço inválido."}
            amount, sizing = self._calculate_smart_entry_amount(signal)
            target_dt, duration = self._calculate_target_expiration_time()
            trade = dict(id='PAPER-'+uuid.uuid4().hex, day=datetime.now(timezone.utc).date().isoformat(),
                         time=datetime.now().strftime('%H:%M:%S'), created_at=datetime.now().timestamp(), expiration_time=target_dt.strftime('%H:%M:%S'),
                         symbol=self.config['symbol'], market=self.config['market_type'], action=signal['action'],
                         amount=amount, sizing_reason=sizing, entry_price=price, confidence=signal['confidence'],
                         strategy=self.config['strategy_id'], status='OPEN', remaining_sec=duration, pnl=0.0,
                         mode='PAPER', source=quality['source'], timeframe=self.config['timeframe'],
                         payout_pct=self.config['payout_pct'], stop_loss=signal.get('stop_loss'),
                         take_profit=signal.get('take_profit'), bar_time=signal.get('bar_time'),
                         validation=signal.get('indicators', {}).get('ml_validation', {}))
            self.journal.save(trade)
            self._has_open_trade = True
            self._last_trade_timestamp = datetime.now().timestamp()
            self._last_signal_bar = signal.get('bar_time')
            self.trade_history.insert(0, trade)
            task = asyncio.create_task(self._settle_trade_after_real_expiration(trade, target_dt))
            self._settlement_tasks.add(task)
            task.add_done_callback(self._settlement_tasks.discard)
            self.add_log('TRADE', f"PAPER {trade['action']} {trade['symbol']} • R$ {amount:.2f} • {quality['source']}")
            return dict(success=True, ticket=trade['id'], mode='PAPER', message='Ordem virtual registrada; nenhum envio à corretora.')

    async def _settle_trade_after_real_expiration(self, trade, target_dt):
        try:
            while datetime.now() < target_dt:
                trade['remaining_sec'] = max(0, int((target_dt-datetime.now()).total_seconds()))
                self.stats['open_trade_remaining_sec'] = trade['remaining_sec']
                await asyncio.sleep(.5)
            if trade['source'] == 'SAFIRION_LIVE':
                if self.safirion.symbol != trade['symbol'].upper().replace('/', '').replace('-', ''):
                    raise ValueError('Ativo da ponte mudou durante a operação.')
                age = datetime.now().timestamp()-self.safirion.last_tick_timestamp
                if self.safirion.bridge_ws is None or age > self.config['max_tick_age_seconds']:
                    raise ValueError('Cotação real indisponível ao encerrar a simulação.')
                close_price = self.safirion.last_live_price
            else:
                if ('SIMULATED' if self.mt5.simulation_mode else 'MT5_LIVE') != trade['source']:
                    raise ValueError('Fonte de dados mudou durante a operação.')
                close_price = float(self.mt5.get_candles(trade['symbol'], trade['timeframe'], count=240)['close'].iloc[-1])
            change = close_price-trade['entry_price']
            direction = 1 if trade['action'] == 'BUY' else -1
            if trade['market'] == 'SAFIRION_OB':
                pnl = 0 if change == 0 else (trade['amount']*trade['payout_pct']/100 if change*direction>0 else -trade['amount'])
                note = 'PAPER: payout configurado, empate devolve a entrada; sem confirmação da corretora.'
            else:
                # Virtual unlevered notional, not MT5 lots or a made-up fixed win.
                pnl = trade['amount'] * change / trade['entry_price'] * direction
                note = 'PAPER: retorno sobre valor nominal, sem alavancagem, custos ou execução de stops; não representa lotes MT5.'
            trade.update(status='WIN' if pnl>0 else ('LOSS' if pnl<0 else 'DRAW'),
                         exit_price=close_price, pnl=round(pnl, 6), settlement_note=note)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            trade.update(status='INTERRUPTED', pnl=-trade['amount'],
                         settlement_note=f'Sem resultado verificável: reserva de risco debitada na simulação. {exc}')
            self.is_running = False
            self.add_log('ERROR', trade['settlement_note'])
        finally:
            # Cancellation leaves OPEN persisted for conservative recovery on restart.
            if trade['status'] != 'OPEN':
                trade['settled_at'] = datetime.now().timestamp()
            trade['remaining_sec'] = 0
            self.journal.save(trade)
            self._refresh_stats()
            self.stats['open_trade_remaining_sec'] = 0
            self._has_open_trade = False
            self._last_trade_timestamp = datetime.now().timestamp()
            self.add_log('INFO', f"PAPER {trade['id'][:14]}: {trade['status']} • R$ {trade['pnl']:.2f}")

    async def close(self):
        for task in tuple(self._settlement_tasks):
            task.cancel()
        await asyncio.gather(*self._settlement_tasks, return_exceptions=True)
        await self.safirion.close()

    async def run_loop(self):
        while True:
            try:
                snapshot = self.get_market_snapshot()
                if self.is_running:
                    signal = snapshot['signal']
                    blocked = self._risk_block(signal)
                    if blocked:
                        self.stats['status_reason'] = blocked + ' ' + signal['reason']
                    else:
                        result = await self.execute_trade_from_signal(signal)
                        self.stats['status_reason'] = result['message']
                    if self.stats['daily_pnl'] <= -self.config['stop_loss'] or self.stats['daily_pnl'] >= self.config['stop_gain'] or self.stats['loss_streak'] >= self.config['max_consecutive_losses'] or len(self.trade_history) >= self.config['max_daily_trades']:
                        self.is_running = False
                        self.add_log('ALERT', 'Limite de risco da sessão: robô pausado.')
            except Exception as exc:
                self.is_running = False
                self.stats['status_reason'] = 'Análise indisponível: execução pausada.'
                self.add_log('ERROR', f'Falha de análise: {exc}')
            await asyncio.sleep(1)
