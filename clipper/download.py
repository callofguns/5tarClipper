"""Downloads a YouTube or Twitch VOD link with yt-dlp."""
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from clipper.errors import UserFixable
from clipper.tools import find_program

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com",
    "twitch.tv", "www.twitch.tv", "m.twitch.tv",
}


def check_link(url: str) -> str:
    """Only YouTube and Twitch VOD links are accepted."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        raise UserFixable("Only YouTube and Twitch VOD links are supported "
                          "(youtube.com, youtu.be or twitch.tv/videos/...).")
    host = parsed.hostname.lower()
    if "twitch.tv" in host and not parsed.path.startswith("/videos/"):
        raise UserFixable("For Twitch, paste a VOD link like https://www.twitch.tv/videos/123456789 "
                          "(live streams are handled by the Twitch live mode, not here).")
    return url


def _js_runtimes() -> dict:
    runtimes = {}
    for name in ("deno", "node"):
        path = find_program(name, required=False)
        if path:
            runtimes[name] = {"path": path}
    if not runtimes:
        raise UserFixable("YouTube links need a JavaScript runtime. Install it from the setup checklist.")
    return runtimes


def download(url: str, folder: Path, on_progress: Callable[[float, str], None]) -> dict:
    """Downloads into folder/source.mp4. Returns {title, uploader, duration, webpage_url}."""
    import yt_dlp

    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "source.mp4"

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = d.get("downloaded_bytes", 0) / total if total else 0
            speed = d.get("speed") or 0
            on_progress(min(frac, 1.0), f"{d.get('downloaded_bytes', 0) / 1e6:.0f} MB"
                        + (f" of {total / 1e6:.0f} MB" if total else "")
                        + (f" at {speed / 1e6:.1f} MB/s" if speed else ""))
        elif d["status"] == "finished":
            on_progress(1.0, "joining video and audio...")

    opts = {
        "outtmpl": str(folder / "source.%(ext)s"),
        # Best quality up to 1080p (our clips are 1080 wide anyway), saved as .mp4
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": str(Path(find_program("ffmpeg")).parent),
        "js_runtimes": _js_runtimes(),             # YouTube needs a JavaScript runtime now
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).replace("ERROR: ", "")
        if "Sign in" in msg or "bot" in msg:
            msg += " (YouTube wants a signed-in browser for this video.)"
        if "subscriber" in msg.lower():
            msg += " (Subscriber-only VODs can't be downloaded.)"
        raise UserFixable(f"Download failed: {msg}") from e

    if not target.exists():   # yt-dlp may have kept another extension
        found = sorted(folder.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
        found = [p for p in found if p.suffix not in (".part", ".ytdl", ".json")]
        if not found:
            raise UserFixable("Download finished but no video file was saved.")
        found[0].rename(target)
    return {"title": info.get("title") or "", "uploader": info.get("uploader") or "",
            "duration": info.get("duration"), "webpage_url": info.get("webpage_url") or url}
