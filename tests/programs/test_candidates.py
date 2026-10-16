import json
from collections import Counter
from datetime import date, timedelta

from program_helpers import CACHE, DERIVED, adv, needs_cache

from agentenv_airops.build import fleet, pack
from agentenv_airops.programs import candidates
from agentenv_airops.programs.advisories import Run, Stop
from agentenv_airops.programs.model import Task


def dates(first: str, n: int, gap: int) -> list[str]:
    start = date.fromisoformat(first)
    return [(start + timedelta(days=gap * k)).isoformat() for k in range(n)]


def test_storm_events_chain_dates_within_two_days_across_hubs():
    event = candidates.events(["2024-07-01", "2024-07-03", "2024-07-06", "2024-07-08", "2024-07-08", "2024-07-20"])
    assert event == {"2024-07-01": 0, "2024-07-03": 0, "2024-07-06": 1, "2024-07-08": 1, "2024-07-20": 2}
    assert [candidates.season(d) for d in ("2024-06-01", "2024-08-31", "2024-12-01", "2025-02-28")] == [
        "summer", "summer", "winter", "winter"]


def test_the_eval_draw_takes_twelve_per_hub_from_distinct_events_and_train_drops_whole_events():
    jfk = dates("2023-06-01", 15, 6) + dates("2024-01-01", 6, 4)
    bos = dates("2023-06-02", 15, 6) + dates("2024-12-01", 3, 4) + ["2024-12-02"]
    qualifying = [(d, "JFK") for d in jfk] + [(d, "BOS") for d in bos]
    event = candidates.events(d for d, _ in qualifying)
    chosen = candidates.draw(qualifying, event, seed=7)
    by = Counter((h, candidates.season(d)) for d, h in chosen)
    assert by == {("JFK", "summer"): 8, ("JFK", "winter"): 4, ("BOS", "summer"): 9, ("BOS", "winter"): 3}
    assert all(n == 1 for n in Counter((h, event[d]) for d, h in chosen).values())
    assert chosen == candidates.draw(qualifying, event, seed=7)
    split = candidates.split(qualifying, event, seed=7)
    taken = {event[d] for d, _ in chosen}
    assert {k for k, v in split.items() if v == "eval"} == set(chosen)
    assert all(event[d] not in taken for (d, _), v in split.items() if v == "train")
    assert {k for k in qualifying if event[k[0]] not in taken} <= set(split)


def test_settling_keeps_the_drawn_eval_and_drops_rather_than_replaces_a_day_that_now_fails():
    event = {"2024-07-01": 0, "2024-07-02": 0, "2024-07-10": 1, "2024-07-20": 2, "2024-07-21": 2}
    drawn = {("2024-07-01", "JFK"): "eval", ("2024-07-20", "BOS"): "eval", ("2024-07-10", "JFK"): "train"}
    now = [("2024-07-02", "BOS"), ("2024-07-10", "JFK"), ("2024-07-10", "BOS"), ("2024-07-20", "BOS"),
           ("2024-07-21", "JFK")]
    assert candidates.settle(drawn, now, event) == {("2024-07-10", "BOS"): "train", ("2024-07-10", "JFK"): "train",
                                                   ("2024-07-20", "BOS"): "eval"}


def test_the_first_program_hour_reads_gdps_from_their_period_and_stops_over_their_window():
    run = Run(adv("g", "gdp", 500, 780, 1199, (30,) * 7), 500, 1000)
    stop = Stop("s", "JFK", 640, 700, None)
    assert candidates.in_force([run], [stop]) == [(640, 700), (780, 1000)]
    assert candidates.first_hour([run], [stop]) == 10 and candidates.first_hour([run], []) == 13
    assert candidates.first_hour([], [Stop("s", "JFK", 1270, 1300, None)]) is None
    assert candidates.any_in_force([], [Stop("s", "JFK", 1230, 1300, None)], 300, 1440)


def test_a_built_task_round_trips_through_json_with_its_crew_too(task, built, crewed):
    for t in (task, crewed):
        line = t.to_json()
        again = Task.from_dict(json.loads(line))
        assert again == t and again.to_json() == line
    assert crewed.bases and all(f.crew for f in crewed.flights) and not any(f.crew for f in task.flights)
    assert (task.task_id, task.split, task.window, task.start, task.version) == (
        "b6-jfk-20230626-1700", "train", (1020, 1320), 960, 3)
    assert set(task.programs) == {"JFK", "BOS"} and built.row["task_id"] == task.task_id
    assert task.sources["nasr"] and task.sources["advisories"]["2023-06-26/JFK"]


def test_the_months_are_the_cached_thirteen_and_six_more_in_order():
    added = ("2025-08", "2025-12", "2026-01", "2026-02", "2026-06", "2026-07")
    assert candidates.MONTHS == tuple(sorted(pack.MONTHS + added)) and len(candidates.MONTHS) == 19


def test_dates_reading_advisories_in_an_archive_outage_are_dropped():
    assert [d for d in dates("2025-08-19", 11, 1) if candidates.gap(d)] == dates("2025-08-22", 5, 1)
    assert [d for d in dates("2025-02-12", 8, 1) if candidates.gap(d)] == ["2025-02-15", "2025-02-16"]


def test_the_fleet_takes_the_two_published_inventories_nearest_the_month():
    assert candidates.B43_PUBLISHED == tuple(range(2021, 2026))
    for m in pack.MONTHS:
        assert candidates.b43_years(m) == fleet.b43_years(m)
    assert candidates.b43_years("2025-08") == [2025, 2024]
    assert all(candidates.b43_years(m) == [2025, 2024] for m in ("2026-01", "2026-02", "2026-06", "2026-07"))


@needs_cache
def test_candidates_are_counted_per_local_date_without_carrier_limited_stops():
    keys, counts = candidates.candidates(CACHE)
    assert {k: v for k, v in counts.items() if isinstance(v, int)} == {
        "utc_day_hubs": 342, "local_day_hubs": 303, "limited_only": 9, "candidates": 294}
    assert counts["archive_gap_dates"] == ["2025-02-15", "2025-02-16", *dates("2025-08-22", 5, 1)]
    assert ("2024-08-19", "JFK") in keys and ("2026-07-04", "JFK") in keys and len(keys) == len(set(keys))


@needs_cache
def test_a_program_day_whose_neighbour_is_not_loaded_is_rejected_with_the_day_missing():
    keys, counts = candidates.candidates(CACHE, ("2025-07",))
    assert counts["rejected"] == {"2025-07-01/JFK": "no BTS day 2025-06-30", "2025-07-01/BOS": "no BTS day 2025-06-30",
                                  "2025-07-31/JFK": "no BTS day 2025-08-01", "2025-07-31/BOS": "no BTS day 2025-08-01"}
    assert not {k for k in keys if k[0] in ("2025-07-01", "2025-07-31")}
    assert {("2025-07-31", "JFK"), ("2025-07-31", "BOS")} <= set(candidates.candidates(CACHE)[0])


@needs_cache
def test_a_storm_day_builds_at_its_rule_window_in_parallel_as_in_series():
    e_fit = {f.advisory: f.e for f in candidates.fits_of(("2024-08-19", "JFK", CACHE, DERIVED))}
    args = (CACHE, DERIVED, e_fit, {"JFK": 24.0, "BOS": 16.0}, {"JFK": 1.0, "BOS": 1.0})
    keys = [("2024-08-19", "JFK"), ("2024-08-19", "BOS")]
    serial, parallel = candidates.build_all(keys, *args), candidates.build_all(keys, *args, workers=2)
    assert [b.task.to_json() for b in serial] == [b.task.to_json() for b in parallel]
    task = serial[1].task
    assert (task.task_id, task.slide, task.clauses["program_in_window"]) == ("b6-jfk-20240819-1300", 0, True)
    assert len(task.programs["JFK"].segments) == 1 and task.programs["JFK"].segments[0].slots
