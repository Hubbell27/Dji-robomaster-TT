"""Audit logging.

Each event is written to the ``audit_events`` table in its own transaction (so
denied/failed actions are recorded even when the request itself rolls back)
and mirrored as a structured JSON line to stdout, which ECS ships to an
encrypted CloudWatch log group with multi-year retention.

Never pass PHI in ``details``.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .db import get_sessionmaker
from .models import AuditEvent, utcnow

_log = logging.getLogger("intake.audit")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False


@dataclass
class Actor:
    type: str  # staff | patient | system
    id: str | None = None
    email: str | None = None


SYSTEM = Actor(type="system", id="system")


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    # Behind the ALB the real client address is the right-most XFF entry;
    # anything to its left is client-supplied and untrusted.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()[:64]
    return request.client.host if request.client else None


def record(
    request: Request | None,
    actor: Actor,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    location_id: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    event = AuditEvent(
        occurred_at=utcnow(),
        actor_type=actor.type,
        actor_id=actor.id,
        actor_email=actor.email,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        location_id=location_id,
        outcome=outcome,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent", "")[:400] if request else None),
        request_id=(getattr(request.state, "request_id", None) if request else None),
        details=details or {},
    )
    with get_sessionmaker()() as db:
        db.add(event)
        db.commit()
        payload = {
            "type": "audit",
            "id": event.id,
            "at": event.occurred_at.isoformat(),
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "actor_email": event.actor_email,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "location_id": event.location_id,
            "outcome": event.outcome,
            "ip": event.ip_address,
            "request_id": event.request_id,
            "details": event.details,
        }
    _log.info(json.dumps(payload, default=str))
