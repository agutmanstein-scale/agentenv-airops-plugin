import importlib.util
import sys

from program_helpers import ROOT

sys.path.insert(0, str(ROOT / "scripts"))  # v4_check imports v4_pack and world_check beside it, as a script run does
SPEC = importlib.util.spec_from_file_location("v4_check", ROOT / "scripts" / "v4_check.py")
v4_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v4_check)


def refs(anchor=100, naive=110, follower=104, recorded=120, cancels=(3, 1), jetblue=(10, 90), free=6, flown=88,
         within=80, proven=True, exact=True, split="eval"):
    """A task's references as v4_pack.refs writes them, the numbers the criteria read."""
    return {"split": split, "refs": ["naive", "anchor", "follower", "recorded"],
            "anchor": {"objective": anchor, "total": anchor, "proven": proven, "exact": exact, "status": "Optimal",
                       "gap_pct": 0.0 if proven else 4.0, "cancelled": cancels[0], "auto_cancelled": cancels[1]},
            "naive": {"total": naive}, "follower": {"total": follower}, "recorded": {"total": recorded},
            "jetblue": {"cancelled": jetblue[0], "flown": jetblue[1]},
            "probe": {"feasible": free, "cancelled": jetblue[0]},
            "fidelity": {"flown": flown, "jetblue_flown": jetblue[1], "within": within}}


TESTS = {"agreement": {"pass": True, "summary": "40 passed"}, "playable": {"pass": True, "summary": "1 passed"}}


def test_harder_compares_the_median_excesses_over_the_anchor_on_the_same_dates():
    v2 = {"a": refs(naive=110, follower=102), "b": refs(naive=106, follower=104), "c": refs(naive=200)}
    v3 = {"a": refs(naive=115, follower=103), "b": refs(naive=113, follower=106)}
    c = v4_check.criteria(v3, v2, TESTS)
    do = c["harder_do_nothing"]
    assert (do["n"], do["v3"], do["v2"], do["value"], do["pass"]) == (2, 0.14, 0.08, 1.75, True)
    f = c["harder_follower"]
    assert (f["v3"], f["v2"], f["value"], f["pass"]) == (0.045, 0.03, 1.5, True)
    v3["b"]["follower"]["total"] = 105
    assert not v4_check.criteria(v3, v2, TESTS)["harder_follower"]["pass"]


def test_an_unproven_anchor_drops_its_date_from_the_excesses_and_is_flagged_not_failed():
    v2 = {"a": refs(), "b": refs(naive=150)}
    v3 = {"a": refs(naive=130), "b": refs(naive=101, proven=False)}
    c = v4_check.criteria(v3, v2, TESTS)
    assert c["harder_do_nothing"]["n"] == 1 and c["harder_do_nothing"]["value"] == 3.0
    p = c["sound_anchors_proven"]
    assert p["pass"] and p["flagged"] == {"b": {"status": "Optimal", "gap_pct": 4.0}} and p["value"] == 1
    v3["b"]["anchor"] = {"error": "RuntimeError: boom"}
    p = v4_check.criteria(v3, v2, TESTS)["sound_anchors_proven"]
    assert not p["pass"] and p["failed"] == ["b"]
    assert not v4_check.criteria(v3, v2, TESTS)["sound_anchors_exact"]["pass"]


def test_realistic_pools_the_counts_over_the_dates_and_compares_with_v2():
    v2 = {"a": refs(cancels=(2, 0), free=7, within=86), "b": refs(cancels=(1, 0), free=6, within=84)}
    v3 = {"a": refs(cancels=(3, 1), free=5, flown=88, within=80),
          "b": refs(cancels=(3, 0), free=4, flown=87, within=79), "x": refs(split="control", cancels=(0, 0))}
    c = v4_check.criteria(v3, v2, TESTS)
    o = c["realistic_optimum_cancels"]
    assert (o["v3"], o["v2"], o["v3_counts"], o["pass"]) == (0.35, 0.15, [7, 20], True)
    free = c["realistic_free_share"]
    assert (free["v3"], free["v2"], free["pass"]) == (0.45, 0.65, True)
    flown = c["realistic_replay_flown"]
    assert (flown["v3"], flown["v3_counts"], flown["pass"]) == (0.9722, [175, 180], True)
    within = c["realistic_replay_within_15"]
    assert within["v3"] == round(159 / 175, 4) and within["v2"] == round(170 / 176, 4) and not within["pass"]
    assert c["realistic_recorded_excess"]["v3"] == c["realistic_recorded_excess"]["v2"] == 0.2
    assert not c["realistic_recorded_excess"]["pass"]
    v3["a"]["probe"]["feasible"] = 8
    assert not v4_check.criteria(v3, v2, TESTS)["realistic_free_share"]["pass"]


def test_sound_and_playable_read_the_anchors_and_the_tests_and_a_test_not_run_fails():
    v3 = {"a": refs(), "x": refs(split="control", exact=False)}
    c = v4_check.criteria(v3, {"a": refs()}, TESTS)
    assert c["sound_anchors_exact"]["not_exact"] == ["x"] and not c["sound_anchors_exact"]["pass"]
    assert c["sound_agreement_tests"]["pass"] and c["playable"]["value"] == "1 passed"
    c = v4_check.criteria(v3, {"a": refs()}, {})
    assert not c["playable"]["pass"] and c["playable"]["value"] == "not run"


def with_calm(r: dict, cancelled: int, ferries: int) -> dict:
    return {**r, "calm": {"cancelled": cancelled, "ferries": ferries}}


def test_a_crew_pack_adds_v4s_guards_and_a_no_crew_pack_keeps_v3s_criteria(tmp_path):
    v2 = {"a": refs(within=84), "b": refs(within=85), "c": refs(within=80)}
    v3 = {"a": with_calm(refs(recorded=120, cancels=(5, 0), jetblue=(10, 90), within=83), 2, 1),
          "b": with_calm(refs(recorded=99, cancels=(4, 0), jetblue=(2, 90), within=84), 0, 0),
          "c": with_calm(refs(recorded=130, cancels=(1, 0), jetblue=(0, 90), within=80), 1, 0),
          "x": with_calm(refs(split="control"), 9, 9)}
    plain = {"a": with_calm(refs(), 2, 1), "b": with_calm(refs(), 0, 0), "c": with_calm(refs(), 0, 0)}
    assert not any(k.startswith("guard_") for k in v4_check.criteria(v3, v2, TESTS))
    g = v4_check.criteria(v3, v2, TESTS, True, plain)
    assert (g["guard_recorded"]["value"], g["guard_recorded"]["pass"]) == (round(2 / 3, 4), False)
    assert (g["guard_anchor_cancels"]["value"], g["guard_anchor_cancels"]["pass"]) == (0.8333, True)
    calm = g["guard_calm_vs_v3"]
    assert (calm["n"], calm["value"], calm["worse"], calm["pass"]) == (3, 2, ["c"], False)
    assert g["guard_within_15"]["v3"] == round(247 / 264, 4) and g["guard_within_15"]["pass"]
    v3["c"]["fidelity"]["within"] = 75
    assert not v4_check.criteria(v3, v2, TESTS, True, plain)["guard_within_15"]["pass"]
    assert not v4_check.criteria(v3, v2, TESTS, True, None)["guard_calm_vs_v3"]["pass"]
    plain["c"]["calm"]["cancelled"] = 1
    assert v4_check.criteria(v3, v2, TESTS, True, plain)["guard_calm_vs_v3"]["pass"]
    (tmp_path / "refs").mkdir()
    (tmp_path / "refs" / "a.json").write_text('{"calm": {"cancelled": 1, "ferries": 0}}')
    (tmp_path / "refs" / "b.json").write_text('{"cal')
    assert v4_check.read_refs(tmp_path, ["a", "b", "z"]) == {"a": {"calm": {"cancelled": 1, "ferries": 0}}}
