"""Spots chat "spikes": the moments when a channel's chat suddenly gets much busier than normal.

Pure logic with no network, so it's easy to test with fake chat.

How it scores chat:
- Every message is worth 1 point, plus extra points for reactions like LUL, KEKW or "CLIP IT"
  (whole words, any capitals, capped per message).
- One person can add at most a few points per 10 seconds, so a single spammer can't fake a spike.
- "Normal" is the average 10-second score over the last 5 minutes, never lower than a small floor,
  so a near-silent chat doesn't trigger on three messages.
- A spike = the last 10 seconds score at least 3x normal, with at least 5 different people chatting,
  after 2 minutes of warm-up and at least 90 seconds since the last spike on this channel.
"""
import re
from collections import Counter, deque
from dataclasses import dataclass

DEFAULTS = {
    "window_seconds": 10, "baseline_seconds": 300, "warmup_seconds": 120,
    "spike_multiplier": 3.0, "min_unique_chatters": 5, "min_baseline_score": 2.0,
    "max_points_per_chatter": 3, "cooldown_seconds": 90,
    "reactions": {"LUL": 1, "KEKW": 1, "OMG": 1, "W": 1, "CLIP IT": 1, "no way": 1},
    "max_reaction_points_per_message": 2,
}


def settings(tcfg: dict | None) -> dict:
    """The trigger settings from config.yaml's twitch: section, with defaults for anything missing."""
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (tcfg or {}).items() if k in DEFAULTS and v is not None})
    return out


def reaction_patterns(reactions: dict) -> list[tuple[str, re.Pattern, float]]:
    pats = []
    for phrase, points in reactions.items():
        words = r"\s+".join(re.escape(w) for w in str(phrase).split())
        pats.append((str(phrase), re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE), float(points)))
    return pats


@dataclass
class Spike:
    time: float
    score: float
    baseline: float
    chatters: int
    reactions: dict

    @property
    def ratio(self) -> float:
        return self.score / self.baseline if self.baseline else 0.0

    @property
    def reason(self) -> str:
        top = sorted(self.reactions.items(), key=lambda kv: -kv[1])[:3]
        extra = "".join(f", {name} ×{n}" for name, n in top)
        return f"Chat {self.ratio:.1f}x busier than normal: {self.chatters} people{extra}"


class Detector:
    def __init__(self, cfg: dict, started: float):
        self.cfg = settings(cfg)
        self.patterns = reaction_patterns(self.cfg["reactions"])
        self.started = started
        self.last_spike = float("-inf")
        # (time, chatter, points, reactions found in this message)
        self.messages: deque[tuple[float, str, float, tuple[str, ...]]] = deque()

    def score_message(self, text: str) -> tuple[float, tuple[str, ...]]:
        found, extra = [], 0.0
        for name, pat, points in self.patterns:
            n = len(pat.findall(text or ""))
            if n:
                found.append(name)
                extra += n * points
        return 1.0 + min(extra, self.cfg["max_reaction_points_per_message"]), tuple(found)

    def add(self, t: float, chatter: str, text: str) -> None:
        points, found = self.score_message(text)
        self.messages.append((t, chatter.lower(), points, found))

    def _forget_old(self, now: float) -> None:
        keep = now - self.cfg["baseline_seconds"] - 2 * self.cfg["window_seconds"]
        while self.messages and self.messages[0][0] < keep:
            self.messages.popleft()

    def _capped(self, per_chatter: dict) -> float:
        cap = self.cfg["max_points_per_chatter"]
        return sum(min(cap, p) for p in per_chatter.values())

    def stats(self, now: float) -> dict:
        """The current 10-second score, the normal level and who's chatting."""
        self._forget_old(now)
        w, base = self.cfg["window_seconds"], self.cfg["baseline_seconds"]
        win_start = now - w
        window: dict[str, float] = {}
        reactions: Counter = Counter()
        span = min(base, max(0.0, win_start - self.started))
        n_buckets = int(span // w)
        buckets: list[dict[str, float]] = [{} for _ in range(n_buckets)]
        for t, who, points, found in self.messages:
            if win_start < t <= now:
                window[who] = window.get(who, 0) + points
                reactions.update(found)
            elif t <= win_start and n_buckets:
                i = int((win_start - t) // w)
                if i < n_buckets:
                    buckets[i][who] = buckets[i].get(who, 0) + points
        avg = sum(self._capped(b) for b in buckets) / n_buckets if n_buckets else 0.0
        return {"score": self._capped(window), "baseline": max(avg, self.cfg["min_baseline_score"]),
                "chatters": len(window), "reactions": dict(reactions),
                "warming_up": now - self.started < self.cfg["warmup_seconds"]}

    def check(self, now: float) -> Spike | None:
        s = self.stats(now)
        c = self.cfg
        if (not s["warming_up"]
                and s["score"] >= c["spike_multiplier"] * s["baseline"]
                and s["chatters"] >= c["min_unique_chatters"]
                and now - self.last_spike >= c["cooldown_seconds"]):
            self.last_spike = now
            return Spike(now, s["score"], s["baseline"], s["chatters"], s["reactions"])
        return None
