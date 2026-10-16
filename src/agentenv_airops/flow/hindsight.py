"""Hindsight in the Command Center model: the classic ground-holding problem with the day's true capacity known,
solved exactly (H), and a relaxation that bounds every policy from below (LB).

H. The exempt flights (the unseen stream, origins outside the contiguous US, flights airborne at 00:00 local) land
first come, first served in the world's queue as if alone. Every controllable flight takes a landing minute those
leave a slot free at, no earlier than its unimpeded arrival d0 + tau, with all of its delay on the ground: at minute s
it costs its gate minutes s - (d0 + tau) at its ground rate and its passengers' arrival delay. That is a
transportation problem over minutes (a minute holds as many flights as it has free slots), totally unimodular, so the
simplex vertex is integral. Released at s - tau, the plan replays exactly in the world's queue: an exempt flight
competes only for slots at or after its own arrival, where the controllable flights arriving earlier never land, so
each exempt flight lands as when alone and each controllable flight finds its minute free; `solve` asserts both. No
flight needs a slot past the first N free ones after its arrival (N controllable flights): one of those is always
empty and no later, which bounds the columns.

LB. Every flight that flies may take any slot at or after its unimpeded arrival, a BTS flight at its ground rate (any
of them could be held at the gate), an unseen one at its air rate, or divert at the world's diversion price with no
gate minutes. A minute in the air costs at least a minute at the gate, so every policy's day in the world is a
feasible assignment and LB is at most any policy's system cost. Columns stop where diverting is cheaper.
"""

import bisect
import itertools
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from . import costs, queue
from .demand import Arrival
from .lp import Lp, integral
from .world import World, evaluate


@dataclass(frozen=True)
class Hindsight:
    releases: dict[int, int]
    landing: dict[int, int]
    system: int
    columns: int
    wall_s: float


def solve(world: World) -> Hindsight:
    t0 = time.monotonic()
    alone = exempt_only(world)
    free = Counter(world.slots) - Counter(m for m in alone.values() if m is not None)
    minutes = sorted(free)
    counts = list(itertools.accumulate(free[m] for m in minutes))
    flights = [a for a in world.arrivals if not a.cancelled and not a.exempt]
    lp, cols = Lp(), []
    for a in flights:
        mine = []
        for s in reach(minutes, counts, a.eta0, len(flights)):
            mine.append((lp.col(ground_cost(a, s)), s))
        lp.row([c for c, _ in mine], [1.0] * len(mine), 1, 1)
        cols.append(mine)
    by_minute: dict[int, list[int]] = {}
    for mine in cols:
        for c, s in mine:
            by_minute.setdefault(s, []).append(c)
    for s, cs in by_minute.items():
        lp.row(cs, [1.0] * len(cs), 0, free[s])
    x = integral(lp.solve()[0])
    landing = {a.id: next(s for c, s in mine if x[c]) for a, mine in zip(flights, cols, strict=True)}
    releases = {i: s - world.arrivals[i].tau for i, s in landing.items()}
    out = evaluate(world, releases)
    for i, s in landing.items():
        if out.flights[i].landing != s:
            raise AssertionError(f"flight {i} lands at {out.flights[i].landing}, not its hindsight minute {s}")
    for i, m in alone.items():
        if out.flights[i].landing != m:
            raise AssertionError(f"exempt flight {i} lands at {out.flights[i].landing}, not {m} as when alone")
    return Hindsight(releases, landing, out.totals["system"], len(lp.cost), round(time.monotonic() - t0, 2))


def exempt_only(world: World) -> dict[int, int | None]:
    """The exempt flights' landings (None: diverted) in the world's queue with nobody else."""
    return queue.land([(a.eta0, a.key, a.id) for a in world.arrivals if not a.cancelled and a.exempt], world.slots)


def reach(minutes: Sequence[int], counts: Sequence[int], at: int, n: int) -> list[int]:
    """The free minutes from `at` on that hold the first n free slots there (`counts` cumulative)."""
    i = bisect.bisect_left(minutes, at)
    before = counts[i - 1] if i else 0
    j = bisect.bisect_left(counts, before + n, lo=i)
    return list(minutes[i:j + 1])


def ground_cost(a: Arrival, s: int) -> int:
    return a.rates.ground * (s - a.eta0) + a.pax * max(0, s + a.taxi_in - a.sched_arr)


def bound(world: World, divert_after: int = queue.DIVERT_AFTER) -> float:
    """LB: the relaxation's optimum (passenger-minutes)."""
    have = Counter(world.slots)
    minutes = sorted(have)
    lp, by_minute = Lp(), {}
    for a in world.arrivals:
        if a.cancelled:
            continue
        divert = diversion_cost(a, divert_after)
        mine = [lp.col(divert)]
        for s in minutes[bisect.bisect_left(minutes, a.eta0):]:
            c = relaxed_cost(a, s)
            if c >= divert:
                break
            col = lp.col(c)
            mine.append(col)
            by_minute.setdefault(s, []).append(col)
        lp.row(mine, [1.0] * len(mine), 1, 1)
    for s, cs in by_minute.items():
        lp.row(cs, [1.0] * len(cs), 0, have[s])
    return lp.solve()[1]


def relaxed_cost(a: Arrival, s: int) -> int:
    rate = a.rates.air if a.unseen else a.rates.ground
    return rate * (s - a.eta0) + a.pax * max(0, s + a.taxi_in - a.sched_arr)


def diversion_cost(a: Arrival, divert_after: int) -> int:
    return a.rates.air * (divert_after + costs.DIVERSION_BLOCK) + a.pax * costs.DIVERSION_PAX
