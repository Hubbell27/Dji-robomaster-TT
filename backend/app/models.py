"""Database models.

PHI never lands in a plaintext column: patient identity, form answers and
signatures are stored in ``*_enc`` columns (see ``crypto.py``). Plaintext
columns hold only workflow metadata (status, timestamps, location, staff ids).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    Column,
    TypeDecorator,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        # Always store UTC so comparisons are correct on every backend.
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Role(str, enum.Enum):
    admin = "admin"
    front_desk = "front_desk"


class IntakeStatus(str, enum.Enum):
    pending = "pending"  # link issued, patient has not verified yet
    in_progress = "in_progress"  # patient verified and/or saved a draft
    submitted = "submitted"
    expired = "expired"
    locked = "locked"  # too many failed DOB attempts
    cancelled = "cancelled"
    purged = "purged"  # retention period elapsed; PHI deleted, row kept as a tombstone


OPEN_STATUSES = (IntakeStatus.pending, IntakeStatus.in_progress)


staff_locations = Table(
    "staff_locations",
    Base.metadata,
    Column("staff_id", String(36), ForeignKey("staff_users.id", ondelete="CASCADE"), primary_key=True),
    Column("location_id", String(36), ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True),
)


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    address: Mapped[str] = mapped_column(String(500), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StaffUser(Base):
    __tablename__ = "staff_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Linked to the Cognito user on first sign-in (matched by verified email).
    cognito_sub: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # --- office-mode (auth_mode=local) credentials ---
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Bumped on password reset / deactivation to end every open session.
    session_version: Mapped[int] = mapped_column(Integer, default=0)

    locations: Mapped[list[Location]] = relationship(secondary=staff_locations, lazy="selectin")

    def can_access_location(self, location_id: str) -> bool:
        return self.role == Role.admin or any(loc.id == location_id for loc in self.locations)


class Intake(Base):
    __tablename__ = "intakes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("staff_users.id"))
    status: Mapped[IntakeStatus] = mapped_column(
        Enum(IntakeStatus, native_enum=False, length=20), default=IntakeStatus.pending, index=True
    )
    language: Mapped[str] = mapped_column(String(5), default="en")

    # Only a SHA-256 of the link token is stored; the token itself is shown once.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    dob_failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Bumped whenever the link is reissued/revoked so old patient sessions die.
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)

    # Encrypted: {"first_name", "last_name", "dob"} entered by staff.
    identity_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Encrypted: full form answers + consent signatures (draft, then final).
    form_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    pdf_key: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Keyed hash of normalized name + DOB (see crypto.patient_index) for
    # returning-patient lookup without plaintext names.
    patient_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    prefilled_from_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    appointment_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    # Encrypted list of medical alert codes computed at submission.
    alerts_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reviewed_by_id: Mapped[str | None] = mapped_column(ForeignKey("staff_users.id"), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    first_opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    location: Mapped[Location] = relationship(lazy="joined")
    created_by: Mapped[StaffUser] = relationship(lazy="joined", foreign_keys=[created_by_id])
    reviewed_by: Mapped[StaffUser | None] = relationship(lazy="joined", foreign_keys=[reviewed_by_id])
    files: Mapped[list[IntakeFile]] = relationship(
        back_populates="intake", lazy="selectin", order_by="IntakeFile.created_at"
    )

    def aad(self, purpose: str) -> str:
        return f"intake:{self.id}:{purpose}"


class IntakeFile(Base):
    __tablename__ = "intake_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    intake_id: Mapped[str] = mapped_column(ForeignKey("intakes.id", ondelete="CASCADE"), index=True)
    # e.g. primary_front, primary_back, secondary_front, secondary_back
    kind: Mapped[str] = mapped_column(String(40))
    storage_key: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    intake: Mapped[Intake] = relationship(back_populates="files")


class AuditEvent(Base):
    """Append-only audit trail. The app role is granted INSERT/SELECT only.

    ``details`` must never contain PHI; it holds ids, field names and reasons.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # staff | patient | system
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    location_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
