"""Intake workflow helpers shared by the patient and staff routers."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import audit, crypto
from .auth import new_link_token
from .config import get_settings
from .models import OPEN_STATUSES, Intake, IntakeFile, IntakeStatus, new_id, utcnow
from .storage import get_storage


def patient_link(token: str) -> str:
    # The token travels in the URL fragment, which browsers never send to the
    # server, so it cannot leak into ALB/CloudFront access logs or Referer headers.
    return f"{get_settings().public_base_url.rstrip('/')}/intake#t={token}"


def identity(intake: Intake) -> dict[str, str]:
    return crypto.decrypt_json(intake.identity_enc, intake.aad("identity"))


def load_form(intake: Intake) -> dict[str, Any]:
    return crypto.decrypt_json(intake.form_enc, intake.aad("form")) or {}


def save_form(intake: Intake, form: dict[str, Any]) -> None:
    intake.form_enc = crypto.encrypt_json(form, intake.aad("form"))


def rotate_link(intake: Intake) -> str:
    token, token_hash = new_link_token()
    intake.token_hash = token_hash
    intake.expires_at = utcnow() + timedelta(days=get_settings().link_ttl_days)
    intake.session_epoch += 1
    intake.dob_failed_attempts = 0
    return token


def expire_stale_intakes(db: Session, request: Request | None = None) -> int:
    """Mark open intakes whose link has passed its expiry as expired."""
    now = utcnow()
    ids = db.scalars(
        select(Intake.id).where(Intake.status.in_(OPEN_STATUSES), Intake.expires_at <= now)
    ).all()
    if not ids:
        return 0
    db.execute(
        update(Intake)
        .where(Intake.id.in_(ids), Intake.status.in_(OPEN_STATUSES))
        .values(status=IntakeStatus.expired, session_epoch=Intake.session_epoch + 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    for intake_id in ids:
        audit.record(request, audit.SYSTEM, "intake.link_expired", resource_type="intake", resource_id=intake_id)
    return len(ids)


def store_encrypted_file(intake: Intake, kind: str, data: bytes, content_type: str) -> IntakeFile:
    file_id = new_id()
    f = IntakeFile(id=file_id, intake_id=intake.id, kind=kind, content_type=content_type,
                   size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                   storage_key=f"intakes/{intake.id}/{kind}/{file_id}")
    blob = crypto.encrypt_bytes(data, f"file:{f.id}")
    get_storage().put(f.storage_key, blob, "application/octet-stream")
    return f


def read_encrypted_file(storage_key: str, file_id: str) -> bytes:
    return crypto.decrypt_bytes(get_storage().get(storage_key), f"file:{file_id}")
