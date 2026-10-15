"""The v2 day: v1's dispatch inside the FAA's programs at JFK and BOS, with JetBlue's slots and EDCTs in place of
hourly caps.

A plan maps flight ids to {"cancel", "not_before", "claim", "decided", "left"}, every key optional; a flight left out
flies as early as it can. A cancelled flight is cancelled for good, decided at "decided" (the first watch when absent).
A flight with "left" (the day so far, at a watch) leaves at that minute whatever would hold it, counting where the MIP
counts a pinned departure: on its slot if the minute plus its scheduled block is a free one of the segment governing
it, on nothing if that is off the pool, else under the nominal cap. The others are dispatched as in v1: in order of
their next try, each takes the first-ready aircraft of its family at its origin. What else holds a flight ready at
minute t, in this order:
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
auto-cancels there, decided there.

Moves are fixed and uncosted, and decoupled: an aircraft of the tail's family appears at the Move's destination,
ready at its `ready`, and the first aircraft of that family ready at its origin from `leave` on leaves, before any
flight at the same minute. If none ever comes, the family ends the day one aircraft spare, which needs no ferry. A busy
block keeps the aircraft that flew its leg until the block's ready time, if that is later than its turn; a cancelled
leg has none. At the end of the day v1's ferries put the aircraft on tomorrow's needs.

A World is what a day runs on: the truth (the task's lb, truth blocks and programs as they ran), the day as known at
a watch (M12: lb and the truth block once the scheduled departure is within LOOK, else 0 and the scheduled block, the
programs as issued by then, proposed GDPs included, every stop to its as-issued end), or the calm day (no delay,
scheduled blocks, no programs). Nominal caps, deadlines and the hub cutoff are the same in all of them.
"""

import bisect
import heapq
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..core.cost import ferry_cost
from ..core.known import LOOK
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN, Ferry, _assign
from . import rbs, repairs
from .cost import Cost, priced
from .model import Flight, Segment, Task

Plan = Mapping[int | str, Mapping]
SlotKey = tuple[str, int]
HourKey = tuple[str, int]


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


def known(task: Task, t: int, programs: bool = True) -> World:
    """The day as known at watch t; `programs` False drops every program from the model."""
    seen = {f.id: f.sched_dep <= t + LOOK for f in task.flights}
    lb = {f.id: f.lb if seen[f.id] else 0 for f in task.flights}
    block = {f.id: f.block if seen[f.id] else f.sched_block for f in task.flights}
    segments, stops = {h: () for h in task.programs}, ()
    if programs:
        issued = {h: rbs.forecast(task, h, t) for h in task.programs}
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
    spare: dict[str, int]
    moves: dict[str, int | None]
    cost: Cost

    @property
    def counts(self) -> dict[str, int]:
        c = Counter(r.status for r in self.flights.values())
        return {"flights": len(self.flights), FLOWN: c[FLOWN], CANCELLED: c[CANCELLED],
                AUTO_CANCELLED: c[AUTO_CANCELLED], "ferries": len(self.ferries), "wasted": len(self.wasted),
                "slots": sum(1 for r in self.flights.values() if r.slot is not None)}

    def rows(self) -> list[dict]:
        return [self.flights[i].to_dict() for i in sorted(self.flights)]


def execute(task: Task, plan: Plan, world: World | None = None) -> Execution:
    """The day under `plan` in `world` (the truth by default)."""
    world = truth(task) if world is None else world
    rules = Rules(task, world)
    want = {int(k): v for k, v in plan.items()}
    runs: dict[int, FlightRun] = {}
    when: dict[int, float] = {}
    first: dict[int, int] = {}
    heap = []
    for f in task.flights:
        d = want.get(f.id, {})
        if d.get("cancel"):
            decided = d.get("decided")
            runs[f.id] = FlightRun(f.id, CANCELLED, decided=task.start if decided is None else decided)
            continue
        c = d["left"] if d.get("left") is not None else max(task.earliest(f, world.lb[f.id]), d.get("not_before") or 0)
        when[f.id] = first[f.id] = c
        heap.append((c, 1, c, f.id))
    ready = {t.tail: t.ready for t in task.tails}
    family = {t.tail: t.family for t in task.tails}
    at: dict[tuple[str, str], set[str]] = defaultdict(set)
    for t in task.tails:
        at[(t.family, t.pos)].add(t.tail)
    sinks = []
    for t in task.tails:
        for k, m in enumerate(t.repositions):
            name = f"{t.tail}~{k}"
            ready[name], family[name] = m.ready, t.family
            at[(t.family, m.dest)].add(name)
            key = -1 - len(sinks)
            sinks.append((name, t.family, m.orig))
            when[key] = first[key] = m.leave
            heap.append((m.leave, 0, m.leave, key))
    heapq.heapify(heap)
    bound, claimed = _claims(task, want, runs, rules)
    taken: set[SlotKey] = set()
    used: Counter = Counter()
    legs: dict[str, list[int]] = {p: [] for p in ready}
    waiting: dict[tuple[str, str], set[int]] = defaultdict(set)
    fired: dict[str, int | None] = {name: None for name, _, _ in sinks}
    while heap:
        t, kind, c, i = heapq.heappop(heap)
        if i in runs or when[i] != t:
            continue
        if i < 0:
            name, fam, orig = sinks[-1 - i]
            if fired[name] is not None:
                continue
            here = at[(fam, orig)]
            free = [p for p in here if ready[p] <= t]
            if not free:
                _wait(waiting[(fam, orig)], i, here, ready, when, heap, kind, c)
                continue
            waiting[(fam, orig)].discard(i)
            p = min(free, key=lambda p: (ready[p], p))
            here.remove(p)
            fired[name] = t
            continue
        f = task.flight(i)
        if t > f.deadline:
            runs[i] = FlightRun(i, AUTO_CANCELLED, decided=f.deadline)
            continue
        here = at[(f.family, f.orig)]
        free = [p for p in here if ready[p] <= t]
        if not free:
            _wait(waiting[(f.family, f.orig)], i, here, ready, when, heap, kind, c)
            continue
        waiting[(f.family, f.orig)].discard(i)
        if want.get(i, {}).get("left") is not None:
            s, slot, hour = t, *_history(f, t, rules, taken)
        else:
            s, slot, hour = _leave(f, t, rules, claimed.get(i), bound, taken, used)
        if s != t:
            when[i] = s
            heapq.heappush(heap, (s, kind, c, i))
            continue
        p = min(free, key=lambda p: (ready[p], p))
        here.remove(p)
        arr = t + world.block[i]
        ready[p] = task.back(f, arr)
        at[(f.family, f.dest)].add(p)
        if slot is not None:
            taken.add(slot)
        if hour is not None:
            used[hour] += 1
        legs[p].append(i)
        runs[i] = FlightRun(i, FLOWN, p, t, arr, slot[1] if slot else None, slot[0] if slot else None)
        for j in waiting[(f.family, f.dest)]:
            if ready[p] < when[j]:
                when[j] = ready[p]
                heapq.heappush(heap, (ready[p], 0 if j < 0 else 1, first[j], j))
    for f in task.flights:
        runs.setdefault(f.id, FlightRun(f.id, AUTO_CANCELLED, decided=f.deadline))
    ends = {p: station for (_, station), ps in at.items() for p in ps}
    spare = Counter(family[name] for name, n in fired.items() if n is None)
    out = ferries(task, ends, family, spare)
    wasted = tuple(sorted(k for k, i in bound.items() if runs[i].status != FLOWN or (runs[i].program, runs[i].slot)
                          != k))
    cost = priced(task, {i: r.arr for i, r in runs.items()},
                  {i: r.decided for i, r in runs.items() if r.status != FLOWN}, [(x.orig, x.dest) for x in out])
    return Execution({i: runs[i] for i in sorted(runs)}, {p: tuple(v) for p, v in legs.items()},
                     dict(sorted(ends.items())), out, wasted, dict(sorted(spare.items())), fired, cost)


def _wait(waiting: set[int], i: int, here: set[str], ready: Mapping[str, int], when: dict, heap: list, kind: int,
          c: int) -> None:
    waiting.add(i)
    when[i] = min((ready[p] for p in here), default=float("inf"))
    if here:
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


def ferries(task: Task, ends: Mapping[str, str], family: Mapping[str, str],
            spare: Mapping[str, int] | None = None) -> tuple[Ferry, ...]:
    """v1's cheapest end-of-day ferries onto tomorrow's needs, per family, where `spare` aircraft of a family may stay
    wherever they are."""
    out = []
    for fam in sorted({t.family for t in task.tails}):
        need = Counter(t.end_req for t in task.tails if t.family == fam)
        left, extra = Counter(need), []
        for p in sorted(p for p in ends if family[p] == fam):
            if left[ends[p]] > 0:
                left[ends[p]] -= 1
            else:
                extra.append(p)
        holes = sorted(left.elements())
        if not extra:
            continue
        free = (spare or {}).get(fam, 0)
        matrix = [[ferry_cost(task, ends[p], b) for b in holes] + [0] * free for p in extra]
        for k, j in enumerate(_assign(matrix)):
            if j < len(holes):
                out.append(Ferry(extra[k], fam, ends[extra[k]], holes[j], matrix[k][j]))
    return tuple(out)
