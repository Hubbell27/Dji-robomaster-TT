"""Staff dashboard endpoints. Every PHI access is audit-logged."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, crypto, forms, services
from ..auth import StaffContext, current_staff
from ..db import get_db
from ..models import Intake, IntakeFile, IntakeStatus, Location, new_id

router = APIRouter(prefix="/api/staff", tags=["staff"])

_LIST_LIMIT = 1000


class CreateIntakeIn(BaseModel):
    location_id: str
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    dob: str
    language: Literal["en", "es"] = "en"

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
    }


@router.get("/me")
def me(ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    u = ctx.user
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role.value,
            "locations": [{"id": l.id, "name": l.name} for l in u.locations]}


@router.get("/locations")
def locations(db: Session = Depends(get_db), ctx: StaffContext = Depends(current_staff)) -> list[dict[str, Any]]:
    q = select(Location).where(Location.active.is_(True)).order_by(Location.name)
    ids = ctx.location_ids()
    if ids is not None:
        q = q.where(Location.id.in_(ids))
    return [{"id": l.id, "name": l.name} for l in db.scalars(q)]


@router.get("/intakes")
def list_intakes(
    request: Request,
    view: Literal["pending", "completed", "attention", "all"] = "pending",
    location_id: str | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    ctx: StaffContext = Depends(current_staff),
) -> dict[str, Any]:
    services.expire_stale_intakes(db, request)
    stmt = select(Intake).order_by(Intake.created_at.desc()).limit(_LIST_LIMIT)
    statuses = {
        "pending": [IntakeStatus.pending, IntakeStatus.in_progress],
        "completed": [IntakeStatus.submitted],
        "attention": [IntakeStatus.expired, IntakeStatus.locked],
        "all": list(IntakeStatus),
    }[view]
    stmt = stmt.where(Intake.status.in_(statuses))
    allowed = ctx.location_ids()
    if location_id:
        if allowed is not None and location_id not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to that location")
        stmt = stmt.where(Intake.location_id == location_id)
    elif allowed is not None:
        stmt = stmt.where(Intake.location_id.in_(allowed))
    if view == "completed":
        stmt = stmt.order_by(None).order_by(Intake.submitted_at.desc())

    rows = [_summary(i) for i in db.scalars(stmt)]
    # Names are encrypted at rest, so search happens after decryption.
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in f"{r['patient']['first_name']} {r['patient']['last_name']}".lower()
                or needle == r["patient"]["dob"]]
    page = rows[offset: offset + limit]
    audit.record(request, ctx.actor, "intake.list_viewed", resource_type="intake",
                 location_id=location_id, details={"view": view, "searched": bool(q), "returned": len(page)})
    return {"total": len(rows), "items": page}


@router.post("/intakes", status_code=201)
def create_intake(body: CreateIntakeIn, request: Request, db: Session = Depends(get_db),
                  ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    location = db.get(Location, body.location_id)
    if location is None or not location.active or not ctx.user.can_access_location(location.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to that location")
    intake = Intake(id=new_id(), location_id=location.id, created_by_id=ctx.user.id, language=body.language,
                    status=IntakeStatus.pending, session_epoch=0, dob_failed_attempts=0)
    token = services.rotate_link(intake)
    intake.identity_enc = crypto.encrypt_json(
        {"first_name": body.first_name.strip(), "last_name": body.last_name.strip(), "dob": body.dob},
        intake.aad("identity"),
    )
    db.add(intake)
    db.commit()
    db.refresh(intake)
    audit.record(request, ctx.actor, "intake.created", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"language": body.language})
    return {"intake": _summary(intake), "link": services.patient_link(token)}


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
    if intake.status in (IntakeStatus.submitted, IntakeStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, f"cannot reissue a {intake.status.value} intake")
    previous = intake.status.value
    token = services.rotate_link(intake)
    intake.status = IntakeStatus.in_progress if intake.form_enc else IntakeStatus.pending
    db.commit()
    audit.record(request, ctx.actor, "intake.link_reissued", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"previous_status": previous})
    return {"intake": _summary(intake), "link": services.patient_link(token)}


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
