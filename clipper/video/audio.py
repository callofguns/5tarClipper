"""Pulls the audio out of a video and finds loud moments (explosions, screaming, hype)."""
import wave
from pathlib import Path

import numpy as np

from clipper.tools import find_program, run

SAMPLE_RATE = 16000   # 16 kHz mono: what Whisper wants, and plenty for loudness
STEP_SECONDS = 0.5    # one loudness value every half second


def extract_audio(video: Path, wav_path: Path) -> Path:
    if wav_path.exists() and wav_path.stat().st_mtime >= video.stat().st_mtime:
        return wav_path   # already done
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    run([find_program("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(video), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
         str(wav_path)], "Extracting audio")
    return wav_path


def loudness_curve(wav_path: Path) -> np.ndarray:
    """Loudness in dB for each half-second of audio."""
    with wave.open(str(wav_path), "rb") as w:
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    samples /= 32768.0
    step = int(SAMPLE_RATE * STEP_SECONDS)
    n = len(samples) // step
    if n == 0:
        return np.zeros(0)
    chunks = samples[: n * step].reshape(n, step)
    rms = np.sqrt(np.mean(chunks ** 2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-6))


def find_loud_moments(db: np.ndarray, db_above_median: float, min_seconds: float,
                      merge_gap_seconds: float = 1.0) -> list[dict]:
    """Stretches where the sound is well above the video's typical level."""
    if len(db) == 0:
        return []
    median = float(np.median(db))
    loud = db >= median + db_above_median

    # Find runs of "loud" half-seconds, joining runs separated by short gaps.
    runs: list[list[int]] = []
    for i, is_loud in enumerate(loud):
        if not is_loud:
            continue
        if runs and (i - runs[-1][1]) * STEP_SECONDS <= merge_gap_seconds:
            runs[-1][1] = i
        else:
            runs.append([i, i])

    moments = []
    for a, b in runs:
        length = (b - a + 1) * STEP_SECONDS
        if length < min_seconds:
            continue
        peak = a + int(np.argmax(db[a: b + 1]))
        moments.append({
            "start": round(a * STEP_SECONDS, 1),
            "end": round((b + 1) * STEP_SECONDS, 1),
            "peak": round(peak * STEP_SECONDS + STEP_SECONDS / 2, 1),
            "db_above_normal": round(float(db[peak]) - median, 1),
        })
    return moments
