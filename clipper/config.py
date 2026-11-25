"""Loads config.yaml and checks it, with friendly error messages.

Two ways the app runs:
- From the source code (developer): everything lives in the project folder.
- As the installed Windows app: settings, caches and tools live in
  %LOCALAPPDATA%\\GTA6 Clipper, and clips go to your Videos folder.
"""
import os
import shutil
import sys
from pathlib import Path

import yaml

FROZEN = getattr(sys, "frozen", False)          # True inside the installed .exe
CODE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
DATA_ROOT = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GTA6 Clipper") if FROZEN else CODE_ROOT
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
    if not video.get("output_folder"):
        video["output_folder"] = str(Path.home() / "Videos" / "GTA6 Clipper") if FROZEN else "output/review"
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
