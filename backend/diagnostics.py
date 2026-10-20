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
    checks = explain_checks(checks, snapshot, last_bar, last_trade_timestamp)
    payload=dict(analysis_id=analysis_id,bar_time_basis='UTC' if q['source']=='MT5_LIVE' else 'SOURCE_LOCAL',action=s['action'],score=s['confidence'],raw_signal=s.get('diagnostic',{}),validation=v,
        failed_checks=[g for g in checks if g['status']=='FAIL' and not g.get('informational')],reason=s['reason'],first_block=snapshot['risk']['blocked_reason'],checks=checks,
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


LABELS = {
    'ML_CLOSED_HISTORY':'Velas fechadas para ML', 'ML_TRAINING_SAMPLES':'Amostras de treinamento',
    'ML_TEST_SAMPLES':'Amostras de validação', 'ML_TRAIN_CLASSES':'Classes no treinamento', 'ML_TEST_CLASSES':'Classes na validação',
    'SPREAD_LIMIT':'Spread máximo', 'MINIMUM_VOLUME':'Lote mínimo e orçamento', 'MARGIN':'Margem livre suficiente',
    'REWARD_RISK':'Retorno/risco líquido mínimo', 'STRUCTURAL_STOP':'Distância máxima do stop', 'ENTRY_DRIFT':'Desvio desde o sinal',
    'SYMBOL_UNAVAILABLE':'Disponibilidade do símbolo', 'ORDER_CHECK':'Pré-validação da corretora',
    'AUTOMATION_REQUESTED':'Execução automática solicitada', 'DIAGNOSTIC_STORE':'Diário de diagnóstico disponível',
    'DATA_SOURCE':'Dados reais MT5', 'HISTORY':'Histórico contínuo e atual', 'SYMBOL_VERIFIED':'Símbolo correspondente',
    'TICK_FRESH':'Idade da cotação', 'ML_AVAILABLE':'Modelo disponível', 'ML_ACCURACY':'Acurácia acima da referência',
    'ML_BALANCED':'Acurácia balanceada', 'ML_BRIER':'Erro probabilístico Brier', 'ML_VALIDATED':'Validação temporal aprovada',
    'ML_DIRECTION':'Força direcional ML', 'SIGNAL_DIRECTION':'Sinal de compra ou venda', 'MIN_CONFIDENCE':'Score final do sinal',
    'VOLATILITY':'Volatilidade aceitável', 'ACCOUNT_PERMISSIONS':'Permissões da conta e terminal',
    'ONE_POSITION':'Vaga global para posição/intenção', 'DAILY_LOSS':'Perda diária', 'GOAL_CONVERSION':'Conversão da meta disponível',
    'DAILY_GOAL':'Meta diária ainda não atingida', 'LOSS_STREAK':'Sequência de perdas', 'DAILY_ENTRIES':'Entradas no dia UTC',
    'COOLDOWN':'Intervalo desde a última operação', 'UNUSED_BAR':'Candle ainda não operado', 'RISK_BUDGET':'Orçamento disponível',
    'NATIVE_PREFLIGHT':'Pré-validação de ordem MT5', 'SYMBOL_TRADE_MODE':'Permissão de negociação do símbolo',
    'GLOBAL_EXPOSURE':'Exposição global da conta', 'PAIR_RETRY':'Pré-validação local em recuperação',
    'ML_ESTIMATE_MIN':'Estimativa direcional do modelo', 'BUY_PLAN':'Plano de compra', 'SELL_PLAN':'Plano de venda',
}


def explain_checks(checks, snapshot, last_bar, last_trade_timestamp):
    """Decorate existing gate outcomes; never recompute or approve a decision."""
    c=snapshot['config']; signal=snapshot['signal']; q=snapshot['data_quality']; stats=snapshot['stats']
    v=signal.get('indicators',{}).get('ml_validation',{}); goal=snapshot['goal']
    values={
        'AUTOMATION_REQUESTED':(snapshot['is_running'],'=',True,''),
        'DIAGNOSTIC_STORE':(snapshot.get('operational',{}).get('audit',{}).get('healthy',True),'=',True,''),
        'DATA_SOURCE':(q.get('source'),'=','MT5_LIVE' if c['execution_mode']=='LIVE_MT5' else 'fonte identificada (PAPER)',''),
        'HISTORY':(q.get('closed_candles'),'>=',50,'velas; exige histórico atual'),
        'SYMBOL_VERIFIED':(q.get('symbol_verified'),'=',True,''),
        'TICK_FRESH':(q.get('tick_age_seconds'),'entre',[-5,c['max_tick_age_seconds']],'s'),
        'ML_AVAILABLE':(v.get('enabled'),'=',True,''),
        'ML_ACCURACY':(v.get('accuracy'),'>',v.get('baseline_accuracy'),'%'),
        'ML_BALANCED':(v.get('balanced_accuracy'),'>=',55,'%'),
        'ML_BRIER':(v.get('brier_score'),'<',.25,''),
        'ML_VALIDATED':(v.get('validated'),'=',True,''),
        'ML_DIRECTION':(v.get('ml_confidence'),'>=',60,'%'),
        'SIGNAL_DIRECTION':(signal['action'],'em',['BUY','SELL'],''),
        'MIN_CONFIDENCE':(signal['confidence'],'>=',c['min_confidence'],'%'),
        'VOLATILITY':((signal.get('diagnostic') or {}).get('extreme_volatility'),'=',False,'amplitude da vela deve ser ≤ 3 × ATR'),
        'ACCOUNT_PERMISSIONS':(snapshot.get('diagnostics',{}).get('terminal_permissions'),'=',True,''),
        'ONE_POSITION':(snapshot['has_open_trade'],'=',False,''),
        'DAILY_LOSS':(stats['daily_pnl'],'>',-c['stop_loss'],'moeda da conta'),
        'GOAL_CONVERSION':(goal['ready'],'=',True,''), 'DAILY_GOAL':(goal['reached'],'=',False,''),
        'LOSS_STREAK':(stats['loss_streak'],'<',c['max_consecutive_losses'],'perdas'),
        'DAILY_ENTRIES':(snapshot['entry_count'],'<',c['max_daily_trades'],'entradas'),
        'COOLDOWN':(round(max(0,time.time()-last_trade_timestamp),2),'>=',c['cooldown_seconds'],'s'),
        'UNUSED_BAR':(signal.get('bar_time'),'diferente de',last_bar,''),
        'RISK_BUDGET':(snapshot['risk']['next_amount'],'>',0,'moeda da conta'),
    }
    decorated=[]
    for check in checks:
        g={**check,'label':LABELS.get(check['code'],check['code'])}
        if check['code']=='DATA_SOURCE' and c['execution_mode']=='PAPER':
            g['label']='Fonte PAPER identificada'
        if check['code'] in values:
            g.update(zip(('observed','operator','required','unit'),values[check['code']]))
        if check['code'] in ('ML_ACCURACY','ML_BALANCED','ML_BRIER'):
            g['note']='Métrica exibida arredondada; resultado usa o critério exato do modelo.'
        decorated.append(g)
    # Raw estimate and executable score are intentionally separate: a rejected
    # ML validation can force executable confidence to zero despite a high estimate.
    if v.get('enabled'):
        decorated.append(dict(code='ML_ESTIMATE_MIN',label=LABELS['ML_ESTIMATE_MIN'],
            status='PASS' if v.get('ml_confidence',0)>=c['min_confidence'] else 'FAIL',
            observed=v.get('ml_confidence'),operator='>=',required=c['min_confidence'],unit='%',
            informational=True,reason='Estimativa do modelo; não substitui o score final nem aprova entrada.'))
    if c['strategy_id']=='ml_random_forest' or c['strategy_params']['use_ml_filter']:
        details=[('ML_CLOSED_HISTORY','Velas fechadas para ML',q.get('closed_candles'),100),
            ('ML_TRAINING_SAMPLES','Amostras de treinamento',v.get('training_samples'),40),
            ('ML_TEST_SAMPLES','Amostras de validação',v.get('validation_samples'),20),
            ('ML_TRAIN_CLASSES','Classes no treinamento',len(v['training_classes']) if 'training_classes' in v else None,2),
            ('ML_TEST_CLASSES','Classes na validação',len(v['validation_classes']) if 'validation_classes' in v else None,2)]
        for code,label,value,minimum in details:
            decorated.append(dict(code=code,label=label,status='NOT_EVALUATED' if value is None else 'PASS' if value>=minimum else 'FAIL',
                                  observed=value,operator='>=',required=minimum,unit=''))
    if c['execution_mode']=='LIVE_MT5':
        native=[('SPREAD_LIMIT','Spread máximo',c['mt5_max_spread_points'],'pontos MT5'),
            ('REWARD_RISK','Retorno/risco líquido mínimo',c['strategy_params']['min_reward_risk'],''),
            ('STRUCTURAL_STOP','Distância máxima do stop',c['strategy_params']['max_stop_atr'],'× ATR'),
            ('ENTRY_DRIFT','Desvio máximo desde o sinal',c['strategy_params']['max_entry_drift_atr'],'× ATR'),
            ('MINIMUM_VOLUME','Lote mínimo, passo e teto',c['mt5_volume_cap'],'teto em lotes; depende da especificação'),
            ('MARGIN','Margem suficiente','Margem calculada ≤ margem livre','moeda da conta')]
        for code,label,limit,unit in native:
            decorated.append(dict(code=code,label=label,status='NOT_EVALUATED',observed=None,required=limit,unit=unit,
                reason='Verificado na pré-validação de uma oportunidade elegível. Consulte DECISION para o resultado da tentativa.'))
    return decorated


def supplemental_check(code, passed, observed=None, required=None, reason=''):
    return {**gate(code,passed,reason),'label':LABELS.get(code,code),'observed':observed,'required':required}


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


def preflight_code(reason):
    rules=[('Spread ','SPREAD_LIMIT'),('Símbolo indisponível','SYMBOL_UNAVAILABLE'),
           ('Símbolo não disponível','SYMBOL_UNAVAILABLE'),('lote mínimo','MINIMUM_VOLUME'),
           ('Margem','MARGIN'),('Já há posição','EXTERNAL_EXPOSURE'),('retorno/risco','REWARD_RISK'),
           ('Retorno/risco','REWARD_RISK'),('Stop estrutural','STRUCTURAL_STOP'),
           ('Preço se afastou','ENTRY_DRIFT'),('Cotação','STALE_QUOTE'),
           ('Sem cotação','QUOTE_UNAVAILABLE'),('Envio MT5 bloqueado','ACCOUNT_PERMISSIONS'),
           ('conta/servidor','ACCOUNT_BINDING'),('Pré-validação MT5 recusou','ORDER_CHECK')]
    return next((code for text,code in rules if text in reason),'PREFLIGHT_REJECTED')
