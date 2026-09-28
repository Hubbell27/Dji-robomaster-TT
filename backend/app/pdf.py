"""PDF rendering of a completed intake submission (one PDF per submission)."""

from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from xml.sax.saxutils import escape

from .forms import form_definition, is_visible

_styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_styles["Heading1"], fontSize=16, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=_styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4,
                    textColor=colors.HexColor("#0b4f6c"))
BODY = ParagraphStyle("body", parent=_styles["BodyText"], fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.5, leading=9, textColor=colors.HexColor("#555555"))
LABEL = ParagraphStyle("label", parent=BODY, textColor=colors.HexColor("#444444"))

_GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f6f8")),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
])


def _p(text: Any, style=BODY) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _option_label(field: dict[str, Any], value: str) -> str:
    if field["type"] == "yesno":
        return {"yes": "Yes", "no": "No"}.get(value, value)
    for o in field.get("options", []):
        if o["value"] == value:
            return o["label"]["en"]
    return value


def _fmt_value(field: dict[str, Any], value: Any) -> str:
    if field["type"] in ("select", "yesno"):
        return _option_label(field, value)
    if field["type"] == "checkboxes":
        return ", ".join(_option_label(field, v) for v in value) or "None"
    return str(value)


def _image(data: bytes, max_w: float, max_h: float) -> RLImage:
    img = RLImage(io.BytesIO(data))
    scale = min(max_w / img.imageWidth, max_h / img.imageHeight)
    img.drawWidth, img.drawHeight = img.imageWidth * scale, img.imageHeight * scale
    img.hAlign = "LEFT"
    return img


def render_submission_pdf(
    *,
    intake_id: str,
    location: dict[str, str],
    language: str,
    submitted_at: datetime,
    answers: dict[str, Any],
    consents: dict[str, Any],
    signature_meta: dict[str, Any],
    card_images: dict[str, bytes],
) -> bytes:
    definition = form_definition()
    buf = io.BytesIO()
    patient_name = f"{answers.get('first_name', '')} {answers.get('last_name', '')}".strip()

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(0.6 * inch, 0.45 * inch,
                          "CONFIDENTIAL: Contains protected health information (PHI). Handle per HIPAA policy.")
        canvas.drawRightString(LETTER[0] - 0.6 * inch, 0.45 * inch, f"{patient_name}  |  Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.7 * inch,
                            title=f"Patient Intake - {patient_name}", author=location.get("name", ""),
                            subject="Patient intake submission")
    width = LETTER[0] - 1.2 * inch
    story: list[Any] = []

    story.append(_p(location.get("name", ""), H1))
    loc_line = " · ".join(x for x in (location.get("address"), location.get("phone")) if x)
    if loc_line:
        story.append(_p(loc_line, SMALL))
    story.append(Spacer(1, 6))
    story.append(Table(
        [[_p("Patient", LABEL), _p(patient_name), _p("Date of birth", LABEL), _p(answers.get("dob", ""))],
         [_p("Submitted", LABEL), _p(submitted_at.strftime("%Y-%m-%d %H:%M UTC")),
          _p("Form language", LABEL), _p({"en": "English", "es": "Spanish"}.get(language, language))],
         [_p("Intake ID", LABEL), _p(intake_id, SMALL), _p("Form version", LABEL), _p(definition["version"])]],
        colWidths=[1.0 * inch, 2.4 * inch, 1.1 * inch, width - 4.5 * inch], style=_GRID,
    ))

    for section in definition["sections"]:
        story.append(_p(section["title"]["en"], H2))
        rows = []
        after: list[Any] = []
        for field in section["fields"]:
            if not is_visible(field, answers):
                continue
            value = answers.get(field["key"])
            label = field["label"]["en"]
            if field["type"] == "file":
                if field["key"] in card_images:
                    after.append(KeepTogether([_p(label, LABEL), Spacer(1, 2),
                                               _image(card_images[field["key"]], 3.4 * inch, 2.3 * inch),
                                               Spacer(1, 6)]))
                continue
            if field["type"] == "list":
                if value:
                    header = [_p(sub["label"]["en"], LABEL) for sub in field["item_fields"]]
                    body = [[_p(_fmt_value(sub, item[sub["key"]]) if item.get(sub["key"]) else "")
                             for sub in field["item_fields"]] for item in value]
                    t = Table([header] + body, repeatRows=1, style=TableStyle([
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f6f8")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]))
                    after.append(_p(label, LABEL))
                    after.append(t)
                continue
            if value in (None, "", []):
                display = "None" if field["type"] == "checkboxes" else "(not answered)"
            else:
                display = _fmt_value(field, value)
            rows.append([_p(label, LABEL), _p(display)])
        if rows:
            story.append(Table(rows, colWidths=[2.6 * inch, width - 2.6 * inch], style=_GRID))
        if after:
            story.append(Spacer(1, 6))
            story.extend(after)

    story.append(PageBreak())
    story.append(_p("Consents and Signatures", H1))
    story.append(_p(
        f"Signed electronically on {signature_meta['signed_at']} from IP address {signature_meta.get('ip') or 'unknown'}. "
        f"User agent: {signature_meta.get('user_agent') or 'unknown'}.", SMALL))
    rel_labels = {o["value"]: o["label"]["en"] for o in definition["ui"]["signer_relationship"]["options"]}
    for consent in definition["consents"]:
        c = consents[consent["key"]]
        block: list[Any] = [_p(consent["title"][language], H2)]
        block += [_p(para) for para in consent["body"][language]]
        if language != "en":
            block.append(_p("English translation:", LABEL))
            block += [_p(para, SMALL) for para in consent["body"]["en"]]
        sig_png = base64.b64decode(c["signature"].split(",", 1)[1])
        block.append(Spacer(1, 4))
        block.append(Table(
            [[_p("Agreed", LABEL), _p("Yes" if c["agreed"] else "No")],
             [_p("Signed by (typed)", LABEL), _p(c["typed_name"])],
             [_p("Signing as", LABEL), _p(rel_labels.get(c["relationship"], c["relationship"]))],
             [_p("Signature", LABEL), _image(sig_png, 3.0 * inch, 0.9 * inch)],
             [_p("Consent text SHA-256", LABEL), _p(c["text_sha256"], SMALL)]],
            colWidths=[1.6 * inch, width - 1.6 * inch], style=_GRID,
        ))
        story.append(KeepTogether(block))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
