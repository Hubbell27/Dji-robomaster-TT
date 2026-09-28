"""Operational commands.

    python -m app.cli bootstrap --email admin@office.com --name "Office Admin" --location "Main Street"

Creates the first location and admin account (and, in Cognito mode, sends the
Cognito invitation email). Safe to re-run; existing records are left alone.
"""

import argparse

from sqlalchemy import select

from . import audit
from .db import get_sessionmaker
from .models import Location, Role, StaffUser
from .routers.admin import _cognito_invite


def bootstrap(email: str, name: str, location_name: str) -> None:
    email = email.lower()
    with get_sessionmaker()() as db:
        loc = db.scalar(select(Location).where(Location.name == location_name))
        if loc is None:
            loc = Location(name=location_name)
            db.add(loc)
        user = db.scalar(select(StaffUser).where(StaffUser.email == email))
        if user is None:
            user = StaffUser(email=email, full_name=name, role=Role.admin, active=True, locations=[loc])
            db.add(user)
        db.commit()
        _cognito_invite(email, name)
        audit.record(None, audit.SYSTEM, "admin.bootstrap", resource_type="staff", resource_id=user.id,
                     details={"email": email, "location": location_name})
    print(f"Admin {email} ready at location '{location_name}'.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="create the first location and admin user")
    b.add_argument("--email", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--location", required=True)
    args = parser.parse_args()
    if args.cmd == "bootstrap":
        bootstrap(args.email, args.name, args.location)


if __name__ == "__main__":
    main()
