"""Desktop configuration (config.json in the data folder)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import data_dir


@dataclass
class DesktopConfig:
    role: str = "server"  # server | workstation
    office_name: str = "Dental Office"
    timezone: str = "America/New_York"
    https_port: int = 443
    http_port: int = 80
    # Address put into patient links. "auto" = this PC's current LAN IP address.
    public_host: str = "auto"
    extra_hostnames: list[str] = field(default_factory=list)
    # Nightly backup destination; empty = <data dir>\backups. May be a shared drive.
    backup_dir: str = ""
    backup_hour: int = 21
    backup_keep_days: int = 30
    retention_years: int = 10
    # Workstations only: the office PC that holds the data, e.g. https://FRONTDESK-PC
    server_url: str = ""

    @staticmethod
    def path() -> Path:
        return data_dir() / "config.json"

    @classmethod
    def load(cls) -> "DesktopConfig":
        p = cls.path()
        if not p.exists():
            raise FileNotFoundError(f"Dental Intake is not set up yet ({p} missing). Run setup first.")
        raw = json.loads(p.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def effective_backup_dir(self) -> Path:
        return Path(self.backup_dir) if self.backup_dir else data_dir() / "backups"
