"""Chat spike detector, with fake chat (no Twitch needed)."""
from clipper.live.detector import Detector, settings


def quiet_chat(d, start, end, every=2.0):
    """Normal background chat: a message every 2 seconds from a rotating set of 6 people."""
    t, i = start, 0
    while t < end:
        d.add(t, f"regular{i % 6}", "nice drive")
        t += every
        i += 1


def burst(d, t, people, text="KEKW no way", per_person=1):
    for p in range(people):
        for k in range(per_person):
            d.add(t + p * 0.3 + k * 0.05, f"viewer{p}", text)


def test_real_spike_triggers_with_a_reason():
    d = Detector({}, started=0)
    quiet_chat(d, 0, 300)
    burst(d, 292, people=12)
    spike = d.check(300)
    assert spike is not None
    assert spike.chatters >= 12
    assert "busier than normal" in spike.reason and "KEKW" in spike.reason


def test_one_spammer_cannot_trigger():
    d = Detector({}, started=0)
    quiet_chat(d, 0, 300)
    burst(d, 291, people=1, per_person=60, text="CLIP IT CLIP IT KEKW")
    assert d.check(300) is None


def test_four_people_is_not_enough():
    d = Detector({}, started=0)
    quiet_chat(d, 0, 300, every=10)
    burst(d, 292, people=4, per_person=3)
    assert d.check(300) is None


def test_no_spike_during_warmup():
    d = Detector({}, started=0)
    quiet_chat(d, 0, 60)
    burst(d, 52, people=15)
    assert d.check(60) is None           # only 1 minute of history


def test_cooldown_between_spikes():
    d = Detector({}, started=0)
    quiet_chat(d, 0, 400)
    burst(d, 292, people=15)
    assert d.check(300) is not None
    burst(d, 342, people=15)
    assert d.check(350) is None          # 50 s later: still cooling down
    burst(d, 392, people=25)
    assert d.check(400) is not None      # 100 s later: allowed again


def test_steady_busy_chat_is_normal():
    """A chat that's always fast shouldn't trigger: it's only a spike compared with normal."""
    d = Detector({}, started=0)
    for s in range(300):
        for p in range(4):
            d.add(s + p * 0.2, f"fan{(s * 4 + p) % 40}", "KEKW")
    assert all(d.check(t) is None for t in range(130, 300, 5))


def test_reactions_are_whole_words_any_case():
    d = Detector({}, started=0)
    assert d.score_message("lul that was wild")[1] == ("LUL",)
    assert d.score_message("LULW")[1] == ()                  # different emote
    assert d.score_message("clip   it!!")[1] == ("CLIP IT",)
    assert d.score_message("W")[1] == ("W",)
    assert d.score_message("WWW")[1] == ()
    assert d.score_message("now we're talking")[1] == ()
    assert d.score_message("W W W W W")[0] == 1 + 2          # capped at 2 reaction points


def test_settings_fill_in_defaults():
    s = settings({"spike_multiplier": 4, "unknown": 1})
    assert s["spike_multiplier"] == 4 and s["min_unique_chatters"] == 5 and "unknown" not in s
