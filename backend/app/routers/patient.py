"""Patient-facing endpoints (unauthenticated link + DOB check, then session)."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, forms, services
from ..auth import PatientContext, current_patient, hash_link_token, issue_patient_session
from ..config import get_settings
from ..db import get_db
from ..images import InvalidImage, normalize_card_photo
from ..models import Intake, IntakeFile, IntakeStatus, utcnow
from ..pdf import render_submission_pdf
from ..storage import get_storage

router = APIRouter(prefix="/api/patient", tags=["patient"])

_UNAVAILABLE = "This link is invalid or has expired. Please contact the office for a new link."


class VerifyIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    dob: str = Field(min_length=10, max_length=10)


class DraftIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    language: str | None = None


class SubmitIn(BaseModel):
    answers: dict[str, Any]
    consents: dict[str, Any]
    language: str = "en"


def _session_payload(intake: Intake) -> dict[str, Any]:
    ident = services.identity(intake)
    return {
        "session_token": issue_patient_session(intake),
        "first_name": ident["first_name"],
        "language": intake.language,
        "location": {"name": intake.location.name, "phone": intake.location.phone},
        "expires_at": intake.expires_at.isoformat(),
    }


@router.get("/form-definition")
def form_definition() -> dict[str, Any]:
    return forms.form_definition()


@router.post("/verify")
def verify(body: VerifyIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    services.expire_stale_intakes(db, request)
    intake = db.scalar(select(Intake).where(Intake.token_hash == hash_link_token(body.token)))
    if intake is None:
        audit.record(request, audit.Actor("patient"), "patient.verify", outcome="failure",
                     details={"reason": "unknown_token"})
        raise HTTPException(status.HTTP_404_NOT_FOUND, _UNAVAILABLE)

    actor = audit.Actor("patient", f"intake:{intake.id}")
    common = dict(resource_type="intake", resource_id=intake.id, location_id=intake.location_id)
    if intake.status == IntakeStatus.submitted:
        audit.record(request, actor, "patient.verify", outcome="failure", details={"reason": "already_submitted"}, **common)
        raise HTTPException(status.HTTP_410_GONE, "This form has already been submitted. Thank you!")
    if intake.status not in (IntakeStatus.pending, IntakeStatus.in_progress):
        audit.record(request, actor, "patient.verify", outcome="failure", details={"reason": intake.status.value}, **common)
        raise HTTPException(status.HTTP_410_GONE, _UNAVAILABLE)

    expected = services.identity(intake)["dob"]
    if not hmac.compare_digest(expected.encode(), body.dob.encode()):
        intake.dob_failed_attempts += 1
        locked = intake.dob_failed_attempts >= get_settings().max_dob_attempts
        if locked:
            intake.status = IntakeStatus.locked
            intake.session_epoch += 1
        db.commit()
        audit.record(request, actor, "patient.verify", outcome="failure",
                     details={"reason": "dob_mismatch", "attempts": intake.dob_failed_attempts, "locked": locked}, **common)
        if locked:
            raise HTTPException(status.HTTP_423_LOCKED,
                                "Too many attempts. This link has been locked; please contact the office.")
        remaining = get_settings().max_dob_attempts - intake.dob_failed_attempts
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Date of birth does not match our records. {remaining} attempt(s) remaining.")

    intake.dob_failed_attempts = 0
    intake.status = IntakeStatus.in_progress
    intake.first_opened_at = intake.first_opened_at or utcnow()
    db.commit()
    audit.record(request, actor, "patient.verify", **common)
    return _session_payload(intake)


@router.get("/intake")
def get_intake(request: Request, ctx: PatientContext = Depends(current_patient)) -> dict[str, Any]:
    intake = ctx.intake
    ident = services.identity(intake)
    draft = services.load_form(intake).get("answers", {})
    # Staff-entered identity pre-fills the form; DOB is fixed (it was verified).
    answers = {"first_name": ident["first_name"], "last_name": ident["last_name"], **draft, "dob": ident["dob"]}
    audit.record(request, ctx.actor, "patient.intake_opened", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id)
    return {
        **_session_payload(intake),
        "answers": answers,
        "files": [{"id": f.id, "kind": f.kind} for f in intake.files],
    }


@router.put("/intake/draft")
def save_draft(body: DraftIn, request: Request, db: Session = Depends(get_db),
               ctx: PatientContext = Depends(current_patient)) -> dict[str, Any]:
    intake = db.merge(ctx.intake)
    answers = forms.sanitize_answers(body.answers)
    answers["dob"] = services.identity(intake)["dob"]
    services.save_form(intake, {"answers": answers})
    if body.language in forms.LANGUAGES:
        intake.language = body.language
    db.commit()
    audit.record(request, ctx.actor, "patient.draft_saved", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"fields": len(answers)})
    return {"saved_at": utcnow().isoformat(), "session_token": issue_patient_session(intake)}


@router.post("/intake/files/{kind}")
async def upload_file(kind: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
                      ctx: PatientContext = Depends(current_patient)) -> dict[str, Any]:
    if kind not in forms.file_field_keys():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown upload type")
    limit = get_settings().max_upload_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Photo is too large (10 MB max).")
    try:
        jpeg = normalize_card_photo(raw)
    except InvalidImage as e:
        audit.record(request, ctx.actor, "patient.file_rejected", resource_type="intake", resource_id=ctx.intake.id,
                     outcome="failure", details={"kind": kind, "reason": str(e)})
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None

    intake = db.merge(ctx.intake)
    for old in [f for f in intake.files if f.kind == kind]:
        get_storage().delete(old.storage_key)
        db.delete(old)
    new_file = services.store_encrypted_file(intake, kind, jpeg, "image/jpeg")
    db.add(new_file)
    db.commit()
    audit.record(request, ctx.actor, "patient.file_uploaded", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"kind": kind, "file_id": new_file.id, "bytes": len(jpeg)})
    return {"id": new_file.id, "kind": kind}


@router.delete("/intake/files/{file_id}", status_code=204)
def delete_file(file_id: str, request: Request, db: Session = Depends(get_db),
                ctx: PatientContext = Depends(current_patient)) -> Response:
    f = db.get(IntakeFile, file_id)
    if f is None or f.intake_id != ctx.intake.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    get_storage().delete(f.storage_key)
    db.delete(f)
    db.commit()
    audit.record(request, ctx.actor, "patient.file_deleted", resource_type="intake", resource_id=ctx.intake.id,
                 details={"kind": f.kind, "file_id": file_id})
    return Response(status_code=204)


@router.post("/intake/submit")
def submit(body: SubmitIn, request: Request, db: Session = Depends(get_db),
           ctx: PatientContext = Depends(current_patient)):
    intake = db.merge(ctx.intake)
    language = body.language if body.language in forms.LANGUAGES else "en"
    ident = services.identity(intake)
    raw_answers = {**body.answers, "dob": ident["dob"]}
    files_by_kind = {f.kind: f for f in intake.files}
    answers, errors = forms.validate_answers(raw_answers, {k: f.id for k, f in files_by_kind.items()})
    consents, consent_errors = forms.validate_consents(body.consents)
    errors += consent_errors
    if errors:
        # Keep their progress even when validation fails.
        services.save_form(intake, {"answers": forms.sanitize_answers(raw_answers)})
        db.commit()
        audit.record(request, ctx.actor, "patient.submit", resource_type="intake", resource_id=intake.id,
                     outcome="failure", details={"reason": "validation", "error_fields": [e["field"] for e in errors][:50]})
        return JSONResponse(status_code=422, content={"detail": "Please correct the highlighted items.", "errors": errors})

    now = utcnow()
    signature_meta = {
        "signed_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "ip": audit.client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:400],
    }
    for key, c in consents.items():
        c["text_sha256"] = forms.consent_text_hash(key, language)
        c["language"] = language

    # Files no longer referenced (e.g. patient switched to "no insurance") are removed.
    used_file_ids = {v for k, v in answers.items() if k in forms.file_field_keys()}
    for f in list(intake.files):
        if f.id not in used_file_ids:
            get_storage().delete(f.storage_key)
            db.delete(f)
    card_images = {
        k: services.read_encrypted_file(files_by_kind[k].storage_key, files_by_kind[k].id)
        for k in forms.file_field_keys() if k in answers
    }

    pdf_bytes = render_submission_pdf(
        intake_id=intake.id,
        location={"name": intake.location.name, "address": intake.location.address, "phone": intake.location.phone},
        language=language, submitted_at=now, answers=answers, consents=consents,
        signature_meta=signature_meta, card_images=card_images,
    )
    pdf_file = services.store_encrypted_file(intake, "submission_pdf", pdf_bytes, "application/pdf")
    db.add(pdf_file)

    services.save_form(intake, {
        "answers": answers,
        "consents": consents,
        "signature_meta": signature_meta,
        "form_version": forms.form_definition()["version"],
    })
    intake.language = language
    intake.pdf_key = pdf_file.id
    intake.status = IntakeStatus.submitted
    intake.submitted_at = now
    intake.session_epoch += 1  # end the patient's session; the link is now spent
    db.commit()
    audit.record(request, ctx.actor, "patient.submit", resource_type="intake", resource_id=intake.id,
                 location_id=intake.location_id, details={"pdf_file_id": pdf_file.id})
    return {"status": "submitted", "submitted_at": now.isoformat()}
