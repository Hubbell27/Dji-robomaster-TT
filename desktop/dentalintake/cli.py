"""DentalIntakeServer.exe — setup and maintenance commands (run as administrator).

  setup        first-time setup of the office PC (the installer runs this)
  workstation  set this PC up to use another office PC's Dental Intake
  serve        run the server (the Windows service runs this)
  backup       make a backup now
  restore ZIP  restore a backup (stop the service first)
  export-key   save the encryption key file (to a USB drive)
  import-key   put a saved key file on this PC (moving to a new PC)
  reset-admin  new temporary password + authenticator reset for a staff member
  status       show settings, counts and recent backups
  fingerprint  show the office security code
  url          print the dashboard address
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from pathlib import Path

from . import tls
from .config import DesktopConfig
from .paths import data_dir


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _detect_timezone() -> str:
    try:
        from tzlocal import get_localzone_name

        name = get_localzone_name()
        if name and "/" in name:
            return name
    except Exception:
        pass
    return "America/New_York"


def _boot(cfg: DesktopConfig) -> None:
    from . import server

    server.check_data_dir()
    server.apply_env(cfg)


def cmd_setup(a) -> int:
    from . import server

    p = DesktopConfig.path()
    cfg = DesktopConfig.load() if p.exists() else DesktopConfig()
    cfg.role = "server"
    cfg.office_name = a.office_name or cfg.office_name
    cfg.timezone = a.timezone or (cfg.timezone if p.exists() else _detect_timezone())
    if a.backup_dir is not None:
        cfg.backup_dir = a.backup_dir
    if a.public_host:
        cfg.public_host = a.public_host
    if not p.exists():
        if a.https_port:
            cfg.https_port = a.https_port
        elif not _port_free(443):
            cfg.https_port = 8443
        if not _port_free(80):
            cfg.http_port = 8080
    cfg.save()
    _boot(cfg)
    server.migrate()
    from app.keys import check_keyfile_before_start

    check_keyfile_before_start()  # creates the key file on a fresh install
    tls.ensure_server_cert(data_dir() / "tls", cfg.office_name, cfg.extra_hostnames)

    temp = None
    if a.admin_email:
        from sqlalchemy import select

        from app import audit, passwords
        from app.db import get_sessionmaker
        from app.models import Location, Role, StaffUser

        with get_sessionmaker()() as db:
            loc = db.scalar(select(Location).order_by(Location.created_at)) or Location(name=cfg.office_name)
            db.add(loc)
            user = db.scalar(select(StaffUser).where(StaffUser.email == a.admin_email.lower()))
            if user is None:
                temp = passwords.temporary_password()
                user = StaffUser(email=a.admin_email.lower(), full_name=a.admin_name or a.admin_email,
                                 role=Role.admin, active=True, locations=[loc],
                                 password_hash=passwords.hash_password(temp), must_change_password=True)
                db.add(user)
            db.commit()
            audit.record(None, audit.SYSTEM, "admin.bootstrap", resource_type="staff", resource_id=user.id,
                         details={"email": user.email, "via": "installer"})

    info = {
        "url": server.base_url(cfg) + "/staff",
        "local_url": f"https://localhost{'' if cfg.https_port == 443 else f':{cfg.https_port}'}/staff",
        "https_port": cfg.https_port,
        "http_port": cfg.http_port,
        "ca_file": str(data_dir() / "tls" / "office-ca.crt"),
        "security_code": tls.fingerprint(data_dir() / "tls" / "office-ca.crt"),
        "admin_email": a.admin_email,
        "temporary_password": temp,
    }
    if a.result_file:
        # Read once by the installer to show the admin's one-time password, then deleted.
        fd = os.open(a.result_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for k, v in info.items():
                f.write(f"{k}={'' if v is None else v}\n")
    else:
        print(json.dumps(info, indent=2))
    return 0


def cmd_workstation(a) -> int:
    url = a.server.rstrip("/")
    if not url.startswith("https://"):
        url = "https://" + url.removeprefix("http://")
    cfg = DesktopConfig(role="workstation", server_url=url)
    cfg.save()
    print(f"This PC will use Dental Intake at {url}/staff")
    return 0


def cmd_serve(_a) -> int:
    from . import server

    server.run()
    return 0


def cmd_backup(a) -> int:
    cfg = DesktopConfig.load()
    _boot(cfg)
    from app import backup

    path = backup.run_backup(a.dest, reason="manual")
    print(f"Backup saved: {path}")
    return 0


def cmd_restore(a) -> int:
    cfg = DesktopConfig.load()
    if not _port_free(cfg.https_port):
        print("Stop the Dental Intake service first (Services > Dental Intake > Stop), then run restore again.")
        return 2
    if not a.yes:
        ok = input("This REPLACES all current data with the backup. Type RESTORE to continue: ")
        if ok.strip() != "RESTORE":
            print("Cancelled.")
            return 1
    from . import server

    server.check_data_dir()
    server.apply_env(cfg)
    if not _backup_matches_key(a.zip):
        print("This backup was made with a different encryption key. Import the key file from your USB drive "
              "first (DentalIntakeServer.exe import-key E:\\dental-intake-key.json), then restore. Nothing was changed.")
        return 3
    from app.backup import restore_backup

    restore_backup(a.zip, str(data_dir()))
    server.apply_env(cfg)
    server.migrate()
    from app.keys import check_keyfile_before_start

    check_keyfile_before_start()
    print("Restored. The previous data was kept as intake.db.before-restore. Start the service again.")
    return 0


def _backup_matches_key(zip_path: str) -> bool:
    """Decrypt one record from the backup with this PC's key before touching any data."""
    import sqlite3
    import tempfile
    import zipfile

    from app import crypto

    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as z:
        z.extract("intake.db", tmp)
        con = sqlite3.connect(Path(tmp) / "intake.db")
        try:
            row = con.execute("SELECT id, identity_enc FROM intakes WHERE identity_enc IS NOT NULL LIMIT 1").fetchone()
        finally:
            con.close()
    if row is None:
        return True
    try:
        crypto.decrypt_json(row[1], f"intake:{row[0]}:identity")
        return True
    except Exception:
        return False


def _key_path() -> Path:
    return data_dir() / "keys" / "intake-keys.json"


def cmd_export_key(a) -> int:
    dest = a.dest
    if not dest:
        try:
            import tkinter
            from tkinter import filedialog, messagebox

            root = tkinter.Tk()
            root.withdraw()
            dest = filedialog.asksaveasfilename(title="Save the Dental Intake encryption key (choose your USB drive)",
                                                initialfile="dental-intake-key.json",
                                                filetypes=[("Key file", "*.json")])
            if not dest:
                return 1
        except Exception:
            print("Give a destination path, e.g.: DentalIntakeServer.exe export-key E:\\dental-intake-key.json")
            return 2
    shutil.copyfile(_key_path(), dest)
    msg = (f"Key saved to {dest}.\n\nKeep this USB drive in the office safe, away from the backups. "
           "Without this key, backups cannot be read by anyone.")
    print(msg)
    if not a.dest:
        messagebox.showinfo("Dental Intake", msg)
    return 0


def cmd_import_key(a) -> int:
    data = json.loads(Path(a.src).read_text())
    if not all(k in data for k in ("master_key", "index_key", "patient_session_secret", "staff_session_secret")):
        print("That file is not a Dental Intake key file.")
        return 2
    dest = _key_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        os.replace(dest, dest.with_suffix(".json.replaced"))
    shutil.copyfile(a.src, dest)
    print("Key imported. Restart the Dental Intake service.")
    return 0


def cmd_reset_admin(a) -> int:
    cfg = DesktopConfig.load()
    _boot(cfg)
    from app.cli import reset_admin

    reset_admin(a.email)
    return 0


def cmd_status(_a) -> int:
    cfg = DesktopConfig.load()
    print(f"Role: {cfg.role}")
    if cfg.role == "workstation":
        print(f"Server: {cfg.server_url}")
        return 0
    from . import server

    print(f"Dashboard: {server.base_url(cfg)}/staff")
    print(f"Data folder: {data_dir()}")
    print(f"Key file present: {_key_path().exists()}")
    ca = data_dir() / "tls" / "office-ca.crt"
    if ca.exists():
        print(f"Office security code: {tls.fingerprint(ca)}")
    _boot(cfg)
    from app import backup
    from app.cli import status

    status()
    backups = backup.list_backups(str(cfg.effective_backup_dir()))
    print(f"Backups in {cfg.effective_backup_dir()}: {len(backups)}")
    for b in backups[:3]:
        print(f"  {b.name}")
    return 0


def cmd_fingerprint(_a) -> int:
    print(tls.fingerprint(data_dir() / "tls" / "office-ca.crt"))
    return 0


def cmd_url(_a) -> int:
    cfg = DesktopConfig.load()
    if cfg.role == "workstation":
        print(cfg.server_url + "/staff")
    else:
        from . import server

        print(server.base_url(cfg) + "/staff")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="DentalIntakeServer", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--office-name")
    s.add_argument("--admin-email")
    s.add_argument("--admin-name")
    s.add_argument("--timezone")
    s.add_argument("--backup-dir")
    s.add_argument("--public-host")
    s.add_argument("--https-port", type=int)
    s.add_argument("--result-file")
    s.set_defaults(fn=cmd_setup)
    w = sub.add_parser("workstation")
    w.add_argument("--server", required=True)
    w.set_defaults(fn=cmd_workstation)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)
    b = sub.add_parser("backup")
    b.add_argument("--dest")
    b.set_defaults(fn=cmd_backup)
    r = sub.add_parser("restore")
    r.add_argument("zip")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_restore)
    e = sub.add_parser("export-key")
    e.add_argument("dest", nargs="?")
    e.set_defaults(fn=cmd_export_key)
    i = sub.add_parser("import-key")
    i.add_argument("src")
    i.set_defaults(fn=cmd_import_key)
    ra = sub.add_parser("reset-admin")
    ra.add_argument("email")
    ra.set_defaults(fn=cmd_reset_admin)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("fingerprint").set_defaults(fn=cmd_fingerprint)
    sub.add_parser("url").set_defaults(fn=cmd_url)
    a = parser.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        # The installer runs these commands hidden, so keep the reason somewhere an admin can read it.
        _log_failure(a.cmd)
        raise


def _log_failure(cmd: str) -> None:
    import datetime
    import traceback

    try:
        logs = data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "cli.log", "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.datetime.now().isoformat(timespec='seconds')} {cmd} failed\n{traceback.format_exc()}\n")
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
