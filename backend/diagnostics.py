"""Persistent single-instrument decisions and bounded runtime recovery.

Observations explain existing decisions; they never approve or send an order.
"""
import hashlib
import json
import math
import time
import threading
from collections import OrderedDict


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,allow_nan=False).encode()).hexdigest()


def gate(code, passed=None, reason=''):
    return dict(code=code,status='NOT_EVALUATED' if passed is None else ('PASS' if passed else 'FAIL'),reason=reason)


def analysis_event(snapshot, last_bar, last_trade_timestamp):
    c=snapshot['config'];s=snapshot['signal'];q=snapshot['data_quality'];stats=snapshot['stats']
    v=s.get('indicators',{}).get('ml_validation',{});now=time.time();d=s.get('diagnostic') or {}
    checks=[gate('AUTOMATION_REQUESTED',snapshot['is_running']),gate('DIAGNOSTIC_STORE',snapshot.get('operational',{}).get('audit',{}).get('healthy',True)),gate('DATA_SOURCE',q['source']=='MT5_LIVE' if c['execution_mode']=='LIVE_MT5' else True),
            gate('HISTORY',q.get('history_fresh',False) and q['closed_candles']>=50,q.get('history_error') or ''),
            gate('SYMBOL_VERIFIED',q['symbol_verified'])]
    age=q.get('tick_age_seconds')
    checks.append(gate('TICK_FRESH',age is not None and -5<=age<=c['max_tick_age_seconds'] if q['source']=='MT5_LIVE' else None))
    validation_checks=v.get('validation_checks',{})
    ml_required=c['strategy_id']=='ml_random_forest' or c['strategy_params']['use_ml_filter']
    checks.extend([gate('ML_AVAILABLE',v.get('enabled',False) if ml_required else None,v.get('reason','')),
        gate('ML_ACCURACY',validation_checks.get('accuracy',v['accuracy']>v['baseline_accuracy']) if v.get('enabled') and ml_required else None),
        gate('ML_BALANCED',validation_checks.get('balanced',v['balanced_accuracy']>=55) if v.get('enabled') and ml_required else None),
        gate('ML_BRIER',validation_checks.get('brier',v.get('brier_score',1)<.25) if v.get('enabled') and ml_required else None),
        gate('ML_VALIDATED',v.get('validated',False) if ml_required else None),
        gate('ML_DIRECTION',d.get('directional_estimate',v.get('ml_confidence',0))>=60 if v.get('enabled') and c['strategy_params']['use_ml_filter'] else None),
        gate('SIGNAL_DIRECTION',s['action'] in ('BUY','SELL'),s['reason']),
        gate('MIN_CONFIDENCE',s['confidence']>=c['min_confidence']),
        gate('VOLATILITY',not d.get('extreme_volatility') if d else None)])
    goal=snapshot['goal']
    checks.extend([gate('ACCOUNT_PERMISSIONS',snapshot['diagnostics']['terminal_permissions'] if c['execution_mode']=='LIVE_MT5' else None),
        gate('ONE_POSITION',not snapshot['has_open_trade']),gate('DAILY_LOSS',stats['daily_pnl']>-c['stop_loss']),
        gate('GOAL_CONVERSION',goal['ready'],goal.get('error') or ''),gate('DAILY_GOAL',not goal['reached'] if goal['ready'] else None),
        gate('LOSS_STREAK',stats['loss_streak']<c['max_consecutive_losses']),
        gate('DAILY_ENTRIES',snapshot['entry_count']<c['max_daily_trades']),
        gate('COOLDOWN',now-last_trade_timestamp>=c['cooldown_seconds']),
        gate('UNUSED_BAR',s.get('bar_time')!=last_bar),gate('RISK_BUDGET',snapshot['risk']['next_amount']>0),
        gate('NATIVE_PREFLIGHT',None,'Revalidado apenas na tentativa real de execução.')])
    analysis_id=digest([c['execution_mode'],c.get('mt5_account_login'),c.get('mt5_account_server'),c['symbol'],c['timeframe'],c['strategy_id'],s.get('bar_time'),digest(c),v.get('model_version')])
    payload=dict(analysis_id=analysis_id,bar_time_basis='UTC' if q['source']=='MT5_LIVE' else 'SOURCE_LOCAL',action=s['action'],score=s['confidence'],raw_signal=s.get('diagnostic',{}),validation=v,
        reason=s['reason'],first_block=snapshot['risk']['blocked_reason'],checks=checks,
        data_quality=q,plans=snapshot.get('trade_plans',{}),risk=snapshot['risk'],goal=goal,
        automation_requested=snapshot['is_running'],config=c,config_version=digest(c),
        model_version=v.get('model_version'),dataset_version=v.get('dataset_version'))
    # Gate transitions are recorded, repeated panel reads do not inflate counts.
    identity=[c['execution_mode'],c.get('mt5_account_login'),c.get('mt5_account_server'),c['symbol'],c['timeframe'],
              c['strategy_id'],s.get('bar_time'),payload['config_version'],payload['model_version'],
              [(g['code'],g['status']) for g in checks],s['action'],s['confidence'],s['reason'],
              ('RETRY_WAIT' if (payload['first_block'] or '').startswith('Nova pré-validação') else payload['first_block']),snapshot['is_running']]
    return dict(event_key=digest(identity),kind='ANALYSIS',symbol=c['symbol'],timeframe=c['timeframe'],
                bar_time=s.get('bar_time'),code=('PAUSED' if not snapshot['is_running'] else ('CANDIDATE' if not payload['first_block'] else 'BLOCKED')),payload=payload)


class DiagnosticRecorder:
    def __init__(self,journal):
        self._lock = threading.RLock()
        self.journal=journal
        self.pending=OrderedDict()
        self.healthy=True;self.error=None;self.dropped=0
        self.last_key=None;self.last_event=None;self.last_flush=0.
        self.persisted_count=journal.diagnostic_count()

    def record(self,event):
        with self._lock:
            event={**event,'observed_at':time.time()}
            if event['event_key']!=self.last_key:
                if len(self.pending)>=1000:
                    self.pending.popitem(last=False);self.dropped+=1
                self.pending[event['event_key']]=event
                self.last_key=event['event_key'];self.last_event=event
            return self.flush()

    def flush(self):
        with self._lock:
            now=time.monotonic()
            if not self.healthy and now-self.last_flush<2:
                return False
            self.last_flush=now
            try:
                written=False
                for key in list(self.pending)[:100]:
                    self.journal.record_diagnostic(self.pending[key])
                    del self.pending[key]
                    written=True
                if written:self.persisted_count=self.journal.diagnostic_count()
                self.healthy=not self.pending
                self.error=None if self.healthy else 'Diagnósticos aguardando persistência.'
            except Exception:
                self.healthy=False
                self.error='Diário de diagnósticos indisponível; novas entradas bloqueadas até recuperação.'
            return self.healthy

    def status(self):
        with self._lock:
            return dict(healthy=self.healthy,persisted_count=self.persisted_count,pending=len(self.pending),dropped=self.dropped,error=self.error,
                        last_event_key=self.last_event['event_key'] if self.last_event else None)


class RecoveryState:
    """Error backoff never disables the user's automation request."""
    def __init__(self):
        self.faults={}
        self.alerted={}

    def fail(self,key,code,message):
        now=time.time();old=self.faults.get(key,{})
        attempts=old.get('attempts',0)+1
        delay=min(60,5*2**min(attempts-1,4))
        value=dict(code=code,message=message,attempts=attempts,retry_at=now+delay,
                   first_seen=old.get('first_seen',now),last_seen=now)
        self.faults[key]=value
        alert_key=(key,code)
        notify=now-self.alerted.get(alert_key,0)>=300
        if notify:self.alerted[alert_key]=now
        return value,notify

    def clear(self,key):
        return self.faults.pop(key,None)

    def retry_block(self):
        now=time.time()
        for key,fault in self.faults.items():
            if key=='preflight' and fault['retry_at']>now:
                return f"Nova pré-validação em {math.ceil(fault['retry_at']-now)}s: {fault['message']}"
        return None
