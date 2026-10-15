"""The v2 references: plans played on the truth through sim.execute and priced by notice (cost.py).

- anchor: the hindsight MIP on the truth, every cancellation priced at the first watch, proven and canonical;
- follower: at every watch (M13) the MIP on the day as known (M12; unknown delays 0, W6; the programs as issued, a
  proposed GDP included), its plan frozen as the env freezes (M11): a flight the plan, run on that day, has leaving
  before t + FREEZE commits to fly; a projected auto-cancel stays open until its deadline - FREEZE; a cancellation is
  final from the watch that confirms it, priced there and fixed in every later solve (W21). As v1's, its plan holds
  each flight to the minute its last solve gave it, and that plan runs on the truth; every watch solve is canonical
  (the earliest optimal departures, v1's), and starts from the plan's own projection, so no truth reaches the search;
- program-blind follower: the follower with every program removed from its model (the nominal caps stay);
- rule: at every watch, cancel what the plan run on the day as known auto-cancels while it is still open;
- naive: the empty plan;
- airline replay: JetBlue's cancellations decided at sched - NOTICE, its recorded departures as holds and its realised
  arrival order as slot claims (each claims the slot of its recorded departure plus its scheduled block); auto-cancels
  are counted over the flights it flew by their deadline, which covers dmax and the hub cutoff;
- recorded day: JetBlue's real outcome priced directly, cancellations decided at sched - NOTICE, plus end-of-day
  ferries from its recorded end positions to tomorrow's needs. A diverted flight that never reached its destination
  counts as cancelled when it left, the moment the desk knew;
- calm day: no delay, scheduled blocks, no programs: the hindsight optimum's forced cancellations and ferries.
The departures the forecast's RBS treats as gone at a watch are those the current plan made on the truth before it,
which the env knows. No reference may cost less than the anchor (`below_anchor`).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from ..core.known import FREEZE, LOOK, STEP
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN
from . import mip, sim
from .advisories import HUBS
from .cost import NOTICE, Cost, cancel_price, flat_price, priced
from .model import Flight, Task


@dataclass(frozen=True)
class Followed:
    plan: dict[int, dict]
    run: sim.Execution
    watches: tuple[dict, ...]

    @property
    def cost(self) -> int:
        return self.run.cost.total


@dataclass(frozen=True)
class Replay:
    run: sim.Execution
    flew: int
    auto: int


@dataclass(frozen=True)
class Calm:
    solution: mip.Solution
    run: sim.Execution

    @property
    def cancelled(self) -> int:
        return sum(d is None for d in self.solution.deps.values())

    @property
    def ferries(self) -> int:
        return len(self.run.ferries)


def anchor(task: Task, time_limit: float = 600.0) -> tuple[mip.Solution, sim.Execution]:
    sol = mip.solve(task, time_limit=time_limit, start=naive(task))
    return sol, sim.execute(task, sol.plan(task.start))


def flat_anchor(task: Task, time_limit: float = 600.0, start: sim.Execution | None = None) -> mip.Solution:
    """The hindsight optimum with every cancellation at the flat 523 per passenger, started from `start` (a day on
    the truth, naive by default)."""
    return mip.solve(task, price={f.id: flat_price(f) for f in task.flights}, time_limit=time_limit, canonical=False,
                     start=naive(task) if start is None else start)


def follow(task: Task, programs: bool = True, time_limit: float = 600.0, canonical: bool = True) -> Followed:
    plan: dict[int, dict] = {}
    pinned: set[int] = set()
    log = []
    for t in watch_times(task):
        world = sim.known(task, t, departed(task, plan, t), programs)
        run = sim.execute(task, plan, world)
        if t > task.start:
            pinned |= frozen(task, run, t)
        fixed = {i: run.flights[i].dep if run.flights[i].status == FLOWN else None for i in pinned}
        price = {f.id: _price(f, plan.get(f.id, {}), f.deadline if f.id in pinned else t) for f in task.flights}
        sol = mip.solve(task, world, price, fixed, t + FREEZE if t > task.start else None, time_limit=time_limit,
                        canonical=canonical, start=run)
        if not sol.proven:
            raise RuntimeError(f"{task.task_id}: watch {t} solve {sol.status} at gap {sol.gap_pct}%")
        cancelled = []
        for i, d in sol.deps.items():
            if i in pinned:
                continue
            if d is None:
                plan[i] = {"cancel": True, "decided": t}
                pinned.add(i)
                cancelled.append(i)
            else:
                plan[i] = {"not_before": d}
        log.append({"t": t, "frozen": len(pinned), "cancelled": cancelled, "planned": sol.objective,
                    "status": sol.status, "gap_pct": sol.gap_pct, "wall_s": sol.wall_s, "cols": sol.cols,
                    "rows": sol.rows})
    return Followed(plan, sim.execute(task, plan), tuple(log))


def rule(task: Task) -> Followed:
    plan: dict[int, dict] = {}
    log = []
    for t in watch_times(task):
        run = sim.execute(task, plan, sim.known(task, t, departed(task, plan, t)))
        doomed = [i for i, r in run.flights.items()
                  if r.status == AUTO_CANCELLED and task.flight(i).deadline >= t + FREEZE]
        for i in doomed:
            plan[i] = {"cancel": True, "decided": t}
        log.append({"t": t, "cancelled": doomed})
    return Followed(plan, sim.execute(task, plan), tuple(log))


def naive(task: Task) -> sim.Execution:
    return sim.execute(task, {})


def airline_plan(task: Task) -> dict[int, dict]:
    return {f.id: {"cancel": True, "decided": f.sched_dep - NOTICE} if f.actual.dep is None
            else {"not_before": f.actual.dep, "claim": f.actual.dep + f.sched_block} for f in task.flights}


def airline(task: Task) -> Replay:
    run = sim.execute(task, airline_plan(task))
    flew = [f.id for f in task.flights if f.actual.dep is not None and f.actual.dep <= f.deadline]
    return Replay(run, len(flew), sum(run.flights[i].status == AUTO_CANCELLED for i in flew))


def recorded(task: Task) -> tuple[Cost, int]:
    """JetBlue's recorded day like for like, and its end-of-day ferries."""
    arr, decided = {}, {}
    for f in task.flights:
        a = f.actual
        arr[f.id] = a.arr if a.dep is not None and a.arr is not None else None
        if arr[f.id] is None:
            decided[f.id] = f.sched_dep - NOTICE if a.dep is None else a.dep
    out = sim.ferries(task, {t.tail: t.actual_end for t in task.tails}, {t.tail: t.family for t in task.tails})
    return priced(task, arr, decided, [(x.orig, x.dest) for x in out]), len(out)


def calm(task: Task, time_limit: float = 600.0) -> Calm:
    world = sim.calm(task)
    sol = mip.solve(task, world, time_limit=time_limit, canonical=False, start=sim.execute(task, {}, world))
    return Calm(sol, sim.execute(task, sol.plan(task.start), world))


def watch_times(task: Task) -> list[int]:
    """Every STEP minutes from the start, through the first watch that knows every delay, while some flight could
    still leave unfrozen (t + FREEZE <= its deadline)."""
    reveal = max(f.sched_dep - LOOK for f in task.flights)
    last = max(f.deadline for f in task.flights) - FREEZE
    ts = [task.start]
    while ts[-1] < reveal and ts[-1] + STEP <= last:
        ts.append(ts[-1] + STEP)
    return ts


def frozen(task: Task, run: sim.Execution, t: int) -> set[int]:
    """What `run` (the plan on the day as known at watch t) commits: flights leaving before t + FREEZE, every
    cancellation, and projected auto-cancels whose deadline is within FREEZE."""
    return {i for i, r in run.flights.items()
            if (r.status == FLOWN and r.dep < t + FREEZE) or r.status == CANCELLED
            or (r.status == AUTO_CANCELLED and task.flight(i).deadline < t + FREEZE)}


def departed(task: Task, plan: Mapping[int, Mapping], t: int) -> dict[int, int | None]:
    """The task flights `plan` has made leave on the truth before t."""
    run = sim.execute(task, plan)
    return {i: r.dep if r.dep is not None and r.dep < t else None for i, r in run.flights.items()}


def below_anchor(costs: Mapping[str, int], anchor_cost: int) -> dict[str, int]:
    return {k: v for k, v in costs.items() if v < anchor_cost}


def seats_ok(task: Task, run: sim.Execution) -> bool:
    """pax <= seats of the aircraft that flew it, for every flown flight (a Move's aircraft is its tail's family)."""
    seats = {t.tail: t.seats for t in task.tails}
    return all(task.flight(i).pax <= seats[r.tail.split("~")[0]] for i, r in run.flights.items() if r.status == FLOWN)


def past_midnight(task: Task, run: sim.Execution | None = None) -> int:
    """Hub movements (departures from or arrivals at JFK or BOS) after 24:00: the run's, or JetBlue's recorded."""
    out = 0
    for f in task.flights:
        dep, arr = (f.actual.dep, f.actual.arr) if run is None else (run.flights[f.id].dep, run.flights[f.id].arr)
        out += dep is not None and dep >= 1440 and f.orig in HUBS
        out += arr is not None and arr >= 1440 and f.dest in HUBS
    return out


def _price(f: Flight, entry: Mapping, at: int) -> int:
    return cancel_price(f, entry["decided"] if entry.get("cancel") else at)
