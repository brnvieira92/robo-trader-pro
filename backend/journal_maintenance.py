"""Explicit offline diagnostic compaction, mandatory backup, no record deletion.

python -m backend.journal_maintenance inspect --journal data/paper.sqlite3
python -m backend.journal_maintenance compact --journal data/paper.sqlite3 --backup data/backups/before-compact.sqlite3
Stop the backend first, after all positions/intents have settled.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
from backend.diagnostic_codec import decode_payload,encode_payload,PREFIX
from backend.journal import PaperJournal


def inspect(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
        return dict(database_bytes=path.stat().st_size,page_size=db.execute('PRAGMA page_size').fetchone()[0],
            reusable_pages=db.execute('PRAGMA freelist_count').fetchone()[0],
            diagnostic_events=db.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()[0],
            trades=db.execute('SELECT COUNT(*) FROM trades').fetchone()[0],
            note='Consulta de metadados; não comprova integridade integral nem altera o arquivo.')


def _preserved_digest(db):
    result=hashlib.sha256()
    for table in ('trades','settings','telegram_outbox'):
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone():continue
        for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid'):
            result.update(json.dumps(row,ensure_ascii=False).encode())
    return result.hexdigest()


def compact(path,backup):
    path=path.resolve();backup=backup.resolve()
    if not path.exists():raise ValueError('Diário não encontrado; confira --journal.')
    if backup.exists() or backup==path:raise ValueError('Escolha um arquivo de backup novo; nada será sobrescrito.')
    # Acquire the same cross-process lock BEFORE opening/modifying the database.
    journal=object.__new__(PaperJournal);journal.path=path
    journal.acquire_runtime_lock()
    try:
        with closing(journal.connection()) as db:
            for row in db.execute('SELECT payload FROM trades'):
                t=json.loads(row[0])
                if t.get('status') in ('SUBMITTING','PENDING','OPEN','NEEDS_REVIEW'):
                    raise ValueError('Há posição/intenção pendente no diário; aguarde reconciliação antes da manutenção.')
            backup.parent.mkdir(parents=True,exist_ok=True)
            size=path.stat().st_size+(Path(str(path)+'-wal').stat().st_size if Path(str(path)+'-wal').exists() else 0)
            required=max(16*1024*1024,3*size)
            if shutil.disk_usage(path.parent).free<required or shutil.disk_usage(backup.parent).free<required:
                raise ValueError('Espaço livre insuficiente para backup e VACUUM; reserve pelo menos três vezes o tamanho atual.')
            backup.touch(exist_ok=False)
            with closing(sqlite3.connect(backup)) as destination:db.backup(destination)
            before=_preserved_digest(db);count=db.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()[0]
            cursor=0;converted=0
            while True:
                rows=db.execute('SELECT id,payload FROM diagnostic_events WHERE id>? ORDER BY id LIMIT 100',(cursor,)).fetchall()
                if not rows:break
                with db:
                    for event_id,raw in rows:
                        cursor=event_id
                        if raw.startswith(PREFIX):continue
                        packed=encode_payload(decode_payload(raw))
                        if packed.startswith(PREFIX):
                            db.execute('UPDATE diagnostic_events SET payload=? WHERE id=?',(packed,event_id));converted+=1
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if before!=_preserved_digest(db) or count!=db.execute('SELECT COUNT(*) FROM diagnostic_events').fetchone()[0]:
                raise RuntimeError('Verificação de preservação falhou; backup original disponível. Não iniciar o robô antes de revisar.')
        return dict(converted_events=converted,backup=str(backup),financial_settings_outbox_unchanged=True,
                    diagnostic_event_count_unchanged=True,after=inspect(path))
    finally:journal.release_runtime_lock()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','compact'])
    parser.add_argument('--journal',type=Path,default=Path(os.environ.get('TRADER_JOURNAL_PATH',Path(__file__).resolve().parent.parent/'data'/'paper.sqlite3')))
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    try:
        if args.action=='compact' and args.backup is None:raise ValueError('--backup é obrigatório para compactar.')
        result=inspect(args.journal) if args.action=='inspect' else compact(args.journal,args.backup)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
        print(json.dumps(dict(error=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
