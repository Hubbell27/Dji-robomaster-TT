from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db import get_sessionmaker
from app.models import Intake, IntakeFile, IntakeStatus, utcnow
from app.retention import run_retention

from .conftest import jpeg_bytes
from .test_intake_flow import _complete_answers, _consents, _create, _location_id, _verify


def _session(client, token, dob="1985-04-12"):
    return {"Authorization": f"Bearer {_verify(client, token, dob).json()['session_token']}"}


def _submit(client, token, answers=None, with_cards=True):
    s = _session(client, token)
    if with_cards:
        for kind in ("primary_card_front", "primary_card_back"):
            client.post(f"/api/patient/intake/files/{kind}", files={"file": ("c.jpg", jpeg_bytes(), "image/jpeg")},
                        headers=s)
    r = client.post("/api/patient/intake/submit",
                    json={"answers": answers or _complete_answers(), "consents": _consents()}, headers=s)
    assert r.status_code == 200, r.text


def test_today_view_orders_by_appointment(client, desk):
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).replace(hour=9, minute=0, second=0, microsecond=0)
    _create(client, desk, first_name="Late", appointment_at=(today + timedelta(hours=6)).isoformat())
    _create(client, desk, first_name="Early", appointment_at=today.isoformat())
    _create(client, desk, first_name="Tomorrow", appointment_at=(today + timedelta(days=1)).isoformat())
    _create(client, desk, first_name="Walkin")  # no appointment, created today
    names = [i["patient"]["first_name"] for i in client.get("/api/staff/intakes?view=today", headers=desk).json()["items"]]
    assert names == ["Early", "Late", "Walkin"]
    counts = client.get("/api/staff/intakes/counts", headers=desk).json()
    assert counts["today"] == 3 and counts["pending"] == 4


def test_returning_patient_prefill(client, desk):
    _, token = _create(client, desk)
    _submit(client, token)

    # Same person typed differently (case / accent / spacing) is still found.
    r = client.get("/api/staff/patients/lookup",
                   params={"first_name": "MARÍA", "last_name": " lopez", "dob": "1985-04-12"}, headers=desk)
    assert r.json()["returning"] is True and r.json()["visits"] == 1

    new_id, new_token = _create(client, desk, first_name="María", last_name="Lopez")
    s = _session(client, new_token)
    data = client.get("/api/patient/intake", headers=s).json()
    assert data["prefilled"] is True
    assert data["answers"]["medication_list"][0]["name"] == "Lisinopril"
    assert {f["kind"] for f in data["files"]} == {"primary_card_front", "primary_card_back"}
    # Card photos are copies, not shared references.
    with get_sessionmaker()() as db:
        ids = {f.id for f in db.get(Intake, new_id).files}
        assert all(db.get(IntakeFile, i).intake_id == new_id for i in ids)
    # The patient can submit with no changes (consents are re-signed every time).
    answers = {**data["answers"]}
    r = client.post("/api/patient/intake/submit", json={"answers": answers, "consents": _consents()}, headers=s)
    assert r.status_code == 200, r.text

    # Opting out of prefill starts blank.
    _, t3 = _create(client, desk, prefill=False)
    assert "medication_list" not in client.get("/api/patient/intake", headers=_session(client, t3)).json()["answers"]


def test_review_queue_and_alerts(client, desk):
    intake_id, token = _create(client, desk)
    answers = _complete_answers() | {"blood_thinners": "yes", "conditions": ["artificial_valve"]}
    _submit(client, token, answers)

    items = client.get("/api/staff/intakes?view=review", headers=desk).json()["items"]
    assert [i["id"] for i in items] == [intake_id]
    labels = [a["label"] for a in items[0]["alerts"]]
    assert "Allergy: Penicillin / amoxicillin" in labels
    assert "Takes blood thinners" in labels and "Artificial heart valve" in labels

    r = client.post(f"/api/staff/intakes/{intake_id}/review", json={"reviewed": True}, headers=desk)
    assert r.json()["reviewed_by"] == "Frank Desk"
    assert client.get("/api/staff/intakes?view=review", headers=desk).json()["total"] == 0
    assert client.get("/api/staff/intakes?view=completed", headers=desk).json()["total"] == 1
    assert client.get("/api/staff/intakes/counts", headers=desk).json()["review"] == 0


def test_retention_purges_phi(client, desk):
    old_id, token = _create(client, desk)
    _submit(client, token)
    stale_id, _ = _create(client, desk)
    keep_id, _ = _create(client, desk)
    with get_sessionmaker()() as db:
        old = db.get(Intake, old_id)
        old.submitted_at = utcnow() - timedelta(days=365 * 11)
        stale = db.get(Intake, stale_id)
        stale.status = IntakeStatus.cancelled
        db.commit()
        # updated_at is auto-bumped on commit; backdate it directly.
        stale.updated_at = utcnow() - timedelta(days=100)
        db.commit()
        storage_keys = [f.storage_key for f in old.files]
    with get_sessionmaker()() as db:
        assert run_retention(db) == {"submissions": 1, "drafts": 1}
    with get_sessionmaker()() as db:
        old = db.get(Intake, old_id)
        assert old.status == IntakeStatus.purged and old.identity_enc is None and old.form_enc is None
        assert old.files == [] and old.pdf_key is None
        assert db.get(Intake, keep_id).status == IntakeStatus.pending
    from app.storage import get_storage
    import pytest
    for key in storage_keys:
        with pytest.raises(FileNotFoundError):
            get_storage().get(key)
    all_ids = [i["id"] for i in client.get("/api/staff/intakes?view=all", headers=desk).json()["items"]]
    assert old_id not in all_ids and stale_id not in all_ids


def test_simulated_busy_day(client, desk, admin):
    """30 patients through one day: most finish, some never open, one locks out,
    returning patients are pre-filled, and staff work the review queue."""
    tz = ZoneInfo("America/New_York")
    start = datetime.now(tz).replace(hour=8, minute=0, second=0, microsecond=0)
    created = []
    for n in range(30):
        appt = (start + timedelta(minutes=20 * n)).astimezone(timezone.utc).isoformat()
        created.append(_create(client, desk, first_name=f"Pat{n}", last_name="Day",
                               dob=f"19{50 + n}-0{1 + n % 9}-1{n % 9}", appointment_at=appt))
    for n, (_, token) in enumerate(created):
        dob = f"19{50 + n}-0{1 + n % 9}-1{n % 9}"
        if n % 10 == 9:
            continue  # never opened
        if n == 28:
            for _ in range(5):
                _verify(client, token, "1900-01-01")
            continue
        s = _session(client, token, dob)
        answers = _complete_answers() | {"first_name": f"Pat{n}", "last_name": "Day", "has_insurance": "no"}
        r = client.post("/api/patient/intake/submit", json={"answers": answers, "consents": _consents()}, headers=s)
        assert r.status_code == 200, r.text
    counts = client.get("/api/staff/intakes/counts", headers=desk).json()
    assert counts == {"today": 30, "pending": 3, "review": 26, "attention": 1}
    for item in client.get("/api/staff/intakes?view=review&limit=200", headers=desk).json()["items"][:20]:
        assert client.get(f"/api/staff/intakes/{item['id']}/pdf", headers=desk).status_code == 200
        client.post(f"/api/staff/intakes/{item['id']}/review", json={"reviewed": True}, headers=desk)
    assert client.get("/api/staff/intakes/counts", headers=desk).json()["review"] == 6
    today = client.get("/api/staff/intakes?view=today&limit=200", headers=desk).json()["items"]
    assert [i["patient"]["first_name"] for i in today][:3] == ["Pat0", "Pat1", "Pat2"]
    # Next visit for a returning patient is pre-filled.
    _, t = _create(client, desk, first_name="Pat0", last_name="Day", dob="1950-01-10")
    assert client.get("/api/patient/intake", headers=_session(client, t, "1950-01-10")).json()["prefilled"] is True
    assert client.get("/api/admin/audit?action=intake.reviewed", headers=admin).json()["total"] == 20


def test_prefill_and_lookup_respect_locations(client, desk, north):
    """A front-desk user at another location must not see or copy this patient's history."""
    _, token = _create(client, desk)
    _submit(client, token)
    params = {"first_name": "Maria", "last_name": "Lopez", "dob": "1985-04-12"}
    assert client.get("/api/staff/patients/lookup", params=params, headers=north).json() == {"returning": False}
    north_loc = _location_id("Northside Dental")
    r = client.post("/api/staff/intakes", headers=north, json={
        "location_id": north_loc, "first_name": "Maria", "last_name": "Lopez", "dob": "1985-04-12"})
    detail = client.get(f"/api/staff/intakes/{r.json()['intake']['id']}", headers=north).json()
    assert detail["prefilled"] is False and detail["answers"] == {} and detail["files"] == []
