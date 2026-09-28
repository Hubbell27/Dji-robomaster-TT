"""Operational commands.

    python -m app.cli bootstrap --email admin@office.com --name "Office Admin" --location "Main Street"
    python -m app.cli reset-admin --email admin@office.com     # office mode: new temp password + MFA reset
    python -m app.cli purge                                     # run the retention purge now
    python -m app.cli status                                    # quick health summary

``bootstrap`` creates the first location and admin account. In Cognito mode it
sends the invitation email; in office mode it prints a one-time temporary
password. Safe to re-run; existing records are left alone.
"""

import argparse
import sys

from sqlalchemy import func, select

from . import audit, passwords
from .config import get_settings
from .db import get_sessionmaker
from .models import Intake, Location, Role, StaffUser


def bootstrap(email: str, name: str, location_name: str) -> None:
    email = email.lower()
    local = get_settings().auth_mode == "local"
    temp = None
    with get_sessionmaker()() as db:
        loc = db.scalar(select(Location).where(Location.name == location_name))
        if loc is None:
            loc = Location(name=location_name)
            db.add(loc)
        user = db.scalar(select(StaffUser).where(StaffUser.email == email))
        if user is None:
            user = StaffUser(email=email, full_name=name, role=Role.admin, active=True, locations=[loc])
            if local:
                temp = passwords.temporary_password()
                user.password_hash = passwords.hash_password(temp)
                user.must_change_password = True
            db.add(user)
        db.commit()
        if not local:
            from .routers.admin import _cognito_invite

            _cognito_invite(email, name)
        audit.record(None, audit.SYSTEM, "admin.bootstrap", resource_type="staff", resource_id=user.id,
                     details={"email": email, "location": location_name})
    print(f"Admin {email} ready at location '{location_name}'.")
    if temp:
        print(f"Temporary password (shown once): {temp}")
        print("Sign in, set a new password, and scan the QR code with an authenticator app.")


def reset_admin(email: str) -> None:
    if get_settings().auth_mode != "local":
        sys.exit("reset-admin is only for office mode; use the Cognito console in AWS.")
    with get_sessionmaker()() as db:
        user = db.scalar(select(StaffUser).where(StaffUser.email == email.lower()))
        if user is None:
            sys.exit(f"No staff user {email}")
        temp = passwords.temporary_password()
        user.password_hash = passwords.hash_password(temp)
        user.must_change_password = True
        user.totp_enabled = False
        user.totp_secret_enc = None
        user.totp_last_step = None
        user.failed_logins = 0
        user.locked_until = None
        user.active = True
        user.session_version += 1
        db.commit()
        audit.record(None, audit.SYSTEM, "admin.staff_password_reset", resource_type="staff", resource_id=user.id,
                     details={"via": "cli", "reset_mfa": True})
    print(f"Temporary password for {email} (shown once): {temp}")


def purge() -> None:
    from .retention import run_retention

    with get_sessionmaker()() as db:
        result = run_retention(db)
    print(f"Purged {result['submissions']} old submission(s) and {result['drafts']} stale unfinished form(s).")


def status() -> None:
    with get_sessionmaker()() as db:
        counts = dict(db.execute(select(Intake.status, func.count()).group_by(Intake.status)).all())
        staff = db.scalar(select(func.count()).select_from(StaffUser).where(StaffUser.active.is_(True)))
    print(f"Active staff: {staff}")
    for k, v in sorted(counts.items(), key=lambda kv: kv[0].value):
        print(f"  {k.value:12} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap", help="create the first location and admin user")
    b.add_argument("--email", required=True)
    b.add_argument("--name", required=True)
    b.add_argument("--location", required=True)
    r = sub.add_parser("reset-admin", help="office mode: issue a new temporary password and reset MFA")
    r.add_argument("--email", required=True)
    sub.add_parser("purge", help="run the retention purge now")
    sub.add_parser("status", help="print a quick summary")
    args = parser.parse_args()
    if args.cmd == "bootstrap":
        bootstrap(args.email, args.name, args.location)
    elif args.cmd == "reset-admin":
        reset_admin(args.email)
    elif args.cmd == "purge":
        purge()
    elif args.cmd == "status":
        status()


if __name__ == "__main__":
    main()
