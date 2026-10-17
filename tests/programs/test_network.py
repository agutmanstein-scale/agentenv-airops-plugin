import json
from collections import Counter
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd
import pytest
from day_helpers import TURN, flight
from program_helpers import CACHE, DATE, DERIVED, WINDOW, needs_cache

from agentenv_airops.build import pack
from agentenv_airops.build.fleet import Aircraft
from agentenv_airops.programs import candidates, mip, network, references, repairs, sim, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.cost import cancel_price
from agentenv_airops.programs.layers import arrcaps, closures, config, depcaps, weather
from agentenv_airops.programs.model import LAGS, LAYERED, Busy, Layers, Move, Outcome, Tail, Task

START = 360
RECORD = ("Tail_Number", "Origin", "Dest", "sd", "sa", "ad", "aa", "CRSElapsedTime", "Cancelled", "end")


def record(*rows) -> pd.DataFrame:
    """BTS rows (tail, origin, destination, scheduled departure and arrival, actual departure and arrival, cancelled,
    the station the leg ended at) with local minutes."""
    return pd.DataFrame([dict(zip(RECORD, (t, o, d, sd, sa, ad, aa, sa - sd, c, end), strict=True))
                         for t, o, d, sd, sa, ad, aa, c, end in rows], columns=list(RECORD))


def flown(i: int, tail: str, orig: str, dest: str, sched: int, dep: int, block: int = 60, end: str | None = None):
    """A task flight `tail` flew, leaving at `dep` and ending at `end` (its destination unless it diverted)."""
    return flight(i, orig, dest, sched, block, actual=Outcome(False, end is not None and end != dest, "", dep,
                                                               dep + block, end or dest, tail))


def cancelled(i: int, tail: str, orig: str, dest: str, sched: int):
    """A task flight JetBlue cancelled, BTS leaving it on `tail`."""
    return flight(i, orig, dest, sched, 60, actual=Outcome(True, False, "A", None, None, None, tail))


def built(flights, today=(), before=(), tails=None):
    """network_tails over the flights, with `today` the record's legs of the date and `before` the day before's."""
    cur = record(*today)
    names = sorted(tails or {f.actual.tail for f in flights} | set(cur.Tail_Number))
    fleet = {t: Aircraft(t, "A32X", "A320-232", 150, "B-43 2024") for t in names}
    day = pack.Day(DATE, "JFK", "America/New_York", 0, record(*before), cur, record(), record(), fleet, {}, {}, TURN)
    sc = pack.Scope((START + 60, START + 360), START, names, 0, True, cur, cur[(cur.Cancelled == 0) & (cur.ad < START)],
                    {})
    tails, counts = network.network_tails(day, sc, flights, dict.fromkeys(names, "A320"), {"A320": TURN})
    return {t.tail: t for t in tails}, counts


def test_a_tail_flies_the_legs_it_flew_in_that_order_and_a_swap_or_a_stale_tail_makes_no_move():
    flights = [flown(0, "N2", "JFK", "BOS", 400, 405), cancelled(1, "N1", "JFK", "DCA", 450),
               flown(2, "N2", "BOS", "JFK", 500, 520), flown(3, "N1", "JFK", "MCO", 550, 560),
               flown(4, "N3", "JFK", "BUF", 600, 720), flown(5, "N3", "DCA", "JFK", 610, 615)]
    tails, counts = built(flights, today=[("N1", "BUF", "JFK", 300, 360, 300, 350, 0, "JFK"),
                                          ("N2", "MCO", "JFK", 200, 350, 200, 340, 0, "JFK"),
                                          ("N3", "BOS", "DCA", 200, 260, 205, 265, 0, "DCA")])
    assert {t: x.legs for t, x in tails.items()} == {"N1": (3,), "N2": (0, 2), "N3": (5, 4)}
    assert not any(x.repositions for x in tails.values()) and "moves" not in counts
    assert all(f.id not in x.legs for x in tails.values() for f in flights if f.actual.dep is None)


def test_a_station_gap_on_the_flown_path_is_a_move_and_a_long_daytime_ground_gap_no_cancelled_leg_fills_is_busy():
    flights = [flown(0, "N1", "BUF", "JFK", 400, 400), flown(1, "N1", "BOS", "DCA", 600, 600),
               flown(2, "N1", "DCA", "JFK", 1000, 1000), flown(3, "N2", "JFK", "PWM", 420, 420, end="BOS"),
               flown(4, "N2", "BOS", "JFK", 700, 700), flown(5, "N3", "JFK", "BOS", 400, 400),
               cancelled(6, "N3", "BOS", "DCA", 600), flown(7, "N3", "BOS", "JFK", 1000, 1000),
               flown(8, "N4", "JFK", "BOS", 500, 800), flown(9, "N4", "BOS", "JFK", 900, 920)]
    tails, counts = built(flights)
    assert tails["N1"].repositions == (Move(0, "JFK", "BOS", 460, 600 - TURN),)
    assert tails["N1"].busy == (Busy(1, 1000 - TURN),)
    assert tails["N2"].repositions == (Move(3, "PWM", "BOS", 480, 700 - TURN),) and not tails["N2"].busy
    assert tails["N3"].legs == (5, 7) and not tails["N3"].busy and not tails["N3"].repositions
    assert tails["N4"].legs == (8, 9) and not tails["N4"].busy
    assert (counts["moves"], counts["moves after a diversion"], counts["busy"]) == (1, 1, 1)


def test_each_tail_starts_where_the_record_has_it_at_the_start():
    flights = [flown(0, "N1", "JFK", "BOS", 400, 400), flown(1, "N3", "MCO", "JFK", 480, 495),
               flown(2, "N4", "PWM", "JFK", 500, 505), flown(3, "N6", "BOS", "JFK", 700, 700),
               flown(4, "N2", "BOS", "JFK", 450, 450), cancelled(5, "N5", "BUF", "JFK", 600),
               flown(6, "N8", "BOS", "JFK", 700, 700), cancelled(7, "N8", "BOS", "PWM", 500),
               flown(8, "N9", "SJU", "JFK", 420, 700), flown(9, "N10", "MCO", "JFK", 500, 520),
               flown(10, "N11", "BOS", "JFK", 650, 660)]
    today = [("N1", "BUF", "JFK", 250, 320, 260, 330, 0, "JFK"), ("N3", "JFK", "DCA", 250, 320, 255, 340, 0, "DCA"),
             ("N5", "BUF", "JFK", 600, 660, None, None, 1, None), ("N6", "JFK", "BOS", 300, 400, 300, 400, 0, "BOS"),
             ("N7", "JFK", "BOS", 200, 300, 210, 310, 0, "BOS"), ("N8", "JFK", "BOS", 300, 400, 300, 400, 0, "BOS"),
             ("N10", "JFK", "BOS", 200, 260, 200, 260, 0, "BOS"), ("N11", "JFK", "BOS", 300, 400, 350, 450, 0, "BOS")]
    before = [("N2", "JFK", "BOS", -200, -100, -190, -90, 0, "BOS"),
              ("N2", "BOS", "JFK", -500, -400, -490, -390, 0, "JFK"),
              ("N2", "MCO", "PWM", -80, 100, 900, 1080, 0, "PWM"),
              ("N9", "JFK", "BOS", -300, -200, -290, -190, 0, "BOS"),
              ("N9", "BOS", "SJU", -60, 180, 400, 640, 0, "SJU"),
              ("N10", "BOS", "MCO", -30, 120, 300, 450, 0, "MCO")]
    tails, counts = built(flights, today, before)
    starts = {t: (x.pos, x.ready) for t, x in tails.items()}
    assert starts == {"N1": ("JFK", 330 + TURN), "N2": ("BOS", 0), "N3": ("MCO", 480 - TURN), "N4": ("PWM", 500 - TURN),
                      "N5": ("BUF", 0), "N6": ("BOS", 700 - TURN), "N7": ("BOS", 310 + TURN),
                      "N8": ("BOS", 400 + TURN), "N9": ("SJU", 640 + TURN), "N10": ("MCO", 450 + TURN),
                      "N11": ("BOS", 450 + TURN)}
    assert counts == {"busy": 0, "start: day before": 3, "start: first flown leg": 1, "start: first scheduled leg": 1,
                      "start: today": 3, "start: today, busy": 1, "start: today, moved to the first flown leg": 1,
                      "start: today, no flown leg": 1}
    late = [flown(0, "N3", "MCO", "JFK", 360, 365)]
    assert built(late, today[1:2])[0]["N3"].ready == 340 + TURN


def test_tomorrows_needs_fly_the_whole_schedule_and_its_moves_from_the_start_and_a_short_station_counts_zero():
    flights = [flown(0, "N1", "JFK", "BOS", 400, 400), cancelled(1, "N2", "MCO", "JFK", 450),
               flown(2, "N3", "BOS", "DCA", 500, 500), flown(3, "N3", "PWM", "JFK", 700, 700),
               replace(flown(4, "N4", "JFK", "BUF", 400, 400), family="E190")]
    tails = [Tail(n, fam, "", 150, pos, 0, legs, (), moves, pos, pos) for n, fam, pos, legs, moves in (
        ("N1", "A320", "JFK", (0,), ()), ("N2", "A320", "JFK", (), ()),
        ("N3", "A320", "BOS", (2, 3), (Move(2, "DCA", "PWM", 560, 670),)), ("N4", "E190", "JFK", (4,), ()))]
    need, short = network.needs(tails, flights)
    assert need == {"A320": {"BOS": 1, "JFK": 2}, "E190": {"BUF": 1}} and short == {"A320 MCO": 1}
    assert sum(need["A320"].values()) == 3


def test_a_storm_date_holds_every_jetblue_leg_from_the_start_and_every_typed_tail(slice_inputs, fits,
                                                                                 tmp_path_factory):
    derived = tmp_path_factory.mktemp("derived")
    ones = {"JFK": 1.0, "BOS": 1.0}
    e_fit, e_median = unseen.table(fits, ones)
    b = network.build(DATE, ("JFK",), slice_inputs, derived, e_fit, e_median, ones, windows={"JFK": WINDOW},
                      split="control")
    task = b.task
    assert (task.task_id, task.start, task.hub, task.hubs, task.version) == ("b6-20230626-1600", 960, "JFK",
                                                                             ("JFK",), 3)
    cur = candidates.context(slice_inputs, derived, DATE, "JFK").day.cur
    decided = cur[((cur.Cancelled == 0) & (cur.ad >= 960)) | ((cur.Cancelled == 1) & (cur.sd >= 960))]
    assert len(task.flights) == len(decided) == 20 and {t.tail for t in task.tails} == set(cur.Tail_Number)
    for t in task.tails:
        mine = sorted((f for f in task.flights if f.actual.tail == t.tail and f.actual.dep is not None),
                      key=lambda f: (f.actual.dep, f.id))
        assert t.legs == tuple(f.id for f in mine)
    assert not any(t.repositions for t in task.tails)
    families = Counter(t.family for t in task.tails)
    assert {fam: sum(by.values()) for fam, by in task.needs.items()} == families
    assert task.clauses["short"] == {"E190 LGA": 1, "E190 RDU": 1}
    assert task.needed == Counter({(fam, s): n for fam, by in task.needs.items() for s, n in by.items()})
    again = Task.from_dict(json.loads(task.to_json()))
    assert again == task and again.to_json() == task.to_json()
    replay = references.airline(task)
    assert replay.auto == 0 and replay.flew == sum(f.actual.dep is not None for f in task.flights)


def dates(first: str, n: int, gap: int) -> list[str]:
    start = date.fromisoformat(first)
    return [(start + timedelta(days=gap * k)).isoformat() for k in range(n)]


def test_the_eval_draw_takes_twelve_dates_per_storm_hub_one_per_event_and_draws_a_date_for_one_hub_at_most():
    both = dates("2023-06-01", 15, 6)
    hubs = {**{d: ("JFK", "BOS") for d in both}, **{d: ("JFK",) for d in dates("2024-01-01", 6, 4)},
            **{d: ("BOS",) for d in dates("2024-12-01", 3, 4) + ["2024-12-02"]}}
    event = candidates.events(hubs)
    drawn = network.draw(hubs, event, seed=7)
    by = Counter((h, candidates.season(d)) for d, h in drawn.items())
    assert by == {("JFK", "summer"): 8, ("JFK", "winter"): 4, ("BOS", "summer"): 7, ("BOS", "winter"): 3}
    assert all(n == 1 for n in Counter((h, event[d]) for d, h in drawn.items()).values())
    assert drawn == network.draw(hubs, event, seed=7) and all(h in hubs[d] for d, h in drawn.items())
    split = network.split(hubs, event, seed=7)
    taken = {event[d] for d in drawn}
    assert {d for d, v in split.items() if v == "eval"} == set(drawn)
    assert all(event[d] not in taken for d, v in split.items() if v == "train")
    assert network.settle(split, [d for d in split if d != min(drawn)], event) == {
        d: v for d, v in split.items() if d != min(drawn)}


@needs_cache
def test_storm_dates_union_the_hub_candidates_and_the_control_rule_picks_two_dates_for_both_hubs():
    storms, counts = network.storms(CACHE)
    assert len(storms) == 207 and sum(len(h) for h in storms.values()) == counts["candidates"] == 294
    assert Counter(storms.values()) == {("JFK", "BOS"): 87, ("JFK",): 67, ("BOS",): 53}
    assert network.control_dates(CACHE, DERIVED) == {"2024-08-21": {"JFK": (1080, 1320), "BOS": (1020, 1260)},
                                                     "2025-02-04": {"JFK": (1080, 1320), "BOS": (420, 660)}}


def test_a_storm_date_with_crew_and_layers_carries_the_builders_layers_and_without_them_is_unchanged(
        slice_inputs, fits, tmp_path_factory):
    """With crew bases every leg has build_candidate's crew and the task its bases; with a layer config its layers are
    the builders' on the date's frame and closures, the pools at its bases, the closure rates and the hubs' weather
    news at the default lags (the desk's view, sim.known); stripped of both it is the plain build
    byte for byte. Layered, its airline replay is a solution of the model at its cost, and the optimum replays
    exactly."""
    derived = tmp_path_factory.mktemp("derived")
    ones = {"JFK": 1.0, "BOS": 1.0}
    e_fit, e_median = unseen.table(fits, ones)
    args = (DATE, ("JFK",), slice_inputs, derived, e_fit, e_median, ones)
    plain = network.build(*args, windows={"JFK": WINDOW}, split="control").task
    cfg = config.Config(rate={"JFK": 0.7, "BOS": 0.75}, reserves={"JFK": 2, "BOS": 1, "SJU": 3}, dates=("2023-06-25",))
    built = network.build_all([(DATE, ("JFK",), {"JFK": WINDOW}, "control")], slice_inputs, derived, e_fit, e_median,
                              ones, bases=repairs.BASES, layers=cfg)
    task = built[0].task
    assert task.bases == repairs.BASES and all(f.crew is not None for f in task.flights)
    assert any(f.crew.deadline is not None for f in task.flights)
    frame = candidates.frame(slice_inputs, derived, DATE)
    spans = config.spans(slice_inputs, tuple(candidates.months_of(DATE)))
    assert task.layers == Layers(depcaps.build(task, frame), arrcaps.build(task, frame),
                                 closures.build(task, spans, cfg.rate), {"JFK": 2, "BOS": 1},
                                 weather=weather.build(task, slice_inputs), rates=cfg.rate)
    assert task.layers.dep_caps and task.layers.closures and task.layers.weather and task.layers.lags == LAGS
    line = task.to_json()
    assert json.loads(line)["version"] == LAYERED and Task.from_dict(json.loads(line)).to_json() == line
    bare = replace(task, bases=(), layers=None, flights=tuple(replace(f, crew=None) for f in task.flights))
    assert bare.to_json() == plain.to_json()
    replay = references.airline(task)
    assert replay.auto == references.airline(plain).auto == 0 and replay.flew == references.airline(plain).flew
    price = {f.id: cancel_price(f, task.start if replay.run.flights[f.id].decided is None
                                else replay.run.flights[f.id].decided) for f in task.flights}
    net = mip.Network(task, price=price)
    x = net.vector(replay.run)
    assert x is not None and all(lo - 1e-6 <= sum(v * x[c] for c, v in coefs.items()) <= up + 1e-6
                                 for coefs, lo, up in net.m.rows)
    assert round(sum(c * v for c, v in zip(net.m.cost, x, strict=True))) == replay.run.cost.total
    sol = mip.solve(task, start=replay.run)
    run = sim.execute(task, sol.plan(task.start))
    assert sol.proven and run.cost.total == sol.objective <= replay.run.cost.total
    assert all((run.flights[i].dep if run.flights[i].status == "flown" else None) == d for i, d in sol.deps.items())


def test_the_follower_plays_a_layered_storm_date_through_a_closure_the_desk_prices_at_the_fitted_rate(
        slice_inputs, fits, tmp_path_factory):
    """The slice's layered date has a watch as its JFK closure begins: the day as known there prices the closure at
    the rate the build carries (without it, sim.known can't), and the follower plays every watch to a proven optimum,
    no cheaper than hindsight's."""
    ones = {"JFK": 1.0, "BOS": 1.0}
    e_fit, e_median = unseen.table(fits, ones)
    cfg = config.Config(rate={"JFK": 0.7, "BOS": 0.75}, reserves={"JFK": 2, "BOS": 1}, dates=("2023-06-25",))
    task = network.build(DATE, ("JFK",), slice_inputs, tmp_path_factory.mktemp("derived"), e_fit, e_median, ones,
                         windows={"JFK": WINDOW}, split="control", bases=repairs.BASES, layers=cfg).task
    ts = references.watch_times(task)
    (a, b, _, _), = task.layers.closures["JFK"]
    t = next(t for t in ts if a <= t < b)
    assert task.layers.rates == cfg.rate and sim.known(task, t).closures["JFK"][0][:2] == (a, t + sim.GUESS)
    with pytest.raises(ValueError, match="no rate"):
        sim.known(replace(task, layers=replace(task.layers, rates={})), t)
    followed = references.follow(task)
    assert [w["t"] for w in followed.watches] == ts and all(w["status"] == "Optimal" for w in followed.watches)
    assert followed.cost == sim.execute(task, followed.plan).cost.total >= mip.solve(task).objective


@needs_cache
def test_the_follower_plays_a_real_layered_storm_date_through_its_closures_and_event_watches():
    """15 July 2024 (BOS closes three times) with crew and every layer at the pack's fitted constants: the watches wake
    on its events, the day as known prices the closures under way at the build's rates, and the follower proves every
    watch, no cheaper than hindsight's optimum."""
    d = "2024-07-15"
    storms, _ = network.storms(CACHE, ("2024-07",))
    ones = dict.fromkeys(HUBS, 1.0)
    e_fit, e_median = unseen.table(candidates.e_table(CACHE, DERIVED), ones)
    cfg = config.Config(rate={"BOS": 0.678, "JFK": 0.755}, reserves={"BOS": 1, "FLL": 1, "JFK": 1, "MCO": 3})
    task = network.build(d, storms[d], CACHE, DERIVED, e_fit, e_median, ones, bases=repairs.BASES, layers=cfg).task
    ts = references.watch_times(task)
    assert task.layers.closures["BOS"] and task.layers.weather and any(t % 60 != task.start % 60 for t in ts)
    under = [t for t in ts if any(a <= t < b for a, b, _, _ in task.layers.closures["BOS"])]
    assert under and all(sim.known(task, t).closures["BOS"][-1][1] == t + sim.GUESS for t in under)
    followed = references.follow(task, time_limit=600.0)
    assert [w["t"] for w in followed.watches] == ts and all(w["status"] == "Optimal" for w in followed.watches)
    assert followed.cost == sim.execute(task, followed.plan).cost.total >= mip.solve(task).objective
