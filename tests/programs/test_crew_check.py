import importlib.util

from program_helpers import ROOT

SPEC = importlib.util.spec_from_file_location("crew_check", ROOT / "scripts" / "crew_check.py")
crew_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crew_check)


def test_the_record_guard_reads_one_percent_of_the_flights_flown_and_at_most_two():
    assert [crew_check.replays(a, n) for a, n in ((0, 0), (1, 100), (1, 99), (2, 200), (2, 150), (3, 1000))] == [
        True, True, False, True, False, False]


def test_the_calm_guard_allows_two_percent_and_from_zero_only_zero():
    assert crew_check.rise(102.0, 100.0) == 2.0 and crew_check.calm_pass(102.0, 100.0)
    assert not crew_check.calm_pass(102.1, 100.0)
    assert crew_check.calm_pass(0.0, 0.0) and crew_check.rise(5.0, 0.0) is None and not crew_check.calm_pass(5.0, 0.0)


def notice(best=100.0, bound=99.0, exact=True, crewless=0, below=None) -> dict:
    return {"best": best, "bound": bound, "exact": exact, "flown_without_crew": crewless, "below_bound": below or {},
            "gap_pct": round(100 * (best - bound) / best, 4)}


def test_a_best_plan_counts_when_it_replays_exactly_crewed_and_nothing_played_is_under_its_bound():
    assert crew_check.best_ok(notice())
    assert not crew_check.best_ok(notice(exact=False))
    assert not crew_check.best_ok(notice(crewless=1))
    assert not crew_check.best_ok(notice(below={"naive": 98.0}))
    assert not crew_check.best_ok(notice(bound=101.0))


def joint(best=100.0, flat=95.0, recorded=120.0, naive=96.0) -> dict:
    return {"notice": notice(best), "flat": notice(flat, flat), "recorded": {"total": recorded},
            "played": {"naive": {"flat": naive, "notice": naive, "uncrewed": 0}}}


def test_a_control_needs_the_recorded_day_over_the_best_plan_and_naive_within_two_percent_of_the_flat_best():
    assert crew_check.control_checks(joint())["ok"]
    assert not crew_check.control_checks(joint(recorded=99.0))["ok"]
    c = crew_check.control_checks(joint(naive=97.0))
    assert not c["ok"] and c["naive_flat_over_best"] == round(97 / 95 - 1, 4)


def row(date, split="train", rec=True, calm=True, best=100.0, bound=99.0) -> dict:
    band = {"record": {"pass": rec, "uncrewed": 0}, "calm": {"pass": calm, "rise_pct": 0.0},
            "joint": {**joint(best), "notice": {**notice(best, bound), "crew_cancelled": 0, "optimum": {
                "objective": 99.0}, "crew_delayed": {"flights": 0, "minutes": 0}, "crew_cost": {
                "deadhead": 0.0, "hotel": 0.0, "end": 1.0}, "pieces": {"aircraft": bound, "cover": None}}}}
    return {"date": date, "task_id": f"b6-{date}", "split": split, "seconds": 1.0, "peak_rss_gb": 1.0,
            "bands": {"middle": band}}


def test_the_guards_count_storm_dates_at_ninety_percent_and_gaps_within_each_threshold():
    rows = [row(f"d{k}") for k in range(9)] + [row("d9", rec=False, calm=False, bound=90.0),
                                              row("c1", "control"), row("c2", "control")]
    g = crew_check.guards(rows, ("middle",))["guards"]
    assert g["record"]["value"] == 0.9 and g["record"]["pass"] and g["record"]["n"] == 10
    assert g["calm"]["value"] == 0.9 and g["calm"]["pass"]
    assert g["best"]["kept"] == 12 and g["best"]["gap_within"] == {"0.01%": 0, "1.0%": 11, "5.0%": 11}
    assert g["controls"]["pass"] and g["go"]
    rows[1] = {"date": "d1", "task_id": "b6-d1", "split": "train", "error": "boom"}
    g = crew_check.guards(rows, ("middle",))["guards"]
    assert g["best"]["left_out"] == 1 and g["record"]["n"] == 9 and not g["record"]["pass"]
