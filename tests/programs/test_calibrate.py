from dataclasses import replace

import pandas as pd
from day_helpers import based, chain_day, crewed, day, flight, tail

from agentenv_airops.programs import references
from agentenv_airops.programs.candidates import midnight
from agentenv_airops.programs.layers import calibrate, config, crew
from agentenv_airops.programs.model import Outcome

DATE = "2024-01-01"


def flew(i: int, orig: str, dest: str, sched: int, dep: int, tail_: str, deadline: int = 700):
    """A base departure on a pairing whose crew deadline is 150, JetBlue leaving at `dep`."""
    return based(flight(i, orig, dest, sched, 60, deadline=deadline,
                        actual=Outcome(False, False, "", dep, dep + 60, dest, tail_)), 150)


def two_late_at_jfk():
    """JetBlue flew two JFK departures past their crew deadline (their rest ending past their deadline, so one without
    a reserve auto-cancels) and one before it, and one BOS departure past it whose rest ends before its deadline (900):
    without a reserve it waits out the rest and still flies."""
    flights = [flew(0, "JFK", "BUF", 100, 200, "N1"), flew(1, "JFK", "PWM", 105, 205, "N2"),
               flew(2, "JFK", "DCA", 110, 120, "N3"), flew(3, "BOS", "BUF", 100, 200, "N4", deadline=900)]
    tails = [tail("N1", "JFK", end="BUF"), tail("N2", "JFK", end="PWM"), tail("N3", "JFK", end="DCA"),
             tail("N4", "BOS", end="BUF")]
    return replace(day(flights, tails), bases=("BOS", "JFK"), base=midnight(DATE), date=DATE)


def with_on_time(task, n: int):
    """The task with n more flights JetBlue flew on time, outstation to outstation without a crew deadline, so its
    airline replay counts n more flights and auto-cancels none of them."""
    fill = [flight(len(task.flights) + k, "BUF", "PWM", 300 + k, 60,
                   actual=Outcome(False, False, "", 300 + k, 360 + k, "PWM", f"F{k}")) for k in range(n)]
    return replace(task, flights=(*task.flights, *fill),
                   tails=(*task.tails, *(tail(f"F{k}", "BUF", end="PWM") for k in range(n))))


def three_late_at_jfk():
    """two_late_at_jfk with a third JFK departure flown past its crew deadline, its rest ending past its deadline."""
    t = two_late_at_jfk()
    return replace(t, flights=(*t.flights, flew(4, "JFK", "MCO", 115, 210, "N5")),
                   tails=(*t.tails, tail("N5", "JFK", end="MCO")))


def one_late_at_jfk():
    t = two_late_at_jfk()
    return replace(t, date="2024-01-02", base=midnight("2024-01-02"),
                   flights=(t.flights[0], replace(t.flights[1], actual=replace(t.flights[1].actual, dep=120, arr=190)),
                            *t.flights[2:]))


def late_at_bos_turning_at_jfk():
    """JetBlue flew a BOS departure past its crew deadline (150) and the aircraft's next leg out of JFK on time, before
    that leg's crew deadline (400). Denied a reserve, the BOS crew waits out its rest (to 750) and still flies, so the
    JFK leg leaves at 840: on unlimited JFK reserves it flies, but on a JFK pool its crew rests to 1000, past its
    deadline (900), and it auto-cancels."""
    flights = [flew(0, "BOS", "JFK", 100, 200, "N1", deadline=900),
               based(flight(1, "JFK", "BUF", 300, 60, deadline=900,
                            actual=Outcome(False, False, "", 300, 360, "BUF", "N1")), 400)]
    return replace(day(flights, [tail("N1", "BOS", end="BUF")]), bases=("BOS", "JFK"), base=midnight(DATE), date=DATE)


def test_a_pool_that_loses_a_flight_only_with_another_bases_pool_lifts_the_bases_to_that_dates_calls():
    task = late_at_bos_turning_at_jfk()
    assert calibrate.least(task) == ({"BOS": 0, "JFK": 0}, {"BOS": 1, "JFK": 0})
    assert calibrate.auto(task, {"BOS": 0, "JFK": 0}) == 1 > calibrate.auto(task, {}) == 0
    pools = calibrate.pools([task])
    assert pools == {"BOS": 1, "JFK": 0} and calibrate.auto(task, pools) == 0


def test_a_bases_pool_is_the_smallest_that_keeps_the_replay_within_g1s_share_and_count_of_auto_cancels():
    """Two JFK crews JetBlue flew late need a reserve each to fly. Among 104 counted flights one auto-cancel is within
    1% and two are not, so JFK needs one reserve; among 204 two are within both, so it needs none; three late among
    305 are within 1% but over two, so it needs one."""
    small, large, three = with_on_time(two_late_at_jfk(), 100), with_on_time(two_late_at_jfk(), 200), \
        with_on_time(three_late_at_jfk(), 300)
    assert [references.airline(t).flew for t in (small, large, three)] == [104, 204, 305]
    assert [calibrate.auto(t, {"JFK": 0}) for t in (small, large, three)] == [2, 2, 3]
    assert calibrate.least(small) == ({"BOS": 0, "JFK": 1}, {"BOS": 1, "JFK": 2})
    assert calibrate.least(large) == ({"BOS": 0, "JFK": 0}, {"BOS": 1, "JFK": 2})
    assert calibrate.least(three) == ({"BOS": 0, "JFK": 1}, {"BOS": 1, "JFK": 3})
    assert calibrate.pools([large]) == {"BOS": 0, "JFK": 0}
    assert calibrate.pools([large, small]) == calibrate.pools([three]) == {"BOS": 0, "JFK": 1}
    assert calibrate.auto(small, {"JFK": 1}) == 1 and calibrate.auto(three, {"JFK": 1}) == 2


def test_two_bases_within_g1_alone_but_not_together_are_lifted_to_that_dates_calls():
    """A late JFK crew and a late BOS crew, each one whose rest ends past its deadline: among 150 counted flights
    either base alone at 0 auto-cancels one (within 1%), together two (over 1%), so the date lifts both to its calls."""
    t = with_on_time(replace(day([flew(0, "JFK", "BUF", 100, 200, "N1"), flew(1, "BOS", "BUF", 100, 200, "N2")],
                                 [tail("N1", "JFK", end="BUF"), tail("N2", "BOS", end="BUF")]),
                             bases=("BOS", "JFK"), base=midnight(DATE), date=DATE), 148)
    assert references.airline(t).flew == 150
    assert calibrate.least(t) == ({"BOS": 0, "JFK": 0}, {"BOS": 1, "JFK": 1})
    assert calibrate.auto(t, {"BOS": 0}) == calibrate.auto(t, {"JFK": 0}) == 1 < calibrate.auto(t, {"BOS": 0, "JFK": 0})
    assert calibrate.pools([t]) == {"BOS": 1, "JFK": 1}


def test_a_date_out_of_g1_even_with_unlimited_reserves_holds_the_pool_to_no_more_auto_cancels_than_that():
    """A late JFK crew and an outstation crew JetBlue flew past its crew deadline, which auto-cancels whatever the
    pool: on two flights that one is already out of G1, so the pool only has to keep the replay at it."""
    away = crewed(flight(1, "BUF", "PWM", 100, 60, deadline=300,
                         actual=Outcome(False, False, "", 200, 260, "PWM", "N2")), 150)
    t = replace(day([flew(0, "JFK", "BUF", 100, 200, "N1"), away],
                    [tail("N1", "JFK", end="BUF"), tail("N2", "BUF", end="PWM")]),
                bases=("BOS", "JFK"), base=midnight(DATE), date=DATE)
    assert references.airline(t).flew == 2 and calibrate.auto(t, {}) == 1 < calibrate.auto(t, {"JFK": 0})
    assert calibrate.least(t) == ({"BOS": 0, "JFK": 1}, {"BOS": 0, "JFK": 1})
    assert calibrate.pools([t]) == {"BOS": 0, "JFK": 1}


def test_on_a_few_flights_g1_allows_no_auto_cancel_and_a_bases_pool_is_the_largest_over_the_dates():
    two, one = two_late_at_jfk(), one_late_at_jfk()
    assert calibrate.least(two) == ({"BOS": 0, "JFK": 2}, {"BOS": 1, "JFK": 2})
    assert calibrate.least(one) == ({"BOS": 0, "JFK": 1}, {"BOS": 1, "JFK": 1})
    assert calibrate.auto(two, {"JFK": 1}) == 1 > calibrate.auto(two, {}) == 0 == calibrate.auto(two, {"BOS": 0})
    pools = calibrate.pools([two, one])
    assert pools == {"BOS": 0, "JFK": 2}
    for t in (two, one):
        assert references.airline(calibrate.pooled(t, pools)).run.flights[0].status == "flown"
        assert calibrate.auto(t, pools) == calibrate.auto(t, {})


def test_the_fit_rates_closures_on_the_train_dates_alone_and_pools_reserves_on_the_layered_train_tasks():
    """JFK pushed back 10 in the hour before a closure on the train date and 7 inside it; a closure on another date
    (2 before, 2 inside) is left out. The cap clauses stay at 0.9 and 5."""
    train, other = midnight(DATE), midnight("2024-01-05")
    spans = {"JFK": [(train + 600, train + 660), (other + 600, other + 660)], "BOS": []}
    pushed = pd.DataFrame([("JFK", float(a + 600 + k)) for a, n_before, n_in in ((train, 10, 7), (other, 2, 2))
                           for k in [*range(-60, -60 + n_before), *range(n_in)]], columns=["Origin", "act_dep"])
    empty = pd.DataFrame(columns=["Origin", "Dest", "sched_dep", "act_dep", "sched_arr", "act_arr"], dtype=float)
    tasks = [two_late_at_jfk(), one_late_at_jfk()]
    cfg = calibrate.fit([DATE, "2024-01-02"], tasks, lambda d: empty, spans, pushed)
    assert cfg.rate == {"JFK": 0.7} and (cfg.short, cfg.min_sched) == (0.9, 5)
    assert cfg.dates == ("2024-01-01", "2024-01-02")
    layered = [replace(t, layers=config.build(t, replace(cfg, reserves={}), empty, spans)) for t in tasks]
    assert layered[0].layers.closures == {"JFK": ((600, 660, 0, 0),)}
    assert cfg.reserves == calibrate.pools(layered) == {"BOS": 0, "JFK": 2}
    assert config.Config.from_dict(cfg.to_dict()) == cfg
    assert calibrate.fit(["2024-01-02"], tasks, lambda d: empty, spans, pushed).reserves == {"BOS": 0, "JFK": 1}


def scored(naive: int, date: str = "d0", anchor: int = 100, recorded: int = 120, cancels: int = 1, jetblue: int = 2,
           calm: int = 0, ferries: int = 0, proven: bool = True, flights: int = 200) -> dict:
    return {"date": date, "naive": naive, "recorded": recorded, "anchor": anchor, "proven": proven,
            "anchor_cancels": cancels, "jetblue_cancels": jetblue, "calm_cancels": calm, "calm_ferries": ferries,
            "flights": flights}


RATE = 0.01


def test_the_crew_fit_takes_the_hardest_cut_whose_guards_all_hold_ties_to_the_laxer(monkeypatch):
    """Over two dates each, d1's calm day forcing 2 cancellations and 1 ferry without crews, within the fair-weather
    bar (1% of its 200 flights): the hardest cut forces one more there, past it; the next costs the recorded day under
    the anchor on one date, the next cancels more than JetBlue; of the three left at 20% (one matching the no-crew calm
    day, one with d1 unproven, left out) the larger buffer wins."""
    rules = calibrate.grid((0, 30, 60), (4, 6))
    table = {(rules[0], 0): scored(150), (rules[0], 1): scored(150, "d1", calm=3, ferries=1),
             (rules[1], 0): scored(140), (rules[1], 1): scored(140, "d1", recorded=90),
             (rules[2], 0): scored(130, cancels=4), (rules[2], 1): scored(130, "d1"),
             (rules[3], 0): scored(120), (rules[3], 1): scored(120, "d1", calm=5, proven=False),
             (rules[4], 0): scored(120), (rules[4], 1): scored(120, "d1", calm=2, ferries=1),
             (rules[5], 0): scored(110), (rules[5], 1): scored(110, "d1"),
             ("plain", 0): scored(105), ("plain", 1): scored(105, "d1", calm=2, ferries=1)}
    monkeypatch.setattr(calibrate, "score", lambda job, time_limit: table[job])
    chosen, rows = calibrate.crew_fit({r: [(r, 0), (r, 1)] for r in rules}, [("plain", 0), ("plain", 1)], RATE)
    assert chosen == crew.Rule("v4", 60, 4) and [r["guards"] for r in rows[:6]] == [False] * 3 + [True] * 3
    assert [r["checks"] for r in rows[:3]] == [{"calm": False, "recorded": True, "cancels": True},
                                               {"calm": True, "recorded": False, "cancels": True},
                                               {"calm": True, "recorded": True, "cancels": False}]
    assert (rows[3]["proven"], rows[3]["do_nothing"]) == (1, 0.2)
    assert (rows[4]["calm_share"], rows[0]["calm_share"]) == (1.0, 0.5)
    assert rows[6]["crew"] is None and rows[6]["do_nothing"] == 0.05 and rows[6]["checks"]["calm"]
    monkeypatch.setattr(calibrate, "score", lambda job, time_limit: scored(150, calm=3 if job[0] != "plain" else 0))
    assert calibrate.crew_fit({r: [(r, 0)] for r in rules}, [("plain", 0)], RATE)[0] is None


def test_the_calm_guard_holds_a_crew_to_the_fair_weather_bar_not_to_the_no_crew_count(monkeypatch):
    """A crew may cancel more than the no-crew calm day as long as it stays within JetBlue's fair-weather bar (2 of
    200 flights at 1%), and a date whose calm day is past the bar even without crews is the base's, not the crew's:
    only a crew that pushes a date within the bar past it fails."""
    within, past = crew.Rule("v4", 0, 4), crew.Rule("v4", 30, 4)
    table = {(within, 0): scored(150, calm=2), (within, 1): scored(150, "d1", calm=9),
             (past, 0): scored(150, calm=3), (past, 1): scored(150, "d1", calm=9),
             ("plain", 0): scored(105), ("plain", 1): scored(105, "d1", calm=5)}
    monkeypatch.setattr(calibrate, "score", lambda job, time_limit: table[job])
    chosen, rows = calibrate.crew_fit({r: [(r, 0), (r, 1)] for r in (within, past)}, [("plain", 0), ("plain", 1)],
                                      RATE)
    assert chosen == within and [r["calm_share"] for r in rows[:2]] == [1.0, 0.5]
    assert [r["checks"]["calm"] for r in rows[:2]] == [True, False]


def test_a_score_is_kept_under_the_cache_by_its_tasks_signature(tmp_path, monkeypatch):
    task = chain_day(crew.Rule("v4"))
    first = calibrate.kept_score((task, 60, tmp_path))
    monkeypatch.setattr(calibrate, "score", lambda t, time_limit: {"rescored": True})
    assert calibrate.kept_score((task, 60, tmp_path)) == first and len(list(tmp_path.iterdir())) == 1
    assert calibrate.kept_score((task, 30, tmp_path)) == {"rescored": True}
    assert calibrate.kept_score((chain_day(crew.Rule()), 60, tmp_path)) == {"rescored": True}


def test_the_crew_fit_scores_a_day_by_its_references_and_samples_train_dates_by_seed():
    s = calibrate.score(chain_day(crew.Rule("v4")), 60)
    assert (s["calm_cancels"], s["calm_ferries"], s["jetblue_cancels"], s["proven"]) == (0, 0, 0, True)
    assert s["flights"] == len(chain_day(crew.Rule("v4")).flights)
    assert s["naive"] >= s["anchor"] and s["date"] == "2024-07-01"
    dates = [f"2024-07-{d:02d}" for d in range(1, 31)]
    picked = calibrate.sample(dates)
    assert len(picked) == calibrate.SAMPLE == 12 and list(picked) == sorted(set(picked)) and set(picked) <= set(dates)
    assert calibrate.sample(dates) == picked != calibrate.sample(dates, seed=1)
    assert calibrate.sample(dates[:5]) == tuple(dates[:5])
