"""Local SQLite paper audit trail. Never store broker credentials."""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from backend.diagnostic_codec import encode_payload, decode_payload

class PaperJournal:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('TRADER_JOURNAL_PATH', Path(__file__).resolve().parent.parent / 'data' / 'paper.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            # Long read-only reports must not hold up trading/audit writes.
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS diagnostic_events (id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, bar_time TEXT, code TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, observations INTEGER NOT NULL DEFAULT 1, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS diagnostic_symbol_time ON diagnostic_events(symbol,first_seen)')
            db.execute('CREATE INDEX IF NOT EXISTS diagnostic_kind_code ON diagnostic_events(kind,code)')
            db.execute('CREATE INDEX IF NOT EXISTS diagnostic_last_seen ON diagnostic_events(last_seen)')

    def connection(self):
        return sqlite3.connect(self.path,timeout=1)

    def save(self, trade):
        with self.connection() as db:
            db.execute('INSERT INTO trades VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                       (trade['id'], trade['day'], json.dumps(trade, allow_nan=False)))

    def today(self):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM trades WHERE day=? ORDER BY rowid DESC', (day,))]

    def load_config(self):
        with self.connection() as db:
            row = db.execute("SELECT payload FROM settings WHERE key='config'").fetchone()
            return json.loads(row[0]) if row else None

    def save_config(self, config):
        with self.connection() as db:
            db.execute("INSERT INTO settings VALUES ('config', ?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload", (json.dumps(config, allow_nan=False),))

    def all_trades(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM trades ORDER BY rowid DESC')]

    def reserve_live(self, trade):
        from backend.mt5_execution import ACTIVE_LIVE
        from backend.portfolio_risk import local_block, reserved_risk
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active=[t for row in db.execute('SELECT payload FROM trades')
                    if (t:=json.loads(row[0])).get('mode')=='LIVE_MT5' and t['status'] in ACTIVE_LIVE]
            if trade.get('max_open_positions', 1) > 1:
                block=local_block(trade['portfolio_config'], active, trade['symbol'])
                if block: raise ValueError(block)
                if any((t.get('account_login'),t.get('account_server')) != (trade['account_login'],trade['account_server']) for t in active):
                    raise ValueError('Há exposição em outra conta; reserva global bloqueada.')
                if sum(reserved_risk(t) for t in active)+reserved_risk(trade)>trade['portfolio']['risk_limit']+1e-8:
                    raise ValueError('Reserva atômica ultrapassaria o orçamento global.')
            elif active:
                raise ValueError('Há uma intenção/posição MT5 pendente no diário. Aguarde a reconciliação.')
            db.execute('INSERT INTO trades VALUES (?, ?, ?)',(trade['id'],trade['day'],json.dumps(trade,allow_nan=False)))

    def acquire_runtime_lock(self):
        """One running server per ledger, including on Windows and across workers."""
        handle=open(str(self.path)+'.lock','a+b')
        try:
            if os.name=='nt':
                import msvcrt
                handle.seek(0,2)
                if handle.tell()==0:
                    handle.write(b'0');handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError('Outra instância está usando este diário. Use apenas um servidor/worker.') from None
        self._runtime_lock=handle

    def release_runtime_lock(self):
        handle=getattr(self,'_runtime_lock',None)
        if handle:
            if os.name=='nt':
                import msvcrt
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
            handle.close();self._runtime_lock=None

    def setting(self, key, default=None):
        with self.connection() as db:
            row=db.execute('SELECT payload FROM settings WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connection() as db:
            db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                       (key,json.dumps(value,allow_nan=False)))

    def record_diagnostic(self, event):
        with self.connection() as db:
            db.execute('INSERT INTO diagnostic_events (event_key,kind,symbol,timeframe,bar_time,code,first_seen,last_seen,payload) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO UPDATE SET last_seen=excluded.last_seen,observations=diagnostic_events.observations+1,payload=excluded.payload',
                (event['event_key'],event['kind'],event['symbol'],event['timeframe'],event.get('bar_time'),event['code'],event['observed_at'],event['observed_at'],encode_payload(event['payload'])))

    def diagnostics(self, symbol=None, kind=None, limit=100, before=None):
        filters=[];args=[]
        if symbol is not None:filters.append('symbol=?');args.append(symbol)
        if kind is not None:filters.append('kind=?');args.append(kind)
        if before is not None:filters.append('id<?');args.append(before)
        where=' WHERE '+' AND '.join(filters) if filters else ''
        with self.connection() as db:
            db.row_factory=sqlite3.Row
            rows=db.execute('SELECT * FROM diagnostic_events'+where+' ORDER BY id DESC LIMIT ?',(*args,limit)).fetchall()
            return [{**dict(row),'payload':decode_payload(row['payload'])} for row in rows]

    def diagnostic_summary(self):
        with self.connection() as db:
            rows=db.execute('SELECT symbol,kind,code,COUNT(*),SUM(observations) FROM diagnostic_events GROUP BY symbol,kind,code').fetchall()
            return [dict(symbol=r[0],kind=r[1],code=r[2],events=r[3],observations=r[4]) for r in rows]

    def diagnostic_count(self):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()[0]

    def repair_reconciliation_context(self):
        """Repair provable legacy labels only; retain original label in payload.

        Event identity/times/counts and all trade rows remain unchanged. Unknown
        or conflicting correlations are skipped, never guessed from the pair.
        """
        repaired=skipped=0
        with self.connection() as db:
            rows=db.execute("SELECT id,symbol,timeframe,payload FROM diagnostic_events WHERE kind='RECONCILIATION'").fetchall()
            for event_id,symbol,timeframe,raw in rows:
                try:
                    payload=decode_payload(raw)
                    if not isinstance(payload,dict):skipped+=1;continue
                    trade_id=payload.get('trade_id')
                    row=db.execute('SELECT payload FROM trades WHERE id=?',(trade_id,)).fetchone()
                    trade=json.loads(row[0]) if row else None
                    if payload.get('mode','LIVE_MT5')!='LIVE_MT5' or not trade or trade.get('mode')!='LIVE_MT5' or not trade.get('symbol') or not trade.get('timeframe'):
                        skipped+=1;continue
                    if payload.get('position_id') and payload['position_id']!=trade.get('position_id'):
                        skipped+=1;continue
                    if any(payload.get(k) is not None and payload[k]!=trade.get(k) for k in ('account_login','account_server')):
                        skipped+=1;continue
                    if (symbol,timeframe)==(trade['symbol'],trade['timeframe']):continue
                    payload['context_repair']=dict(version=1,original_symbol=symbol,original_timeframe=timeframe,trade_id=trade_id)
                    db.execute('UPDATE diagnostic_events SET symbol=?,timeframe=?,payload=? WHERE id=?',
                        (trade['symbol'],trade['timeframe'],encode_payload(payload),event_id))
                    repaired+=1
                except (ValueError,TypeError,KeyError):skipped+=1
        return dict(repaired=repaired,skipped=skipped)

    def storage_status(self):
        """Cheap metadata only; no full payload scan or VACUUM during trading."""
        def file_size(path):
            try:return path.stat().st_size
            except FileNotFoundError:return 0
        with self.connection() as db:
            page_size=db.execute('PRAGMA page_size').fetchone()[0]
            pages=db.execute('PRAGMA page_count').fetchone()[0]
            free=db.execute('PRAGMA freelist_count').fetchone()[0]
        return dict(database_bytes=file_size(self.path),wal_bytes=file_size(Path(str(self.path)+'-wal')),
            allocated_bytes=page_size*pages,reusable_bytes=page_size*free,new_diagnostic_codec='zlib-json-v1',
            historical_compaction_automatic=False,financial_history_preserved=True)
