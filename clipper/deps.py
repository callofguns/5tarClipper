"""The setup checklist: what the app needs, and one-click downloads for the big optional pieces.

Downloads come only from the official sources and are checked against their published
SHA-256 checksums before use:
  FFmpeg   - gyan.dev (the build FFmpeg's own site links to for Windows)
  Deno     - github.com/denoland/deno releases (lets yt-dlp read YouTube pages)
  GPU      - NVIDIA's cuBLAS / cuDNN packages on pypi.org
"""
import hashlib
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

import httpx

from clipper.tools import NO_WINDOW, TOOLS_DIR, find_program

CUDA_DIR = TOOLS_DIR / "cuda"
CUDA_WHEELS = [("nvidia-cublas-cu12", "12.9.2.10"), ("nvidia-cudnn-cu12", "9.28.1.4"),
               ("nvidia-cuda-nvrtc-cu12", "12.9.86")]
CUDA_MARKERS = ["cublas64_12.dll", "cudnn64_9.dll"]

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

_lock = threading.Lock()
_jobs: dict[str, dict] = {}     # name -> {"state": running|done|error, "progress", "text"}


# ---------------------------------------------------------------- checks
def cuda_dll_dirs() -> list[Path]:
    """Folders that may hold NVIDIA's GPU files: our download folder, or pip's nvidia packages."""
    dirs = [CUDA_DIR] if CUDA_DIR.is_dir() else []
    import importlib.util
    spec = importlib.util.find_spec("nvidia")
    if spec and spec.submodule_search_locations:
        for base in spec.submodule_search_locations:
            dirs += [p for p in Path(base).glob("*/bin") if p.is_dir()]
    return dirs


def gpu_files_present() -> bool:
    return all(any((d / m).exists() for d in cuda_dll_dirs()) for m in CUDA_MARKERS)


def nvidia_gpu_present() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


_claude_cache: tuple[float, str | None] = (0.0, None)


def claude_version() -> str | None:
    global _claude_cache
    import time
    if time.time() - _claude_cache[0] < 60:        # checked recently
        return _claude_cache[1]
    path = find_program("claude", required=False)
    version = None
    if path:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                                 stdin=subprocess.DEVNULL, **NO_WINDOW)
            version = out.stdout.strip() or "installed"
        except Exception:
            version = "installed"
    _claude_cache = (time.time(), version)
    return version


def ollama_installed() -> bool:
    import os
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return default.exists() or find_program("ollama", required=False) is not None


def _local_ai_item() -> dict:
    from clipper.config import load_config
    from clipper.video import local_ai
    model = load_config()["video"]["local_model"]
    item = {"id": "localai", "name": f"Local AI model ({model}, free, runs on this PC)", "required": False}
    if not local_ai.server_running():
        if ollama_installed():
            return {**item, "ok": False, "action": "none",
                    "help": "Ollama is installed but not running. Open Ollama from the Start menu, "
                            "and this updates by itself."}
        return {**item, "ok": False, "action": "link", "link": "https://ollama.com/download",
                "link_text": "Install Ollama",
                "help": "Free alternative to Claude. Install Ollama, then come back here to download the model."}
    return {**item, "ok": local_ai.model_ready(model), "action": "download", "size": "about 5 GB"}


def status() -> dict:
    has_gpu = nvidia_gpu_present()
    items = [
        {"id": "ffmpeg", "name": "FFmpeg (cuts and renders video)", "required": True,
         "ok": bool(find_program("ffmpeg", False) and find_program("ffprobe", False)),
         "action": "download", "size": "about 100 MB"},
        {"id": "deno", "name": "JavaScript runtime (needed for YouTube links)", "required": True,
         "ok": bool(find_program("deno", False) or find_program("node", False)),
         "action": "download", "size": "about 45 MB"},
        {"id": "claude", "name": "Claude Code (best moment picks)", "required": False,
         "ok": claude_version() is not None, "action": "link",
         "link": "https://code.claude.com/docs/en/quickstart", "link_text": "How to install",
         "help": "Needs a Claude plan. Install Claude Code, run 'claude' once in a terminal to log in, "
                 "then restart this app. Without it, Local AI or No AI picks the moments."},
        _local_ai_item(),
        {"id": "gpu", "name": "NVIDIA GPU files (much faster transcription)", "required": False,
         "ok": gpu_files_present(), "action": "download" if has_gpu else "none", "size": "about 1.3 GB",
         "help": None if has_gpu else "No NVIDIA graphics card found, so transcription will use the CPU (slower, still works)."},
    ]
    with _lock:
        for it in items:
            it["job"] = dict(_jobs[it["id"]]) if it["id"] in _jobs else None
    return {"items": items, "ready": all(i["ok"] for i in items if i["required"]), "nvidia_gpu": has_gpu}


# ---------------------------------------------------------------- downloads
def start_install(name: str) -> None:
    funcs = {"ffmpeg": _install_ffmpeg, "deno": _install_deno, "gpu": _install_gpu,
             "localai": _install_local_model}
    if name not in funcs:
        raise ValueError("Unknown item")
    with _lock:
        if _jobs.get(name, {}).get("state") == "running":
            return
        _jobs[name] = {"state": "running", "progress": 0.0, "text": "Starting..."}
    threading.Thread(target=_run_install, args=(name, funcs[name]), daemon=True).start()


def _set(name, **kw):
    with _lock:
        _jobs[name].update(kw)


def _run_install(name, func):
    try:
        func(name)
        _set(name, state="done", progress=1.0, text="Installed")
    except Exception as e:
        _set(name, state="error", text=f"{type(e).__name__}: {e}")


def _download(name: str, url: str, dest: Path, sha256: str | None, label: str,
              part: tuple[float, float] = (0.0, 1.0)) -> Path:
    """Stream a file to disk with progress, then check its SHA-256."""
    h = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1024 * 1024):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                frac = done / total if total else 0
                _set(name, progress=part[0] + (part[1] - part[0]) * frac,
                     text=f"{label}: {done / 1e6:.0f}" + (f" of {total / 1e6:.0f} MB" if total else " MB"))
    if sha256 and h.hexdigest().lower() != sha256.lower():
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"{label}: checksum didn't match, the download may be damaged. Try again.")
    return dest


def _fetch_checksum(url: str) -> str:
    """Reads a published SHA-256 file. Formats differ ("<hash>  name", or PowerShell's
    "Hash : <HASH>" table), so just find the 64-character hex value."""
    r = httpx.get(url, follow_redirects=True, timeout=30)
    r.raise_for_status()
    m = re.search(r"\b[0-9a-fA-F]{64}\b", r.text)
    if not m:
        raise RuntimeError(f"Couldn't read the checksum published at {url}")
    return m.group(0)


def _install_ffmpeg(name):
    expected = _fetch_checksum(FFMPEG_URL + ".sha256")
    with tempfile.TemporaryDirectory() as tmp:
        z = _download(name, FFMPEG_URL, Path(tmp) / "ffmpeg.zip", expected, "FFmpeg")
        _set(name, text="Unpacking...")
        with zipfile.ZipFile(z) as zf:
            for exe in ("ffmpeg.exe", "ffprobe.exe"):
                member = next(m for m in zf.namelist() if m.endswith(f"/bin/{exe}"))
                target = TOOLS_DIR / exe[:-4] / exe
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def _install_deno(name):
    expected = _fetch_checksum(DENO_URL + ".sha256sum")
    with tempfile.TemporaryDirectory() as tmp:
        z = _download(name, DENO_URL, Path(tmp) / "deno.zip", expected, "Deno")
        target = TOOLS_DIR / "deno"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            with zf.open("deno.exe") as src, (target / "deno.exe").open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _install_local_model(name):
    from clipper.config import load_config
    from clipper.video import local_ai
    model = load_config()["video"]["local_model"]
    local_ai.pull(model, lambda frac, text: _set(name, progress=frac, text=text))


def _install_gpu(name):
    CUDA_DIR.mkdir(parents=True, exist_ok=True)
    n = len(CUDA_WHEELS)
    for i, (pkg, version) in enumerate(CUDA_WHEELS):
        meta = httpx.get(f"https://pypi.org/pypi/{pkg}/{version}/json", timeout=30).json()
        wheel = next(u for u in meta["urls"] if u["filename"].endswith("win_amd64.whl"))
        with tempfile.TemporaryDirectory() as tmp:
            w = _download(name, wheel["url"], Path(tmp) / wheel["filename"], wheel["digests"]["sha256"],
                          f"GPU files {i + 1}/{n}", part=(i / n, (i + 1) / n))
            _set(name, text=f"Unpacking GPU files {i + 1}/{n}...")
            with zipfile.ZipFile(w) as zf:
                for member in zf.namelist():
                    if "/bin/" in member and member.lower().endswith(".dll"):
                        with zf.open(member) as src, (CUDA_DIR / Path(member).name).open("wb") as dst:
                            shutil.copyfileobj(src, dst)
