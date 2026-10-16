"""Hourly ground delay programs in the Command Center model (P5): at every hour from 00:00 local the flow manager
re-solves the stochastic ground-holding problem of Ball, Hoffman, Odoni and Rifkin (2003) on what it knows then and
rations by schedule into its planned arrivals, every open flight's EDCT issued afresh (compression). Reactive and
TAF-based differ only in the capacity scenarios they hand it (forecast.py); neither issues ground stops.

The LP's periods are minutes, the world's queue's own resolution, from the decision to DRAIN bins past the last
arrival it knows of: a scenario's landings per 15-minute bin become slots at the minutes queue.slots gives them, so a
plan lands where the runway can take it (15-minute periods leave slots empty in the world). X_m is the open flights'
planned arrivals, G_m those still on the ground, W_qm the airborne queue in scenario q: G_m = G_{m-1} + D_m - X_m with
G zero at the horizon, W_qm >= W_q,m-1 + X_m + E_m - S_qm from the airborne queue now (state.airborne), all
non-negative; it minimises c_g sum G + sum_q p_q c_a sum W. D_m counts the open flights (BTS arrivals from the
contiguous US still on the ground and on the FAA's list) at their arrival as known now (state.eta), E_m every other
arrival to come (flights off the ground, the unseen stream, origins outside the contiguous US), S_qm the scenario's
slots; c_g and c_a are the open flights' mean gate and air rates plus their passengers, with no diversion term. The
constraints are the dual of a network, so the simplex vertex is integral (asserted).

Ration by schedule: the planned arrivals are the program's slots; in key order each open flight takes the first one
at or after its known arrival (none left: its known arrival). A flight planned later than its known arrival gets an
EDCT, the slot less tau; the rest are released and leave at their d0, never before the decision. The program's flights
are those held more than HOLD_MIN minutes (a bin; shorter holds only meet the slot spacing) and its issued end the
last of their planned arrivals; the honest expected end weighs the end of each scenario's own deterministic plan,
rationed the same way (the decision itself when it holds nobody that long), by its probability.
"""

import bisect
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from . import queue, state
from .forecast import Forecast, Scenarios
from .lp import Lp, integral
from .world import BINS, World

STEP = 60
DRAIN = 32
HOLD_MIN = 15


@dataclass(frozen=True)
class Issue:
    at: int
    edct: dict[int, int]
    released: frozenset[int]
    planned: dict[int, int]
    program: frozenset[int]
    end: int | None
    honest: float | None
    airborne: int
    scenarios: int


def run(world: World, forecast: Forecast, honest: bool = False) -> tuple[dict[int, int], list[Issue]]:
    """Every flying BTS arrival's wheels-off under hourly programs on `forecast`, and the issuances."""
    flying = [a for a in world.arrivals if not a.unseen and not a.cancelled]
    edct: dict[int, int] = {}
    issues = []
    t = 0
    while True:
        gone = {a.id: r for a in flying if (r := max(a.d0, edct.get(a.id, a.d0))) < t}
        dropped = {a.id for a in world.arrivals if a.cancelled and a.leaves <= t}
        got = issue(world, t, gone, dropped, forecast, honest)
        if got is None:
            break
        issues.append(got)
        edct |= got.edct | dict.fromkeys(got.released, t)
        t += STEP
    return {a.id: max(a.d0, edct.get(a.id, a.d0)) for a in flying}, issues


def issue(world: World, t: int, gone: Mapping[int, int], dropped: Collection[int], forecast: Forecast,
          honest: bool = False) -> Issue | None:
    """The program issued at minute t, None when no flight is open. `gone` holds the wheels-off of flights that left,
    `dropped` the flights off the FAA's list."""
    flights = [a for a in world.arrivals if a.id not in dropped]
    open_ = [a for a in flights if not a.unseen and not a.noncontiguous and a.id not in gone]
    if not open_:
        return None
    known = {a.id: state.eta(a, t, gone) for a in flights}
    ids = {a.id for a in open_}
    k0 = t // 15
    k_end = min(BINS - 1, max(known.values()) // 15 + DRAIN)
    m0, n = 15 * k0, 15 * (k_end - k0 + 1)
    demand, exempt = [0] * n, [0] * n
    for a in flights:
        e = known[a.id]
        if a.id in ids:
            demand[min(e - m0, n - 1)] += 1
        elif e >= t:
            exempt[min(e - m0, n - 1)] += 1
    air = state.airborne(world, gone, t)
    c_g = sum(a.rates.ground + a.pax for a in open_) / len(open_)
    c_a = sum(a.rates.air + a.pax for a in open_) / len(open_)
    scenarios = forecast(world, t, k_end)
    planned = ration(open_, known, solve(demand, exempt, air, scenarios, c_g, c_a), m0)
    program = frozenset(i for i in ids if planned[i] - known[i] > HOLD_MIN)
    end = max((planned[i] for i in program), default=None)
    expected = None
    if honest and end is not None:
        expected = 0.0
        for p, caps in scenarios:
            mine = ration(open_, known, solve(demand, exempt, air, [(1.0, caps)], c_g, c_a), m0)
            expected += p * max((mine[i] for i in ids if mine[i] - known[i] > HOLD_MIN), default=t)
    edct = {a.id: planned[a.id] - a.tau for a in open_ if planned[a.id] > known[a.id]}
    return Issue(t, edct, frozenset(ids - edct.keys()), planned, program, end, expected, air, len(scenarios))


def solve(demand: list[int], exempt: list[int], air: int, scenarios: Scenarios, c_g: float, c_a: float) -> list[int]:
    """The stochastic ground-holding LP over minutes: the planned arrivals X per minute."""
    n = len(demand)
    lp = Lp()
    x = [lp.col(0.0) for _ in range(n)]
    g = [lp.col(c_g, 0.0 if m == n - 1 else float("inf")) for m in range(n)]
    for m in range(n):
        cols, coefs = [g[m], x[m]], [1.0, 1.0]
        if m:
            cols.append(g[m - 1])
            coefs.append(-1.0)
        lp.row(cols, coefs, demand[m], demand[m])
    for p, caps in scenarios:
        slots = [0] * n
        for s in queue.slots(caps):
            slots[s] += 1
        w = [lp.col(p * c_a) for _ in range(n)]
        for m in range(n):
            cols, coefs = [w[m], x[m]], [1.0, -1.0]
            if m:
                cols.append(w[m - 1])
                coefs.append(-1.0)
            lp.row(cols, coefs, exempt[m] - slots[m] + (air if m == 0 else 0), float("inf"))
    values = integral(lp.solve()[0])
    return [int(values[c]) for c in x]


def ration(open_: list, known: Mapping[int, int], x: list[int], m0: int) -> dict[int, int]:
    """Each open flight's planned arrival: in key order, the first planned slot at or after its known arrival."""
    free = [m0 + m for m, v in enumerate(x) for _ in range(v)]
    out = {}
    for a in sorted(open_, key=lambda a: a.key):
        e = known[a.id]
        i = bisect.bisect_left(free, e)
        out[a.id] = free.pop(i) if i < len(free) else e
    return out
