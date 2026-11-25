"""Finds and runs outside programs (FFmpeg, Deno, Claude Code)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from clipper.config import DATA_ROOT

TOOLS_DIR = DATA_ROOT / "tools"     # FFmpeg / Deno / GPU files downloaded by the setup checklist
# Hide the black console window that would flash up for every FFmpeg/Claude run in the .exe
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def find_program(name: str, required: bool = True) -> str | None:
    local = TOOLS_DIR / name / f"{name}.exe"
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    candidates = []
    if name in ("ffmpeg", "ffprobe"):
        # winget installs FFmpeg here; a terminal opened before the install won't have it on PATH yet.
        packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        candidates += list(packages.glob(f"Gyan.FFmpeg*/*/bin/{name}.exe"))
    if name == "claude":
        candidates += [Path.home() / ".local" / "bin" / "claude.exe"]
    for c in candidates:
        if c.exists():
            return str(c)
    if required:
        raise FileNotFoundError(f"Can't find {name}. Open the app's setup checklist to install it.")
    return None


def run(cmd: list[str], what: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a command; if it fails, raise an error with the last lines of its output."""
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=cwd, stdin=subprocess.DEVNULL, **NO_WINDOW)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"{what} failed (exit code {result.returncode}):\n{tail}")
    return result


def video_info(video: Path) -> dict:
    """Width, height, frame rate and length of a video, using ffprobe."""
    out = run([find_program("ffprobe"), "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate:format=duration",
               "-of", "json", str(video)], "Reading video info").stdout
    data = json.loads(out)
    if not data.get("streams"):
        raise RuntimeError("That file has no video in it.")
    stream = data["streams"][0]
    num, _, den = stream["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "fps": fps, "duration": float(data["format"]["duration"])}
