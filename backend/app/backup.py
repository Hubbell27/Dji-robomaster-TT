"""Built-in backups for the Windows desktop edition (SQLite).

A backup is one zip file, ``intake-backup-YYYYMMDD-HHMMSS.zip``, holding a
consistent copy of the database (SQLite online-backup API, safe while the app
is running) plus the encrypted file store. Patient data inside stays
application-encrypted; the key file is deliberately NOT included, so a stolen
backup is unreadable. Keep the key on a separate USB drive.

The destination can be a local folder or a shared network drive
(``\\\\nas\\DentalIntake\\Backups``). Writes go to a ``.partial`` file that is
renamed only when complete, so a half-written backup never looks valid.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url

from . import audit
from .config import get_settings
from .models import utcnow

_NAME = re.compile(r"^intake-backup-(\d{8})-(\d{6})\.zip$")


def sqlite_path() -> Path:
    url = make_url(get_settings().sqlalchemy_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise RuntimeError("built-in backups are for the desktop (SQLite) edition; "
                           "the Docker edition uses office/backup.sh")
    return Path(url.database)


def _local_now() -> datetime:
    return datetime.now(ZoneInfo(get_settings().office_timezone))


def list_backups(dest: str | None = None) -> list[Path]:
    d = Path(dest or get_settings().backup_dir)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if _NAME.match(p.name)), key=lambda p: p.name, reverse=True)


def backed_up_today(dest: str | None = None) -> bool:
    today = _local_now().strftime("%Y%m%d")
    return any(_NAME.match(p.name).group(1) == today for p in list_backups(dest))


def run_backup(dest: str | None = None, reason: str = "scheduled") -> Path:
    s = get_settings()
    dest_dir = Path(dest or s.backup_dir)
    if not str(dest_dir):
        raise RuntimeError("no backup folder configured")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = _local_now().strftime("%Y%m%d-%H%M%S")
        final = dest_dir / f"intake-backup-{stamp}.zip"
        partial = dest_dir / f".{final.name}.partial"
        files_root = Path(s.local_storage_dir)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "intake.db"
            src = sqlite3.connect(str(sqlite_path()))
            dst = sqlite3.connect(str(snapshot))
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            file_count = 0
            with zipfile.ZipFile(partial, "w") as z:
                z.write(snapshot, "intake.db", compress_type=zipfile.ZIP_DEFLATED)
                if files_root.is_dir():
                    for p in files_root.rglob("*"):
                        if p.is_file():
                            # Already encrypted by the app; compression would not help.
                            z.write(p, f"files/{p.relative_to(files_root).as_posix()}", compress_type=zipfile.ZIP_STORED)
                            file_count += 1
                z.writestr("manifest.json", json.dumps({
                    "created_at": utcnow().isoformat(), "reason": reason, "files": file_count,
                    "format": 1, "note": "Encrypted data. Requires the office key file (not included) to read.",
                }, indent=2))
            # Flush to disk before the rename (Windows needs a writable handle for fsync).
            with open(partial, "r+b") as f:
                os.fsync(f.fileno())
        os.replace(partial, final)
        _prune(dest_dir, s.backup_keep_days)
        audit.record(None, audit.SYSTEM, "backup.completed",
                     details={"file": final.name, "files": file_count, "reason": reason})
        return final
    except Exception as e:
        audit.record(None, audit.SYSTEM, "backup.failed", outcome="failure",
                     details={"reason": reason, "error": type(e).__name__, "message": str(e)[:200]})
        raise


def _prune(dest_dir: Path, keep_days: int) -> None:
    cutoff = (_local_now() - timedelta(days=keep_days)).strftime("%Y%m%d")
    backups = list_backups(str(dest_dir))
    for p in backups[7:]:  # always keep the newest 7 regardless of age
        if _NAME.match(p.name).group(1) < cutoff:
            p.unlink(missing_ok=True)


def restore_backup(zip_path: str, data_dir: str) -> None:
    """Replace the database and file store in ``data_dir`` with a backup's contents.

    The service must be stopped first. The current database is kept as
    ``intake.db.before-restore`` in case the wrong backup was chosen.
    """
    data = Path(data_dir)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if "intake.db" not in names or "manifest.json" not in names:
            raise ValueError("this file is not a Dental Intake backup")
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise ValueError("unsafe path in backup")
        db = data / "intake.db"
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        if db.exists():
            os.replace(db, data / "intake.db.before-restore")
        files_dir = data / "files"
        if files_dir.exists():
            old = data / "files.before-restore"
            if old.exists():
                import shutil

                shutil.rmtree(old)
            os.replace(files_dir, old)
        z.extract("intake.db", data)
        for n in names:
            if n.startswith("files/") and not n.endswith("/"):
                z.extract(n, data)
