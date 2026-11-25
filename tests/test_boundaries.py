from clipper.video.boundaries import adjust, sentences


def seg(*words):
    """words: (start, end, text) tuples -> one transcript segment."""
    ws = [{"start": s, "end": e, "word": t} for s, e, t in words]
    return {"start": ws[0]["start"], "end": ws[-1]["end"], "text": " ".join(w["word"] for w in ws),
            "words": ws}


TRANSCRIPT = {"segments": [
    seg((10.0, 10.5, "Okay"), (10.6, 11.0, "watch"), (11.1, 12.0, "this.")),
    seg((20.0, 20.5, "No"), (20.6, 21.0, "way"), (21.2, 22.0, "dude!"),
        (22.2, 23.0, "That"), (23.1, 24.0, "was"), (24.1, 25.0, "insane.")),
    seg((50.0, 51.0, "Next"), (51.1, 52.0, "mission.")),
]}


def test_sentences_split_on_punctuation():
    assert sentences(TRANSCRIPT) == [(10.0, 12.0), (20.0, 22.0), (22.2, 25.0), (50.0, 52.0)]


def test_start_and_end_snap_out_of_sentences():
    start, end, notes = adjust(10.7, 23.5, TRANSCRIPT, [], 300, 5, 60)
    assert start == 10.0                 # moved back to "Okay"
    assert 25.0 <= end <= 25.5           # moved forward past "insane." plus a small tail
    assert notes == []


def test_loud_action_is_not_cut():
    loud = [{"start": 30.0, "end": 34.0, "peak": 32.0, "db_above_normal": 15}]
    start, end, _ = adjust(10.0, 32.0, TRANSCRIPT, loud, 300, 5, 60)
    assert end >= 35.0                   # action ends 34.0, plus 1 s padding


def test_too_long_trims_at_a_sentence_end():
    start, end, _ = adjust(10.0, 52.0, TRANSCRIPT, [], 300, 5, 20)
    assert start == 10.0
    assert end <= 30.0 and end >= 25.0   # ends right after "insane."


def test_too_short_extends_to_min_length():
    start, end, _ = adjust(10.0, 12.0, TRANSCRIPT, [], 300, 20, 60)
    assert end - start >= 20
    assert end < 50                      # doesn't jump to "Next mission." 25 s later


def test_too_short_pulls_in_close_sentences():
    start, end, _ = adjust(20.0, 22.0, TRANSCRIPT, [], 300, 4, 60)
    assert start == 20.0 and 25.0 <= end <= 25.5   # adds "That was insane."


def test_never_goes_outside_the_video():
    start, end, _ = adjust(0.0, 9.0, TRANSCRIPT, [], 30, 20, 60)
    assert start >= 0 and end <= 30
