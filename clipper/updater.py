"""Keeps the installed app up to date from GitHub releases.

- On launch (and every 6 hours while open) it asks GitHub for the latest release.
- One-click update: download the installer, check its SHA-256 against the release's .sha256
  file, run it, and the app reopens on the new version.
- After an update the app offers to turn on automatic updates. With those on, new versions
  download in the background and install when you close the app.

Only runs in the installed app (running from source shows no updates).
"""
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from clipper.config import DATA_ROOT, FROZEN

try:
    from clipper._version import VERSION      # written by the release build
except ImportError:
    VERSION = "dev"

REPO = "callofguns/GTA6-Clipper"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
# Installer names, newest first. Releases also carry the pre-rename name so versions from before
# the rename (v0.4-Beta, v0.4.1-Beta) can still find their update.
ASSETS = ("5tarClipper-Setup.exe", "GTA6-Clipper-Setup.exe")
SETTINGS = DATA_ROOT / "settings.json"
UPDATES_DIR = DATA_ROOT / "updates"
CHECK_EVERY = 6 * 3600

_lock = threading.Lock()
_state = {"state": "idle", "latest": None, "notes_url": None, "progress": None, "text": "",
          "error": None, "installer": None, "checked_at": 0}


# ---------------------------------------------------------------- versions
def parse_version(v: str) -> tuple:
    """'v0.10-Beta' -> ((0, 10, 0), 0). A plain release sorts after its -Beta of the same number."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(?:-(.+))?$", (v or "").strip())
    if not m:
        return ((0,), 0)
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (3 - len(nums))
    return (nums, 0 if m.group(2) else 1)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def enabled() -> bool:
    return FROZEN and VERSION != "dev"


# ---------------------------------------------------------------- settings
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> dict:
    s = load_settings()
    s.update(changes)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


def startup() -> None:
    """Called once when the app opens: notice if we were just updated, then start checking."""
    if not enabled():
        return
    s = load_settings()
    if s.get("updating_from") and s.get("updating_from") != VERSION:
        # Just updated by a one-click update: say so, and offer automatic updates (once).
        save_settings(just_updated={"from": s["updating_from"], "to": VERSION,
                                    "offer_auto": not s.get("auto_update")},
                      updating_from=None)
    elif s.get("updating_from"):
        save_settings(updating_from=None)       # the update didn't happen; forget it
    _cleanup_old_installers()
    threading.Thread(target=_check_loop, daemon=True, name="update-check").start()


def status() -> dict:
    s = load_settings()
    with _lock:
        st = dict(_state)
    st.pop("installer", None)
    return {**st, "current": VERSION, "enabled": enabled(), "auto": bool(s.get("auto_update")),
            "just_updated": s.get("just_updated"),
            "available": bool(st["latest"]) and is_newer(st["latest"], VERSION)}


def set_auto(on: bool) -> None:
    save_settings(auto_update=bool(on), just_updated=None)
    if on and status()["available"]:
        start_download(install_after=False)


def dismiss_prompt() -> None:
    save_settings(just_updated=None)


# ---------------------------------------------------------------- checking
def _set(**kw):
    with _lock:
        _state.update(kw)


def check_now() -> None:
    if not enabled():
        return
    try:
        r = httpx.get(API_LATEST, timeout=20, follow_redirects=True,
                      headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        rel = r.json()
    except (httpx.HTTPError, ValueError) as e:
        _set(checked_at=time.time(), error=f"Couldn't check for updates: {e}")
        return
    latest = rel.get("tag_name", "").lstrip("v")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    _set(checked_at=time.time(), latest=latest, notes_url=rel.get("html_url"),
         assets=assets, error=None)
    if is_newer(latest, VERSION) and load_settings().get("auto_update"):
        with _lock:
            busy = _state["state"] in ("downloading", "ready", "installing")
        if not busy:
            start_download(install_after=False)


def _check_loop():
    while True:
        check_now()
        time.sleep(CHECK_EVERY)


# ---------------------------------------------------------------- download + install
def _safe_asset_url(url: str) -> bool:
    # GitHub repo names ignore capitals (the repo was renamed gta6-clipper -> GTA6-Clipper once).
    return url.lower().startswith(f"https://github.com/{REPO}/releases/download/".lower())


def start_download(install_after: bool) -> None:
    with _lock:
        if _state["state"] in ("downloading", "installing"):
            return
        if _state["state"] == "ready" and _state.get("installer"):
            if install_after:
                threading.Thread(target=_install, args=(Path(_state["installer"]), True),
                                 daemon=True).start()
            return
        _state.update(state="downloading", progress=0.0, text="Starting download...", error=None)
    threading.Thread(target=_download, args=(install_after,), daemon=True).start()


def _download(install_after: bool):
    try:
        with _lock:
            latest, assets = _state["latest"], dict(_state.get("assets") or {})
        name = next((n for n in ASSETS if n in assets and n + ".sha256" in assets), ASSETS[0])
        exe_url, sum_url = assets.get(name), assets.get(name + ".sha256")
        if not exe_url or not sum_url or not (_safe_asset_url(exe_url) and _safe_asset_url(sum_url)):
            raise RuntimeError("The latest release is missing its installer or checksum file.")
        r = httpx.get(sum_url, follow_redirects=True, timeout=30)
        r.raise_for_status()
        m = re.search(r"\b[0-9a-fA-F]{64}\b", r.text)
        if not m:
            raise RuntimeError("Couldn't read the release's checksum.")
        expected = m.group(0).lower()

        UPDATES_DIR.mkdir(parents=True, exist_ok=True)
        target = UPDATES_DIR / f"5tarClipper-Setup-{latest}.exe"
        part = target.with_suffix(".part")
        h = hashlib.sha256()
        with httpx.stream("GET", exe_url, follow_redirects=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with part.open("wb") as f:
                for chunk in resp.iter_bytes(1024 * 1024):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    _set(progress=done / total if total else None,
                         text=f"Downloading v{latest}: {done / 1e6:.0f}"
                              + (f" of {total / 1e6:.0f} MB" if total else " MB"))
        if h.hexdigest() != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError("The download didn't match its checksum, so it wasn't installed. Try again.")
        part.replace(target)
        _set(state="ready", installer=str(target), progress=1.0,
             text=f"v{latest} is ready to install.")
        if install_after:
            _install(target, relaunch=True)
    except Exception as e:
        _set(state="error", error=f"Update failed: {e}", text="")


_quit_app = None      # set by the window app: closes the window so files can be replaced


def set_quit_hook(fn) -> None:
    global _quit_app
    _quit_app = fn


def _install(installer: Path, relaunch: bool) -> None:
    """Run the installer and close the app so it can replace the files."""
    with _lock:
        latest = _state["latest"]
    save_settings(updating_from=VERSION if relaunch else None)
    _set(state="installing", text=f"Installing v{latest}. The app will reopen by itself."
         if relaunch else f"Installing v{latest}.")
    args = [str(installer), "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
    args += ["/SILENT", "/RELAUNCH=1"] if relaunch else ["/VERYSILENT"]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen(args, creationflags=flags, close_fds=True)
    if relaunch and _quit_app:
        time.sleep(1.0)
        _quit_app()


def install_on_exit_if_ready() -> None:
    """Automatic updates: when the app closes, quietly install an update that was downloaded."""
    if not enabled() or not load_settings().get("auto_update"):
        return
    with _lock:
        ready = _state["state"] == "ready" and _state.get("installer")
        installer = _state.get("installer")
    if ready and installer and Path(installer).exists():
        _install(Path(installer), relaunch=False)


def _cleanup_old_installers():
    for p in [*UPDATES_DIR.glob("5tarClipper-Setup-*"), *UPDATES_DIR.glob("GTA6-Clipper-Setup-*")]:
        try:
            p.unlink()
        except OSError:
            pass
