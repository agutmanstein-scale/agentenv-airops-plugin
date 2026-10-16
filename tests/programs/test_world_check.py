import importlib.util

from program_helpers import ROOT

SPEC = importlib.util.spec_from_file_location("world_check", ROOT / "scripts" / "world_check.py")
world_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(world_check)


def test_the_airline_replay_reads_one_percent_of_the_flights_flown_and_at_most_two_literally():
    assert [world_check.replays(a, n) for a, n in ((0, 0), (1, 100), (1, 99), (2, 200), (2, 150), (3, 1000))] == [
        True, True, False, True, False, False]


def test_fidelity_pools_modelled_over_observed_delay_over_the_days_within_half_to_twice():
    days = [(120.0, 100.0, 5), (80.0, 100.0, 4), (300.0, 100.0, 3), (40.0, 100.0, 2), (10.0, 0.0, 1)]
    assert world_check.pooled(days) == {"ratio": 1.0, "days": 2, "dropped": 2, "no_delay": 1}
    assert world_check.pooled([(50.0, 100.0, 1), (200.0, 100.0, 1)]) == {"ratio": 1.25, "days": 2, "dropped": 0,
                                                                          "no_delay": 0}
    assert world_check.pooled([(0.0, 0.0, 0)])["ratio"] is None


def test_fidelity_passes_only_with_every_hubs_eval_and_train_pool_in_band_out_of_sample():
    fid = {"a": (110.0, 100.0, 3), "b": (90.0, 100.0, 3), "c": (100.0, 100.0, 3), "d": (95.0, 100.0, 3),
           "e": (500.0, 100.0, 3)}
    split = {"a": "eval", "b": "eval", "c": "train", "d": "train", "e": "train"}
    hub = {"a": "JFK", "b": "BOS", "c": "JFK", "d": "BOS", "e": "BOS"}
    gate = world_check.fidelity_gate(fid, split, hub, True)
    assert gate["pass"] and gate["train_BOS"] == {"ratio": 0.95, "days": 1, "dropped": 1, "no_delay": 0}
    assert not world_check.fidelity_gate(fid, split, hub, False)["pass"]
    assert not world_check.fidelity_gate(fid, {**split, "a": "eval", "c": "eval"}, hub, True)["pass"]
    high = world_check.fidelity_gate({**fid, "b": (130.0, 100.0, 3)}, split, hub, True)
    assert high["eval_BOS"]["ratio"] == 1.3 and not high["pass"]


def test_a_task_is_left_out_for_every_reference_that_failed_or_did_not_prove():
    ok = {k: {"proven": True} for k in world_check.REFERENCES}
    assert world_check.unproven(ok) == []
    r = {**ok, "follower": {"error": "RuntimeError: watch 600 solve Time limit reached"}, "flat": {"proven": False}}
    assert world_check.unproven(r) == ["follower", "flat"]
    assert world_check.unproven({k: v for k, v in ok.items() if k != "calm"}) == ["calm"]
