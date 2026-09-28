"""Admin-only endpoints: staff accounts, locations, and the audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import StaffContext, require_admin
from ..config import get_settings
from ..db import get_db
from ..models import AuditEvent, Location, Role, StaffUser

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ----------------------------------------------------------------- cognito sync


def _cognito():
    import boto3

    return boto3.client("cognito-idp", region_name=get_settings().aws_region)


def _cognito_invite(email: str, full_name: str) -> None:
    s = get_settings()
    if s.auth_mode != "cognito":
        return
    try:
        _cognito().admin_create_user(
            UserPoolId=s.cognito_user_pool_id,
            Username=email,
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"},
                            {"Name": "name", "Value": full_name}],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except _cognito().exceptions.UsernameExistsException:
        pass


def _cognito_set_enabled(email: str, enabled: bool) -> None:
    s = get_settings()
    if s.auth_mode != "cognito":
        return
    client = _cognito()
    if enabled:
        client.admin_enable_user(UserPoolId=s.cognito_user_pool_id, Username=email)
    else:
        client.admin_disable_user(UserPoolId=s.cognito_user_pool_id, Username=email)
        client.admin_user_global_sign_out(UserPoolId=s.cognito_user_pool_id, Username=email)


# ------------------------------------------------------------------------ staff


class StaffIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: Role = Role.front_desk
    location_ids: list[str] = Field(default_factory=list)


class StaffPatch(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role: Role | None = None
    active: bool | None = None
    location_ids: list[str] | None = None


def _staff_out(u: StaffUser) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role.value, "active": u.active,
            "linked": u.cognito_sub is not None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "locations": [{"id": l.id, "name": l.name} for l in u.locations]}


def _locations(db: Session, ids: list[str]) -> list[Location]:
    locs = list(db.scalars(select(Location).where(Location.id.in_(ids)))) if ids else []
    if len(locs) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown location id")
    return locs


@router.get("/staff")
def list_staff(db: Session = Depends(get_db), ctx: StaffContext = Depends(require_admin)) -> list[dict[str, Any]]:
    return [_staff_out(u) for u in db.scalars(select(StaffUser).order_by(StaffUser.full_name))]


@router.post("/staff", status_code=201)
def create_staff(body: StaffIn, request: Request, db: Session = Depends(get_db),
                 ctx: StaffContext = Depends(require_admin)) -> dict[str, Any]:
    email = body.email.lower()
    if db.scalar(select(StaffUser).where(StaffUser.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a staff user with that email already exists")
    user = StaffUser(email=email, full_name=body.full_name, role=body.role, active=True,
                     locations=_locations(db, body.location_ids))
    db.add(user)
    db.flush()
    _cognito_invite(email, body.full_name)
    db.commit()
    audit.record(request, ctx.actor, "admin.staff_created", resource_type="staff", resource_id=user.id,
                 details={"email": email, "role": body.role.value, "location_ids": body.location_ids})
    return _staff_out(user)


@router.patch("/staff/{staff_id}")
def update_staff(staff_id: str, body: StaffPatch, request: Request, db: Session = Depends(get_db),
                 ctx: StaffContext = Depends(require_admin)) -> dict[str, Any]:
    user = db.get(StaffUser, staff_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "staff user not found")
    if user.id == ctx.user.id and (body.active is False or (body.role and body.role != Role.admin)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot deactivate or demote yourself")
    changes = body.model_dump(exclude_unset=True)
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role = body.role
    if body.location_ids is not None:
        user.locations = _locations(db, body.location_ids)
    if body.active is not None and body.active != user.active:
        user.active = body.active
        _cognito_set_enabled(user.email, body.active)
    db.commit()
    audit.record(request, ctx.actor, "admin.staff_updated", resource_type="staff", resource_id=user.id,
                 details={k: (v.value if isinstance(v, Role) else v) for k, v in changes.items()})
    return _staff_out(user)


# -------------------------------------------------------------------- locations


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field("", max_length=500)
    phone: str = Field("", max_length=50)
    active: bool = True


@router.get("/locations")
def list_locations(db: Session = Depends(get_db), ctx: StaffContext = Depends(require_admin)) -> list[dict[str, Any]]:
    return [{"id": l.id, "name": l.name, "address": l.address, "phone": l.phone, "active": l.active}
            for l in db.scalars(select(Location).order_by(Location.name))]


@router.post("/locations", status_code=201)
def create_location(body: LocationIn, request: Request, db: Session = Depends(get_db),
                    ctx: StaffContext = Depends(require_admin)) -> dict[str, Any]:
    if db.scalar(select(Location).where(Location.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "a location with that name already exists")
    loc = Location(**body.model_dump())
    db.add(loc)
    db.commit()
    audit.record(request, ctx.actor, "admin.location_created", resource_type="location", resource_id=loc.id,
                 details={"name": loc.name})
    return {"id": loc.id, **body.model_dump()}


@router.put("/locations/{location_id}")
def update_location(location_id: str, body: LocationIn, request: Request, db: Session = Depends(get_db),
                    ctx: StaffContext = Depends(require_admin)) -> dict[str, Any]:
    loc = db.get(Location, location_id)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "location not found")
    for k, v in body.model_dump().items():
        setattr(loc, k, v)
    db.commit()
    audit.record(request, ctx.actor, "admin.location_updated", resource_type="location", resource_id=loc.id,
                 details=body.model_dump())
    return {"id": loc.id, **body.model_dump()}


# ------------------------------------------------------------------------ audit


@router.get("/audit")
def list_audit(
    request: Request,
    action: str | None = Query(None, max_length=80),
    actor_email: str | None = Query(None, max_length=320),
    resource_id: str | None = Query(None, max_length=64),
    outcome: str | None = Query(None, max_length=20),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    ctx: StaffContext = Depends(require_admin),
) -> dict[str, Any]:
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if actor_email:
        stmt = stmt.where(AuditEvent.actor_email == actor_email.lower())
    if resource_id:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if outcome:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    if since:
        stmt = stmt.where(AuditEvent.occurred_at >= since)
    if until:
        stmt = stmt.where(AuditEvent.occurred_at < until)
    audit.record(request, ctx.actor, "admin.audit_viewed",
                 details={"action": action, "actor_email": actor_email, "resource_id": resource_id})
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    events = db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset))
    return {"total": total, "items": [
        {"id": e.id, "occurred_at": e.occurred_at.isoformat(), "actor_type": e.actor_type, "actor_id": e.actor_id,
         "actor_email": e.actor_email, "action": e.action, "resource_type": e.resource_type,
         "resource_id": e.resource_id, "location_id": e.location_id, "outcome": e.outcome,
         "ip_address": e.ip_address, "user_agent": e.user_agent, "details": e.details}
        for e in events]}
