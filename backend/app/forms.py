"""Form definition loading, draft sanitizing and submission validation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

_DEF_PATH = Path(__file__).parent / "content" / "intake_form.json"
_DEFAULT_MAX = 500
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TEL_RE = re.compile(r"^[0-9+()\-.\s]{7,25}$")
_SIGNATURE_PREFIX = "data:image/png;base64,"
_MAX_SIGNATURE_BYTES = 200_000
LANGUAGES = ("en", "es")


@lru_cache
def form_definition() -> dict[str, Any]:
    return json.loads(_DEF_PATH.read_text(encoding="utf-8"))


def all_fields() -> list[dict[str, Any]]:
    return [f for s in form_definition()["sections"] for f in s["fields"]]


def file_field_keys() -> set[str]:
    return {f["key"] for f in all_fields() if f["type"] == "file"}


def consent_text_hash(consent_key: str, lang: str) -> str:
    """Hash of the exact consent wording shown, recorded with each signature."""
    c = next(c for c in form_definition()["consents"] if c["key"] == consent_key)
    payload = json.dumps({"title": c["title"][lang], "body": c["body"][lang]}, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def is_visible(field: dict[str, Any], answers: dict[str, Any]) -> bool:
    cond = field.get("show_if")
    if not cond:
        return True
    parent = next((f for f in all_fields() if f["key"] == cond["field"]), None)
    if parent is not None and not is_visible(parent, answers):
        return False
    return answers.get(cond["field"]) == cond["equals"]


def _clip(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        return None
    return str(value)[:max_len]


def _sanitize_value(field: dict[str, Any], value: Any) -> Any:
    t = field["type"]
    if t == "checkboxes":
        if not isinstance(value, list):
            return []
        allowed = {o["value"] for o in field["options"]}
        return [v for v in value if isinstance(v, str) and v in allowed]
    if t == "list":
        if not isinstance(value, list):
            return []
        items = []
        for item in value[: field.get("max_items", 50)]:
            if isinstance(item, dict):
                items.append(
                    {
                        sub["key"]: _clip(item.get(sub["key"]), sub.get("max", _DEFAULT_MAX))
                        for sub in field["item_fields"]
                        if item.get(sub["key"]) not in (None, "")
                    }
                )
        return items
    return _clip(value, field.get("max", _DEFAULT_MAX))


def sanitize_answers(raw: Any) -> dict[str, Any]:
    """Keep only known keys with well-formed values. Used for drafts."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for field in all_fields():
        if field["key"] in raw:
            v = _sanitize_value(field, raw[field["key"]])
            if v not in (None, "", []):
                out[field["key"]] = v
    return out


def _check_scalar(field: dict[str, Any], value: str, label: str) -> str | None:
    t = field["type"]
    if t in ("select", "yesno"):
        allowed = {"yes", "no"} if t == "yesno" else {o["value"] for o in field["options"]}
        if value not in allowed:
            return f"{label}: invalid choice"
    elif t == "date":
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return f"{label}: invalid date"
        if d > date.today() or d.year < 1900:
            return f"{label}: date out of range"
    elif t == "email":
        if not _EMAIL_RE.match(value):
            return f"{label}: invalid email"
    elif t == "tel":
        if not _TEL_RE.match(value) or sum(c.isdigit() for c in value) < 7:
            return f"{label}: invalid phone number"
    if field.get("pattern") and not re.match(field["pattern"], value):
        return f"{label}: invalid format"
    return None


def validate_answers(answers: dict[str, Any], uploaded_file_ids: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate a full submission.

    ``uploaded_file_ids`` maps file field key -> IntakeFile id actually stored
    for this intake. Returns (clean answers with hidden fields removed, errors).
    """
    answers = sanitize_answers(answers)
    clean: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for field in all_fields():
        key = field["key"]
        label = field["label"]["en"]
        if not is_visible(field, answers):
            continue
        value = answers.get(key)
        if field["type"] == "file":
            value = uploaded_file_ids.get(key)
        if value in (None, "", []):
            if field.get("required"):
                errors.append({"field": key, "message": f"{label} is required"})
            continue
        if field["type"] == "list":
            for i, item in enumerate(value):
                for sub in field["item_fields"]:
                    sv = item.get(sub["key"])
                    if sv in (None, ""):
                        if sub.get("required"):
                            errors.append({"field": f"{key}.{i}.{sub['key']}", "message": f"{label} #{i + 1}: {sub['label']['en']} is required"})
                    elif (err := _check_scalar(sub, sv, sub["label"]["en"])):
                        errors.append({"field": f"{key}.{i}.{sub['key']}", "message": err})
        elif field["type"] not in ("checkboxes", "file"):
            if (err := _check_scalar(field, value, label)):
                errors.append({"field": key, "message": err})
        clean[key] = value
    return clean, errors


def _valid_signature_png(data_url: Any) -> bool:
    if not isinstance(data_url, str) or not data_url.startswith(_SIGNATURE_PREFIX):
        return False
    try:
        raw = base64.b64decode(data_url[len(_SIGNATURE_PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(raw) > _MAX_SIGNATURE_BYTES:
        return False
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format != "PNG":
                return False
            img.verify()
            w, h = img.size
    except Exception:
        return False
    return 50 <= w <= 3000 and 20 <= h <= 1500


def validate_consents(raw: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    clean: dict[str, Any] = {}
    raw = raw if isinstance(raw, dict) else {}
    relationships = {o["value"] for o in form_definition()["ui"]["signer_relationship"]["options"]}
    for consent in form_definition()["consents"]:
        key = consent["key"]
        title = consent["title"]["en"]
        c = raw.get(key) if isinstance(raw.get(key), dict) else {}
        if c.get("agreed") is not True:
            errors.append({"field": f"consent.{key}", "message": f"{title}: you must agree"})
        typed = c.get("typed_name")
        if not isinstance(typed, str) or not (2 <= len(typed.strip()) <= 200):
            errors.append({"field": f"consent.{key}.typed_name", "message": f"{title}: type your full name"})
        if c.get("relationship") not in relationships:
            errors.append({"field": f"consent.{key}.relationship", "message": f"{title}: select who is signing"})
        if not _valid_signature_png(c.get("signature")):
            errors.append({"field": f"consent.{key}.signature", "message": f"{title}: signature is required"})
        clean[key] = {
            "agreed": c.get("agreed") is True,
            "typed_name": typed.strip() if isinstance(typed, str) else "",
            "relationship": c.get("relationship"),
            "signature": c.get("signature"),
        }
    return clean, errors


# Answers that staff should see at a glance. level "high" = clinically urgent.
_ALERT_CONDITIONS = {
    "artificial_valve": ("Artificial heart valve", "high"),
    "pacemaker": ("Pacemaker / defibrillator", "high"),
    "bleeding_disorder": ("Bleeding disorder", "high"),
    "heart_disease": ("Heart disease", "high"),
    "rheumatic_fever": ("Rheumatic fever history", "info"),
    "joint_replacement": ("Joint replacement", "info"),
    "radiation": ("Head/neck radiation", "high"),
    "diabetes": ("Diabetes", "info"),
    "seizures": ("Seizures", "high"),
    "stroke": ("Stroke history", "info"),
    "high_bp": ("High blood pressure", "info"),
    "asthma": ("Asthma", "info"),
}


def compute_alerts(answers: dict[str, Any]) -> list[dict[str, str]]:
    """Derive medical alerts from submitted answers for the staff dashboard."""
    alerts: list[dict[str, str]] = []
    by_key = {f["key"]: f for f in all_fields()}

    def add(code: str, label: str, level: str = "high") -> None:
        if not any(a["code"] == code for a in alerts):
            alerts.append({"code": code, "label": label, "level": level})

    if answers.get("has_allergies") == "yes":
        opts = {o["value"]: o["label"]["en"] for o in by_key["common_allergies"]["options"]}
        for v in answers.get("common_allergies") or []:
            add(f"allergy:{v}", f"Allergy: {opts.get(v, v)}")
        for item in answers.get("allergy_list") or []:
            name = (item.get("allergen") or "").strip()
            if not name:
                continue
            severe = item.get("severity") == "severe"
            # Merge with a matching checkbox allergy instead of listing it twice.
            match = next((a for a in alerts if a["code"].startswith("allergy:")
                          and (name.lower() in a["label"].lower() or a["label"].lower().split(": ", 1)[1].split(" /")[0] in name.lower())), None)
            if match:
                if severe:
                    match["label"] = f"SEVERE {match['label']}"
                continue
            add(f"allergy_item:{name.lower()}", f"{'SEVERE ' if severe else ''}Allergy: {name}")
    if answers.get("blood_thinners") == "yes":
        add("blood_thinners", "Takes blood thinners")
    if answers.get("premed_antibiotics") == "yes":
        add("premed", "Antibiotic premedication required")
    if answers.get("bisphosphonates") == "yes":
        add("bisphosphonates", "Bisphosphonate history (MRONJ risk)")
    if answers.get("pregnant") in ("pregnant", "nursing"):
        add("pregnant", "Pregnant" if answers["pregnant"] == "pregnant" else "Nursing", "info")
    for v in answers.get("conditions") or []:
        if v in _ALERT_CONDITIONS:
            label, level = _ALERT_CONDITIONS[v]
            add(f"condition:{v}", label, level)
    if answers.get("tobacco") == "current":
        add("tobacco", "Current tobacco use", "info")
    alerts.sort(key=lambda a: a["level"] != "high")
    return alerts
