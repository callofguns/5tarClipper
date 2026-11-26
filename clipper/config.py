"""Loads config.yaml and checks it, with friendly error messages.

Two ways the app runs:
- From the source code (developer): everything lives in the project folder.
- As the installed Windows app: settings, caches and tools live in
  %LOCALAPPDATA%\\5tarClipper, and clips go to Videos\\5tarClipper.

The app used to be called "GTA VI Clipper" and used folders named "GTA6 Clipper". On first launch
after the rename, those folders are moved to the new names so nothing is lost or re-downloaded.
"""
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

APP_NAME = "5tarClipper"
OLD_FOLDER_NAME = "GTA6 Clipper"                # before v0.5: keep moving people's data across
FROZEN = getattr(sys, "frozen", False)          # True inside the installed .exe
CODE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def move_old_folder(old: Path, new: Path, tries: int = 20) -> Path:
    """Rename old -> new if only the old one exists. Returns the folder to use.

    Right after an update, the installer may still be closing for a few seconds and keep a file
    open in the old folder, so retry for ~10 s; if it's still busy, keep using the old folder for
    now and try again next launch."""
    if new.exists() or not old.exists():
        return new
    for _ in range(tries):
        try:
            old.rename(new)
            return new
        except OSError:
            time.sleep(0.5)
    return old


def _data_root() -> Path:
    if not FROZEN:
        return CODE_ROOT
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return move_old_folder(base / OLD_FOLDER_NAME, base / APP_NAME)


DATA_ROOT = _data_root()
CONFIG_PATH = DATA_ROOT / "config.yaml"

# Kept for older imports
PROJECT_ROOT = DATA_ROOT


class ConfigError(Exception):
    pass


def load_config(path: Path = CONFIG_PATH) -> dict:
    if FROZEN and not path.exists():                # first run: start from the default settings
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CODE_ROOT / "config.yaml", path)
    if not path.exists():
        raise ConfigError(f"Can't find {path}.")
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"config.yaml has a formatting mistake:\n{e}") from e

    video = cfg.get("video")
    if not isinstance(video, dict):
        raise ConfigError("config.yaml needs a 'video:' section.")
    if video.get("layout") not in ("center", "facecam"):
        raise ConfigError("video.layout must be 'center' or 'facecam'.")
    check_facecam(video.get("facecam"), "video.facecam in config.yaml")
    if not (5 <= video.get("min_clip_seconds", 20) < video.get("max_clip_seconds", 60) <= 600):
        raise ConfigError("video.min_clip_seconds must be smaller than video.max_clip_seconds.")
    video.setdefault("picker", "auto")
    video.setdefault("local_model", "granite4.2:8b")
    video.setdefault("claude_model", "sonnet")
    if video["picker"] not in ("auto", "claude", "local", "none"):
        raise ConfigError("video.picker must be auto, claude, local or none.")
    if not video.get("output_folder"):
        if FROZEN:
            videos = Path.home() / "Videos"
            video["output_folder"] = str(move_old_folder(videos / OLD_FOLDER_NAME, videos / APP_NAME, tries=1))
        else:
            video["output_folder"] = "output/review"
    return cfg


def check_facecam(value, where: str) -> list[int]:
    """A facecam area is 4 whole numbers: x, y, width, height."""
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(isinstance(v, int) and v >= 0 for v in value)
            or value[2] == 0 or value[3] == 0):
        raise ConfigError(f"{where} must be 4 whole numbers: x, y, width, height (got {value!r}).")
    return list(value)


def project_path(relative: str) -> Path:
    """Turn a folder name from the config into a full path in the app's data folder."""
    p = Path(relative)
    return p if p.is_absolute() else DATA_ROOT / p


def code_path(relative: str) -> Path:
    """A file that ships with the app (web page, default config, icon)."""
    return CODE_ROOT / relative
