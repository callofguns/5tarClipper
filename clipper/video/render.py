"""Cuts a clip with FFmpeg and turns it into a 1080x1920 vertical video with burned-in captions."""
from pathlib import Path

from clipper.tools import find_program, run

W, H = 1080, 1920
CAM_H = 608            # facecam layout: top part (16:9 at full width)
GAME_H = H - CAM_H     # facecam layout: gameplay part below it

_encoder_ok: dict[str, bool] = {}


def layout_filter(layout: str, facecam: list[int] | None) -> tuple[str, int]:
    """FFmpeg filter that builds the vertical frame, and the caption height (y) that suits it."""
    if layout == "center":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},setsar=1[base]"), 1350
    x, y, w, h = facecam
    return (f"[0:v]split=2[a][b];"
            f"[a]crop={w}:{h}:{x}:{y},scale={W}:{CAM_H}:force_original_aspect_ratio=increase:"
            f"flags=lanczos,crop={W}:{CAM_H}[cam];"
            f"[b]scale={W}:{GAME_H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{GAME_H}[game];"
            f"[cam][game]vstack=inputs=2,setsar=1[base]"), CAM_H + 140


def check_facecam_fits(facecam: list[int], width: int, height: int) -> None:
    x, y, w, h = facecam
    if x + w > width or y + h > height:
        raise ValueError(f"Facecam area {facecam} goes outside the {width}x{height} video. "
                         "Fix it in the note or config.yaml (run facecam-preview to check).")


def render_clip(video: Path, out_file: Path, start: float, end: float, layout: str,
                facecam: list[int] | None, ass_file: Path | None, fps: float) -> Path:
    graph, _ = layout_filter(layout, facecam)
    last = "base"
    if fps > 60.5:
        graph += f";[{last}]fps=60[f]"
        last = "f"
    if ass_file is not None:
        # Run FFmpeg inside the captions folder so the filter only needs the plain file name
        # (Windows paths like C:\... confuse FFmpeg's filter syntax).
        graph += f";[{last}]subtitles={ass_file.name}[v]"
        last = "v"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    base = [find_program("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(video.resolve()), "-t", f"{end - start:.3f}",
            "-filter_complex", graph, "-map", f"[{last}]", "-map", "0:a:0?",
            "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    encoders = [("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
                                "-cq", "21", "-b:v", "0"]),
                ("libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "20"])]
    cwd = ass_file.parent if ass_file is not None else None
    errors = []
    for name, opts in encoders:
        if _encoder_ok.get(name) is False:
            continue
        try:
            run(base + opts + [str(out_file.resolve())], f"Rendering with {name}", cwd=cwd)
            _encoder_ok[name] = True
            return out_file
        except RuntimeError as e:
            _encoder_ok[name] = False if name == "h264_nvenc" else None
            errors.append(str(e))
    raise RuntimeError("\n\n".join(errors))
