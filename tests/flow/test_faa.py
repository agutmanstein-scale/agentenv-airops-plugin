from dataclasses import replace

from flow_helpers import arrival, world

from agentenv_airops.flow import faa
from agentenv_airops.programs.advisories import Advisory, Run, Scope, Stop

EVERY = Scope(True, frozenset(), None, frozenset(), frozenset(), False)


def gdp(id: str, start: int, end: int, rates: tuple, scope: Scope = EVERY) -> Advisory:
    return Advisory(id, "JFK", "gdp", -100, start, end, rates, None, None, None, "", scope, None, None, False)


def flights() -> list:
    return [arrival(k, 70, 0) for k in range(3)] + [arrival(3, 70, 65, unseen=True)]


def test_exempt_flights_take_slots_first_and_the_rest_are_rationed_by_schedule():
    w = replace(world(flights(), [4] * 192), runs=(Run(gdp("a", 60, 179, (4, 4)), -100, 1000),))
    assert faa.replay(w) == {0: 0, 1: 30, 2: 45}


def test_a_revision_rechecks_waiting_flights_and_exempts_the_ones_already_off():
    first, second = Run(gdp("a", 60, 179, (4, 4)), -100, 10), Run(gdp("b", 60, 179, (2, 2)), 10, 1000)
    w = replace(world(flights(), [4] * 192), runs=(first, second))
    assert faa.replay(w) == {0: 0, 1: 60, 2: 90}


def test_a_stop_holds_departures_and_an_out_of_scope_origin_is_never_held():
    stop = Stop("gs", "JFK", 0, 40, EVERY)
    w = replace(world(flights()[:1], [4] * 192), stops=(stop,))
    assert faa.replay(w) == {0: 40}
    east = Scope(False, frozenset({"ZNY"}), None, frozenset(), frozenset(), False)
    w = replace(world(flights(), [4] * 192), runs=(Run(gdp("a", 60, 179, (1, 1), east), -100, 1000),))
    assert faa.replay(w) == {0: 0, 1: 0, 2: 0}


def test_the_list_spills_past_the_period_and_compresses_for_a_cancellation():
    w = replace(world(flights()[:3], [4] * 192), runs=(Run(gdp("a", 60, 119, (1,)), -100, 1000),))
    assert faa.replay(w) == {0: 0, 1: 60, 2: 120}
    cancelled = arrival(3, 60, 0, cancelled=True)
    w = replace(world(flights()[:3] + [cancelled], [4] * 192), runs=(Run(gdp("a", 60, 119, (1,)), -100, 1000),))
    assert faa.replay(w) == {0: 0, 1: 60, 2: 120}
    assert faa.replay(w, capacity=True) == {0: 0, 1: 3, 2: 7}
