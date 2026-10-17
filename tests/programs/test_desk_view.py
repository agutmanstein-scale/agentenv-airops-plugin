import csv
import io
import json
import random
import shutil
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from day_helpers import (
    capped,
    closed,
    closed_day,
    day,
    flight,
    outcapped,
    random_day,
    reserve_day,
    tail,
    three_into_a_program,
)
from program_helpers import adv
from test_reference_plays import stopped_then_released

from agentenv_airops.build import bts
from agentenv_airops.core.known import CAP_LOOK, FREEZE, STEP
from agentenv_airops.programs import references, sim
from agentenv_airops.programs.layers import closures, weather
from agentenv_airops.programs.model import LAGS, Layers, Task
from agentenv_airops.v3day import clock
from agentenv_airops.v3day.day import Day


def two_days_of_caps() -> Task:
    """JFK caps hours 2 and 5 at one departure, BUF hours 2 and 6 at one arrival."""
    task = day([flight(0, "JFK", "BUF", 100, 60), flight(1, "JFK", "MCO", 500, 150)],
               [tail("N1", "JFK", end="BUF"), tail("N2", "JFK", end="MCO")])
    return outcapped(capped(task, {"JFK": {2: 1, 5: 1}}), {"BUF": {2: 1, 6: 1}})


def test_known_carries_dep_and_outstation_caps_only_cap_look_ahead():
    task = two_days_of_caps()
    w = sim.known(task, 0)
    assert w.dep_caps == {"JFK": {2: 1}} and w.nominal["BUF"] == {2: 1}
    w = sim.known(task, 120)
    assert w.dep_caps == {"JFK": {2: 1, 5: 1}} and w.nominal["BUF"] == {2: 1, 6: 1}
    assert all(60 * h <= 60 + CAP_LOOK for h in sim.known(task, 60).dep_caps["JFK"])
    assert sim.known(task, 0).nominal["JFK"] == sim.truth(task).nominal["JFK"]


def test_known_carries_a_closure_once_begun_expected_guess_on_until_its_end_comes():
    task = closed_day()
    assert sim.known(task, 119).closures == {}
    assert sim.known(task, 120).closures == {"JFK": ((120, 120 + sim.GUESS, 1, 1),)}
    assert sim.known(task, 170).closures == {"JFK": ((120, 170 + sim.GUESS, 3, 1),)}
    assert sim.known(task, 180).closures == sim.known(task, 300).closures == {"JFK": ((120, 180, 1, 2),)}


def two_closures_but_their_ends() -> tuple[Task, Task]:
    """Eighteen departures from JFK every 10 minutes from 120, its ramp closed from 120 to 150 on one day and to 300
    on the other, each priced at rate 0.75 over its true length (closures.build)."""
    base = day([flight(i, "JFK", "BUF", 120 + 10 * i, 60) for i in range(18)],
               [tail(f"N{i}", "JFK", end="BUF") for i in range(18)])
    return tuple(closed(base, closures.build(base, {"JFK": [(base.base + 120, base.base + end)]}, {"JFK": 0.75}),
                        {"JFK": 0.75}) for end in (150, 300))


def test_two_closures_differing_only_in_their_true_end_look_the_same_until_it_comes():
    short, long = two_closures_but_their_ends()
    assert short.layers.closures["JFK"][0][2] < long.layers.closures["JFK"][0][2]
    for t in (120, 135):
        assert sim.known(short, t).closures == sim.known(long, t).closures
        assert sim.known(short, t).closures["JFK"][0][:2] == (120, t + sim.GUESS)
    assert closures_situation(short, 120) == closures_situation(long, 120)
    assert sim.known(short, 150).closures == short.layers.closures != sim.known(long, 150).closures


def closures_situation(task: Task, t: int) -> list[dict]:
    d = Day(task)
    while d.t < t:
        d.advance()
    return d.situation()["closures"]["JFK"]


def test_a_closure_under_way_at_a_hub_without_a_rate_cant_be_known():
    task = closed(day([flight(0, "JFK", "BUF", 400, 60)], [tail("N1", "JFK", end="BUF")]), {"JFK": ((120, 180, 1, 1),)})
    assert sim.known(task, 60).closures == {} and sim.known(task, 180).closures == {"JFK": ((120, 180, 1, 1),)}
    with pytest.raises(ValueError, match="no rate"):
        sim.known(task, 150)


def test_a_layered_tasks_watches_run_until_every_cap_is_known():
    task = outcapped(day([flight(0, "JFK", "SFO", 300, 360)], [tail("N1", "JFK", end="SFO")]), {"SFO": {11: 0}})
    ts = references.watch_times(task)
    assert ts[-1] >= 60 * 11 - CAP_LOOK and sim.known(task, ts[-1]).nominal["SFO"] == {11: 0}
    known = sim.execute(task, {}, sim.known(task, ts[-1])).flights[0]
    assert known == sim.execute(task, {}).flights[0] and known.dep == 360


def test_known_keeps_the_reserve_pools_of_the_truth():
    task = reserve_day()
    plan = {0: {"reserve": True}, 1: {"reserve": True}}
    assert sim.execute(task, plan, sim.known(task, 0)).reserved == sim.execute(task, plan).reserved == {0}


@pytest.mark.parametrize("seed", range(4))
def test_without_layers_known_and_watch_times_are_v2s(seed):
    for task in (random_day(random.Random(seed)), three_into_a_program(), stopped_then_released()):
        for t in references.watch_times(task):
            w = sim.known(task, t)
            assert w == sim.known(replace(task, layers=Layers()), t)
            assert w.dep_caps == {} and w.closures == {} and w.nominal == sim.nominal_caps(task)
            assert repr(w) == repr(sim.World(w.lb, w.block, w.segments, w.stops, sim.nominal_caps(task)))
        ts = references.watch_times(task)
        assert ts == references.watch_times(replace(task, layers=Layers()))
        assert ts == list(range(task.start, ts[-1] + 1, STEP))


def eventful() -> Task:
    """closed_day (JFK closed 120-180) with a ground-stop advisory signed at 200 (for 11:40-12:00, when nothing lands)
    and a SPECI observed at 300."""
    task = closed_day()
    jfk = task.programs["JFK"]
    task = replace(task, programs=task.programs | {"JFK": replace(jfk, advisories=jfk.advisories + (
        adv("a1", "gs", 200, 700, 720),))})
    return replace(task, layers=replace(task.layers, weather=((300, "speci", "JFK", "KJFK 2605Z TS"),)))


def test_a_layered_tasks_watches_wake_on_news_and_closures_15_to_60_apart():
    task = eventful()
    ts = references.watch_times(task)
    assert references.events(task) == [120, 180, 200, 300 + LAGS["speci"]]
    assert {200, 305} <= set(ts) and 120 in ts
    assert ts == [0, 60, 120, 180, 200, 260, 305]
    assert all(references.WATCH_MIN <= b - a <= STEP for a, b in zip(ts, ts[1:], strict=False))
    assert references.watch_times(replace(task, layers=None)) == [0, 60, 120]


def test_an_event_close_to_a_watch_moves_to_watch_min_after_it_absorbing_the_rest():
    task = closed(day([flight(0, "JFK", "BUF", 400, 60)], [tail("N1", "JFK", end="BUF")]),
                  {"JFK": ((125, 160, 1, 1), (130, 170, 1, 1))})
    ts = references.watch_times(task)
    assert ts[:4] == [0, 60, 120, 135] and ts[4:7] == [160, 175, 190]



def test_a_closure_outlasting_the_hourly_watches_is_watched_until_its_end_comes():
    flights = [flight(i, "JFK", "BUF", 200 + 20 * i, 60, deadline=600 + 20 * i) for i in range(6)]
    task = closed(day(flights, [tail(f"N{i}", "JFK", end="BUF") for i in range(6)]), {"JFK": ((200, 420, 1, 1),)},
                  {"JFK": 0.5})
    ts = references.watch_times(task)
    assert ts == [0, 60, 120, 180, 200, 260, 320, 380, 420]
    assert sim.known(task, 380).closures["JFK"][0][1] == 380 + sim.GUESS
    assert sim.known(task, ts[-1]).closures == task.layers.closures
    assert all(references.WATCH_MIN <= b - a <= STEP for a, b in zip(ts, ts[1:], strict=False))


def test_an_event_just_before_the_last_hourly_watch_moves_it_onto_the_last_minute():
    f = flight(0, "JFK", "BUF", 600, 60, deadline=665)
    capped_far = replace(day([f], [tail("N1", "JFK", end="BUF")]), layers=Layers(dep_caps={"JFK": {30: 5}}))
    last = f.deadline - FREEZE
    assert references.watch_times(capped_far)[-1] == 600
    task = replace(capped_far, layers=replace(capped_far.layers, weather=((590, "speci", "JFK", "KJFK SPECI TS"),
                                                                          (598, "speci", "JFK", "KJFK SPECI +TS"))))
    ts = references.watch_times(task)
    assert ts[-3:] == [540, 595, last]
    news = {n for w in clock.timetable(task) for n in w.news}
    assert {"speci-jfk-590", "speci-jfk-598"} <= news


def test_a_negative_lag_and_an_advisory_lag_the_known_programs_would_not_wait_for_are_refused():
    with pytest.raises(ValueError, match="not a whole number"):
        Layers(lags={"speci": -60})
    with pytest.raises(ValueError, match="not a whole number"):
        Layers(lags={"taf": 2.5})
    with pytest.raises(ValueError, match="advisory lag must be 0"):
        Layers(lags={"advisory": 30})
    assert Layers(lags={"advisory": 0, "speci": 0}).lags == LAGS | {"speci": 0}

def test_the_follower_on_a_layered_day_wakes_on_events_and_its_plan_replays():
    task = eventful()
    followed = references.follow(task, time_limit=60)
    ts = [w["t"] for w in followed.watches]
    assert ts == references.watch_times(task) and 305 in ts and any(t % STEP for t in ts)
    again = sim.execute(task, followed.plan)
    assert again.rows() == followed.run.rows() and again.cost == followed.run.cost
    _, anchor = references.anchor(task, time_limit=60)
    assert followed.cost >= anchor.cost.total


def test_lags_left_out_keep_their_default_and_an_unknown_kind_is_refused():
    layers = Layers.from_dict({"dep_caps": {}, "arr_caps": {}, "closures": {}, "reserves": {},
                               "lags": {"speci": 7}})
    assert layers.lags == LAGS | {"speci": 7} and layers.to_dict()["lags"] == layers.lags
    assert replace(closed_day(), layers=layers).lag("taf") == 10
    with pytest.raises(ValueError, match="unknown kind"):
        Layers(lags={"metra": 5})
    with pytest.raises(KeyError):
        closed_day().lag("metra")


def test_weather_and_lags_round_trip_and_are_written_only_when_set():
    task = closed_day()
    assert "weather" not in task.layers.to_dict() and "lags" not in task.layers.to_dict()
    layers = replace(task.layers, weather=((50, "metar", "JFK", "KJFK 260051Z"),), lags=LAGS | {"taf": 20})
    back = Task.from_dict(json.loads(replace(task, layers=layers).to_json()))
    assert back.layers == layers and back.lag("taf") == 20 and back.lag("advisory") == 0
    assert day([flight(0, "JFK", "BUF", 100, 60)], [tail("N1", "JFK")]).lag("metar") == LAGS["metar"] == 5


def minute(row: dict, task: Task) -> int:
    return bts.epoch_minutes(datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)) - task.base


def test_the_weather_builder_reads_the_hubs_metars_specis_and_tafs_over_the_span(task, slice_inputs, tmp_path):
    root = tmp_path / "inputs"
    shutil.copytree(slice_inputs / "iem", root / "iem")
    metars = (root / "iem" / "metar" / "JFK_2023_06.csv").read_text()
    rows = list(csv.DictReader(io.StringIO(metars)))
    hi = max(f.deadline for f in task.flights)
    seen = next(r for r in rows if task.start <= minute(r, task) <= hi)
    special = dict(seen, valid=seen["valid"][:-2] + "07", metar=seen["metar"].replace("51Z", "07Z"))
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows([seen, special])
    path = root / "iem" / "speci" / "JFK_2023_06.csv"
    path.parent.mkdir(parents=True)
    path.write_text(out.getvalue())
    items = weather.build(task, root)
    kinds = {k for _, k, _, _ in items}
    assert kinds == {"metar", "speci", "taf"} and {s for _, _, s, _ in items} == set(task.storms)
    assert [x[0] for x in items] == sorted(x[0] for x in items)
    assert all(task.start - weather.LEAD_TAF <= m <= hi for m, _, _, _ in items)
    assert sum(k == "speci" for _, k, _, _ in items) == 1
    assert any(k == "taf" and m <= task.start for m, k, _, _ in items)
    assert any(k == "metar" and task.start - weather.LEAD_OBS <= m <= task.start for m, k, _, _ in items)
