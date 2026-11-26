"""No-AI moment picker: scores every stretch of the video and keeps the best ones.

Each second of the video gets points for:
  - loudness well above normal (explosions, crashes, screaming)
  - reaction words and phrases ("no way", "oh my god", "let's go", laughing, ...)
  - exclamations and fast, excited talking
Then every possible clip window is scored and the best non-overlapping ones are kept.
"""
import math
import re

import numpy as np

from clipper.video.audio import STEP_SECONDS

# phrase -> points. Matched as whole words, any capitals. Can be changed in config.yaml (video.reactions).
DEFAULT_REACTIONS = {
    "no way": 3, "oh my god": 3, "oh my gosh": 2, "what the": 2, "let's go": 3, "lets go": 3,
    "holy": 2, "insane": 2, "crazy": 2, "clip that": 3, "clip it": 3, "bro": 1, "dude": 1,
    "yo": 1, "wait": 1, "run": 1, "go go": 2, "help": 1, "we're dead": 2, "i'm dead": 2,
    "haha": 2, "hahaha": 3, "lol": 1, "lmao": 2, "oh no": 2, "nooo": 2, "yes": 1, "wow": 2,
}
# Intro/outro talk rarely makes a good clip: these lower the score.
BORING = {"subscribe": -3, "brand new": -2, "in today's video": -3, "in this video": -2,
          "sponsor": -3, "link in the description": -3, "comment down below": -3, "see you": -2,
          "notification": -2, "like button": -3}


def _per_second_scores(transcript: dict, loud: list[dict], db_curve, duration: float,
                       reactions: dict) -> tuple[np.ndarray, list[list[str]], np.ndarray]:
    n = max(1, int(math.ceil(duration)))
    score = np.zeros(n)
    hits: list[list[str]] = [[] for _ in range(n)]
    loud_s = np.zeros(n)

    # 1) Loudness: how far each second is above the video's normal level.
    if db_curve is not None and len(db_curve):
        db = np.asarray(db_curve, dtype=float)
        median = float(np.median(db))
        per_sec = int(round(1 / STEP_SECONDS))
        for s in range(n):
            chunk = db[s * per_sec:(s + 1) * per_sec]
            if len(chunk):
                loud_s[s] = max(0.0, float(chunk.max()) - median - 3) / 6   # +9 dB above normal = 1 point
    score += loud_s

    # 2) Speech: reaction phrases, exclamations, talking speed.
    patterns = [(p, pts, re.compile(r"\b" + re.escape(p) + r"\b", re.I))
                for p, pts in {**reactions, **BORING}.items()]
    for seg in transcript["segments"]:
        text = seg["text"]
        sec = min(n - 1, int(seg["start"]))
        for phrase, pts, rx in patterns:
            for _ in rx.finditer(text):
                # credit the reaction to the second where it's spoken (spread across the segment)
                s = min(n - 1, int(seg["start"] + (seg["end"] - seg["start"]) / 2))
                score[s] += pts
                if pts > 0:
                    hits[s].append(phrase)
        score[sec] += 0.7 * text.count("!")
        for w in seg["words"]:
            s = min(n - 1, int(w["start"]))
            score[s] += 0.12                  # talking at all is a little interesting
            if re.search(r"(.)\1\1", w["word"]):  # "nooo", "yesss"
                score[s] += 0.8
    return score, hits, loud_s


def _sentences(transcript: dict) -> list[tuple[float, float, str]]:
    """Whole sentences with their text, split only at . ! ? (Whisper's segments often break
    mid-sentence). Only sentences that start with a capital letter count as complete."""
    from clipper.video.boundaries import all_words
    out, cur = [], []
    for w in all_words(transcript):
        cur.append(w)
        if w["word"].rstrip('"\')').endswith((".", "!", "?")):
            text = " ".join(x["word"] for x in cur)
            if text[:1].isupper() and (len(cur) >= 3 or (len(cur) == 2 and text.endswith("!"))):
                out.append((cur[0]["start"], cur[-1]["end"], text))
            cur = []
    return out


def _title_for(start: float, end: float, transcript: dict, hit_phrases: list[str], loud_peak) -> str:
    """Use the most exciting whole sentence in the window as the title, or describe the action."""
    best, best_pts = None, -99.0
    sents = _sentences(transcript)
    inside = [s for s in sents if s[0] >= start and s[1] <= end]
    if not inside:   # very long sentences: settle for one that's mostly in the window
        inside = [s for s in sents if s[0] < end and s[1] > start]
    for i, (a, b, text) in enumerate(inside):
        n_words = len(text.split())
        pts = (sum(text.lower().count(p) for p in hit_phrases) * 3 + text.count("!") * 2
               + text.count("?")
               + (1.0 if 3 <= n_words <= 12 else 0.0)      # short sentences make better titles
               - (5 if re.search(r"\bclip (it|that)\b|\bsubscribe\b", text, re.I) else 0)
               - (0.2 if i == 0 else 0))
        if pts > best_pts:
            best, best_pts = text, pts
    if best:
        words = best.split()
        short = " ".join(words[:10]) + ("..." if len(words) > 10 else "")
        return f"“{short.strip(' ,')}”"
    if loud_peak is not None:
        return f"Loud action at {int(loud_peak // 60)}:{int(loud_peak % 60):02d}"
    return f"Moment at {int(start // 60)}:{int(start % 60):02d}"


def pick(transcript: dict, loud: list[dict], db_curve, duration: float, vcfg: dict) -> list[dict]:
    reactions = vcfg.get("reactions") or DEFAULT_REACTIONS
    score, hits, loud_s = _per_second_scores(transcript, loud, db_curve, duration, reactions)
    n = len(score)
    min_len = int(vcfg["min_clip_seconds"])
    max_len = int(min(vcfg["max_clip_seconds"], n))
    if n <= min_len:
        return [{"start": 0, "end": duration, "title": _title_for(0, duration, transcript, [], None),
                 "reason": "The whole video is shorter than the minimum clip length.", "score": 5}]

    lengths = sorted({min_len, min(max_len, 30), min(max_len, 45), max_len})
    lengths = [L for L in lengths if min_len <= L <= n]
    csum = np.concatenate([[0.0], np.cumsum(score)])
    candidates = []
    for L in lengths:
        sums = csum[L:] - csum[:-L]                 # total points of every window of length L
        density = sums / (L ** 0.85)                # favour dense windows, allow longer ones a bit
        for start in range(0, n - L + 1):
            candidates.append((density[start], start, L))
    candidates.sort(reverse=True)

    chosen = []
    best = candidates[0][0] if candidates else 0
    for dens, start, L in candidates:
        if dens <= 0 or dens < 0.35 * best:
            break
        end = start + L
        if any(start < c[1] + 5 and c[0] - 5 < end for c in chosen):   # keep 5 s apart
            continue
        chosen.append((start, end, dens))
        if len(chosen) == vcfg["max_clips"]:
            break

    total_words = sum(len(seg["words"]) for seg in transcript["segments"])
    speaking = sum(seg["end"] - seg["start"] for seg in transcript["segments"]) or 1
    normal_rate = total_words / speaking            # this video's usual words per second

    moments = []
    for start, end, dens in chosen:
        window_hits = [h for s in range(start, end) for h in hits[s]]
        peak_loud = int(np.argmax(loud_s[start:end])) + start if loud_s[start:end].max() > 0.5 else None
        reasons = []
        if peak_loud is not None:
            peak_db = next((m["db_above_normal"] for m in loud if m["start"] - 1 <= peak_loud <= m["end"] + 1), None)
            reasons.append(f"big sound spike at {peak_loud // 60}:{peak_loud % 60:02d}"
                           + (f" (+{peak_db:.0f} dB)" if peak_db else ""))
        if window_hits:
            top = sorted(set(window_hits), key=window_hits.count, reverse=True)[:3]
            reasons.append("reactions: " + ", ".join(f"‘{t}’" for t in top))
        words = sum(1 for seg in transcript["segments"] for w in seg["words"] if start <= w["start"] < end)
        if words / max(1, end - start) > 1.25 * normal_rate and words >= 20:
            reasons.append("faster talking than usual")
        reason = "; ".join(reasons) or "busiest stretch of the video"
        reason = reason[0].upper() + reason[1:] + "."    # (.capitalize() would turn "dB" into "db")
        moments.append({
            "start": float(start), "end": float(min(end, duration)),
            "title": _title_for(start, end, transcript, window_hits, peak_loud),
            "reason": reason,
            "score": max(1, min(10, round(4 + 5 * dens / best))) if best else 5,
        })
    return moments
