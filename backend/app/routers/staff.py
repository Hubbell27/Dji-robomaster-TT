"""Staff dashboard endpoints. Every PHI access is audit-logged."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .. import audit, crypto, forms, services
from ..auth import StaffContext, current_staff
from ..config import get_settings
from ..db import get_db
from ..models import Intake, IntakeFile, IntakeStatus, Location, new_id, utcnow

router = APIRouter(prefix="/api/staff", tags=["staff"])

_LIST_LIMIT = 1000


class CreateIntakeIn(BaseModel):
    location_id: str
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    dob: str
    language: Literal["en", "es"] = "en"
    appointment_at: datetime | None = None
    # Pre-fill from this patient's most recent completed form, if any.
    prefill: bool = True

    @field_validator("appointment_at")
    @classmethod
    def _appt(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            v = v.replace(tzinfo=ZoneInfo(get_settings().office_timezone))
        return v

    @field_validator("dob")
    @classmethod
    def _dob(cls, v: str) -> str:
        d = date.fromisoformat(v)
        if d > date.today() or d.year < 1900:
            raise ValueError("date of birth out of range")
        return d.isoformat()


def _get_intake(db: Session, ctx: StaffContext, intake_id: str, request: Request) -> Intake:
    intake = db.get(Intake, intake_id)
    if intake is None or not ctx.user.can_access_location(intake.location_id):
        if intake is not None:
            audit.record(request, ctx.actor, "intake.access_denied", resource_type="intake", resource_id=intake_id,
                         location_id=intake.location_id, outcome="denied")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intake not found")
    return intake


def _summary(intake: Intake) -> dict[str, Any]:
    ident = services.identity(intake)
    return {
        "id": intake.id,
        "patient": {"first_name": ident["first_name"], "last_name": ident["last_name"], "dob": ident["dob"]},
        "status": intake.status.value,
        "language": intake.language,
        "location": {"id": intake.location_id, "name": intake.location.name},
        "created_by": intake.created_by.full_name,
        "created_at": intake.created_at.isoformat(),
        "expires_at": intake.expires_at.isoformat(),
        "first_opened_at": intake.first_opened_at.isoformat() if intake.first_opened_at else None,
        "submitted_at": intake.submitted_at.isoformat() if intake.submitted_at else None,
        "dob_failed_attempts": intake.dob_failed_attempts,
        "appointment_at": intake.appointment_at.isoformat() if intake.appointment_at else None,
        "reviewed_at": intake.reviewed_at.isoformat() if intake.reviewed_at else None,
        "reviewed_by": intake.reviewed_by.full_name if intake.reviewed_by else None,
        "alerts": services.alerts(intake),
        "prefilled": intake.prefilled_from_id is not None,
    }


@router.get("/me")
def me(ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    u = ctx.user
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role.value,
            "must_change_password": bool(u.must_change_password and get_settings().auth_mode == "local"),
            "office_timezone": get_settings().office_timezone,
            "locations": [{"id": l.id, "name": l.name} for l in u.locations]}


@router.get("/locations")
def locations(db: Session = Depends(get_db), ctx: StaffContext = Depends(current_staff)) -> list[dict[str, Any]]:
    q = select(Location).where(Location.active.is_(True)).order_by(Location.name)
    ids = ctx.location_ids()
    if ids is not None:
        q = q.where(Location.id.in_(ids))
    return [{"id": l.id, "name": l.name} for l in db.scalars(q)]


View = Literal["today", "pending", "review", "completed", "attention", "all"]


def _today_bounds() -> tuple[datetime, datetime]:
    tz = ZoneInfo(get_settings().office_timezone)
    today = datetime.now(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _view_filter(view: str):
    if view == "today":
        start, end = _today_bounds()
        return and_(
            Intake.status != IntakeStatus.cancelled,
            or_(and_(Intake.appointment_at >= start, Intake.appointment_at < end),
                and_(Intake.appointment_at.is_(None), Intake.created_at >= start, Intake.created_at < end)),
        )
    cond = Intake.status.in_({
        "pending": [IntakeStatus.pending, IntakeStatus.in_progress],
        "review": [IntakeStatus.submitted],
        "completed": [IntakeStatus.submitted],
        "attention": [IntakeStatus.expired, IntakeStatus.locked],
        "all": [s for s in IntakeStatus if s != IntakeStatus.purged],
    }[view])
    if view == "review":
        cond = and_(cond, Intake.reviewed_at.is_(None))
    return cond


def _scoped(stmt, ctx: StaffContext, location_id: str | None):
    allowed = ctx.location_ids()
    if location_id:
        if allowed is not None and location_id not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to that location")
        return stmt.where(Intake.location_id == location_id)
    if allowed is not None:
        return stmt.where(Intake.location_id.in_(allowed))
    return stmt


@router.get("/intakes/counts")
def intake_counts(request: Request, location_id: str | None = None, db: Session = Depends(get_db),
                  ctx: StaffContext = Depends(current_staff)) -> dict[str, int]:
    services.expire_stale_intakes(db, request)
    out = {}
    for view in ("today", "pending", "review", "attention"):
        stmt = _scoped(select(func.count()).select_from(Intake).where(_view_filter(view)), ctx, location_id)
        out[view] = db.scalar(stmt) or 0
    return out


@router.get("/intakes")
def list_intakes(
    request: Request,
    view: View = "today",
    location_id: str | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auto: bool = False,
    db: Session = Depends(get_db),
    ctx: StaffContext = Depends(current_staff),
) -> dict[str, Any]:
    services.expire_stale_intakes(db, request)
    stmt = _scoped(select(Intake).where(_view_filter(view)), ctx, location_id)
    if view == "today":
        stmt = stmt.order_by(Intake.appointment_at.asc().nulls_last(), Intake.created_at.asc())
    elif view in ("review", "completed"):
        stmt = stmt.order_by(Intake.submitted_at.desc())
    else:
        stmt = stmt.order_by(Intake.created_at.desc())
    rows = [_summary(i) for i in db.scalars(stmt.limit(_LIST_LIMIT))]
    # Names are encrypted at rest, so search happens after decryption.
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in f"{r['patient']['first_name']} {r['patient']['last_name']}".lower()
                or needle in f"{r['patient']['last_name']}, {r['patient']['first_name']}".lower()
                or needle == r["patient"]["dob"]]
    page = rows[offset: offset + limit]
    audit.record(request, ctx.actor, "intake.list_viewed", resource_type="intake", location_id=location_id,
                 details={"view": view, "searched": bool(q), "returned": len(page), "auto_refresh": auto})
    return {"total": len(rows), "items": page}


@router.get("/patients/lookup")
def lookup_patient(request: Request, first_name: str = Query(max_length=100), last_name: str = Query(max_length=100),
                   dob: str = Query(max_length=10), db: Session = Depends(get_db),
                   ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    """Is this a returning patient? Used while staff type a new intake."""
    try:
        dob = date.fromisoformat(dob).isoformat()
    except ValueError:
        return {"returning": False}
    prev = services.previous_submissions(db, crypto.patient_index(first_name, last_name, dob))
    audit.record(request, ctx.actor, "patient.lookup", resource_type="patient", details={"matches": len(prev)})
    if not prev:
        return {"returning": False}
    return {"returning": True, "visits": len(prev), "last_submitted_at": prev[0].submitted_at.isoformat(),
            "last_location": prev[0].location.name}


@router.post("/intakes", status_code=201)
def create_intake(body: CreateIntakeIn, request: Request, db: Session = Depends(get_db),
                  ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    location = db.get(Location, body.location_id)
    if location is None or not location.active or not ctx.user.can_access_location(location.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to that location")
    intake = Intake(id=new_id(), location_id=location.id, created_by_id=ctx.user.id, language=body.language,
                    status=IntakeStatus.pending, session_epoch=0, dob_failed_attempts=0,
                    appointment_at=body.appointment_at)
    token = services.rotate_link(intake)
    services.set_identity(intake, body.first_name.strip(), body.last_name.strip(), body.dob)
    source = None
    if body.prefill:
        prev = services.previous_submissions(db, intake.patient_key)
        if prev:
            source = prev[0]
            services.prefill_from(intake, source)
    db.add(intake)
    db.commit()
    db.refresh(intake)
    audit.record(request, ctx.actor, "intake.created", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id,
                 details={"language": body.language, "prefilled_from": source.id if source else None})
    return {"intake": _summary(intake), "link": services.patient_link(token, intake.language)}


@router.get("/intakes/{intake_id}")
def get_intake(intake_id: str, request: Request, db: Session = Depends(get_db),
               ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    intake = _get_intake(db, ctx, intake_id, request)
    form = services.load_form(intake)
    consents = {
        k: {kk: vv for kk, vv in v.items() if kk != "signature"} | {"has_signature": bool(v.get("signature"))}
        for k, v in (form.get("consents") or {}).items()
    }
    audit.record(request, ctx.actor, "intake.viewed", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"status": intake.status.value})
    return {
        **_summary(intake),
        "is_draft": intake.status != IntakeStatus.submitted,
        "answers": form.get("answers", {}),
        "consents": consents,
        "signature_meta": form.get("signature_meta"),
        "form_version": form.get("form_version"),
        "files": [{"id": f.id, "kind": f.kind, "size_bytes": f.size_bytes, "created_at": f.created_at.isoformat()}
                  for f in intake.files if f.kind != "submission_pdf"],
        "has_pdf": intake.pdf_key is not None,
    }


@router.post("/intakes/{intake_id}/reissue")
def reissue_link(intake_id: str, request: Request, db: Session = Depends(get_db),
                 ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    intake = _get_intake(db, ctx, intake_id, request)
    if intake.status in (IntakeStatus.submitted, IntakeStatus.cancelled, IntakeStatus.purged):
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot reissue a {intake.status.value} intake")
    previous = intake.status.value
    token = services.rotate_link(intake)
    intake.status = IntakeStatus.in_progress if intake.form_enc else IntakeStatus.pending
    db.commit()
    audit.record(request, ctx.actor, "intake.link_reissued", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"previous_status": previous})
    return {"intake": _summary(intake), "link": services.patient_link(token, intake.language)}


@router.post("/intakes/{intake_id}/cancel")
def cancel_intake(intake_id: str, request: Request, db: Session = Depends(get_db),
                  ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    intake = _get_intake(db, ctx, intake_id, request)
    if intake.status == IntakeStatus.submitted:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot cancel a submitted intake")
    intake.status = IntakeStatus.cancelled
    intake.session_epoch += 1
    db.commit()
    audit.record(request, ctx.actor, "intake.cancelled", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id)
    return _summary(intake)


class ReviewIn(BaseModel):
    reviewed: bool = True


@router.post("/intakes/{intake_id}/review")
def mark_reviewed(intake_id: str, body: ReviewIn, request: Request, db: Session = Depends(get_db),
                  ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    """Mark a completed form as reviewed / entered into the patient's chart."""
    intake = _get_intake(db, ctx, intake_id, request)
    if intake.status != IntakeStatus.submitted:
        raise HTTPException(status.HTTP_409_CONFLICT, "only completed forms can be reviewed")
    intake.reviewed_at = utcnow() if body.reviewed else None
    intake.reviewed_by_id = ctx.user.id if body.reviewed else None
    db.commit()
    db.refresh(intake)
    audit.record(request, ctx.actor, "intake.reviewed" if body.reviewed else "intake.review_undone",
                 resource_type="intake", resource_id=intake.id, location_id=intake.location_id)
    return _summary(intake)


def _file_response(data: bytes, content_type: str, filename: str, inline: bool) -> Response:
    disposition = "inline" if inline else "attachment"
    return Response(content=data, media_type=content_type, headers={
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/intakes/{intake_id}/pdf")
def download_pdf(intake_id: str, request: Request, db: Session = Depends(get_db),
                 ctx: StaffContext = Depends(current_staff)) -> Response:
    intake = _get_intake(db, ctx, intake_id, request)
    f = db.get(IntakeFile, intake.pdf_key) if intake.pdf_key else None
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no PDF for this intake")
    data = services.read_encrypted_file(f.storage_key, f.id)
    audit.record(request, ctx.actor, "intake.pdf_downloaded", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id)
    ident = services.identity(intake)
    safe = "".join(c for c in f"{ident['last_name']}_{ident['first_name']}" if c.isalnum() or c in "_-")[:60]
    return _file_response(data, "application/pdf", f"intake_{safe}_{intake.submitted_at:%Y%m%d}.pdf", inline=False)


@router.get("/intakes/{intake_id}/files/{file_id}")
def view_file(intake_id: str, file_id: str, request: Request, db: Session = Depends(get_db),
              ctx: StaffContext = Depends(current_staff)) -> Response:
    intake = _get_intake(db, ctx, intake_id, request)
    f = db.get(IntakeFile, file_id)
    if f is None or f.intake_id != intake.id or f.kind == "submission_pdf":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    data = services.read_encrypted_file(f.storage_key, f.id)
    audit.record(request, ctx.actor, "intake.file_viewed", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"file_id": f.id, "kind": f.kind})
    return _file_response(data, f.content_type, f"{f.kind}.jpg", inline=True)


@router.get("/form-definition")
def form_definition(ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    return forms.form_definition()
