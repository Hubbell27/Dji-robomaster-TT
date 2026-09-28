"""Office-mode staff sign-in: email + password, then a 6-digit authenticator code.

First sign-in walks the user through enrolling an authenticator app (QR code)
and changing the temporary password an admin gave them. Mounted only when
INTAKE_AUTH_MODE=local.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import jwt
import segno
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import audit, crypto, passwords
from ..auth import (LOCAL_AUD, LOCAL_ISS, StaffContext, current_staff, decode_local_token,
                    issue_local_staff_token)
from ..config import get_settings
from ..db import get_db
from ..keys import get_secrets
from ..models import StaffUser, utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

_BAD_LOGIN = "Email or password is incorrect."
_ISSUER = "Dental Intake"


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class MfaIn(BaseModel):
    challenge: str = Field(max_length=2000)
    code: str = Field(max_length=12)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _totp_aad(user: StaffUser) -> str:
    return f"staff:{user.id}:totp"


def _challenge(user: StaffUser) -> str:
    now = utcnow()
    return jwt.encode({"sub": user.id, "sv": user.session_version, "aud": LOCAL_AUD, "iss": LOCAL_ISS,
                       "typ": "mfa", "iat": now, "exp": now + timedelta(minutes=5)},
                      get_secrets().staff_session_secret, algorithm="HS256")


def _register_failure(db: Session, user: StaffUser) -> None:
    """Atomically count a failure so parallel requests can't bypass the lockout."""
    s = get_settings()
    count = db.execute(
        update(StaffUser).where(StaffUser.id == user.id)
        .values(failed_logins=StaffUser.failed_logins + 1).returning(StaffUser.failed_logins)
    ).scalar_one()
    if count >= s.max_login_attempts:
        db.execute(update(StaffUser).where(StaffUser.id == user.id)
                   .values(failed_logins=0, locked_until=utcnow() + timedelta(minutes=s.login_lockout_minutes)))
    db.commit()
    db.refresh(user)


def _is_locked(user: StaffUser) -> bool:
    return bool(user.locked_until and user.locked_until > utcnow())


def _check_locked(request: Request, user: StaffUser) -> None:
    if _is_locked(user):
        minutes = max(1, int((user.locked_until - utcnow()).total_seconds() // 60) + 1)
        audit.record(request, audit.Actor("staff", user.id, user.email), "auth.login", outcome="denied",
                     resource_type="staff", resource_id=user.id, details={"reason": "locked"})
        raise HTTPException(status.HTTP_423_LOCKED,
                            f"Too many failed attempts. Try again in {minutes} minute(s) or ask an admin to unlock you.")


def _session_out(user: StaffUser, auth_time: int | None = None) -> dict[str, Any]:
    token, exp = issue_local_staff_token(user, auth_time)
    return {"token": token, "expires_at": exp, "must_change_password": user.must_change_password}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    email = body.email.strip().lower()
    user = db.scalar(select(StaffUser).where(StaffUser.email == email))
    if user is None or not user.active:
        passwords.verify_password(body.password, None)  # equalize timing
        audit.record(request, audit.Actor("staff", None, email), "auth.login", outcome="failure",
                     details={"reason": "unknown_or_inactive"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_LOGIN)
    if not passwords.verify_password(body.password, user.password_hash):
        # Locked accounts get the same generic answer, so neither the lock nor
        # the account's existence is revealed to someone without the password.
        if not _is_locked(user):
            _register_failure(db, user)
        audit.record(request, audit.Actor("staff", user.id, email), "auth.login", outcome="failure",
                     resource_type="staff", resource_id=user.id, details={"reason": "bad_password"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_LOGIN)
    _check_locked(request, user)

    if user.totp_enabled:
        return {"stage": "mfa", "challenge": _challenge(user)}

    # First sign-in (or MFA was reset): start authenticator enrollment.
    secret = passwords.new_totp_secret()
    user.totp_secret_enc = crypto.encrypt_json(secret, _totp_aad(user))
    db.commit()
    uri = passwords.otpauth_uri(secret, user.email, _ISSUER)
    qr = segno.make(uri, error="m")
    audit.record(request, audit.Actor("staff", user.id, email), "auth.mfa_enroll_started",
                 resource_type="staff", resource_id=user.id)
    return {"stage": "enroll", "challenge": _challenge(user), "otpauth_uri": uri, "secret": secret,
            "qr_svg": qr.svg_inline(scale=5, dark="#0b1f2a")}


@router.post("/verify-mfa")
def verify_mfa(body: MfaIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        claims = decode_local_token(body.challenge, "mfa")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in expired. Please start again.") from None
    user = db.get(StaffUser, claims["sub"])
    if user is None or not user.active or user.session_version != claims.get("sv") or not user.totp_secret_enc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in expired. Please start again.")
    _check_locked(request, user)
    secret = crypto.decrypt_json(user.totp_secret_enc, _totp_aad(user))
    step = passwords.verify_totp(secret, body.code, user.totp_last_step)
    actor = audit.Actor("staff", user.id, user.email)
    if step is None:
        _register_failure(db, user)
        audit.record(request, actor, "auth.mfa", outcome="failure", resource_type="staff", resource_id=user.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't work. Check your authenticator app and try again.")
    # Claim this time step atomically: two parallel requests with the same code
    # cannot both succeed.
    claimed = db.execute(
        update(StaffUser)
        .where(StaffUser.id == user.id,
               (StaffUser.totp_last_step.is_(None)) | (StaffUser.totp_last_step < step))
        .values(totp_last_step=step)
    ).rowcount
    if not claimed:
        db.rollback()
        audit.record(request, actor, "auth.mfa", outcome="failure", resource_type="staff", resource_id=user.id,
                     details={"reason": "replay"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code was already used. Wait for the next code.")
    db.refresh(user)
    enrolled_now = not user.totp_enabled
    user.totp_enabled = True
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    db.commit()
    if enrolled_now:
        audit.record(request, actor, "auth.mfa_enrolled", resource_type="staff", resource_id=user.id)
    audit.record(request, actor, "auth.login", resource_type="staff", resource_id=user.id, details={"mfa": True})
    return _session_out(user)


@router.post("/refresh")
def refresh(request: Request, ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    token = request.headers["authorization"].split(" ", 1)[1]
    claims = decode_local_token(token, "session")
    cap = claims["auth_time"] + get_settings().staff_session_max_hours * 3600
    if utcnow().timestamp() > cap - 60:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Daily session limit reached; please sign in again.")
    return _session_out(ctx.user, claims["auth_time"])


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, db: Session = Depends(get_db),
                    ctx: StaffContext = Depends(current_staff)) -> dict[str, Any]:
    user = db.merge(ctx.user)
    if not passwords.verify_password(body.current_password, user.password_hash):
        _register_failure(db, user)
        audit.record(request, ctx.actor, "auth.password_change", outcome="failure", resource_type="staff",
                     resource_id=user.id, details={"reason": "bad_current_password"})
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if (problem := passwords.password_problem(body.new_password)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a password different from the temporary one.")
    user.password_hash = passwords.hash_password(body.new_password)
    user.must_change_password = False
    user.session_version += 1  # end sessions on other devices; this one gets a fresh token
    db.commit()
    audit.record(request, ctx.actor, "auth.password_change", resource_type="staff", resource_id=user.id)
    return _session_out(user)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db),
           ctx: StaffContext = Depends(current_staff)) -> dict[str, bool]:
    # Revoke server-side so a copied token stops working immediately.
    db.execute(update(StaffUser).where(StaffUser.id == ctx.user.id)
               .values(session_version=StaffUser.session_version + 1))
    db.commit()
    audit.record(request, ctx.actor, "auth.logout", resource_type="staff", resource_id=ctx.user.id)
    return {"ok": True}
