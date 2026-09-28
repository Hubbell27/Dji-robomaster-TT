"""End-to-end check of an INSTALLED Dental Intake (run by Windows CI after a silent install).

Talks to the running Windows service over HTTPS, trusting only the office CA,
and walks a full patient: admin first sign-in with MFA enrollment, password
change, new intake link, patient DOB check, card photo upload, e-signature
submit, staff review queue, PDF download, mark reviewed, audit trail.

  python windows_e2e.py --base https://localhost --ca "C:\\ProgramData\\Dental Intake\\tls\\office-ca.crt"
         --email admin@ci.test --temp-password XXXX
"""

import argparse
import base64
import io
import sys
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.passwords import totp_now  # noqa: E402


def png_signature() -> str:
    img = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    for x in range(20, 380):
        img.putpixel((x, 60 + (x % 17) - 8), (0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def card() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 760), (30, 110, 160)).save(buf, "JPEG")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--ca", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--temp-password", required=True)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, verify=a.ca, timeout=60)

    def ok(r, code=200):
        assert r.status_code == code, f"{r.request.method} {r.request.url} -> {r.status_code} {r.text[:300]}"
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r

    assert ok(c.get("/api/health"))["ok"] is True
    cfg = ok(c.get("/api/config"))
    assert cfg["auth_mode"] == "local" and cfg["deployment"] == "office"

    login = ok(c.post("/api/auth/login", json={"email": a.email, "password": a.temp_password}))
    assert login["stage"] == "enroll"
    sess = ok(c.post("/api/auth/verify-mfa", json={"challenge": login["challenge"], "code": totp_now(login["secret"])}))
    h = {"Authorization": f"Bearer {sess['token']}"}
    sess = ok(c.post("/api/auth/change-password", headers=h,
                     json={"current_password": a.temp_password, "new_password": "Windows-CI-Strong-Pass-1!"}))
    h = {"Authorization": f"Bearer {sess['token']}"}
    print("admin signed in with MFA")

    loc = ok(c.get("/api/staff/locations", headers=h))[0]
    created = ok(c.post("/api/staff/intakes", headers=h, json={
        "location_id": loc["id"], "first_name": "Wendy", "last_name": "Windows", "dob": "1984-03-09",
        "appointment_at": None}), 201)
    link = created["link"]
    token = link.split("#t=", 1)[1].split("&", 1)[0]
    print("intake link:", link.split("#")[0])

    pv = ok(c.post("/api/patient/verify", json={"token": token, "dob": "1984-03-09"}))
    ph = {"Authorization": f"Bearer {pv['session_token']}"}
    for kind in ("primary_card_front", "primary_card_back"):
        ok(c.post(f"/api/patient/intake/files/{kind}", headers=ph, files={"file": ("card.jpg", card(), "image/jpeg")}))
    sig = png_signature()
    answers = {
        "first_name": "Wendy", "last_name": "Windows", "sex": "female", "address1": "1 Main St", "city": "Redmond",
        "state": "WA", "zip": "98052", "phone_mobile": "425-555-0100", "preferred_contact": "text",
        "emergency_name": "Walt Windows", "emergency_relationship": "Spouse", "emergency_phone": "425-555-0101",
        "patient_is_responsible": "yes", "good_health": "yes", "under_care": "no", "hospitalized": "no",
        "premed_antibiotics": "no", "bisphosphonates": "no", "blood_thinners": "yes", "tobacco": "never",
        "pregnant": "no", "taking_medications": "yes", "medication_list": [{"name": "Warfarin", "dose": "5 mg"}],
        "has_allergies": "yes", "common_allergies": ["latex"], "has_insurance": "yes", "primary_carrier": "Delta Dental",
        "primary_member_id": "DD1", "primary_subscriber_name": "Wendy Windows", "primary_subscriber_relationship": "self",
        "primary_subscriber_dob": "1984-03-09", "has_secondary": "no",
    }
    consents = {k: {"agreed": True, "typed_name": "Wendy Windows", "relationship": "self", "signature": sig}
                for k in ("treatment", "privacy", "financial")}
    ok(c.post("/api/patient/intake/submit", headers=ph, json={"answers": answers, "consents": consents}))
    print("patient submitted")

    review = ok(c.get("/api/staff/intakes?view=review", headers=h))
    assert review["total"] == 1
    item = review["items"][0]
    assert any("blood thinners" in x["label"].lower() for x in item["alerts"])
    pdf = c.get(f"/api/staff/intakes/{item['id']}/pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.status_code
    ok(c.post(f"/api/staff/intakes/{item['id']}/review", headers=h, json={"reviewed": True}))
    assert ok(c.get("/api/staff/intakes/counts", headers=h))["review"] == 0
    audit = ok(c.get("/api/admin/audit?action=intake.pdf_downloaded", headers=h))
    assert audit["total"] >= 1
    print(f"reviewed, PDF {len(pdf.content)} bytes, audit trail OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
