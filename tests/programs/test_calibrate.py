from dataclasses import replace

import pandas as pd
from day_helpers import based, day, flight, tail

from agentenv_airops.programs import references
from agentenv_airops.programs.candidates import midnight
from agentenv_airops.programs.layers import calibrate, config
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


def one_late_at_jfk():
    t = two_late_at_jfk()
    return replace(t, date="2024-01-02", base=midnight("2024-01-02"),
                   flights=(t.flights[0], replace(t.flights[1], actual=replace(t.flights[1].actual, dep=120, arr=190)),
                            *t.flights[2:]))


def test_a_bases_pool_is_the_smallest_that_keeps_every_dates_replay_auto_cancels_and_the_largest_over_dates():
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
    assert calibrate.fit([DATE], tasks, lambda d: empty, spans, pushed).reserves == {"BOS": 0, "JFK": 2}
