"""Retention: delete PHI once it is no longer needed.

* Completed submissions older than ``INTAKE_RETENTION_YEARS``.
* Unfinished forms (expired / cancelled / locked) idle longer than
  ``INTAKE_DRAFT_RETENTION_DAYS``.

Purging deletes every file (card photos, PDF) and blanks all encrypted
columns. The row stays as a PHI-free tombstone (status ``purged``) so the
audit trail still resolves; an audit event is written for each purge.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .models import Intake, IntakeStatus, utcnow
from .storage import get_storage

_UNFINISHED = (IntakeStatus.expired, IntakeStatus.cancelled, IntakeStatus.locked)


def purge_intake(db: Session, intake: Intake) -> None:
    for f in list(intake.files):
        get_storage().delete(f.storage_key)
        db.delete(f)
    intake.identity_enc = None
    intake.form_enc = None
    intake.alerts_enc = None
    intake.patient_key = None
    intake.pdf_key = None
    intake.status = IntakeStatus.purged
    intake.purged_at = utcnow()
    intake.session_epoch += 1


def run_retention(db: Session) -> dict[str, int]:
    s = get_settings()
    now = utcnow()
    conditions = [and_(Intake.status.in_(_UNFINISHED), Intake.updated_at < now - timedelta(days=s.draft_retention_days))]
    if s.retention_years > 0:
        conditions.append(and_(Intake.status == IntakeStatus.submitted,
                               Intake.submitted_at < now - timedelta(days=365 * s.retention_years)))
    victims = list(db.scalars(select(Intake).where(or_(*conditions)).limit(500)))
    counts = {"submissions": 0, "drafts": 0}
    for intake in victims:
        kind = "submissions" if intake.status == IntakeStatus.submitted else "drafts"
        previous = intake.status.value
        purge_intake(db, intake)
        db.commit()
        counts[kind] += 1
        audit.record(None, audit.SYSTEM, "intake.purged", resource_type="intake", resource_id=intake.id,
                     location_id=intake.location_id, details={"previous_status": previous, "rule": kind})
    return counts
