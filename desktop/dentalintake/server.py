"""The office server process (runs as the "Dental Intake" Windows service).

It serves the app over HTTPS, and on plain HTTP only two things: the office
certificate (so another PC or a tablet can trust it) and a redirect to HTTPS.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from .config import DesktopConfig
from .paths import alembic_dir, data_dir, is_network_path, static_dir
from . import tls

log = logging.getLogger("dentalintake")


def public_host(cfg: DesktopConfig) -> str:
    if cfg.public_host and cfg.public_host != "auto":
        return cfg.public_host
    ip = tls.lan_ip()
    if ip and tls._in_private_nets(ip):
        return ip
    import socket

    return socket.gethostname().split(".")[0].lower() or "localhost"


def base_url(cfg: DesktopConfig, host: str | None = None) -> str:
    host = host or public_host(cfg)
    port = "" if cfg.https_port == 443 else f":{cfg.https_port}"
    return f"https://{host}{port}"


def apply_env(cfg: DesktopConfig) -> None:
    """Point the shared backend at this PC's data folder. Must run before importing ``app``."""
    d = data_dir()
    env = {
        "INTAKE_ENVIRONMENT": "production",
        "INTAKE_DEPLOYMENT": "office",
        "INTAKE_AUTH_MODE": "local",
        "INTAKE_KEY_PROVIDER": "file",
        "INTAKE_KEY_FILE": str(d / "keys" / "intake-keys.json"),
        "INTAKE_STORAGE_BACKEND": "local",
        "INTAKE_LOCAL_STORAGE_DIR": str(d / "files"),
        "INTAKE_DATABASE_URL": f"sqlite:///{(d / 'intake.db').as_posix()}",
        "INTAKE_PUBLIC_BASE_URL": base_url(cfg),
        "INTAKE_OFFICE_TIMEZONE": cfg.timezone,
        "INTAKE_RETENTION_YEARS": str(cfg.retention_years),
        "INTAKE_BACKUP_DIR": str(cfg.effective_backup_dir()),
        "INTAKE_BACKUP_HOUR": str(cfg.backup_hour),
        "INTAKE_BACKUP_KEEP_DAYS": str(cfg.backup_keep_days),
        "INTAKE_STATIC_DIR": str(static_dir()),
        "INTAKE_TRUST_FORWARDED_FOR": "false",
    }
    custom_form = d / "intake_form.json"
    if custom_form.exists():
        env["INTAKE_FORM_DEFINITION_PATH"] = str(custom_form)
    os.environ.update(env)


def check_data_dir() -> None:
    d = data_dir()
    if is_network_path(d):
        raise SystemExit(
            f"The Dental Intake data folder ({d}) is on a network drive. The database must be on this PC's "
            "own disk — databases on shared drives get corrupted when several computers use them. "
            "Use the shared drive for backups instead."
        )
    d.mkdir(parents=True, exist_ok=True)
    for sub in ("keys", "files", "tls", "logs"):
        (d / sub).mkdir(exist_ok=True)


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir()))
    command.upgrade(cfg, "head")


def setup_logging() -> None:
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logs / "server.log", maxBytes=5_000_000, backupCount=10,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # The audit log is also mirrored into its own file (JSON lines, no PHI).
    audit_handler = logging.handlers.RotatingFileHandler(logs / "audit.log", maxBytes=20_000_000, backupCount=50,
                                                         encoding="utf-8")
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("intake.audit").addHandler(audit_handler)


def http_helper_app(ca_path: Path, https_port: int):
    """Tiny ASGI app for plain HTTP: serve the office CA, redirect everything else."""
    ca_bytes = ca_path.read_bytes()

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "/")
        if path in ("/office-ca.crt", "/office-ca.pem"):
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", b"application/x-x509-ca-cert"),
                (b"content-disposition", b'attachment; filename="dental-intake-office-ca.crt"'),
                (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": ca_bytes})
            return
        host = dict(scope.get("headers") or []).get(b"host", b"localhost").decode("latin-1").split(":")[0]
        port = "" if https_port == 443 else f":{https_port}"
        qs = scope.get("query_string", b"").decode("latin-1")
        location = f"https://{host}{port}{path}" + (f"?{qs}" if qs else "")
        await send({"type": "http.response.start", "status": 308, "headers": [(b"location", location.encode())]})
        await send({"type": "http.response.body", "body": b""})

    return app


async def _serve(cfg: DesktopConfig) -> None:
    import uvicorn

    from app.main import app as intake_app

    tls_dir = data_dir() / "tls"
    cert, key = tls.ensure_server_cert(tls_dir, cfg.office_name, cfg.extra_hostnames)
    https = uvicorn.Server(uvicorn.Config(
        intake_app, host="0.0.0.0", port=cfg.https_port, ssl_certfile=str(cert), ssl_keyfile=str(key),
        proxy_headers=False, server_header=False, log_config=None, access_log=False, timeout_graceful_shutdown=10,
    ))
    http = uvicorn.Server(uvicorn.Config(
        http_helper_app(tls_dir / "office-ca.crt", cfg.https_port), host="0.0.0.0", port=cfg.http_port,
        server_header=False, log_config=None, access_log=False, lifespan="off",
    ))
    log.info("Dental Intake serving %s (http helper on port %s)", base_url(cfg), cfg.http_port)
    await asyncio.gather(https.serve(), http.serve())


def run() -> None:
    cfg = DesktopConfig.load()
    if cfg.role != "server":
        raise SystemExit("This PC is set up as a workstation; the office server runs on the main PC.")
    check_data_dir()
    setup_logging()
    apply_env(cfg)
    migrate()
    try:
        asyncio.run(_serve(cfg))
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("server stopped with an error")
        sys.exit(1)
