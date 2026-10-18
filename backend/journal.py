"""Local SQLite paper audit trail. Never store broker credentials."""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

class PaperJournal:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('TRADER_JOURNAL_PATH', Path(__file__).resolve().parent.parent / 'data' / 'paper.sqlite3'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL)')

    def connection(self):
        return sqlite3.connect(self.path)

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
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT payload FROM trades'):
                existing=json.loads(row[0])
                if existing.get('mode')=='LIVE_MT5' and existing['status'] in ACTIVE_LIVE:
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
