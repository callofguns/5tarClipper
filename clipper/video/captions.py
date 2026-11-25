"""Turns word timestamps into an .ass subtitle file (big bold captions, a few words at a time)."""
from pathlib import Path


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def caption_chunks(words: list[dict], clip_start: float, clip_end: float,
                   words_per_line: int) -> list[tuple[float, float, str]]:
    """Groups the clip's words into short captions. Times are relative to the clip start."""
    inside = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    chunks = []
    group: list[dict] = []
    for i, w in enumerate(inside):
        group.append(w)
        nxt = inside[i + 1] if i + 1 < len(inside) else None
        sentence_end = w["word"].rstrip('"\')').endswith((".", "!", "?", ","))
        pause = nxt is not None and nxt["start"] - w["end"] > 0.6
        if len(group) >= words_per_line or sentence_end or pause or nxt is None:
            start = max(group[0]["start"], clip_start) - clip_start
            end = min(group[-1]["end"], clip_end) - clip_start
            if nxt is not None:   # stay on screen until the next caption, unless there's a pause
                end = max(end, min(nxt["start"] - clip_start, end + 0.6))
            text = " ".join(g["word"] for g in group).upper()
            chunks.append((round(start, 2), round(max(end, start + 0.2), 2), text))
            group = []
    return chunks


def write_ass(path: Path, chunks, font: str, size: int, center_y: int) -> Path:
    """Captions centered horizontally at height center_y on a 1080x1920 frame."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in chunks:
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,"
                     f"{{\\pos(540,{center_y})}}{_escape(text)}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return path
