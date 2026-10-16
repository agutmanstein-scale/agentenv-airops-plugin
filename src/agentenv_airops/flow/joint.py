"""The joint hindsight, C3's floor (P11): one MIP over JetBlue's day and every other arrival at the task hub, the
day's true capacity known.

It is step 0's network (mip.Network) on the truth with the task hub's programs and nominal cap gone and legs into the
task hub at their unimpeded block, joined to the classic ground-holding columns of every other controllable arrival
(hindsight.py): exempt flights land first come, first served as if alone, and every remaining slot minute is a row
that JetBlue's copies and the others' columns share. A copy of a leg into the task hub leaving at d reaches the queue
at d + taxi-out + tau and must land there, on a minute with a free slot; any other copy of it is fixed at zero. Its
objective is step 0's cost of JetBlue's day (cancellations priced at the first watch, as the anchor's) plus the
others' gate minutes and passenger delay; the exempt flights' cost is a constant. Proven to GAP within the time
limit, or the day is a C3 fail with its dual bound reported; the plan replays in the co-simulation's truth (`exact`).
"""

import itertools
from collections import Counter, defaultdict

from ..core.sim import FLOWN
from ..programs import mip, rbs, sim
from ..refs.mip import GAP
from . import hindsight, queue
from .desk import Link, joint, other, truth

INF = float("inf")


def solve(lk: Link, time_limit: float = 3600.0) -> dict:
    task, w = lk.task, lk.world
    o = other(task)
    p = task.programs[o]
    block = {f.id: lk.unimpeded.get(f.id, f.block) for f in task.flights}
    world = sim.World(task.lb, block, {task.hub: (), o: p.segments}, p.stops, {o: rbs.nominal(task, o)})
    net = mip.Network(task, world)
    m = net.m
    exempt = [a for a in w.arrivals if not a.cancelled and a.exempt and a.id not in lk.jet]
    alone = queue.land([(a.eta0, a.key, a.id) for a in exempt], w.slots)
    free = Counter(w.slots) - Counter(s for s in alone.values() if s is not None)
    rows: dict[int, list[int]] = defaultdict(list)
    for (i, d), c in net.xs.items():
        if i in lk.arrival:
            a = w.arrivals[lk.arrival[i]]
            s = d + a.taxi_out + a.tau
            if free[s] > 0:
                rows[s].append(c)
            else:
                m.ub[c] = 0
    others = [a for a in w.arrivals if not a.cancelled and not a.exempt and a.id not in lk.jet]
    minutes = sorted(free)
    counts = list(itertools.accumulate(free[s] for s in minutes))
    n = len(others) + len(lk.arrival)
    cols = {}
    for a in others:
        mine = [(m.col(hindsight.ground_cost(a, s), 1, True), s)
                for s in hindsight.reach(minutes, counts, a.eta0, n)]
        m.row({c: 1 for c, _ in mine}, 1, 1)
        for c, s in mine:
            rows[s].append(c)
        cols[a.id] = mine
    for s, cs in rows.items():
        m.row(dict.fromkeys(cs, 1), -INF, free[s])
    status, objective, bound, gap, wall, x = m.run(time_limit, GAP, 1)
    proven = status == "Optimal" and gap <= GAP
    out = {"status": status, "objective": round(objective), "bound": round(bound, 1), "gap_pct": round(100 * gap, 4),
           "wall_s": round(wall, 1), "proven": proven, "cols": len(m.cost), "rows": len(m.rows)}
    deps = {}
    for f in task.flights:
        flown = [d for d, _ in net.copies[f.id] if x[net.xs[(f.id, d)]] > 0.5]
        deps[f.id] = flown[0] if flown else None
    release = {a.id: next(s for c, s in cols[a.id] if x[c] > 0.5) - a.tau for a in others}
    release |= {a.id: a.d0 for a in w.arrivals if a.id in alone and not a.unseen}
    plan = {i: {"cancel": True, "decided": task.start} if d is None else {"not_before": d} for i, d in deps.items()}
    day = truth(lk, plan, (), release)
    got = joint(lk, day, release)
    fixed_cost = got["joint"] - round(objective)
    exact = (all(day.run.flights[i].dep == d for i, d in deps.items() if d is not None)
             and all(day.run.flights[i].status != FLOWN for i, d in deps.items() if d is None)
             and got["jetblue_held_minutes"] == 0
             and all(day.landing[i] == release[i] + w.arrivals[i].tau for i in cols))
    return {**out, "joint": got["joint"], "exempt_cost": fixed_cost, "exact": exact, "detail": got,
            "lower_bound": round(bound) + fixed_cost}
