from clipper.video.captions import caption_chunks, write_ass


def words(*items):
    return [{"start": s, "end": e, "word": t} for s, e, t in items]


W = words((10.0, 10.3, "we"), (10.4, 10.7, "are"), (10.8, 11.0, "so"), (11.1, 11.5, "dead."),
          (13.0, 13.4, "Run!"))


def test_groups_and_offsets_to_clip_start():
    chunks = caption_chunks(W, clip_start=10.0, clip_end=20.0, words_per_line=3)
    assert [c[2] for c in chunks] == ["WE ARE SO", "DEAD.", "RUN!"]
    assert chunks[0][0] == 0.0                       # relative to the clip start
    assert chunks[1][1] <= 3.0                       # "DEAD." doesn't linger into the pause


def test_only_words_inside_the_clip():
    chunks = caption_chunks(W, clip_start=12.0, clip_end=20.0, words_per_line=3)
    assert [c[2] for c in chunks] == ["RUN!"]


def test_ass_file(tmp_path):
    p = write_ass(tmp_path / "c.ass", [(0.0, 1.5, "HELLO {THERE}")], "Arial", 84, 1400)
    text = p.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Cap,,0,0,0,,{\\pos(540,1400)}HELLO (THERE)" in text
