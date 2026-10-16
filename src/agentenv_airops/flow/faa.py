"""The FAA's programs at the hub as they ran, replayed in the Command Center model's world (P4), and the split row (E3):
the same timing and scope with the world's own capacity as the rates.

Each binding GDP advisory's run (advisories.as_ran) is rationed by schedule in signature order. Its list holds every
BTS arrival scheduled inside its period, less each recorded cancellation that leaves it (NOTICE before its scheduled
departure) before the run ends: compression. At the signature a flight is exempt when the replay already has it off
the ground or the program's scope does not cover its origin; the unseen stream is always exempt. Exempt flights take
the first free slot at or after their arrival as the FAA knows it (state.eta), in arrival order, then the rest in
schedule order (Arrival.key) the first free slot at or after theirs. Slots are the advisory's, h:00 + floor(60j/r)
inside the period and on at the last hour's rate past it, as FSM's list runs on; the split row rations the world's
own landing slots from the period's start instead. A flight leaves at the first minute from its d0 at which no stop
piece covering its origin holds it and the run governing it then (its origin in scope, its scheduled arrival in the
period) has its EDCT, the slot less tau, at or before that minute: a revision signed while it waits re-checks it, and
the end of the run in force releases it. Only the binding programs replay, the ones whose NAS delay d0 takes out.
"""

import bisect
from collections.abc import Callable, Mapping, Sequence

from ..programs import rbs
from ..programs.advisories import Run, Scope
from . import state
from .demand import Arrival
from .world import World

Covers = Callable[[Scope, Arrival], bool]


def replay(world: World, capacity: bool = False) -> dict[int, int]:
    """Each flying BTS arrival's wheels-off under the hub's programs as they ran (`capacity`: at the world's rates)."""
    runs = sorted(world.runs, key=lambda r: (r.since, r.adv.id))
    flights = [a for a in world.arrivals if not a.unseen and not a.cancelled]
    stops = {a.id: sorted((p.start, p.end) for p in world.stops if covers(p.scope, a)) for a in flights}
    release = {a.id: depart(a, [], [], stops[a.id]) for a in flights}
    slots: list[dict[int, int]] = []
    for k, run in enumerate(runs):
        slots.append(ration(world, run, release, capacity))
        for a in flights:
            if release[a.id] >= run.since:
                release[a.id] = depart(a, runs[:k + 1], slots, stops[a.id])
    return release


def covers(scope: Scope, a: Arrival) -> bool:
    return scope.covers(a.orig, a.artcc, a.nm)


def ration(world: World, run: Run, release: Mapping[int, int], capacity: bool) -> dict[int, int]:
    """The slot of every row of the run's list at its signature."""
    adv, t = run.adv, run.since
    gone = {i: r for i, r in release.items() if r < t}
    rows = [a for a in world.arrivals if adv.start <= a.sched_arr <= adv.end
            and not (a.cancelled and a.leaves <= run.until - 1)]
    out = [a for a in rows if a.unseen or a.id in gone or not covers(adv.scope, a)]
    first = sorted((state.eta(a, t, gone), a.key, a.id) for a in out)
    held = {a.id for a in out}
    ranked = [(state.eta(a, t, gone), a.id) for a in sorted((a for a in rows if a.id not in held), key=lambda a: a.key)]
    if capacity:
        offered = list(world.slots[bisect.bisect_left(world.slots, adv.start):])
    else:
        last = max([adv.end] + [e for e, _, _ in first] + [e for e, _ in ranked])
        spill = len(rows) + (last - adv.end) * adv.rates[-1] // 60 + 1
        offered = rbs.slots(adv.start, adv.end, adv.rates)
        offered += [adv.end + 1 + 60 * k // adv.rates[-1] for k in range(spill)]
    return rbs.ration([(e, i) for e, _, i in first], ranked, offered)


def depart(a: Arrival, runs: Sequence[Run], slots: Sequence[Mapping[int, int]],
           stops: Sequence[tuple[int, int]]) -> int:
    t = a.d0
    while True:
        t = held(stops, t)
        k = governing(runs, a, t)
        if k is None:
            return t
        edct = slots[k][a.id] - a.tau
        if edct <= t:
            return t
        t = min(edct, runs[k].until)


def held(stops: Sequence[tuple[int, int]], t: int) -> int:
    for lo, hi in stops:
        if lo <= t < hi:
            t = hi
    return t


def governing(runs: Sequence[Run], a: Arrival, t: int) -> int | None:
    for k, r in enumerate(runs):
        if r.since <= t < r.until:
            adv = r.adv
            return k if adv.start <= a.sched_arr <= adv.end and covers(adv.scope, a) else None
    return None
