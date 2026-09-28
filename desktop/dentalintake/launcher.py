"""Dental Intake.exe — what staff click.

  Dental Intake.exe          open the dashboard in its own window
  Dental Intake.exe tray     tray icon by the clock (starts with Windows)

The window is Microsoft Edge in "app mode" (no address bar or tabs, its own
taskbar icon), which ships with Windows 10/11, so nothing extra is installed.
It falls back to the default browser if Edge is missing.

This process runs as the signed-in Windows user and never touches patient
data directly. Admin actions (backup, saving the key) launch
DentalIntakeServer.exe with a Windows administrator prompt.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Dental Intake"


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def client_info() -> dict:
    """Non-secret settings the installer writes next to the program (readable by staff)."""
    p = install_dir() / "client.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"role": "server", "url": "https://localhost/staff"}


def find_edge() -> str | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    return next((str(c) for c in candidates if c.exists()), None)


def open_window(url: str | None = None) -> None:
    url = url or client_info()["url"]
    edge = find_edge()
    if edge:
        profile = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Dental Intake" / "window"
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                          "--no-default-browser-check", "--window-size=1360,900"],
                         close_fds=True)
    else:
        webbrowser.open(url)


def run_admin(*args: str) -> None:
    """Run DentalIntakeServer.exe with an administrator (UAC) prompt."""
    exe = install_dir() / "DentalIntakeServer.exe"
    if sys.platform == "win32":
        import ctypes

        params = " ".join(f'"{a}"' for a in args)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), params, str(install_dir()), 1)
    else:
        subprocess.Popen([str(exe), *args])


def health(url: str) -> bool:
    base = url.split("/staff")[0]
    try:
        # The office CA is in the Windows trust store (installer), so normal verification works.
        with urllib.request.urlopen(f"{base}/api/health", timeout=4, context=ssl.create_default_context()) as r:
            return r.status == 200
    except Exception:
        return False


def _icon_image(ok: bool = True):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (11, 110, 138, 255) if ok else (180, 35, 24, 255)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=color)
    # A simple tooth
    d.ellipse((16, 12, 34, 32), fill="white")
    d.ellipse((30, 12, 48, 32), fill="white")
    d.polygon([(17, 24), (47, 24), (43, 50), (37, 52), (32, 38), (27, 52), (21, 50)], fill="white")
    return img


def run_tray() -> None:
    import pystray

    info = client_info()
    url = info["url"]
    is_server = info.get("role") == "server"
    state = {"ok": True}

    def status_text(_item=None):
        return "Status: running" if state["ok"] else "Status: NOT RESPONDING (see Help)"

    def backups_folder(_icon=None, _item=None):
        folder = info.get("backup_dir")
        if folder and sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opens Explorer on the configured folder

    items = [
        pystray.MenuItem("Open Dental Intake", lambda: open_window(url), default=True),
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
    ]
    if is_server:
        items += [
            pystray.MenuItem("Back up now", lambda: run_admin("backup")),
            pystray.MenuItem("Save encryption key to USB…", lambda: run_admin("export-key")),
            pystray.MenuItem("Open backups folder", backups_folder, enabled=bool(info.get("backup_dir"))),
            pystray.MenuItem(f"Office security code: {info.get('security_code', '—')}", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
    items += [
        pystray.MenuItem("Help (office guide)", lambda: webbrowser.open(str(install_dir() / "OFFICE_GUIDE.html"))),
        pystray.MenuItem("Hide this icon", lambda icon: icon.stop()),
    ]
    icon = pystray.Icon("DentalIntake", _icon_image(), APP_NAME, pystray.Menu(*items))

    def watch():
        while True:
            ok = health(url)
            if ok != state["ok"]:
                state["ok"] = ok
                icon.icon = _icon_image(ok)
                icon.title = APP_NAME if ok else f"{APP_NAME}: not responding"
                if not ok:
                    try:
                        icon.notify("Dental Intake is not responding. If this lasts, restart the office PC "
                                    "or open Help.", APP_NAME)
                    except Exception:
                        pass
                icon.update_menu()
            time.sleep(60)

    threading.Thread(target=watch, daemon=True).start()
    icon.run()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "tray":
        run_tray()
    else:
        open_window()
    return 0


if __name__ == "__main__":
    sys.exit(main())
