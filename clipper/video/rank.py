"""Asks Claude (through Claude Code's headless mode, `claude -p`) to pick the best moments.

This uses your Claude plan's normal usage limits, the same ones as claude.ai and Claude Code.
One call per video. It never uses an API key: if ANTHROPIC_API_KEY is set we stop,
because Claude Code would then bill the API instead of your plan.
"""
import json
import os
import subprocess
from pathlib import Path

from clipper.errors import TryLater, UserFixable
from clipper.tools import NO_WINDOW, find_program

SYSTEM_PROMPT = """You pick the best short-form clips from a GTA VI gameplay video for vertical \
video (Shorts/TikTok/Reels). You get a timestamped transcript of the creator's speech and a list \
of loud moments in the audio (explosions, crashes, gunfire, screaming, hype). Loud moments \
matter: big action may have little or no speech.

Good clips: a clear setup and payoff, funny or shocking reactions, chaotic action, clutch \
escapes, fails, strong opinions. Each clip must make sense on its own, start at the beginning of \
a sentence or just before the action builds, and end after the payoff/reaction finishes. Never \
end mid-sentence. Clips must not overlap.

All times are in seconds from the start of the video. Titles: short, punchy, no hashtags, no \
clickbait lies. Reason: one sentence on why it works. Score: 1-10."""

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["start", "end", "title", "reason", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}


def build_prompt(transcript: dict, loud: list[dict], duration: float, vcfg: dict, creator: str) -> str:
    lines = [f"Video length: {duration:.1f} s. Creator: {creator}.",
             f"Pick up to {vcfg['max_clips']} clips, each {vcfg['min_clip_seconds']}-"
             f"{vcfg['max_clip_seconds']} seconds long, best first.",
             "", "TRANSCRIPT:"]
    for s in transcript["segments"]:
        lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
    if not transcript["segments"]:
        lines.append("(no speech detected)")
    lines += ["", "LOUD MOMENTS (start-end, peak, dB above normal):"]
    for m in loud:
        lines.append(f"[{m['start']:.1f}-{m['end']:.1f}] peak {m['peak']:.1f}, +{m['db_above_normal']:.0f} dB")
    if not loud:
        lines.append("(none)")
    return "\n".join(lines)


def ask_claude(prompt: str, model: str) -> dict:
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise UserFixable("ANTHROPIC_API_KEY is set, so Claude Code would bill the API instead of your "
                          "Claude plan. Remove that environment variable, restart the app and retry.")
    claude = find_program("claude", required=False)
    if not claude:
        raise UserFixable("Can't find the 'claude' command. Is Claude Code installed and logged in?")
    cmd = [claude, "-p",
           "--output-format", "json",
           "--json-schema", json.dumps(SCHEMA),
           "--tools", "",                       # no file/shell tools: it only reads our text
           "--system-prompt", SYSTEM_PROMPT,
           "--no-session-persistence",
           "--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600, **NO_WINDOW)
    except subprocess.TimeoutExpired as e:
        raise TryLater("Claude took longer than 10 minutes.") from e
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        msg = (proc.stderr or proc.stdout).strip()[-500:]
        raise TryLater(f"Claude Code didn't return a result (maybe a usage limit or login issue): {msg}")
    if data.get("is_error") or "structured_output" not in data:
        raise TryLater(f"Claude Code reported a problem: {str(data.get('result'))[:500]}")
    return data


def clean_moments(raw: list[dict], duration: float, max_clips: int) -> list[dict]:
    """Drop impossible answers, remove overlaps (keeping the higher score), best first."""
    ok = []
    for m in sorted(raw, key=lambda m: -m["score"]):
        start, end = max(0.0, float(m["start"])), min(duration, float(m["end"]))
        if end - start < 5:
            continue
        if any(start < o["end"] and o["start"] < end for o in ok):
            continue
        ok.append({**m, "start": round(start, 2), "end": round(end, 2)})
        if len(ok) == max_clips:
            break
    return ok


def rank_moments(transcript: dict, loud: list[dict], duration: float, vcfg: dict, creator: str,
                 out_path: Path, src_mtime: float, log=print) -> list[dict]:
    if out_path.exists() and out_path.stat().st_mtime >= src_mtime:
        return json.loads(out_path.read_text(encoding="utf-8"))["moments"]

    prompt = build_prompt(transcript, loud, duration, vcfg, creator)
    log(f"Asking Claude ({vcfg['claude_model']}) to pick moments: prompt is "
        f"{len(prompt):,} characters (~{len(prompt) // 4:,} tokens).")
    data = ask_claude(prompt, vcfg["claude_model"])
    usage = data.get("usage") or {}
    tokens_in = sum(usage.get(k, 0) for k in
                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    log(f"Claude answered in {data.get('duration_ms', 0) / 1000:.0f}s (~{tokens_in:,} input + "
        f"{usage.get('output_tokens', 0):,} output tokens; counts toward your plan's usage limits).")
    moments = clean_moments(data["structured_output"]["moments"], duration, vcfg["max_clips"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"prompt": prompt, "moments": moments}, indent=1,
                                   ensure_ascii=False), encoding="utf-8")
    return moments
