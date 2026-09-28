"""Secret material: the office key file, and effective secrets for any mode.

In office deployments the key file is created on first start with fresh random
keys and file mode 0600. It is the only thing that can decrypt the patient
data, so it must be backed up separately from the database backups (e.g. on a
USB drive in the office safe). Losing it means losing all stored records.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import get_settings


@dataclass(frozen=True)
class Secrets:
    master_key_b64: str
    index_key: bytes
    patient_session_secret: str
    staff_session_secret: str


def _new_keyfile() -> dict[str, str]:
    return {
        "version": "1",
        "master_key": base64.b64encode(os.urandom(32)).decode(),
        "index_key": base64.b64encode(os.urandom(32)).decode(),
        "patient_session_secret": secrets.token_urlsafe(48),
        "staff_session_secret": secrets.token_urlsafe(48),
    }


def load_or_create_keyfile(path: str) -> dict[str, str]:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write a complete temp file, then hard-link it into place: the link fails if
    # another process won the race, and readers never see a half-written file.
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(_new_keyfile(), f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, p)
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return json.loads(p.read_text())


@lru_cache
def get_secrets() -> Secrets:
    s = get_settings()
    if s.key_provider == "file":
        k = load_or_create_keyfile(s.key_file)
        return Secrets(
            master_key_b64=k["master_key"],
            index_key=base64.b64decode(k["index_key"]),
            patient_session_secret=k["patient_session_secret"],
            staff_session_secret=k["staff_session_secret"],
        )
    return Secrets(
        master_key_b64=s.local_data_key,
        index_key=base64.b64decode(s.index_key),
        patient_session_secret=s.patient_session_secret,
        staff_session_secret=s.staff_session_secret,
    )


def check_keyfile_before_start() -> None:
    """Refuse to start if the key file vanished but encrypted data exists.

    Creating a fresh key in that situation would silently make every existing
    record unreadable; stopping with a clear message lets the office restore
    the key file from its USB backup instead.
    """
    s = get_settings()
    if s.key_provider != "file":
        return
    from sqlalchemy import func, select

    from .db import get_sessionmaker
    from .models import Intake, StaffUser

    with get_sessionmaker()() as db:
        sample = db.scalar(select(Intake).where(Intake.identity_enc.is_not(None)).limit(1))
        has_data = sample is not None or db.scalar(
            select(func.count()).select_from(StaffUser).where(StaffUser.totp_secret_enc.is_not(None)))
    if Path(s.key_file).exists():
        if sample is not None:
            from . import crypto

            try:
                crypto.decrypt_json(sample.identity_enc, sample.aad("identity"))
            except Exception:
                raise RuntimeError(
                    f"Encryption key file {s.key_file} does not match this database (existing records cannot be "
                    "decrypted with it). Import the key file that belongs to this data before starting."
                ) from None
        return
    if has_data:
        raise RuntimeError(
            f"Encryption key file {s.key_file} is missing but the database contains encrypted records. "
            "Restore the key file (office script: import-key) before starting. Refusing to create a new key."
        )
    # Fresh install: create the key now so it can be exported to USB right away.
    load_or_create_keyfile(s.key_file)
