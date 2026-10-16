import pytest

from agentenv_airops.flow import bars


def day(naive=100.0, h=60.0, lb=50.0, faa=70.0, split=65.0, reactive=90.0, taf=80.0) -> dict:
    return {"naive": naive, "hindsight": h, "lb": lb, "faa": faa, "split": split, "reactive": reactive, "taf": taf}


def runs(naive=100.0, h=60.0, off=90.0, a=89.0, b=88.0, ab=86.0, **flags) -> dict:
    out = {k: {"joint": v} for k, v in (("naive", naive), ("off", off), ("a", a), ("b", b), ("ab", ab))}
    out["hindsight"] = {"joint": h, "proven": not flags.get("unproven")}
    for k in flags.get("timeouts", ()):
        out[k] = {"timeout": True}
    return out


def test_c1_reads_the_faa_over_hindsight_and_flags_a_flip_under_lb():
    got = bars.c1(day(faa=70.0))
    assert got["over"] == pytest.approx(1 / 6) and got["pass"] and not got["flip"]
    got = bars.c1(day(faa=68.0, lb=58.0))
    assert not got["pass"] and got["flip"] and got["split_over"] == pytest.approx(5 / 60)


def test_c2_compares_avoidable_shares_and_a_day_without_room_fails():
    got = bars.c2(day())
    assert got["taf"] == pytest.approx(0.5) and got["reactive"] == pytest.approx(0.25) and got["gain"] == 25.0
    assert got["pass"]
    assert not bars.c2(day(taf=89.0))["pass"]
    flat = bars.c2(day(naive=60.0, taf=60.0, reactive=60.0))
    assert flat["gain"] is None and not flat["pass"]


def test_c3_needs_both_channels_five_points_and_no_fatal_time_out():
    got = bars.c3(runs())
    assert got["shares"]["off"] == pytest.approx(0.25) and got["gain"] == pytest.approx(10.0)
    assert got["gain_a"] == pytest.approx(2.5) and got["pass"]
    assert not bars.c3(runs(ab=89.0))["pass"]
    assert not bars.c3(runs(unproven=True))["pass"]
    assert not bars.c3(runs(timeouts=("ab",)))["pass"]
    late = bars.c3(runs(timeouts=("b",)))
    assert late["pass"] and late["timeouts"] == ["b"] and late["gain_b"] is None
    assert not bars.c3(runs(naive=60.0))["pass"]


def test_the_reading_needs_the_guards_and_every_bar():
    good = {"c1": {"pass": True}, "c2": {"pass": True}, "c3": {"pass": True}}
    no_c3 = {"c1": {"pass": True}, "c2": {"pass": True}, "c3": {"pass": False}}
    assert bars.tally([good] * 12 + [no_c3] * 8, True)["reading"] == "build both seats"
    assert bars.tally([good] * 11 + [no_c3] * 9, True)["reading"] == "Edgar decides on an FAA-only v2"
    assert bars.tally([good] * 20, False)["reading"] == "stop"
