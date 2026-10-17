"""The storm day: v1's dispatch inside the FAA's programs at JFK and BOS, with JetBlue's slots and EDCTs in place of
hourly caps.

A plan maps flight ids to {"cancel", "not_before", "claim", "decided", "left"}, every key optional; a flight left out
flies as early as it can. A cancelled flight is cancelled for good, decided at "decided" (the first watch when absent).
A flight with "left" (the day so far, at a watch) leaves at that minute whatever would hold it, counting where the MIP
counts a pinned departure: on its slot if the minute plus its scheduled block is a free one of the segment governing
it, on nothing if that is off the pool, else under the nominal cap. The others are dispatched as in v1: in order of
their next try, each takes the first-ready aircraft of its family at its origin, the aircraft JetBlue turned into it
first: that aircraft may take it after JetBlue's ground time where that was shorter than the family's turn
(Task.quick), through any Moves it passes on the way, and any other flight only after the turn. What else holds a
flight ready at minute t, in this order:
- a ground stop at its destination hub covering its origin holds it to the stop's end; airborne flights fly on;
- a flight governed at t by a GDP segment (the one in force when it departs, the departure-time lock; its origin in
  scope and its scheduled arrival in the period) needs one of JetBlue's slots in that segment's pool: its claimed slot
  if it can still make it, else the earliest free slot at or after its earliest arrival t + scheduled block. It leaves
  at the slot less its scheduled block (its EDCT) and lands after its own block. With no free slot left before the
  period ends it arrives after the period;
- any other arrival at a hub (exempt, uncontrolled, or past its period) needs room under the nominal cap of its
  arrival hour (rbs.nominal); a slot holder does not count against it.
Claims bind first, in (claim, flight id) order, each to the first unbound slot at or after it in the pool of the
segment that would govern the flight leaving at its claim less its scheduled block. A flight that can't make its
claimed slot loses it and the slot is wasted, as a missed EDCT is. Any other slot is taken when its flight leaves, so
of two flights after one slot the one whose EDCT comes first flies it (FSM-style compression, read as first come at
the EDCT), and a cancelled or late flight leaves its slot to the flights behind. A flight not gone by its deadline
auto-cancels there, decided there. With the crew model (model.Crew, repairs.crews) a flight not gone by its crew
deadline may leave only from the end of its crew's rest on, still by its deadline; one that can't, its rest ending
past its deadline, auto-cancels at its crew deadline, decided there, never before the first watch (Task.auto_at).

Moves are fixed and uncosted, and bound to an aircraft (model.Move, `routes`): the one that flies the leg before the
Move's break, landed and turned, is ready at the Move's destination at the Move's `ready` or then, whichever is later,
never at that leg's destination. Legs the plan cancels into a break were never flown, so they join its
repositioning: the Move binds the aircraft of the tail's last leg before the break the plan doesn't cancel (the tail's
own aircraft when there is none), which goes on through any later break only cancelled legs separate it from and
stays at the last Move's destination. Cancelling a leg after a break moves nothing: the aircraft stays where its Move
put it. A bound leg that auto-cancels took no aircraft, and its Moves move none (a labelled limitation): the sim
learns of an auto-cancel only once the aircraft it would have bound is free for others. So no Move takes an aircraft
it isn't bound to, or delivers one it didn't take. A busy block keeps the aircraft that flew its leg until the
block's ready time, if that is later than its turn; a cancelled leg has none. At the end of the day v1's ferries put
the aircraft on tomorrow's needs (Task.needed).

With a crew pool (crewsim.Pool) a flight ready to leave also needs its crews: the pool's `board` says whether
they are there now, when to try again, or never (the flight then auto-cancels at its deadline unless a crew's arrival
wakes it); a flight with "left" leaves whatever crews it finds. A plan for the crew sim may carry "reports".

A World is what a day runs on: the truth (the task's lb, truth blocks and programs as they ran), the day as known at
a watch (what the desk could know then: lb and the truth block once the scheduled departure is within LOOK, else 0
and the scheduled block, the programs as issued by then, proposed GDPs included, every stop to its as-issued end,
every GDP too unless a base rate ends it earlier), or the calm day (no delay, scheduled blocks, no programs). Nominal
caps, deadlines, the hub cutoff and the crew deadlines, which the schedule fixes and the desk knows from the start, are
the same in all of them.
"""

import bisect
import heapq
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace

from ..core.known import LOOK
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN, Ferry, _assign
from . import rbs, repairs
from .cost import Cost, ferry_cost, priced
from .model import Flight, Move, Segment, Task

Plan = Mapping[int | str, Mapping]
SlotKey = tuple[str, int]
HourKey = tuple[str, int]
Route = tuple[str, tuple[Move, ...]]


@dataclass(frozen=True)
class World:
    lb: Mapping[int, int]
    block: Mapping[int, int]
    segments: Mapping[str, tuple[Segment, ...]]
    stops: tuple
    nominal: Mapping[str, Mapping[int, int]]


def truth(task: Task) -> World:
    return World(task.lb, {f.id: f.block for f in task.flights},
                 {h: p.segments for h, p in task.programs.items()},
                 tuple(s for p in task.programs.values() for s in p.stops), nominal_caps(task))


def known(task: Task, t: int, programs: bool = True, early: int = 0) -> World:
    """The day as known at watch t; `programs` False drops every program from the model, `early` ends every GDP
    that many minutes before its as-issued end, never before t (rbs.forecast)."""
    seen = {f.id: f.sched_dep <= t + LOOK for f in task.flights}
    lb = {f.id: f.lb if seen[f.id] else 0 for f in task.flights}
    block = {f.id: f.block if seen[f.id] else f.sched_block for f in task.flights}
    segments, stops = {h: () for h in task.programs}, ()
    if programs:
        issued = {h: rbs.forecast(task, h, t, early) for h in task.programs}
        segments = {h: s for h, (s, _) in issued.items()}
        stops = tuple(p for _, ps in issued.values() for p in ps)
    return World(lb, block, segments, stops, nominal_caps(task))


def calm(task: Task) -> World:
    return World({f.id: 0 for f in task.flights}, {f.id: f.sched_block for f in task.flights},
                 {h: () for h in task.programs}, (), nominal_caps(task))


def nominal_caps(task: Task) -> dict[str, dict[int, int]]:
    return {h: rbs.nominal(task, h) for h in task.programs}


def with_cutoff(task: Task, cutoff: int) -> Task:
    """The task with every deadline under the hub cutoff `cutoff` (minutes from the task date's midnight)."""
    flights = tuple(replace(f, deadline=repairs.deadline(f.sched_dep, f.orig, task.start, task.dmax, cutoff))
                    for f in task.flights)
    return replace(task, cutoff=cutoff, flights=flights)


class Rules:
    """What holds each flight at a minute in a world: the stops at its destination covering its origin, the segment
    governing it (None when out of scope or outside the period), and the nominal cap of its arrival hour."""

    def __init__(self, task: Task, world: World):
        self.world = world
        scope = {a.id: a.scope for p in task.programs.values() for a in p.advisories}
        self.stops: dict[int, list[tuple[int, int]]] = {}
        self.runs: dict[int, list[tuple[int, int, Segment | None]]] = {}
        for f in task.flights:
            self.stops[f.id] = sorted((p.start, p.end) for p in world.stops
                                      if p.hub == f.dest and task.covers(p.scope, p.hub, f.orig))
            self.runs[f.id] = [(s.since, s.until, s if s.start <= f.sched_arr <= s.end
                                and task.covers(scope[s.id], f.dest, f.orig) else None)
                               for s in world.segments.get(f.dest, ())]

    def held(self, f: Flight, t: int) -> int:
        """The first minute from t no stop holds f."""
        for a, b in self.stops[f.id]:
            if a <= t < b:
                t = b
        return t

    def governs(self, f: Flight, t: int) -> Segment | None:
        for since, until, seg in self.runs[f.id]:
            if since <= t < until:
                return seg
        return None

    def hour(self, f: Flight, t: int) -> tuple[HourKey, int] | None:
        """The nominal cap that f, leaving at t without a slot, lands under."""
        caps = self.world.nominal.get(f.dest)
        h = (t + self.world.block[f.id]) // 60
        return ((f.dest, h), caps[h]) if caps is not None and h in caps else None


@dataclass(frozen=True)
class FlightRun:
    id: int
    status: str
    tail: str | None = None
    dep: int | None = None
    arr: int | None = None
    slot: int | None = None
    program: str | None = None
    decided: int | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "tail": self.tail, "dep": self.dep, "arr": self.arr,
                "slot": self.slot, "program": self.program, "decided": self.decided}


@dataclass(frozen=True)
class Execution:
    flights: dict[int, FlightRun]
    legs: dict[str, tuple[int, ...]]
    ends: dict[str, str]
    ferries: tuple[Ferry, ...]
    wasted: tuple[SlotKey, ...]
    moves: dict[int, int | None]
    cost: Cost

    @property
    def counts(self) -> dict[str, int]:
        c = Counter(r.status for r in self.flights.values())
        return {"flights": len(self.flights), FLOWN: c[FLOWN], CANCELLED: c[CANCELLED],
                AUTO_CANCELLED: c[AUTO_CANCELLED], "ferries": len(self.ferries), "wasted": len(self.wasted),
                "slots": sum(1 for r in self.flights.values() if r.slot is not None)}

    def rows(self) -> list[dict]:
        return [self.flights[i].to_dict() for i in sorted(self.flights)]


def execute(task: Task, plan: Plan, world: World | None = None, crew=None) -> Execution:
    """The day under `plan` in `world` (the truth by default), with the crew pool `crew` when given."""
    world = truth(task) if world is None else world
    rules = Rules(task, world)
    want = {int(k): v for k, v in plan.items() if k != "reports"}
    cancelled = {i for i, d in want.items() if d.get("cancel")}
    runs: dict[int, FlightRun] = {}
    when: dict[int, float] = {}
    first: dict[int, int] = {}
    heap = []
    for f in task.flights:
        d = want.get(f.id, {})
        if f.id in cancelled:
            decided = d.get("decided")
            runs[f.id] = FlightRun(f.id, CANCELLED, decided=task.start if decided is None else decided)
            if crew is not None:
                crew.cancel(f.id, runs[f.id].decided)
            continue
        c = d["left"] if d.get("left") is not None else max(task.earliest(f, world.lb[f.id]), d.get("not_before") or 0)
        when[f.id] = first[f.id] = c
        heap.append((c, 1, c, f.id))
    heapq.heapify(heap)
    carried, own = routes(task, cancelled)
    moves: dict[int, int | None] = dict.fromkeys(task.moved)
    ready = {t.tail: t.ready for t in task.tails}
    family = {t.tail: t.family for t in task.tails}
    at: dict[tuple[str, str], set[str]] = defaultdict(set)
    for t in task.tails:
        station = t.pos
        if t.tail in own:
            station, ready[t.tail] = through(own[t.tail], t.ready, moves)
        at[(t.family, station)].add(t.tail)
    bound, claimed = _claims(task, want, runs, rules)
    taken: set[SlotKey] = set()
    used: Counter = Counter()
    legs: dict[str, list[int]] = {p: [] for p in ready}
    waiting: dict[tuple[str, str], set[int]] = defaultdict(set)
    quick: dict[str, tuple[int, int]] = {}

    def soon(p: str, i: int) -> int:
        q = quick.get(p)
        return q[1] if q and q[0] == i else ready[p]

    while heap:
        t, kind, c, i = heapq.heappop(heap)
        if i in runs or when[i] != t:
            continue
        f = task.flight(i)
        left = want.get(i, {}).get("left") is not None
        s = t if left else f.legal(t)
        if s > f.deadline:
            runs[i] = FlightRun(i, AUTO_CANCELLED, decided=task.auto_at(f))
            if crew is not None:
                crew.cancel(i, runs[i].decided)
            continue
        if s != t:
            when[i] = s
            waiting[(f.family, f.orig)].discard(i)
            heapq.heappush(heap, (s, kind, c, i))
            continue
        here = at[(f.family, f.orig)]
        free = [p for p in here if soon(p, i) <= t]
        if not free:
            _wait(waiting[(f.family, f.orig)], i, [soon(p, i) for p in here], when, heap, kind, c)
            continue
        waiting[(f.family, f.orig)].discard(i)
        if left:
            s, slot, hour = t, *_history(f, t, rules, taken)
        else:
            s, slot, hour = _leave(f, t, rules, claimed.get(i), bound, taken, used)
        if s != t:
            when[i] = s
            heapq.heappush(heap, (s, kind, c, i))
            continue
        if crew is not None:
            go = crew.board(f, t, want.get(i, {}), left)
            if go != t:
                when[i] = f.deadline + 1 if go is None else go
                heapq.heappush(heap, (when[i], kind, c, i))
                continue
        p = min(free, key=lambda p: (quick.get(p, (None,))[0] != i, ready[p], p))
        here.remove(p)
        arr = t + world.block[i]
        back = task.back(f, arr)
        station, ready[p] = through(carried[i], back, moves) if i in carried else (f.dest, back)
        at[(f.family, station)].add(p)
        quick.pop(p, None)
        if i in task.quick:
            turned = task.turned(f, arr)
            quick[p] = (task.quick[i][0], max([turned, *(m.ready for m in carried[i][1])]) if i in carried else turned)
        if slot is not None:
            taken.add(slot)
        if hour is not None:
            used[hour] += 1
        legs[p].append(i)
        runs[i] = FlightRun(i, FLOWN, p, t, arr, slot[1] if slot else None, slot[0] if slot else None)
        if crew is not None:
            crew.fly(f, t, arr, want.get(i, {}))
            for j, at in crew.woken():
                if j not in runs and at < when[j]:
                    when[j] = at
                    heapq.heappush(heap, (at, 1, first[j], j))
        for j in waiting[(f.family, station)]:
            if soon(p, j) < when[j]:
                when[j] = soon(p, j)
                heapq.heappush(heap, (when[j], 1, first[j], j))
    for f in task.flights:
        runs.setdefault(f.id, FlightRun(f.id, AUTO_CANCELLED, decided=task.auto_at(f)))
    ends = {p: station for (_, station), ps in at.items() for p in ps}
    out = ferries(task, ends, family)
    wasted = tuple(sorted(k for k, i in bound.items() if runs[i].status != FLOWN or (runs[i].program, runs[i].slot)
                          != k))
    cost = priced(task, {i: r.dep for i, r in runs.items()}, {i: r.arr for i, r in runs.items()},
                  {i: r.decided for i, r in runs.items() if r.status != FLOWN},
                  [(x.family, x.orig, x.dest) for x in out])
    return Execution({i: runs[i] for i in sorted(runs)}, {p: tuple(v) for p, v in legs.items()},
                     dict(sorted(ends.items())), out, wasted, moves, cost)


def routes(task: Task, cancelled: Collection[int]) -> tuple[dict[int, Route], dict[str, Route]]:
    """Where each Move's aircraft goes, given the cancelled legs: walking a tail's legs, the aircraft of the last one
    not cancelled (its carrier; the tail's own before any) goes through every Move it passes and stays at the last
    one's destination. So: each carrier, and each tail whose own aircraft passes a Move, to its station and the Moves
    it passes."""
    carried: dict[int, Route] = {}
    own: dict[str, Route] = {}
    for t in task.tails:
        carrier, passed = None, []
        for i in t.legs:
            if i not in cancelled:
                if passed:
                    _route(carried, own, t.tail, carrier, (passed[-1].dest, tuple(passed)))
                carrier, passed = i, []
            if i in task.moved:
                passed.append(task.moved[i])
        if passed:
            _route(carried, own, t.tail, carrier, (passed[-1].dest, tuple(passed)))
    return carried, own


def _route(carried: dict[int, Route], own: dict[str, Route], tail: str, carrier: int | None, route: Route) -> None:
    if carrier is None:
        own[tail] = route
    else:
        carried[carrier] = route


def through(route: Route, t: int, record: dict[int, int | None]) -> tuple[str, int]:
    """Where and when an aircraft ready at t is ready after `route`, each Move's time recorded under its leg."""
    station, moves = route
    for m in moves:
        t = record[m.leg] = max(t, m.ready)
    return station, t


def _wait(waiting: set[int], i: int, soon: list[int], when: dict, heap: list, kind: int, c: int) -> None:
    waiting.add(i)
    when[i] = min(soon, default=float("inf"))
    if soon:
        heapq.heappush(heap, (when[i], kind, c, i))


def _claims(task: Task, want: Mapping[int, Mapping], runs: Mapping[int, FlightRun],
            rules: Rules) -> tuple[dict[SlotKey, int], dict[int, SlotKey]]:
    """Bind the claims: (segment, slot) to flight, and flight to its (segment, slot)."""
    bound: dict[SlotKey, int] = {}
    claimed: dict[int, SlotKey] = {}
    for claim, i in sorted((v["claim"], i) for i, v in want.items() if v.get("claim") is not None and i not in runs):
        f = task.flight(i)
        seg = rules.governs(f, claim - f.sched_block)
        if seg is None:
            continue
        for s in seg.slots[bisect.bisect_left(seg.slots, claim):]:
            if (seg.id, s) not in bound:
                bound[(seg.id, s)] = i
                claimed[i] = (seg.id, s)
                break
    return bound, claimed


def _leave(f: Flight, t: int, rules: Rules, claim: SlotKey | None, bound: Mapping[SlotKey, int], taken: set[SlotKey],
           used: Counter) -> tuple[int, SlotKey | None, HourKey | None]:
    """The first minute from t at which f may leave, with the slot it takes there or the nominal hour it counts in."""
    while True:
        held = rules.held(f, t)
        if held != t:
            t = held
            continue
        seg = rules.governs(f, t)
        if seg is not None:
            eta = t + f.sched_block
            if claim is not None and claim[0] == seg.id and claim[1] >= eta:
                s = claim[1]
            else:
                s = next((s for s in seg.slots[bisect.bisect_left(seg.slots, eta):]
                          if (seg.id, s) not in taken and (seg.id, s) not in bound), None)
            if s is not None:
                if s - f.sched_block != t:
                    t = s - f.sched_block
                    continue
                return t, (seg.id, s), None
            if eta <= seg.end:
                t = seg.end + 1 - f.sched_block
                continue
        cap = rules.hour(f, t)
        if cap is not None and used[cap[0]] >= cap[1]:
            t = 60 * (cap[0][1] + 1) - rules.world.block[f.id]
            continue
        return t, None, cap[0] if cap else None


def _history(f: Flight, t: int, rules: Rules, taken: set[SlotKey]) -> tuple[SlotKey | None, HourKey | None]:
    """The slot or nominal hour a flight that left at t counts in (mip._place for a pinned copy)."""
    seg = rules.governs(f, t)
    if seg is not None and t + f.sched_block <= seg.end:
        key = (seg.id, t + f.sched_block)
        return (key if t + f.sched_block in seg.slots and key not in taken else None), None
    cap = rules.hour(f, t)
    return None, cap[0] if cap else None


def ferries(task: Task, ends: Mapping[str, str], family: Mapping[str, str]) -> tuple[Ferry, ...]:
    """v1's cheapest end-of-day ferries onto tomorrow's needs, per family."""
    out = []
    for fam in sorted({t.family for t in task.tails}):
        need = Counter({s: n for (g, s), n in task.needed.items() if g == fam})
        left, extra = Counter(need), []
        for p in sorted(p for p in ends if family[p] == fam):
            if left[ends[p]] > 0:
                left[ends[p]] -= 1
            else:
                extra.append(p)
        holes = sorted(left.elements())
        if not extra:
            continue
        matrix = [[ferry_cost(task, fam, ends[p], b) for b in holes] for p in extra]
        for k, j in enumerate(_assign(matrix)):
            out.append(Ferry(extra[k], fam, ends[extra[k]], holes[j], matrix[k][j]))
    return tuple(out)
