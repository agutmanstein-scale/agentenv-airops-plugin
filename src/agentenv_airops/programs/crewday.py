"""A date's crews: its trips over the published schedule, its crew units at the start, the crew replay of JetBlue's real
day and the crew-aware calm day.

- Trips (pairings.py) are built once per date; every duty of the date is a unit, based where it starts or ends (a base),
  else at the base nearest its start (labelled), and the reserves follow the units based there (crews.reserves).
- The storm day's units at the start come from a crew replay of the record before the start with the rest of the day as
  scheduled (crewreplay.py): the legs JetBlue flew before the start at their recorded times and blocks, and the task's
  flights at their scheduled departures (no earlier than the start) and blocks, as the airline planned the day from the
  record so far; every duty of the date from its report and the reserves from their periods; the fewest legs flown
  before the start without a legal crew first, then the fewest of the rest of the day (AHEAD each), then crew cost. Each
  unit stands at the start where that replay has it, and its trip from the start is the replay's plan for it. So a leg
  cancelled before the start strands no crew it need not, a crew a recorded overrun timed out hands its legs to another,
  and no crew is sent where the day goes on that could not go on. A unit is off when it was released before the start
  with nothing left, and so is a reserve whose period or shift is over unused.
- The calm day's units stand where the schedule assumes them, as network.scheduled stands its aircraft: every leg
  scheduled before the start flown on schedule. A leg scheduled before the start that left after it in the record leaves
  at the start in the calm world; its duty is split there (labelled): a crew flies those legs from their origin at the
  start, its duty so far as scheduled, and the rest of the trip has its crew where the schedule has it, the one crew
  more taken from its base's reserves (short-call first; none left, none taken).
- The crew replay of JetBlue's real day: its flown flights at their recorded departures and blocks (its cancellations
  out), crewed by the units at the start, their reserves and every move, the fewest flights without a legal crew first
  (crewreplay.solve: a dive that always ends in an integer plan, with its LP and Lagrangian bounds).
- The crew-aware calm day (calm_day): the calm optimum without crews (references.calm) crewed by the calm units at its
  departures, then the aircraft MIP again around the flights crews can't take there and around each flight it cancels;
  the cheapest day the crew sim replays is the crew-aware calm plan, and the calm optimum without crews bounds it below.
"""

import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..build import fleet, pack
from ..core.cost import miles
from ..core.model import Airport
from . import candidates, crewreplay, crews, crewsim, mip, network, pairings, sim
from .cost import cancel_price
from .crewreplay import DH, OP, Column, Leg, Problem
from .crews import Unit
from .model import Task

AHEAD = crewreplay.UNCREWED / 100
FLY = 10


@dataclass
class World:
    """A date's crew world: the task, its BTS context, its trips and the airports any crew can reach."""

    task: Task
    ctx: candidates.Context
    built: dict[tuple[str, str], pairings.Built]
    airports: dict[str, Airport]

    @property
    def duties(self) -> list[pairings.Duty]:
        return [d for b in self.built.values() for d in b.duties]

    def local(self, station: str, minute: int) -> int:
        return network.local(self.task, station, minute)

    def rows(self) -> pd.DataFrame:
        day = self.ctx.day
        return pd.concat([day.prev, day.cur])

    def flight_of(self) -> dict[int, int]:
        """Each BTS row of the task's flights, to its flight."""
        by_key = {(f.number, f.orig, f.sched_dep): f.id for f in self.task.flights}
        out = {}
        for r in self.rows().itertuples():
            k = (int(r.Flight_Number_Reporting_Airline), r.Origin, int(r.sd))
            if k in by_key:
                out[int(r.Index)] = by_key[k]
        return out


def world(task: Task, inputs, derived, time_limit: float = 600.0) -> World:
    ctx = candidates.context(inputs, derived, task.date, task.hub)
    legs = pairings.legs_of(ctx.day, pairings.schedule(ctx, inputs, derived), ctx.families)
    built = pairings.build_all(legs, task.date, time_limit)
    codes = {g.orig for g in legs} | {g.dest for g in legs} | set(task.airports) | \
        {b for k in crews.KINDS for b, _ in crews.BASES[k]}
    return World(task, ctx, built, candidates.airports_of(codes))


def home(d: pairings.Duty, date: str, airports: Mapping[str, Airport]) -> str:
    bases = crews.bases(d.kind, date)
    if d.start in bases:
        return d.start
    if d.end in bases:
        return d.end
    return min(sorted(bases), key=lambda b: miles(airports[d.start], airports[b]))


def day_units(w: World) -> list[Unit]:
    """Every duty of the date as a unit at its report, keyed by BTS rows."""
    out = []
    for n, d in enumerate(sorted(w.duties, key=lambda d: (d.kind, crews.GROUPS.index(d.group), d.report, d.legs))):
        out.append(Unit(f"{d.kind[0].upper()}-{d.group}-{n:04d}", d.kind, d.group, crews.size(d.kind, d.group),
                        home(d, w.task.date, w.airports), d.end, d.start, d.report + crews.BRIEF, d.report, d.hour,
                        at=d.start, trip=d.legs, deadheads=d.deadheads))
    return out


def reserves(w: World, units: Sequence[Unit], share: str = "middle") -> list[Unit]:
    working = Counter((u.kind, u.group, u.home) for u in units if not u.reserve)
    return crews.reserves(working, w.task.date, w.local, share)


def family_seats(task: Task) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in task.tails:
        out[t.family] = min(out.get(t.family, t.seats), t.seats)
    return out


def recorded_legs(w: World) -> list[Leg]:
    """The legs JetBlue flew before the start (any typed tail), at their recorded times; a leg of a duty of the date
    needs a crew of that duty's kind."""
    task, day = w.task, w.ctx.day
    need = defaultdict(set)
    for d in w.duties:
        for k in d.legs:
            need[k].add(d.kind)
    first = min(d.report for d in w.duties)
    seats = family_seats(task)
    out = []
    for r in w.rows().itertuples():
        if r.Cancelled != 0 or r.Tail_Number not in w.ctx.families or r.Tail_Number not in day.fleet:
            continue
        if not first <= r.ad < task.start:
            continue
        fam = w.ctx.families[r.Tail_Number]
        cap = seats.get(fam, day.fleet[r.Tail_Number].seats)
        pax = min(fleet.pax(day.fleet[r.Tail_Number].seats, day.lf, r.Origin, r.Dest), cap)
        out.append(Leg(int(r.Index), f"{pack.CARRIER} {int(r.Flight_Number_Reporting_Airline)}", r.Origin, r.end,
                       int(r.ad), network.landed(r), int(r.CRSElapsedTime),
                       crews.TYPE[fam], max(0, cap - pax), tuple(k for k in crews.KINDS if k in need[int(r.Index)])))
    return out


def task_legs(task: Task, deps: Mapping[int, int], blocks: Mapping[int, int], ends: Mapping[int, str] | None = None
              ) -> list[Leg]:
    """The task's flights leaving at `deps` with `blocks`, every one needing a crew."""
    seats = family_seats(task)
    return [Leg(f.id, f.flight, f.orig, (ends or {}).get(f.id, f.dest), d, d + blocks[f.id], f.sched_block,
                crews.TYPE[f.family], max(0, seats[f.family] - f.pax)) for f in task.flights
            if (d := deps.get(f.id)) is not None]


def storm_units(w: World, share: str = "middle", time_limit: float = 900.0, log=None) -> tuple[list[Unit], dict]:
    """The units at the start from a crew replay of the record before it with the rest of the day as scheduled, and
    that replay's summary."""
    task = w.task
    flight_of = w.flight_of()
    units = day_units(w)
    units += reserves(w, units, share)
    keyed = [replace(u, trip=tuple(ahead(flight_of[k]) if k in flight_of else k for k in u.trip),
                     deadheads=tuple(ahead(flight_of[k]) if k in flight_of else k for k in u.deadheads))
             for u in units]
    later = [replace(g, id=ahead(g.id), price=AHEAD) for g in
             task_legs(task, {f.id: max(f.sched_dep, task.start) for f in task.flights},
                       {f.id: f.sched_block for f in task.flights})]
    p = Problem(recorded_legs(w) + later, keyed, task.date, w.local, task_end(w))
    t0 = time.monotonic()
    sol = crewreplay.solve(p, time_limit, log=log)
    out = [s for u in keyed if (s := at_start(p, u, sol.columns.get(u.id), task.start)) is not None]
    before = [i for i in sol.uncrewed if i >= 0]
    return out, {**sol.summary(), "legs": sum(g.id >= 0 for g in p.legs),
                 "needed": sum(bool(g.need) for g in p.legs if g.id >= 0), "units": len(units),
                 "uncrewed_before": len(before), "uncrewed_after_as_scheduled": len(sol.uncrewed) - len(before),
                 "seconds_total": round(time.monotonic() - t0, 1),
                 "causes": [c for c in uncrewed_causes(p, sol) if c["id"] >= 0]}


def ahead(i: int) -> int:
    """A task flight's leg id in the replay of the record before the start, apart from the record's rows."""
    return -1 - i


def at_start(p: Problem, u: Unit, c: Column | None, start: int) -> Unit | None:
    """Unit u after the steps of its column before the start, its trip from the start the column's legs from then (the
    replay's plan for the rest of the day); None when it is off: released before the start with nothing left, or a
    reserve whose period or shift ended unused."""
    steps = () if c is None else c.steps
    trip = tuple(ahead(i) for i, how in steps if i < 0 and how == OP)
    deadheads = tuple(ahead(i) for i, how in steps if i < 0 and how == DH)
    s = {"station": u.station, "ready": u.ready, "report": u.report, "hour": u.hour, "flown": u.flown,
         "minutes": u.minutes, "landed": u.landed, "at": u.at, "until": u.until}
    moved = False
    for n, (i, how) in enumerate(steps):
        if i < 0:
            break
        if n == 0 and c.report is not None:
            s.update(report=c.report, hour=p.hour(u.home, c.report))
        if c.rest is not None and n == len(c.first):
            s.update(report=c.rest[1], hour=p.hour(*c.rest), flown=0, minutes=0, at=c.rest[0])
        g = p.leg[i]
        s.update(station=g.dest, ready=g.arr + crews.CONNECT, landed=g.arr)
        if how == OP:
            s.update(flown=s["flown"] + 1, minutes=s["minutes"] + g.block)
        moved = True
    if moved and u.reserve == "standby":
        s["until"] = None
    if u.reserve and not moved and (u.until is None or u.until < start):
        return None
    if not u.reserve and not trip and not deadheads and s["landed"] is not None and \
            s["landed"] + crews.DEBRIEF < start:
        return None
    return replace(u, trip=trip, deadheads=deadheads, **s)


def calm_units(w: World, share: str = "middle") -> list[Unit]:
    """The calm day's units at the start, as the schedule assumes them (see the module doc)."""
    task = w.task
    units = day_units(w)
    spare = reserves(w, units, share)
    flight_of = w.flight_of()
    rows = w.rows()
    out = []
    for u in units:
        steps = sorted([(k, OP) for k in u.trip] + [(k, DH) for k in u.deadheads], key=lambda s: rows.at[s[0], "sd"]
                       if s[0] in rows.index else -10 ** 6)
        state = {"station": u.station, "ready": u.ready, "flown": 0, "minutes": 0, "landed": None}
        pending: list[int] = []
        before = None
        trip, deadheads = [], []
        for k, how in steps:
            f = task.flight(flight_of[k]) if k in flight_of else None
            if f is not None and f.sched_dep >= task.start:
                (trip if how == OP else deadheads).append(f.id)
                continue
            if f is not None and how == OP:
                if before is None:
                    before = dict(state)
                pending.append(f.id)
            if k in rows.index:
                r = rows.loc[k]
                state = {"station": r.Dest, "ready": int(r.sa) + crews.CONNECT, "landed": int(r.sa),
                         "flown": state["flown"] + (how == OP),
                         "minutes": state["minutes"] + int(r.CRSElapsedTime) * (how == OP)}
        if not trip and not deadheads and not pending:
            continue
        if trip or deadheads:
            out.append(replace(u, trip=tuple(trip), deadheads=tuple(deadheads), **state))
        if pending:
            first = task.flight(pending[0])
            out.append(replace(u, id=u.id + "p", station=first.orig, ready=max(task.start, before["ready"]),
                               flown=before["flown"], minutes=before["minutes"], landed=before["landed"],
                               trip=tuple(pending), deadheads=(), end=task.flight(pending[-1]).dest))
            if trip or deadheads:
                take = [r for r in spare if r.kind == u.kind and r.group == u.group and r.home == u.home]
                take.sort(key=lambda r: (r.reserve != "short", r.id))
                if take:
                    spare.remove(take[0])
    out += [r for r in spare if r.until is not None and r.until >= task.start]
    return out


def task_end(w: World) -> Callable[[Unit, str], float]:
    return lambda u, station: crews.reposition(u, station, w.airports, w.task.date)


def replay_record(w: World, units: Sequence[Unit], time_limit: float = 1800.0, log=None) -> tuple[Problem,
                                                                                                    crewreplay.Solved]:
    """JetBlue's real day crewed: its flown flights at their recorded departures and blocks."""
    task = w.task
    deps, blocks, ends = recorded(task)
    p = Problem(task_legs(task, deps, blocks, ends), units, task.date, w.local, task_end(w), call=task.start)
    return p, crewreplay.solve(p, time_limit, log=log)


def recorded(task: Task) -> tuple[dict[int, int], dict[int, int], dict[int, str]]:
    """The flown flights' recorded departures, blocks and where they ended (a diversion that never got there, an hour
    after its scheduled block)."""
    deps, blocks, ends = {}, {}, {}
    for f in task.flights:
        a = f.actual
        if a.dep is not None:
            deps[f.id], ends[f.id] = a.dep, a.end
            blocks[f.id] = (a.arr if a.arr is not None else a.dep + f.sched_block + 60) - a.dep
    return deps, blocks, ends


def plan_of(p: Problem, sol: crewreplay.Solved, base: Mapping[int, Mapping] | None = None) -> dict:
    """The crew replay's day as a crew sim plan: each flight's crews and riders on top of `base`, and the reports."""
    out: dict = {i: dict(v) for i, v in (base or {}).items()}
    reports = {}
    for c in sol.columns.values():
        u = p.unit[c.unit]
        for i, how in c.steps:
            entry = out.setdefault(i, {})
            if how == OP:
                entry.setdefault("crew", {})[u.kind] = u.id
            else:
                entry.setdefault("deadhead", []).append(u.id)
        if c.report is not None:
            reports[u.id] = c.report
        if c.rest is not None:
            reports[f"{u.id}#2"] = c.rest[1]
    out["reports"] = reports
    return out


@dataclass
class Calm:
    """The crew-aware calm day: the calm optimum without crews, the best crew-aware plan found and its crew sim
    replay."""

    task: Task
    optimum: mip.Solution
    plan: dict
    day: crewsim.CrewDay
    attempts: list[dict]

    @property
    def rise_pct(self) -> float | None:
        base = self.optimum.objective
        if abs(self.day.total - base) < 1e-6:
            return 0.0
        return None if base <= 0 else 100 * (self.day.total - base) / base


def calm_optimum(w: World, time_limit: float = 1800.0) -> mip.Solution:
    """The calm day's optimum without crews, as references.calm solves it."""
    task = network.scheduled(w.task)
    calm = sim.calm(task)
    return mip.solve(task, calm, time_limit=time_limit, canonical=False, start=sim.execute(task, {}, calm))


def calm_day(w: World, units: Sequence[Unit], optimum: mip.Solution | None = None, time_limit: float = 1800.0,
             crew_limit: float = 900.0, tries: int = 4, log=None) -> Calm:
    """The best crew-aware calm day found: the calm optimum without crews crewed at its departures; while flights go
    without a legal crew there, the aircraft MIP again with them held to the minute the crew sim flew them (or
    cancelled at the start where it could not); then, one at a time, each flight the best plan cancels priced to fly
    (its cancellation at FLY times its price), in case its crews cost more stranded than another flight's cancellation
    does. Every plan is crewed by crewreplay.solve and replayed in the crew sim; the cheapest replay wins."""
    task = network.scheduled(w.task)
    calm = sim.calm(task)
    optimum = calm_optimum(w, time_limit) if optimum is None else optimum
    attempts: list[dict] = []
    best: list = []

    def consider(aircraft: mip.Solution, label: str) -> tuple[crewreplay.Solved, crewsim.CrewDay]:
        deps = {i: d for i, d in aircraft.deps.items() if d is not None}
        p = Problem(task_legs(task, deps, {f.id: f.sched_block for f in task.flights}), units, task.date, w.local,
                    task_end(w), call=task.start)
        crewed = crewreplay.solve(p, crew_limit, log=log)
        plan = plan_of(p, crewed, aircraft.plan(task.start))
        exact = crewsim.execute(task, plan, units, calm, w.local, fill=False, airports=w.airports)
        day = exact
        if crewed.uncrewed:
            plan = named(crewsim.execute(task, plan, units, calm, w.local, airports=w.airports))
            day = crewsim.execute(task, plan, units, calm, w.local, fill=False, airports=w.airports)
        attempts.append({"try": label, "aircraft": aircraft.objective, "aircraft_status": aircraft.status,
                         "crew": crewed.summary(), "crew_cost": round(crewed.crew_cost, 1),
                         "planned": round(aircraft.objective + crewed.crew_cost, 1), "exact": round(exact.total, 1),
                         "replayed": round(day.total, 1), "sim_uncrewed": len(day.uncrewed),
                         "causes": uncrewed_causes(p, crewed)})
        if not best or day.total < best[1].total - 1e-6:
            best[:] = [plan, day, crewed]
        return crewed, day

    crewed, day = consider(optimum, "optimum")
    held, dropped, fly = {}, set(), set()
    for n in range(tries):
        if not crewed.uncrewed:
            break
        for i in crewed.uncrewed:
            r = day.run.flights[i]
            if r.dep is None:
                dropped.add(i)
            else:
                held[i] = max(held.get(i, 0), r.dep)
        crewed, day = consider(hold(task, calm, held, dropped, fly, time_limit), f"held {n + 1}")
    tried: set[int] = set()
    for _ in range(tries):
        out = [i for i, r in best[1].run.flights.items() if r.dep is None and i not in dropped and i not in tried]
        if not out:
            break
        i = out[0]
        tried.add(i)
        before = best[1].total
        consider(hold(task, calm, held, dropped, fly | {i}, time_limit), f"fly {task.flight(i).flight}")
        if best[1].total < before - 1e-6:
            fly.add(i)
    return Calm(task, optimum, best[0], best[1], attempts)


def hold(task: Task, calm: sim.World, held: Mapping[int, int], dropped: set[int], fly: set[int],
         time_limit: float) -> mip.Solution:
    """The calm aircraft optimum with each flight in `held` leaving no earlier than its minute there, those in
    `dropped` cancelled at the start, and those in `fly` cancelled only at FLY times the price."""
    lb = dict(calm.lb)
    for i, d in held.items():
        lb[i] = max(lb[i], d - task.flight(i).sched_dep)
    world_ = replace(calm, lb=lb)
    price = {f.id: cancel_price(f, task.start) * (FLY if f.id in fly else 1) for f in task.flights}
    cancels = {i: {"cancel": True} for i in dropped}
    return mip.solve(task, world_, price, fixed=dict.fromkeys(dropped), time_limit=time_limit, canonical=False,
                     start=sim.execute(task, cancels, world_))


def named(day: crewsim.CrewDay) -> dict:
    """A crew sim day as a plan that names everything it did: each flight's minute or cancellation, its crews and
    riders, and the reports."""
    out: dict = {}
    for i, r in day.run.flights.items():
        if r.dep is not None:
            out[i] = {"not_before": r.dep, "crew": dict(day.crews.get(i, {}))}
            if day.riders.get(i):
                out[i]["deadhead"] = list(day.riders[i])
        elif r.status == "cancelled":
            out[i] = {"cancel": True, "decided": r.decided}
        else:
            out[i] = {"not_before": 10 ** 6}
    reports = {}
    for uid, rec in day.units.items():
        for d in rec["duties"]:
            if d["begins"] == "call":
                reports[uid] = d["report"]
            elif d["begins"] == "rest":
                reports[f"{uid}#2"] = d["report"]
    out["reports"] = reports
    return out


def uncrewed_causes(p: Problem, sol: crewreplay.Solved) -> list[dict]:
    """Per leg left without a legal crew: the kinds missing, and for each its planned unit's state as the plan has
    it at the leg's departure (where it is, its duty so far, the rule it fails)."""
    crewed = sol.crews(p)
    planned = defaultdict(dict)
    for u in p.units:
        for i in u.trip:
            planned[i][u.kind] = u
    at = {u.id: whereabouts(p, u, sol.columns.get(u.id)) for u in p.units}
    pricer = crewreplay.Pricer(p)
    out = []
    for i in sol.uncrewed:
        g = p.leg[i]
        row = {"id": g.id, "flight": g.flight, "orig": g.orig, "dest": g.dest, "dep": g.dep, "group": g.group,
               "missing": {}, "could_alone": {}}
        for kind in g.need:
            if kind in crewed.get(i, {}):
                continue
            u = planned[i].get(kind)
            row["missing"][kind] = "no planned unit" if u is None else reason(p, u, at[u.id], g)
            row["could_alone"][kind] = alone(p, pricer, kind, g)
        out.append(row)
    return out


def alone(p: Problem, pricer: crewreplay.Pricer, kind: str, g: Leg) -> list[str]:
    """The units that, alone in the day, have a legal day flying leg g."""
    pi = {k: np.zeros(len(p.legs)) for k in crews.KINDS}
    pi[kind][p.pos[g.id]] = 10.0 * crewreplay.UNCREWED
    y = crewreplay.Duals(pi, np.zeros(len(p.legs)), {u.id: 0.0 for u in p.units}, {})
    return [u.id for u in p.units if u.kind == kind and u.group == g.group
            and pricer.price(u, y, 1)[0] < -crewreplay.UNCREWED]


def whereabouts(p: Problem, u: Unit, c: Column | None) -> list[tuple[int, str, dict]]:
    """The unit's state after each step of its plan: (minute, station, duty so far)."""
    state = {"report": u.report, "hour": u.hour, "flown": u.flown, "minutes": u.minutes}
    out = [(u.ready, u.station, dict(state))]
    if c is None:
        return out
    if c.report is not None:
        state.update(report=c.report, hour=p.hour(u.home, c.report), flown=0, minutes=0)
    for n, (i, how) in enumerate(c.steps):
        if c.rest is not None and n == len(c.first):
            state.update(report=c.rest[1], hour=p.hour(*c.rest), flown=0, minutes=0)
        g = p.leg[i]
        if how == OP:
            state.update(flown=state["flown"] + 1, minutes=state["minutes"] + g.block)
        out.append((g.arr + crews.CONNECT, g.dest, dict(state)))
    return out


def reason(p: Problem, u: Unit, states: Sequence[tuple[int, str, dict]], g: Leg) -> str:
    ready, station, s = max(((t, st, s) for t, st, s in states if t <= g.dep), default=states[0],
                            key=lambda x: x[0])
    if station != g.orig:
        return f"{u.id} at {station}, not {g.orig}"
    if s["report"] is None:
        return f"{u.id} (short-call) not called in time"
    if u.kind == "pilot":
        limit = crews.pilot_limit(s["report"], s["hour"], s["flown"] + 1, u.rap if u.reserve == "short" else None)
        if g.dep + g.sched_block > limit:
            return (f"{u.id} times out: report {s['report']}, {s['flown']} segments, limit {limit} "
                    f"< expected block-in {g.dep + g.sched_block}")
        if s["minutes"] + g.sched_block > crews.flight_time(s["hour"]):
            return f"{u.id} Table A: {s['minutes']} + {g.sched_block} > {crews.flight_time(s['hour'])}"
    elif g.id not in u.trip and g.dep + g.sched_block + crews.DEBRIEF - s["report"] > crews.CABIN_DUTY:
        return f"{u.id} 14 h duty from report {s['report']}"
    return f"{u.id} legal at {g.orig} but used elsewhere"
