import numpy as np

from clipper.video.audio import find_loud_moments


def test_finds_one_loud_stretch_and_ignores_blips():
    db = np.full(120, -40.0)          # 60 seconds of quiet
    db[40:50] = -20.0                 # 5 s loud (20 dB above normal) at 20.0-25.0 s
    db[80] = -15.0                    # a 0.5 s blip: too short
    moments = find_loud_moments(db, db_above_median=10, min_seconds=1.0)
    assert len(moments) == 1
    m = moments[0]
    assert (m["start"], m["end"]) == (20.0, 25.0)
    assert m["db_above_normal"] == 20.0


def test_merges_close_stretches():
    db = np.full(100, -40.0)
    db[20:24] = -20.0                 # 10.0-12.0 s
    db[25:29] = -20.0                 # 12.5-14.5 s (gap of 0.5 s -> merged)
    moments = find_loud_moments(db, 10, 1.0)
    assert [(m["start"], m["end"]) for m in moments] == [(10.0, 14.5)]
