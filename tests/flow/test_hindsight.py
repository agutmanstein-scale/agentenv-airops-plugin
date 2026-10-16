import itertools

import numpy as np
import pytest
from flow_helpers import arrival, world

from agentenv_airops.flow import hindsight, lp
from agentenv_airops.flow import world as flow_world

NB = "narrowbody 165k lb or more"


def test_hindsight_matches_brute_force_and_replays_on_its_minutes():
    flights = [arrival(0, 70, 0, pax=150), arrival(1, 70, 0, pax=40, cls="regional jet under 61 seats"),
               arrival(2, 75, 5, pax=180), arrival(3, 70, 70, unseen=True)]
    w = world(flights, [1] * 192)
    h = hindsight.solve(w)
    out = flow_world.evaluate(w, h.releases)
    assert all(out.flights[i].landing == s and out.flights[i].held == 0 for i, s in h.landing.items())
    taken = set(hindsight.exempt_only(w).values())
    assert taken == {75}
    free = [s for s in w.slots[:12] if s not in taken]
    eta = {0: 60, 1: 60, 2: 65}
    best = min(flow_world.evaluate(w, {i: s - 60 for i, s in zip((0, 1, 2), perm, strict=True)}).totals["system"]
               for perm in itertools.permutations(free, 3) if all(s >= eta[i] for i, s in enumerate(perm)))
    assert h.system == best
    assert h.landing[1] > h.landing[0]


def test_the_relaxation_bounds_hindsight_and_can_be_strictly_lower():
    big = arrival(0, 50, 0, tau=40, pax=400, cls="widebody 580k lb or more")
    small = arrival(1, 60, 50, unseen=True, pax=10, cls="turboprop under 20 seats")
    w = world([big, small], [0, 0, 0, 0] + [1] * 188)
    h = hindsight.solve(w)
    assert h.landing[0] == 75
    lb = hindsight.bound(w)
    assert lb < h.system and lb < flow_world.evaluate(w).totals["system"]
    big_first = 64 * 20 + 400 * 20 + 40 * 25 + 10 * 25
    assert lb == pytest.approx(big_first)
    flat = world([arrival(0, 70, 0), arrival(1, 70, 0)], [1] * 192)
    assert hindsight.bound(flat) == pytest.approx(hindsight.solve(flat).system)


def test_exempt_flights_land_as_if_alone_and_cancelled_ones_never_fly():
    w = world([arrival(0, 70, 0), arrival(1, 70, -5), arrival(2, 70, 0, cancelled=True)], [1] * 192)
    assert hindsight.exempt_only(w) == {1: 60}
    h = hindsight.solve(w)
    assert set(h.landing) == {0} and h.landing[0] == 75


def test_a_fractional_vertex_raises():
    assert list(lp.integral(np.array([1.0, 2.0000000001]))) == [1, 2]
    with pytest.raises(RuntimeError, match="fractional"):
        lp.integral(np.array([0.5]))
