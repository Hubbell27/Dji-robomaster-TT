"""Local-development sign-in. Only mounted when INTAKE_AUTH_MODE=dev, which the
settings validator forbids in production."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import issue_dev_staff_token
from ..db import get_db
from ..models import StaffUser

router = APIRouter(prefix="/api/dev", tags=["dev"])


class DevLoginIn(BaseModel):
    email: str


@router.get("/users")
def users(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"email": u.email, "full_name": u.full_name, "role": u.role.value}
            for u in db.scalars(select(StaffUser).where(StaffUser.active.is_(True)).order_by(StaffUser.email))]


@router.post("/login")
def login(body: DevLoginIn, db: Session = Depends(get_db)) -> dict[str, str]:
    user = db.scalar(select(StaffUser).where(StaffUser.email == body.email.lower()))
    if user is None:
        raise HTTPException(404, "no such user")
    return {"id_token": issue_dev_staff_token(user.email, sub=f"dev-{user.id}")}
