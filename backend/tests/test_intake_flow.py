from datetime import timedelta

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import AuditEvent, Intake, Location, utcnow

from .conftest import jpeg_bytes, signature_data_url


def _location_id(name="Main Street Dental"):
    with get_sessionmaker()() as db:
        return db.scalar(select(Location.id).where(Location.name == name))


def _create(client, headers, **overrides):
    body = {"location_id": _location_id(), "first_name": "Maria", "last_name": "Lopez", "dob": "1985-04-12",
            "language": "en", **overrides}
    r = client.post("/api/staff/intakes", json=body, headers=headers)
    assert r.status_code == 201, r.text
    data = r.json()
    token = data["link"].split("#t=", 1)[1]
    return data["intake"]["id"], token


def _verify(client, token, dob="1985-04-12"):
    return client.post("/api/patient/verify", json={"token": token, "dob": dob})


def _complete_answers():
    return {
        "first_name": "Maria", "last_name": "Lopez", "sex": "female",
        "address1": "12 Elm St", "city": "Springfield", "state": "IL", "zip": "62704",
        "phone_mobile": "(217) 555-0199", "email": "maria@example.com", "preferred_contact": "text",
        "emergency_name": "Juan Lopez", "emergency_relationship": "Spouse", "emergency_phone": "217-555-0100",
        "patient_is_responsible": "yes",
        "good_health": "yes", "under_care": "no", "hospitalized": "no", "conditions": ["high_bp", "asthma"],
        "premed_antibiotics": "no", "bisphosphonates": "no", "blood_thinners": "no", "tobacco": "never",
        "pregnant": "no",
        "taking_medications": "yes",
        "medication_list": [{"name": "Lisinopril", "dose": "10 mg", "frequency": "daily", "reason": "BP"}],
        "has_allergies": "yes", "common_allergies": ["penicillin"],
        "allergy_list": [{"allergen": "Penicillin", "reaction": "Hives", "severity": "moderate"}],
        "has_insurance": "yes", "primary_carrier": "Delta Dental", "primary_member_id": "DD123456",
        "primary_subscriber_name": "Maria Lopez", "primary_subscriber_relationship": "self",
        "primary_subscriber_dob": "1985-04-12", "has_secondary": "no",
    }


def _consents():
    sig = signature_data_url()
    return {k: {"agreed": True, "typed_name": "Maria Lopez", "relationship": "self", "signature": sig}
            for k in ("treatment", "privacy", "financial")}


def test_full_intake_flow(client, desk):
    intake_id, token = _create(client, desk)

    r = _verify(client, token)
    assert r.status_code == 200, r.text
    session = {"Authorization": f"Bearer {r.json()['session_token']}"}

    r = client.get("/api/patient/intake", headers=session)
    assert r.json()["answers"]["dob"] == "1985-04-12"
    assert r.json()["answers"]["first_name"] == "Maria"

    r = client.put("/api/patient/intake/draft", json={"answers": {"city": "Springfield", "bogus": "x"}}, headers=session)
    assert r.status_code == 200

    for kind in ("primary_card_front", "primary_card_back"):
        r = client.post(f"/api/patient/intake/files/{kind}", files={"file": ("card.jpg", jpeg_bytes(), "image/jpeg")},
                        headers=session)
        assert r.status_code == 200, r.text

    r = client.post("/api/patient/intake/submit",
                    json={"answers": _complete_answers(), "consents": _consents(), "language": "en"}, headers=session)
    assert r.status_code == 200, r.text

    # Link and session are spent after submission.
    assert client.get("/api/patient/intake", headers=session).status_code == 401
    assert _verify(client, token).status_code == 410

    r = client.get("/api/staff/intakes?view=completed", headers=desk)
    assert [i["id"] for i in r.json()["items"]] == [intake_id]

    detail = client.get(f"/api/staff/intakes/{intake_id}", headers=desk).json()
    assert detail["status"] == "submitted"
    assert detail["answers"]["medication_list"][0]["name"] == "Lisinopril"
    assert detail["consents"]["treatment"]["has_signature"] is True
    assert "signature" not in detail["consents"]["treatment"]
    assert len(detail["files"]) == 2

    pdf = client.get(f"/api/staff/intakes/{intake_id}/pdf", headers=desk)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert pdf.headers["cache-control"] == "no-store"

    img = client.get(f"/api/staff/intakes/{intake_id}/files/{detail['files'][0]['id']}", headers=desk)
    assert img.headers["content-type"] == "image/jpeg"

    with get_sessionmaker()() as db:
        actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.resource_id == intake_id)))
    assert {"intake.created", "patient.verify", "patient.submit", "intake.viewed",
            "intake.pdf_downloaded", "intake.file_viewed"} <= actions


def test_phi_is_encrypted_at_rest(client, desk):
    intake_id, token = _create(client, desk)
    session = {"Authorization": f"Bearer {_verify(client, token).json()['session_token']}"}
    client.put("/api/patient/intake/draft", json={"answers": {"city": "Springfield"}}, headers=session)
    with get_sessionmaker()() as db:
        intake = db.get(Intake, intake_id)
        assert b"Maria" not in intake.identity_enc and b"1985" not in intake.identity_enc
        assert b"Springfield" not in intake.form_enc
        assert token not in intake.token_hash


def test_validation_errors_returned_and_draft_kept(client, desk):
    _, token = _create(client, desk)
    session = {"Authorization": f"Bearer {_verify(client, token).json()['session_token']}"}
    answers = _complete_answers()
    del answers["city"]
    answers["zip"] = "abc"
    consents = _consents()
    consents["privacy"]["agreed"] = False
    r = client.post("/api/patient/intake/submit", json={"answers": answers, "consents": consents}, headers=session)
    assert r.status_code == 422
    fields = {e["field"] for e in r.json()["errors"]}
    assert {"city", "zip", "consent.privacy", "primary_card_front", "primary_card_back"} <= fields
    assert client.get("/api/patient/intake", headers=session).json()["answers"]["zip"] == "abc"


def test_dob_lockout(client, desk):
    intake_id, token = _create(client, desk)
    for _ in range(4):
        assert _verify(client, token, dob="2000-01-01").status_code == 400
    assert _verify(client, token, dob="2000-01-01").status_code == 423
    # Even the correct DOB no longer works once locked.
    assert _verify(client, token).status_code == 410
    # Staff can reissue, which resets attempts and yields a fresh link.
    r = client.post(f"/api/staff/intakes/{intake_id}/reissue", headers=desk)
    new_token = r.json()["link"].split("#t=", 1)[1]
    assert _verify(client, token).status_code == 404
    assert _verify(client, new_token).status_code == 200


def test_link_expires_after_7_days(client, desk):
    intake_id, token = _create(client, desk)
    with get_sessionmaker()() as db:
        intake = db.get(Intake, intake_id)
        assert timedelta(days=6, hours=23) < intake.expires_at - utcnow() <= timedelta(days=7)
        intake.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert _verify(client, token).status_code == 410
    r = client.get("/api/staff/intakes?view=attention", headers=desk)
    assert r.json()["items"][0]["status"] == "expired"


def test_session_invalidated_on_reissue_and_cancel(client, desk):
    intake_id, token = _create(client, desk)
    session = {"Authorization": f"Bearer {_verify(client, token).json()['session_token']}"}
    client.post(f"/api/staff/intakes/{intake_id}/cancel", headers=desk)
    assert client.get("/api/patient/intake", headers=session).status_code == 401


def test_location_isolation(client, desk, north):
    intake_id, _ = _create(client, desk)
    assert client.get(f"/api/staff/intakes/{intake_id}", headers=north).status_code == 404
    assert client.get("/api/staff/intakes?view=all", headers=north).json()["total"] == 0
    r = client.post("/api/staff/intakes", headers=north, json={
        "location_id": _location_id(), "first_name": "A", "last_name": "B", "dob": "1990-01-01"})
    assert r.status_code == 403


def test_admin_only_endpoints(client, desk, admin):
    assert client.get("/api/admin/audit", headers=desk).status_code == 403
    assert client.get("/api/admin/staff", headers=desk).status_code == 403
    r = client.get("/api/admin/audit?action=auth.admin_required", headers=admin)
    assert r.status_code == 200 and r.json()["total"] >= 1


def test_requires_auth(client):
    assert client.get("/api/staff/intakes").status_code == 401
    assert client.get("/api/staff/intakes", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/api/patient/intake").status_code == 401


def test_deactivated_staff_blocked(client, admin, desk):
    staff = client.get("/api/admin/staff", headers=admin).json()
    desk_id = next(s["id"] for s in staff if s["email"] == "desk@office.test")
    assert client.patch(f"/api/admin/staff/{desk_id}", json={"active": False}, headers=admin).status_code == 200
    assert client.get("/api/staff/me", headers=desk).status_code == 403


def test_rejects_non_image_upload(client, desk):
    _, token = _create(client, desk)
    session = {"Authorization": f"Bearer {_verify(client, token).json()['session_token']}"}
    r = client.post("/api/patient/intake/files/primary_card_front",
                    files={"file": ("x.jpg", b"<html>not an image</html>", "image/jpeg")}, headers=session)
    assert r.status_code == 400


def test_spanish_submission_pdf(client, desk):
    intake_id, token = _create(client, desk, language="es")
    session = {"Authorization": f"Bearer {_verify(client, token).json()['session_token']}"}
    answers = _complete_answers() | {"has_insurance": "no"}
    r = client.post("/api/patient/intake/submit", json={"answers": answers, "consents": _consents(), "language": "es"},
                    headers=session)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/staff/intakes/{intake_id}/pdf", headers=desk).status_code == 200


def test_security_headers(client):
    r = client.get("/api/patient/form-definition")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
