"""Build-time assets: the app icon (.ico) and the HTML office guide."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from dentalintake.launcher import _icon_image  # noqa: E402

out = HERE / "build-assets"
out.mkdir(exist_ok=True)
img = _icon_image(True).resize((256, 256))
img.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

import markdown  # noqa: E402

md = (HERE.parent / "docs" / "WINDOWS_GUIDE.md").read_text(encoding="utf-8")
body = markdown.markdown(md, extensions=["tables", "fenced_code"])
(out / "OFFICE_GUIDE.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Dental Intake - Office Guide</title>
<style>body{{font-family:Segoe UI,system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#16232d}}
table{{border-collapse:collapse}}td,th{{border:1px solid #d8e0e6;padding:.4rem .6rem;vertical-align:top}}
code{{background:#eef2f5;padding:1px 4px;border-radius:4px}}h1,h2{{color:#08566c}}</style></head>
<body>{body}</body></html>""", encoding="utf-8")
print("assets ok")
