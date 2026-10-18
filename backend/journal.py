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
