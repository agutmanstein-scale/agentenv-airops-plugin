import math
from dataclasses import replace

from program_helpers import adv

from agentenv_airops.build import pack
from agentenv_airops.programs import unseen
from agentenv_airops.programs.advisories import Run
from agentenv_airops.programs.model import Arrival

PROGRAM = adv("g", "gdp", 400, 600, 779, (10, 10, 10))
DEMAND = tuple(Arrival("AA", k, "ATL", 450 + 7 * k, 600 + 7 * k, None, False, 0, None, None) for k in range(26))


def covers(scope, hub, origin):
    return True


def test_the_in_sample_rate_reproduces_the_stated_average_delay():
    run = Run(PROGRAM, 400, 780)
    delays = [unseen.average_delay(run, DEMAND, covers, e) for e in range(12)]
    assert delays == sorted(delays) and delays[0] < delays[-1]
    fit = unseen.in_sample(replace(PROGRAM, avg_delay=round(delays[4])), DEMAND, covers)
    assert (fit.e, fit.matched) == (4.0, True)
    miss = unseen.in_sample(replace(PROGRAM, avg_delay=10_000), DEMAND, covers)
    assert not miss.matched


def test_an_unmatched_advisory_takes_its_airports_median():
    fits = [unseen.Fit("a", "JFK", 20.0, 100, 100, True), unseen.Fit("b", "JFK", 26.0, 90, 95, True),
            unseen.Fit("c", "JFK", 60.0, 150, 400, False), unseen.Fit("d", "BOS", 12.0, 40, 41, True)]
    e, median = unseen.table(fits)
    assert median == {"BOS": 12.0, "JFK": 23.0} and e == {"a": 20.0, "b": 26.0, "c": 23.0, "d": 12.0}


def test_fidelity_grows_with_the_scale_and_the_fit_picks_from_the_grid(task):
    ratios = [unseen.fidelity(task, s) for s in (0.0, 0.5, 1.0, 2.0)]
    assert ratios == sorted(ratios)
    modelled, observed, flights = unseen.program_delay(task)
    assert flights > 0 and observed > 0
    scale = unseen.fit_scale([task])
    loss = {s: math.log(max(unseen.fidelity(task, s), unseen.FLOOR)) ** 2 for s in unseen.GRID}
    assert scale == {"JFK": min(unseen.GRID, key=lambda s: (round(loss[s], 9), s))}


def test_the_airports_own_counts_set_what_bts_misses():
    jfk = unseen.missed_share("JFK", 317.0, [], {})
    assert jfk == {"counted_per_day": 640.1, "bts_per_day": 317.0, "missed_share": 0.505}
    bos = unseen.missed_share("BOS", 400.0, ["2024-07"], {"2024-07": 31})
    assert bos["counted_per_day"] == round(19_130 / 31, 1)
    assert sorted(unseen.MASSPORT_ARRIVALS) == sorted(pack.MONTHS)
