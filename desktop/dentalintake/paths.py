"""Where things live on disk.

Program files (read-only):   C:\\Program Files\\Dental Intake\\
Office data (admins only):   C:\\ProgramData\\Dental Intake\\
    config.json   settings written by setup
    intake.db     the database (must be on this PC's own disk)
    keys\\         encryption key file  <- back this up to USB
    files\\        encrypted card photos and PDFs
    tls\\          office certificate authority + HTTPS certificate
    backups\\      nightly backups (unless a shared drive is configured)
    logs\\         service logs (no patient health information)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("DENTAL_INTAKE_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Dental Intake"
    return Path.home() / ".dental-intake"


def bundle_dir() -> Path:
    """Directory holding bundled resources (PyInstaller) or the source tree."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def static_dir() -> Path:
    b = bundle_dir()
    return b / "static" if (b / "static").is_dir() else b / "frontend" / "dist"


def alembic_dir() -> Path:
    b = bundle_dir()
    return b / "alembic" if (b / "alembic").is_dir() else b / "backend" / "alembic"


def is_network_path(p: Path) -> bool:
    s = str(p)
    if s.startswith("\\\\") or s.startswith("//"):
        return True
    if sys.platform == "win32":
        import ctypes

        drive = os.path.splitdrive(str(p.resolve()))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4  # DRIVE_REMOTE
    return False
