"""Startup source identity; never inspect environment or credential files."""
import hashlib
import os
import subprocess
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def source_fingerprint():
    result=hashlib.sha256()
    for file in sorted((ROOT/'backend').rglob('*.py')):
        result.update(file.relative_to(ROOT).as_posix().encode());result.update(file.read_bytes())
    return result.hexdigest()


def _git(*args):
    try:
        run=subprocess.run(['git','-c',f'safe.directory={ROOT.as_posix()}',*args],cwd=ROOT,
            capture_output=True,text=True,timeout=3,check=True)
        return run.stdout.strip()
    except (OSError,subprocess.SubprocessError):return None


class RuntimeIdentity:
    def __init__(self):
        self.loaded_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.pid=os.getpid();self.git_head=_git('rev-parse','HEAD')
        dirty=_git('status','--porcelain','--untracked-files=no')
        self.git_dirty=None if dirty is None else bool(dirty)
        self.fingerprint=source_fingerprint()

    def status(self):
        try:matches=source_fingerprint()==self.fingerprint
        except OSError:matches=None
        return dict(process_id=self.pid,loaded_at_utc=self.loaded_at,git_head_at_load=self.git_head,
            tracked_changes_at_load=self.git_dirty,backend_fingerprint_at_load=self.fingerprint,
            backend_files_match_load=matches,
            note='Identificação capturada ao carregar o backend; não é prova de SHA de bytecode em memória. Alterar arquivos exige reiniciar o processo para carregá-los.')
