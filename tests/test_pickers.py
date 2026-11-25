import numpy as np

from clipper.video import heuristic, rank

VCFG = {"min_clip_seconds": 20, "max_clip_seconds": 60, "max_clips": 3,
        "local_model": "granite4.2:8b", "claude_model": "sonnet"}


def seg(start, text, step=0.4):
    words = [{"start": start + i * step, "end": start + i * step + 0.3, "word": w}
             for i, w in enumerate(text.split())]
    return {"start": words[0]["start"], "end": words[-1]["end"], "text": text, "words": words}


def test_no_ai_finds_the_loud_reaction_moment():
    duration = 300.0
    db = np.full(int(duration / 0.5), -40.0)
    db[400:420] = -15.0                                  # big explosion at 200-210 s
    transcript = {"segments": [
        seg(30, "okay so today we are just driving around the city"),
        seg(120, "let me check the map real quick"),
        seg(203, "NO WAY! Oh my god, did you see that? Clip that!"),
        seg(260, "alright next mission"),
    ]}
    loud = [{"start": 200.0, "end": 210.0, "peak": 205.0, "db_above_normal": 25.0}]
    moments = heuristic.pick(transcript, loud, db, duration, VCFG)
    assert moments, "should find at least one moment"
    best = moments[0]
    assert best["start"] <= 203 and best["end"] >= 207     # covers the explosion + reaction
    # title taken from a reaction that was said (but never "Clip that!")
    assert best["title"] in ("“NO WAY!”", "“Oh my god, did you see that?”")
    assert "sound spike" in best["reason"] and "no way" in best["reason"]
    assert 20 <= best["end"] - best["start"] <= 60
    # results never overlap
    spans = sorted((m["start"], m["end"]) for m in moments)
    assert all(a_end <= b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))


def test_no_ai_works_without_speech():
    duration = 120.0
    db = np.full(240, -40.0)
    db[100:110] = -10.0
    moments = heuristic.pick({"segments": []}, [], db, duration, VCFG)
    assert moments and moments[0]["title"].startswith("Loud action")


def test_no_ai_short_video():
    moments = heuristic.pick({"segments": []}, [], np.full(20, -40.0), 10.0, VCFG)
    assert moments[0]["start"] == 0 and moments[0]["end"] == 10.0


def test_clean_moments_handles_sloppy_ai_answers():
    raw = [
        {"start": "10", "end": 40, "title": "Good", "reason": "ok", "score": 8.6},   # strings/floats
        {"start": 15, "end": 45, "title": "Overlaps the first", "reason": "", "score": 5},
        {"end": 90, "title": "Missing start", "score": 9},
        {"start": 100, "end": 102, "title": "Too short", "reason": "", "score": 7},
        "not even an object",
        {"start": 200, "end": 250, "score": 3},                                      # no title
    ]
    out = rank.clean_moments(raw, duration=300, max_clips=5)
    assert [m["title"] for m in out] == ["Good", "Untitled moment"]
    assert out[0]["score"] == 9 and out[0]["start"] == 10.0


def test_auto_falls_back_to_no_ai(monkeypatch):
    from clipper.video import local_ai
    monkeypatch.setattr(rank, "claude_available", lambda: False)
    monkeypatch.setattr(local_ai, "model_ready", lambda m: False)
    assert rank.resolve_picker("auto", VCFG) == "none"
    monkeypatch.setattr(local_ai, "model_ready", lambda m: True)
    assert rank.resolve_picker("auto", VCFG) == "local"
    monkeypatch.setattr(rank, "claude_available", lambda: True)
    assert rank.resolve_picker("auto", VCFG) == "claude"
    assert rank.resolve_picker("none", VCFG) == "none"
