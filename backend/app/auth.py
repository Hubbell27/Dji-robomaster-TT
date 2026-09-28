"""Staff and patient authentication.

Staff: Amazon Cognito ID tokens (RS256, verified against the pool JWKS). MFA is
enforced by the user pool itself (MfaConfiguration=ON), so every token Cognito
issues represents an MFA-completed sign-in. Authorization (role, locations,
active flag) comes from the ``staff_users`` table, which admins manage.

Patients: after the date-of-birth check, a short-lived HS256 session token
scoped to one intake. Tokens carry the intake's ``session_epoch`` so
reissuing/cancelling a link immediately invalidates outstanding sessions.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import get_settings
from .db import get_db
from .models import OPEN_STATUSES, Intake, Role, StaffUser, utcnow

_bearer_prefix = "bearer "


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith(_bearer_prefix):
        return h[len(_bearer_prefix):].strip()
    return None


# --------------------------------------------------------------------------- staff


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    s = get_settings()
    return jwt.PyJWKClient(f"{s.cognito_issuer}/.well-known/jwks.json", cache_keys=True, lifespan=3600)


def _decode_staff_token(token: str) -> dict:
    s = get_settings()
    if s.auth_mode == "dev":
        return jwt.decode(token, s.dev_auth_secret, algorithms=["HS256"], audience="dev-staff", issuer="dev")
    if s.auth_mode == "local":
        return decode_local_token(token, "session")
    key = _jwks_client().get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=s.cognito_client_id,
        issuer=s.cognito_issuer,
        options={"require": ["exp", "iat", "sub", "token_use"]},
    )
    if claims.get("token_use") != "id":
        raise jwt.InvalidTokenError("expected an ID token")
    if not claims.get("email_verified"):
        raise jwt.InvalidTokenError("email not verified")
    return claims


LOCAL_AUD = "office-staff"
LOCAL_ISS = "dental-intake"
# Paths a user who must change their password may still call.
_PASSWORD_CHANGE_PATHS = ("/api/auth/change-password", "/api/staff/me", "/api/auth/logout")


def issue_local_staff_token(user: StaffUser, auth_time: int | None = None) -> tuple[str, int]:
    """Session token for office-mode sign-in. Returns (token, expires_epoch)."""
    from .keys import get_secrets

    s = get_settings()
    now = utcnow()
    auth_time = auth_time or int(now.timestamp())
    cap = auth_time + s.staff_session_max_hours * 3600
    exp = min(int((now + timedelta(minutes=s.staff_session_minutes)).timestamp()), cap)
    token = jwt.encode(
        {"sub": user.id, "sv": user.session_version, "aud": LOCAL_AUD, "iss": LOCAL_ISS,
         "iat": now, "exp": exp, "auth_time": auth_time, "typ": "session"},
        get_secrets().staff_session_secret, algorithm="HS256",
    )
    return token, exp


def decode_local_token(token: str, typ: str) -> dict:
    from .keys import get_secrets

    claims = jwt.decode(token, get_secrets().staff_session_secret, algorithms=["HS256"],
                        audience=LOCAL_AUD, issuer=LOCAL_ISS, options={"require": ["exp", "sub"]})
    if claims.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def issue_dev_staff_token(email: str, sub: str) -> str:
    s = get_settings()
    assert s.auth_mode == "dev" and s.environment != "production"
    now = utcnow()
    return jwt.encode(
        {"sub": sub, "email": email, "email_verified": True, "aud": "dev-staff", "iss": "dev",
         "iat": now, "exp": now + timedelta(hours=1)},
        s.dev_auth_secret,
        algorithm="HS256",
    )


@dataclass
class StaffContext:
    user: StaffUser
    actor: audit.Actor

    @property
    def is_admin(self) -> bool:
        return self.user.role == Role.admin

    def location_ids(self) -> list[str] | None:
        """None means all locations (admins)."""
        return None if self.is_admin else [loc.id for loc in self.user.locations]


def current_staff(request: Request, db: Session = Depends(get_db)) -> StaffContext:
    token = _bearer(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    try:
        claims = _decode_staff_token(token)
    except jwt.PyJWTError as e:
        audit.record(request, audit.Actor("staff"), "auth.token_rejected", outcome="failure",
                     details={"reason": type(e).__name__})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session") from None

    if get_settings().auth_mode == "local":
        user = db.get(StaffUser, claims["sub"])
        if user is None or not user.active or user.session_version != claims.get("sv"):
            audit.record(request, audit.Actor("staff", claims["sub"]), "auth.access_denied", outcome="denied",
                         details={"reason": "inactive user or revoked session"})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "your session has ended; please sign in again")
        if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_PATHS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "password change required")
        claims["iat"] = claims.get("auth_time")
    else:
        sub, email = claims["sub"], (claims.get("email") or "").lower()
        user = db.scalar(select(StaffUser).where(StaffUser.cognito_sub == sub))
        if user is None and email:
            # First sign-in: link the Cognito identity to the admin-provisioned record.
            user = db.scalar(select(StaffUser).where(StaffUser.email == email, StaffUser.cognito_sub.is_(None)))
            if user is not None:
                user.cognito_sub = sub
        if user is None or not user.active:
            audit.record(request, audit.Actor("staff", sub, email), "auth.access_denied", outcome="denied",
                         details={"reason": "unknown or inactive staff user"})
            raise HTTPException(status.HTTP_403_FORBIDDEN, "your account is not authorized for this application")

    first_request_of_session = user.last_login_at is None or (
        claims.get("iat") and user.last_login_at.timestamp() < claims["iat"]
    )
    if first_request_of_session:
        user.last_login_at = utcnow()
    db.commit()
    ctx = StaffContext(user=user, actor=audit.Actor("staff", user.id, user.email))
    if first_request_of_session:
        audit.record(request, ctx.actor, "auth.session_start", resource_type="staff", resource_id=user.id)
    return ctx


def require_admin(ctx: StaffContext = Depends(current_staff), request: Request = None) -> StaffContext:
    if not ctx.is_admin:
        audit.record(request, ctx.actor, "auth.admin_required", outcome="denied", details={"path": request.url.path})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrator role required")
    return ctx


# ------------------------------------------------------------------------- patient


def _patient_secret() -> str:
    from .keys import get_secrets

    return get_secrets().patient_session_secret


def new_link_token() -> tuple[str, str]:
    """Return (token, sha256 hex). Only the hash is persisted."""
    token = secrets.token_urlsafe(32)
    return token, hash_link_token(token)


def hash_link_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_patient_session(intake: Intake) -> str:
    s = get_settings()
    now = utcnow()
    exp = min(now + timedelta(minutes=s.patient_session_minutes), intake.expires_at)
    return jwt.encode(
        {"sub": intake.id, "ep": intake.session_epoch, "aud": "patient", "iat": now, "exp": exp},
        _patient_secret(),
        algorithm="HS256",
    )


@dataclass
class PatientContext:
    intake: Intake
    actor: audit.Actor


def current_patient(request: Request, db: Session = Depends(get_db)) -> PatientContext:
    token = _bearer(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session required")
    try:
        claims = jwt.decode(token, _patient_secret(), algorithms=["HS256"], audience="patient")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "your session has expired, please reopen your link") from None
    intake = db.get(Intake, claims["sub"])
    if (
        intake is None
        or intake.session_epoch != claims.get("ep")
        or intake.status not in OPEN_STATUSES
        or intake.expires_at <= utcnow()
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "this form is no longer available")
    return PatientContext(intake=intake, actor=audit.Actor("patient", f"intake:{intake.id}"))
