"""Office (single PC) deployment: local password + TOTP login and key file."""

import json
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from app import crypto, passwords
from app.config import get_settings
from app.db import get_sessionmaker
from app.keys import get_secrets
from app.models import Role, StaffUser
from app.storage import get_storage


@pytest.fixture
def office(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_AUTH_MODE", "local")
    monkeypatch.setenv("INTAKE_DEPLOYMENT", "office")
    monkeypatch.setenv("INTAKE_KEY_PROVIDER", "file")
    monkeypatch.setenv("INTAKE_KEY_FILE", str(tmp_path / "keys" / "intake-keys.json"))
    caches = (get_settings, get_secrets, crypto.get_key_provider, get_storage)
    for c in caches:
        c.cache_clear()
    from app.main import create_app

    client = TestClient(create_app())
    with get_sessionmaker()() as db:
        user = StaffUser(email="owner@office.test", full_name="Olive Owner", role=Role.admin,
                         password_hash=passwords.hash_password("Temp-Pass-1234!"), must_change_password=True)
        db.add(user)
        db.commit()
    yield client, tmp_path
    for c in caches:
        c.cache_clear()


def _enroll_and_login(client, email="owner@office.test", password="Temp-Pass-1234!"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["stage"] == "enroll" and data["qr_svg"].startswith("<svg")
    code = passwords.totp_now(data["secret"])
    r = client.post("/api/auth/verify-mfa", json={"challenge": data["challenge"], "code": code})
    assert r.status_code == 200, r.text
    return data["secret"], r.json()


def test_key_file_created_private(office):
    _, tmp = office
    crypto.encrypt_json({"x": 1}, "t")
    path = tmp / "keys" / "intake-keys.json"
    assert json.loads(path.read_text())["master_key"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_first_login_enrolls_mfa_and_forces_password_change(office):
    client, _ = office
    secret, session = _enroll_and_login(client)
    assert session["must_change_password"] is True
    h = {"Authorization": f"Bearer {session['token']}"}
    assert client.get("/api/staff/intakes", headers=h).status_code == 403
    assert client.get("/api/staff/me", headers=h).status_code == 200

    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "Temp-Pass-1234!", "new_password": "short"})
    assert r.status_code == 400
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "Temp-Pass-1234!", "new_password": "Brushing-Twice-Daily-9"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/staff/intakes", headers=h).status_code == 200

    # Next sign-in asks for a code (already enrolled); the old code cannot be replayed.
    r = client.post("/api/auth/login", json={"email": "owner@office.test", "password": "Brushing-Twice-Daily-9"})
    assert r.json()["stage"] == "mfa"
    with get_sessionmaker()() as db:
        last = db.query(StaffUser).filter_by(email="owner@office.test").one().totp_last_step
    replay = passwords._code(secret, last)
    assert client.post("/api/auth/verify-mfa", json={"challenge": r.json()["challenge"], "code": replay}).status_code == 401

    r = client.post("/api/auth/refresh", headers=h)
    assert r.status_code == 200 and r.json()["token"]


def test_lockout_after_failed_passwords(office):
    client, _ = office
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "owner@office.test", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": "owner@office.test", "password": "Temp-Pass-1234!"})
    assert r.status_code == 423
    assert client.post("/api/auth/login", json={"email": "nobody@x.test", "password": "x"}).status_code == 401


def test_admin_creates_staff_and_resets(office):
    client, _ = office
    _, session = _enroll_and_login(client)
    h = {"Authorization": f"Bearer {session['token']}"}
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "Temp-Pass-1234!", "new_password": "Brushing-Twice-Daily-9"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    r = client.post("/api/admin/staff", headers=h, json={"email": "dee@office.test", "full_name": "Dee Desk"})
    assert r.status_code == 201, r.text
    temp = r.json()["temporary_password"]
    staff_id = r.json()["id"]
    _, desk_session = _enroll_and_login(client, "dee@office.test", temp)
    desk_h = {"Authorization": f"Bearer {desk_session['token']}"}
    assert client.get("/api/staff/me", headers=desk_h).status_code == 200

    r = client.post(f"/api/admin/staff/{staff_id}/reset-password", headers=h, json={"reset_mfa": True})
    assert r.status_code == 200 and r.json()["temporary_password"] != temp
    # Reset ends existing sessions immediately.
    assert client.get("/api/staff/me", headers=desk_h).status_code == 401
    # And MFA must be enrolled again.
    login = client.post("/api/auth/login", json={"email": "dee@office.test", "password": r.json()["temporary_password"]})
    assert login.json()["stage"] == "enroll"


def test_totp_window():
    secret = passwords.new_totp_secret()
    now = time.time()
    assert passwords.verify_totp(secret, passwords.totp_now(secret, now), None, now) is not None
    assert passwords.verify_totp(secret, passwords.totp_now(secret, now - 90), None, now) is None


def _signed_in(client):
    _, session = _enroll_and_login(client)
    h = {"Authorization": f"Bearer {session['token']}"}
    r = client.post("/api/auth/change-password", headers=h,
                    json={"current_password": "Temp-Pass-1234!", "new_password": "Brushing-Twice-Daily-9"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_logout_revokes_token(office):
    client, _ = office
    h = _signed_in(client)
    assert client.get("/api/staff/me", headers=h).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/staff/me", headers=h).status_code == 401


def test_locked_account_does_not_reveal_lock_without_password(office):
    client, _ = office
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "owner@office.test", "password": "wrong"})
    wrong = client.post("/api/auth/login", json={"email": "owner@office.test", "password": "still-wrong"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Email or password is incorrect."


def test_totp_code_single_use_under_parallel_requests(office):
    import concurrent.futures as cf

    client, _ = office
    r = client.post("/api/auth/login", json={"email": "owner@office.test", "password": "Temp-Pass-1234!"})
    data = r.json()
    code = passwords.totp_now(data["secret"])
    with cf.ThreadPoolExecutor(6) as ex:
        results = list(ex.map(lambda _: client.post("/api/auth/verify-mfa",
                                                     json={"challenge": data["challenge"], "code": code}).status_code,
                              range(6)))
    assert results.count(200) == 1


def test_wrong_key_file_refused(office, tmp_path, monkeypatch):
    from app import services
    from app.keys import check_keyfile_before_start, load_or_create_keyfile
    from app.models import Intake, IntakeStatus, Location, new_id

    # Create an encrypted record with the current key, then swap in a different key file.
    with get_sessionmaker()() as db:
        loc = db.query(Location).first()
        user = db.query(StaffUser).first()
        i = Intake(id=new_id(), location_id=loc.id, created_by_id=user.id, status=IntakeStatus.pending,
                   session_epoch=0, dob_failed_attempts=0)
        services.rotate_link(i)
        services.set_identity(i, "A", "B", "1990-01-01")
        db.add(i)
        db.commit()
    check_keyfile_before_start()  # matching key: fine
    other = tmp_path / "other" / "keys.json"
    monkeypatch.setenv("INTAKE_KEY_FILE", str(other))
    for c in (get_settings, get_secrets, crypto.get_key_provider):
        c.cache_clear()
    with pytest.raises(RuntimeError, match="missing"):
        check_keyfile_before_start()
    load_or_create_keyfile(str(other))
    with pytest.raises(RuntimeError, match="does not match"):
        check_keyfile_before_start()
