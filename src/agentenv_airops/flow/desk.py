"""JetBlue's desk against the Command Center model's TAF-based programs (C3, P9-P12): step 0's task and follower,
played hour by hour inside the world's queue.

The task (P12) is step 0's, rebuilt on main for the day-hub with no FAA-fitted parameter: scale 1, each advisory's
unseen arrivals per hour and the airport's median taken from the world's unseen stream (`tasks`), early 0, the 03:00
cutoff. At the task hub the FAA's real programs give way to the policy's issuances; the other hub's run as issued.
JetBlue's task flights into the task hub are the world's own arrivals (`link`), flying whenever the desk flies them,
with those scheduled past midnight added; each reaches the queue tau after wheels-off (its departure plus taxi-out)
and its gate taxi-in after landing, and never diverts (step 0's day has no diversion). Every other flight is the
world's, released at its EDCT or d0.

An issuance with a program is one of step 0's segments: its period the scheduled arrivals of the program's flights,
governing departures for the hour until the next issuance; JetBlue's pool is the planned arrivals of its open task
flights in the period, each moved to the desk's convention (slot - tau - taxi-out + scheduled block) so its holder
lands on it, scope the contiguous US. The truth for JetBlue is step 0's sim with those segments as they ran and, for
legs into the task hub, the unimpeded block plus the queue's holding, iterated to a fixed point (at most ROUNDS
rounds; non-convergence is reported). The desk at a watch plans on step 0's day as known (delays LOOK ahead, the
other hub's programs as issued) with the task hub's issuances so far, the latest running to its issued end, or with
channel (b) to the policy's honest expected end, and legs into the task hub at their unimpeded block.

Every hour t (P9): the truth to t; the FAA's issuance on what it knows, JetBlue's cancellations reported when decided
(channel a) or at their scheduled departure, never before they are decided (other carriers keep X2); then the desk's
watch at t, a port of references.follow with its worlds as arguments (the same MIP, FREEZE, canonical solves and
deliberate holds). Joint cost (P10): the world's system cost of every arrival but JetBlue's task flights, plus step
0's cost of the task flights.
"""

import bisect
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from ..core.known import FREEZE, LOOK
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN
from ..programs import advisories, candidates, mip, rbs, references, sim
from ..programs.advisories import GDP, HUBS, Advisory, Scope
from ..programs.model import Segment, Task
from ..programs.repairs import CUTOFF
from . import costs, demand, policies, queue, sources
from .demand import Arrival, Medians
from .forecast import Forecast
from .policies import Issue
from .world import World

ROUNDS = 20
HOURS = 48
NEVER = 10**6
CONTIGUOUS = Scope(True, frozenset(), None, frozenset(), frozenset(), True)
FAMILY = {"A320": "narrowbody 165k lb or more", "A321": "narrowbody 165k lb or more",
          "A321P": "narrowbody 165k lb or more", "A220": "narrowbody under 165k lb", "E190": "narrowbody under 165k lb"}


class Timeout(RuntimeError):
    """A desk watch solve that was not proven in its time limit."""


def tasks(inputs, derived, d: str, hub: str) -> Task:
    """Step 0's task of the day-hub on main, each advisory's unseen arrivals per hour (and each airport's median hour,
    06:00-24:00) counted in the world's unseen stream of its hub (P12)."""
    e_fit, e_median = {}, {}
    for h in HUBS:
        unseen = unseen_arrivals(inputs, derived, d, h)
        e_median[h] = median_rate(unseen)
        for a in advisories.load(inputs, h, candidates.midnight(d), 2 * 1440):
            if a.start is not None and a.end is not None:
                e_fit[a.id] = rate(unseen, a.start, a.end, e_median[h])
    built = candidates.build_candidate(d, hub, inputs, derived, e_fit, e_median, dict.fromkeys(HUBS, 1.0), CUTOFF)
    if built.task is None:
        raise ValueError(f"{d} {hub}: no task ({built.row['reason']})")
    return built.task


def unseen_arrivals(inputs, derived, d: str, hub: str) -> list[int]:
    """The scheduled arrivals of the world's unseen stream at the hub (demand.unseen's spread). JFK's 2025 months, which
    the ATR tables don't reach, count the nearest year's same month (D2's rule for 2022-12)."""
    x = demand.frame(inputs, derived, d)
    at = x[(x.Dest == hub) & (x.sa >= 0) & (x.sa < demand.DAY)]
    month = f"2024-{d[5:7]}" if hub == "JFK" and d.startswith("2025") else d[:7]
    per_day = max(0.0, sources.counted_per_day(hub, month) - demand.flown_per_day(inputs, derived, hub, d[:7]))
    rows = [{"sched_arr": int(s), "cls": costs.CLASSES[0], "pax": 0} for s in at.sa]
    return [r["sched_arr"] for r in demand.unseen(rows, per_day, 0)]


def rate(unseen: Sequence[int], start: int, end: int, fallback: float) -> float:
    lo, hi = max(start, 0), min(end, 1439)
    if hi < lo:
        return fallback
    return sum(lo <= m <= hi for m in unseen) * 60 / (hi - lo + 1)


def median_rate(unseen: Sequence[int]) -> float:
    per = [sum(60 * h <= m < 60 * h + 60 for m in unseen) for h in range(6, 24)]
    return float(sorted(per)[len(per) // 2])


@dataclass(frozen=True)
class Link:
    world: World
    task: Task
    arrival: dict[int, int]
    unimpeded: dict[int, int]

    @property
    def jet(self) -> frozenset[int]:
        return frozenset(self.arrival.values())


def link(world: World, task: Task, med: Medians) -> Link:
    """The world with JetBlue's task arrivals flying when the desk flies them, and the task with the task hub's own
    programs replaced by hourly slots for the policy's issuances."""
    hub = task.hub
    by_key = {(a.carrier, a.number, a.orig, a.sched_dep): a for a in world.arrivals if not a.unseen}
    arrivals = list(world.arrivals)
    found = {}
    for f in task.flights:
        if f.dest != hub:
            continue
        a = by_key.get((task.carrier, f.number, f.orig, f.sched_dep))
        if a is None:
            a = extra(len(arrivals), task, f, med, world.date)
            arrivals.append(a)
        else:
            a = replace(a, cancelled=False)
            arrivals[a.id] = a
        found[f.id] = a.id
    p = task.programs[hub]
    advs = tuple(issued_as(hub, h) for h in range(HOURS))
    task = replace(task, programs={**task.programs, hub: replace(p, advisories=advs, segments=(), stops=())})
    w = replace(world, arrivals=tuple(arrivals))
    return Link(w, task, found, {i: w.arrivals[j].taxi_out + w.arrivals[j].tau + w.arrivals[j].taxi_in
                                 for i, j in found.items()})


def extra(i: int, task: Task, f, med: Medians, d: str) -> Arrival:
    nm = demand.distance(f.orig, task.hub)
    out = med.out(f.orig)
    return Arrival(i, task.carrier, f.number, f.orig, task.artcc.get(f.orig), round(nm, 1), f.sched_dep, f.sched_arr,
                   FAMILY[f.family], float(f.pax), f.pax, False, False, f.sched_dep + f.lb + out,
                   med.tau(f.orig, task.hub, candidates.season(d), nm), out, med.taxi(task.carrier, task.hub), False,
                   None, None, None, False, 0, "task flight past midnight")


def issued_as(hub: str, hour: int) -> Advisory:
    return Advisory(f"faa-{hour:02d}", hub, GDP, 60 * hour, None, None, (), None, None, None, "CONTIGUOUS US",
                    CONTIGUOUS, None, None, False)


def segment(lk: Link, iss: Issue, until: int) -> Segment | None:
    """The issuance as one of step 0's segments, governing departures from its decision to `until`."""
    if iss.end is None:
        return None
    w = lk.world
    held = [w.arrivals[i].sched_arr for i in iss.program]
    start, end = min(held), max(held)
    pool = []
    for i, j in lk.arrival.items():
        f, a = lk.task.flight(i), w.arrivals[j]
        if j in iss.planned and start <= f.sched_arr <= end and not a.noncontiguous:
            pool.append((iss.planned[j] - a.tau - a.taxi_out + f.sched_block, i))
    pool.sort()
    return Segment(f"faa-{iss.at // 60:02d}", lk.task.hub, iss.at, until, start, max([end] + [s for s, _ in pool]),
                   0.0, tuple(s for s, _ in pool), tuple(i for _, i in pool))


def as_ran(lk: Link, issues: Sequence[Issue]) -> tuple[Segment, ...]:
    out = (segment(lk, iss, iss.at + policies.STEP) for iss in issues)
    return tuple(s for s in out if s is not None)


def as_known(lk: Link, issues: Sequence[Issue], t: int, honest: bool) -> tuple[Segment, ...]:
    """The issuances by t as the desk reads them: each until the next, the latest to its issued end, or with
    `honest` to the policy's honest expected end, never before t."""
    out = []
    for k, iss in enumerate(issues):
        if k + 1 < len(issues):
            until = issues[k + 1].at
        elif iss.end is None:
            until = iss.at
        else:
            until = max(t, round(iss.honest if honest else iss.end)) + 1
        s = segment(lk, iss, until)
        if s is not None:
            out.append(s)
    return tuple(out)


def other(task: Task) -> str:
    return next(h for h in HUBS if h != task.hub)


@dataclass(frozen=True)
class Day:
    run: sim.Execution
    landing: dict[int, int | None]
    wheels: dict[int, int]
    rounds: int
    converged: bool


def truth(lk: Link, plan: Mapping, segments: Sequence[Segment], release: Mapping[int, int]) -> Day:
    """The day on `plan` with the task hub's segments as they ran and every other flight at its release."""
    task, w = lk.task, lk.world
    o = other(task)
    p = task.programs[o]
    nominal = {o: rbs.nominal(task, o)}
    block = {f.id: lk.unimpeded.get(f.id, f.block) for f in task.flights}
    fixed = [(r + w.arrivals[i].tau, w.arrivals[i].key, i) for i, r in release.items()]
    fixed += [(a.eta0, a.key, a.id) for a in w.arrivals if a.unseen]
    for k in range(1, ROUNDS + 1):
        world = sim.World(task.lb, block, {task.hub: tuple(segments), o: p.segments}, p.stops, nominal)
        run = sim.execute(task, plan, world)
        wheels = {i: r.dep + w.arrivals[lk.arrival[i]].taxi_out for i, r in run.flights.items()
                  if i in lk.arrival and r.status == FLOWN}
        mine = [(d + w.arrivals[lk.arrival[i]].tau, w.arrivals[lk.arrival[i]].key, lk.arrival[i])
                for i, d in wheels.items()]
        landing = land(fixed + mine, w.slots, lk.jet)
        new = dict(block)
        for i in wheels:
            new[i] = landing[lk.arrival[i]] + w.arrivals[lk.arrival[i]].taxi_in - run.flights[i].dep
        if new == block:
            return Day(run, landing, wheels, k, True)
        block = new
    return Day(run, landing, wheels, ROUNDS, False)


def land(arrivals: Iterable[tuple[int, tuple, int]], offered: Sequence[int],
         never: frozenset[int]) -> dict[int, int | None]:
    """queue.land, with the flights in `never` holding until they land instead of diverting."""
    offered = list(offered)
    nxt = list(range(len(offered) + 1))

    def free(i: int) -> int:
        root = i
        while nxt[root] != root:
            root = nxt[root]
        while nxt[i] != root:
            nxt[i], i = root, nxt[i]
        return root

    out: dict[int, int | None] = {}
    for eta, _, i in sorted(arrivals, key=lambda a: (a[0], a[1])):
        k = free(bisect.bisect_left(offered, eta))
        if k == len(offered):
            raise ValueError("the queue outlasts the slots offered")
        if offered[k] - eta > queue.DIVERT_AFTER and i not in never:
            out[i] = None
        else:
            out[i] = offered[k]
            nxt[k] = k + 1
    return out


def joint(lk: Link, day: Day, release: Mapping[int, int]) -> dict:
    """The joint cost (P10): the world's system cost of every arrival but JetBlue's task flights, and step 0's cost of
    the task flights, with the diversions and held minutes of the others."""
    w = lk.world
    parts = {"ground": 0, "air": 0, "pax": 0}
    diverted = held = 0
    for a in w.arrivals:
        if a.cancelled or a.id in lk.jet:
            continue
        r = a.d0 if a.unseen else release[a.id]
        at = day.landing[a.id]
        eta = r + a.tau
        hold = queue.DIVERT_AFTER if at is None else at - eta
        parts["ground"] += a.rates.ground * (r - a.d0)
        if at is None:
            parts["air"] += a.rates.air * (hold + costs.DIVERSION_BLOCK)
            parts["pax"] += a.pax * costs.DIVERSION_PAX
            diverted += 1
        else:
            parts["air"] += a.rates.air * hold
            parts["pax"] += a.pax * max(0, at + a.taxi_in - a.sched_arr)
        held += hold
    others = sum(parts.values())
    jb = day.run.cost
    jet_held = sum(day.landing[lk.arrival[i]] - d - w.arrivals[lk.arrival[i]].tau for i, d in day.wheels.items())
    return {"joint": others + jb.total, "others": others, "others_parts": parts, "jetblue": jb.total,
            "jetblue_parts": jb.to_dict(), "diversions": diverted, "held_minutes": held,
            "jetblue_held_minutes": jet_held, **day.run.counts, "rounds": day.rounds, "converged": day.converged}


def releases(lk: Link, edct: Mapping[int, int]) -> dict[int, int]:
    return {a.id: max(a.d0, edct.get(a.id, a.d0)) for a in lk.world.arrivals
            if not a.unseen and not a.cancelled and a.id not in lk.jet}


def naive(lk: Link) -> dict:
    """Joint naive: no program at the task hub, the desk's empty plan."""
    rel = releases(lk, {})
    return joint(lk, truth(lk, {}, (), rel), rel)


@dataclass
class Played:
    result: dict
    issues: list[Issue]
    plan: dict
    watches: list[dict]


def play(lk: Link, forecast: Forecast, report: bool, honest: bool, watch_s: float = 1800.0,
         canonical: bool = True, clock: Callable[[], float] = time.monotonic) -> Played:
    """The co-simulation of one channel setting: `report` is channel (a), `honest` channel (b)."""
    task, w = lk.task, lk.world
    watches = references.watch_times(task)
    edct: dict[int, int] = {}
    issues: list[Issue] = []
    plan: dict[int, dict] = {}
    pinned: set[int] = set()
    log: list[dict] = []
    faa = True
    t = 0
    while faa or t <= watches[-1]:
        rel = releases(lk, edct)
        day = truth(lk, plan, as_ran(lk, issues), rel)
        if faa:
            gone = {i: r for i, r in rel.items() if r < t}
            gone |= {lk.arrival[i]: x for i, x in day.wheels.items() if x < t}
            dropped = {a.id for a in w.arrivals if a.cancelled and a.leaves <= t}
            dropped |= {lk.arrival[i] for i, at in reported(lk, plan, day, report).items() if at <= t}
            got = policies.issue(w, t, gone, dropped, forecast, honest=True)
            if got is None:
                faa = False
            else:
                issues.append(got)
                for i, e in (got.edct | dict.fromkeys(got.released, t)).items():
                    if i not in lk.jet:
                        edct[i] = e
        if t in watches:
            t0 = clock()
            plan, pinned, entry = watch(lk, plan, pinned, t, known(lk, issues, t, honest), day)
            log.append({**entry, "wall_s": round(clock() - t0, 2)})
        t += policies.STEP
    rel = releases(lk, edct)
    day = truth(lk, plan, as_ran(lk, issues), rel)
    out = joint(lk, day, rel)
    out["cancelled_by_desk"] = sum(1 for v in plan.values() if v.get("cancel"))
    out["issues"] = len(issues)
    return Played(out, issues, plan, log)


def reported(lk: Link, plan: Mapping, day: Day, report: bool) -> dict[int, int]:
    """When the FAA learns of each JetBlue cancellation: when decided (channel a), else at its scheduled departure,
    never before it is decided."""
    out = {}
    for i in lk.arrival:
        r = day.run.flights[i]
        if r.status in (CANCELLED, AUTO_CANCELLED):
            decided = r.decided
            out[i] = decided if report else max(decided, lk.task.flight(i).sched_dep)
    return out


def known(lk: Link, issues: Sequence[Issue], t: int, honest: bool) -> sim.World:
    """Step 0's day as the desk knows it at watch t, the task hub's programs the issuances by t."""
    task = lk.task
    o = other(task)
    seen = {f.id: f.sched_dep <= t + LOOK for f in task.flights}
    lb = {f.id: f.lb if seen[f.id] else 0 for f in task.flights}
    block = {f.id: lk.unimpeded[f.id] if f.id in lk.unimpeded else f.block if seen[f.id] else f.sched_block
             for f in task.flights}
    segs, stops = rbs.forecast(task, o, t)
    return sim.World(lb, block, {task.hub: as_known(lk, issues, t, honest), o: segs}, stops,
                     {o: rbs.nominal(task, o)})


def watch(lk: Link, plan: dict, pinned: set, t: int, world: sim.World, day: Day,
          time_limit: float = 1800.0, canonical: bool = True) -> tuple[dict, set, dict]:
    """One watch of references.follow on these worlds."""
    task = lk.task
    left = {i: r.dep for i, r in day.run.flights.items() if r.status == FLOWN and r.dep < t}
    run = sim.execute(task, so_far(task, plan, t, left), world)
    pinned = set(pinned)
    if t > task.start:
        pinned |= references.frozen(run, t)
    fixed = {i: run.flights[i].dep if run.flights[i].status == FLOWN else None for i in pinned}
    price = {f.id: references._price(f, plan.get(f.id, {}), task.auto_at(f) if f.id in pinned else t)
             for f in task.flights}
    sol = mip.solve(task, world, price, fixed, t + FREEZE if t > task.start else None, time_limit=time_limit,
                    canonical=canonical, start=run)
    if not sol.proven:
        raise Timeout(f"{task.task_id}: watch {t} solve {sol.status} at gap {sol.gap_pct}%")
    cancelled = sorted(i for i, d in sol.deps.items() if i not in pinned and d is None)
    plan = {i: v for i, v in plan.items() if i in pinned} | {i: {"cancel": True, "decided": t} for i in cancelled}
    pinned |= set(cancelled)
    free = sim.execute(task, so_far(task, plan, t, left), world).flights
    held = {i: d for i, d in sol.deps.items()
            if i not in pinned and (free[i].status != FLOWN or d > free[i].dep)}
    plan |= {i: {"not_before": d} for i, d in held.items()}
    return plan, pinned, {"t": t, "frozen": len(pinned), "cancelled": cancelled, "held": len(held),
                          "planned": sol.objective, "status": sol.status, "gap_pct": sol.gap_pct,
                          "canonical": sol.canonical, "solve_s": sol.wall_s}


def so_far(task: Task, plan: Mapping, t: int, left: Mapping[int, int]) -> dict[int, dict]:
    """references.so_far with the flights that left before t given."""
    out = {}
    for f in task.flights:
        entry = plan.get(f.id, {})
        if f.id in left:
            out[f.id] = {"left": left[f.id]}
        elif entry.get("cancel"):
            out[f.id] = dict(entry)
        else:
            out[f.id] = {**entry, "not_before": max(entry.get("not_before") or 0, t)}
    return out
