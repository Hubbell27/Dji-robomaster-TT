"""Desktop edition: setup, certificates, backups and restore (runs on any OS)."""

import asyncio
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography import x509

from dentalintake import tls
from dentalintake.cli import main
from dentalintake.config import DesktopConfig
from dentalintake.paths import data_dir, is_network_path
from dentalintake.server import http_helper_app


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    result = tmp_path_factory.mktemp("r") / "result.txt"
    assert main(["setup", "--office-name", "Smile Dental", "--admin-email", "owner@smile.test",
                 "--admin-name", "Olivia Owner", "--timezone", "America/Chicago", "--https-port", "9876",
                 "--public-host", "localhost", "--result-file", str(result)]) == 0
    info = dict(line.split("=", 1) for line in result.read_text().splitlines())
    return info


def test_setup_creates_everything(installed):
    d = data_dir()
    assert (d / "intake.db").exists() and (d / "keys" / "intake-keys.json").exists()
    assert installed["temporary_password"] and installed["url"] == "https://localhost:9876/staff"
    assert len(installed["security_code"]) == 14
    cfg = DesktopConfig.load()
    assert cfg.role == "server" and cfg.timezone == "America/Chicago"


@pytest.mark.skipif(__import__("shutil").which("openssl") is None, reason="openssl CLI not available")
def test_certificate_chain_and_name_constraints(installed):
    tls_dir = data_dir() / "tls"
    ca = x509.load_pem_x509_certificate((tls_dir / "office-ca.crt").read_bytes())
    nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    assert any(isinstance(n, x509.IPAddress) and str(n.value) == "192.168.0.0/16" for n in nc.permitted_subtrees)
    out = subprocess.run(["openssl", "verify", "-CAfile", str(tls_dir / "office-ca.crt"), str(tls_dir / "server.crt")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    # A certificate for a public site signed by this CA must be rejected by the constraints.
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import datetime as dt
    ca_key = serialization.load_pem_private_key((tls_dir / "office-ca.key").read_bytes(), None)
    k = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    evil = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "bank.com")]))
            .issuer_name(ca.subject).public_key(k.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("bank.com")]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    evil_p = tls_dir / "evil.crt"
    evil_p.write_bytes(evil.public_bytes(serialization.Encoding.PEM))
    out = subprocess.run(["openssl", "verify", "-CAfile", str(tls_dir / "office-ca.crt"), str(evil_p)],
                         capture_output=True, text=True)
    evil_p.unlink()
    assert out.returncode != 0 and "subtree" in (out.stdout + out.stderr)


def test_server_cert_renews_when_names_change(installed):
    tls_dir = data_dir() / "tls"
    before = (tls_dir / "server.crt").read_bytes()
    tls.ensure_server_cert(tls_dir, "Smile Dental")
    assert (tls_dir / "server.crt").read_bytes() == before  # unchanged when still valid
    tls.ensure_server_cert(tls_dir, "Smile Dental", extra=["10.1.2.3"])
    assert (tls_dir / "server.crt").read_bytes() != before
    san = x509.load_pem_x509_certificate((tls_dir / "server.crt").read_bytes()).extensions \
        .get_extension_for_class(x509.SubjectAlternativeName).value
    assert "10.1.2.3" in {str(i) for i in san.get_values_for_type(x509.IPAddress)}


def test_http_helper_serves_ca_and_redirects(installed):
    app = http_helper_app(data_dir() / "tls" / "office-ca.crt", 9876)

    async def call(path, qs=b""):
        sent = []

        async def send(m):
            sent.append(m)

        await app({"type": "http", "path": path, "query_string": qs, "headers": [(b"host", b"frontdesk:80")]},
                  None, send)
        return sent

    ca = asyncio.run(call("/office-ca.crt"))
    assert ca[0]["status"] == 200 and ca[1]["body"].startswith(b"-----BEGIN CERTIFICATE-----")
    r = asyncio.run(call("/staff", b"a=1"))
    assert r[0]["status"] == 308 and dict(r[0]["headers"])[b"location"] == b"https://frontdesk:9876/staff?a=1"


def test_backup_and_restore_roundtrip(installed, tmp_path):
    from dentalintake.server import apply_env

    apply_env(DesktopConfig.load())
    from app import backup
    from app.db import get_sessionmaker
    from app.models import StaffUser

    zip_path = backup.run_backup(str(tmp_path), reason="test")
    with zipfile.ZipFile(zip_path) as z:
        assert {"intake.db", "manifest.json"} <= set(z.namelist())
        assert "intake-keys.json" not in " ".join(z.namelist())  # key never in backups
    assert backup.backed_up_today(str(tmp_path))
    # Change data, restore, and confirm the backup's state comes back.
    with get_sessionmaker()() as db:
        db.query(StaffUser).update({"full_name": "Changed"})
        db.commit()
    from sqlalchemy import create_engine

    get_sessionmaker().kw["bind"].dispose()
    backup.restore_backup(str(zip_path), str(data_dir()))
    eng = create_engine(f"sqlite:///{(data_dir() / 'intake.db').as_posix()}")
    with eng.connect() as c:
        names = [r[0] for r in c.exec_driver_sql("select full_name from staff_users")]
    assert names == ["Olivia Owner"]
    assert (data_dir() / "intake.db.before-restore").exists()


def test_restore_rejects_foreign_zip(tmp_path):
    from app.backup import restore_backup

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(ValueError):
        restore_backup(str(bad), str(tmp_path))


def test_network_paths_detected():
    assert is_network_path(Path(r"\\nas\share\DentalIntake"))
    assert not is_network_path(Path("/var/tmp"))


def test_workstation_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DENTAL_INTAKE_HOME", str(tmp_path))
    assert main(["workstation", "--server", "frontdesk-pc"]) == 0
    assert json.loads((tmp_path / "config.json").read_text())["server_url"] == "https://frontdesk-pc"
