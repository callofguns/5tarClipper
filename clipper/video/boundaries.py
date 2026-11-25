"""Moves clip start/end so we never cut someone off mid-sentence or in the middle of loud action."""

SENTENCE_GAP = 1.0     # a pause this long (seconds) between words also ends a sentence
ACTION_PAD = 1.0       # keep this much extra around loud action
TAIL_PAD = 0.5         # let the last word / reaction ring out a little
MAX_JOIN_GAP = 5.0     # only pull in a neighbouring sentence if it's this close


def sentences(transcript: dict) -> list[tuple[float, float]]:
    """(start, end) of every sentence, using punctuation, pauses and segment breaks."""
    out = []
    for seg in transcript["segments"]:
        words = seg["words"]
        cur_start = None
        for i, w in enumerate(words):
            if cur_start is None:
                cur_start = w["start"]
            last = i == len(words) - 1
            ends = (w["word"].rstrip('"\')').endswith((".", "!", "?"))
                    or last or words[i + 1]["start"] - w["end"] >= SENTENCE_GAP)
            if ends:
                out.append((cur_start, w["end"]))
                cur_start = None
    return sorted(out)


def all_words(transcript: dict) -> list[dict]:
    return [w for s in transcript["segments"] for w in s["words"]]


def _inside(t: float, spans) -> tuple[float, float] | None:
    for a, b in spans:
        if a < t < b:
            return a, b
    return None


def adjust(start: float, end: float, transcript: dict, loud: list[dict], duration: float,
           min_len: float, max_len: float) -> tuple[float, float, list[str]]:
    """Returns (start, end, notes). Notes explain any compromise that was needed."""
    sents = sentences(transcript)
    words = all_words(transcript)
    action = [(m["start"] - ACTION_PAD, m["end"] + ACTION_PAD) for m in loud]
    notes: list[str] = []

    # 1) Grow outwards until neither edge sits inside a sentence or loud action.
    for _ in range(10):
        before = (start, end)
        for spans in (sents, action):
            hit = _inside(start, spans)
            if hit:
                start = hit[0]
            hit = _inside(end, spans)
            if hit:
                end = hit[1]
        if (start, end) == before:
            break
    start, end = max(0.0, start), min(duration, end)

    # 2) Too long: trim back to the latest sentence end that fits.
    if end - start > max_len:
        fits = [b for a, b in sents if start + min_len <= b <= start + max_len
                and not _inside(b, action)]
        if fits:
            end = max(fits)
        else:
            starts = [a for a, b in sents if end - max_len <= a <= end - min_len]
            if starts:
                start = min(starts)
            else:
                end = start + max_len
                notes.append("had to cut mid-sentence/action to stay under the max length")

    # 3) Too short: add nearby following sentences, then earlier ones, then plain time.
    if end - start < min_len:
        for a, b in sents:
            if a >= end - 0.01:
                if a - end > MAX_JOIN_GAP or b - start > max_len:
                    break
                end = b
                if end - start >= min_len:
                    break
    if end - start < min_len:
        for a, b in reversed(sents):
            if b <= start + 0.01:
                if start - b > MAX_JOIN_GAP or end - a > max_len:
                    break
                start = a
                if end - start >= min_len:
                    break
    if end - start < min_len:
        end = min(duration, start + min_len)
        start = max(0.0, end - min_len)
        hit = _inside(end, sents)
        if hit and hit[1] - start <= max_len:
            end = hit[1]

    # 4) Small tail so the last word isn't clipped, without running into the next word.
    next_starts = [w["start"] for w in words if w["start"] >= end]
    limit = min(next_starts) - 0.05 if next_starts else duration
    end = min(duration, max(end, min(end + TAIL_PAD, limit)))
    if end - start > max_len:
        end = start + max_len

    return round(start, 2), round(end, 2), notes
