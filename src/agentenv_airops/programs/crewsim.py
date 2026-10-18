"""The storm day with crews: sim.execute's dispatch, where a flight ready to leave also needs a legal pilot unit and a
legal cabin unit of its type group at its origin, and the crew event log.

A plan entry may name its crews ({"crew": {"pilot": id, "cabin": id}}) and the units that deadhead on it ({"deadhead":
[id, ...]}); plan["reports"] maps a short-call reserve to its report and "id#2" to a unit's second duty period's report.
A named unit flies its flight: the flight waits until the unit is at its origin, free and legal, and auto-cancels at its
deadline if it never is. With `fill` (the default rule, the trip rule), a flight with a kind unnamed takes the unit
whose trip has it if that unit is there, free and legal, else the first one ready of the other units of the kind and
group there, free and legal and named nowhere in the plan with no trip left (a crew done with its trip, an
airport-standby reserve, a called reserve) or the short-call reserve called for it; no crew leaves its trip. With none,
the flight waits for the first of them to be free or legal again, or for a crew to arrive, or calls a short-call reserve
of the base if one could report sooner, held for it until it reports (if another crew takes the flight, the reserve is
as if never called); else it waits for a crew to arrive. A flight with "left" (the day so far) leaves at its minute with
the crews it finds, and one without a legal crew of a kind is logged so. A rider rides if it is at the origin, free,
legal for the deadhead and the flight has its seats free (the family's smallest seats less the passengers); with `fill`
a unit named nowhere also rides its trip's planned deadheads.

The rules are crews.py's, judged at takeoff (crewreplay.py prices the same ones): a unit's period continues while the
leg fits it; an uncalled short-call reserve reports, and a rested unit starts its second period, on the last whole hour
that leaves BRIEF before the departure, the reserve no earlier than LEAD after the start or its period's start and
called by its period's end, the second period at least the rest after release, a hotel away from base. A ride in the
first period that leaves a full rest after the release starts the second period that way (its night away is a hotel).
A unit connects in CONNECT, is released DEBRIEF after its last block-in, and its flight time counts actual blocks.

Costs, in passenger-minutes: deadheads off a unit's trip, nights away from base, and the end of the day: per kind, type
group and base, the units where the day leaves them sent to where their trips have them tonight at the least cost
(crews.Ends: a unit may take any of its base's trips tomorrow). The log has per unit each period's report time and
place, its legs and deadheads with their times, its release, hotels, and a reserve's call and report; per flight its
crew status over time: "legal", "times out" at a minute (its crew's last legal departure), or "no crew" (no unit
assigned, or its unit stranded or out of time), then "flown", "flown without a legal crew" or "cancelled".
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..core.model import Airport
from . import crews, sim
from .crews import Unit
from .model import Flight, Task

LEGAL, TIMES_OUT, NO_CREW = "legal", "times out", "no crew"


@dataclass
class State:
    unit: Unit
    station: str
    ready: int
    report: int | None
    hour: int | None
    rap: int | None
    flown: int
    minutes: int
    landed: int | None
    trip: list[int]
    deadheads: list[int]
    period: int = 1
    moved: bool = False
    earliest: int | None = None
    claim: int | None = None
    duties: list[dict] = field(default_factory=list)
    hotels: list[dict] = field(default_factory=list)
    call: int | None = None

    def release(self, start: int) -> int | None:
        """When its duty period ends if it flies no more: after its last block-in, at its shift's end for an unused
        standby reserve, else at its report or the start, whichever is later."""
        u = self.unit
        if self.landed is not None:
            return self.landed + crews.DEBRIEF
        if u.reserve == "standby" and not self.moved and u.until is not None:
            return u.until
        return None if self.report is None else max(self.report, start)


@dataclass(frozen=True)
class CrewDay:
    run: sim.Execution
    crews: dict[int, dict[str, str]]
    riders: dict[int, tuple[str, ...]]
    uncrewed: tuple[int, ...]
    cost: dict[str, float]
    units: dict[str, dict]
    status: dict[int, list[tuple]]

    @property
    def crew_cost(self) -> float:
        return sum(self.cost.values())

    @property
    def total(self) -> float:
        return self.run.cost.total + self.crew_cost


def execute(task: Task, plan: Mapping, units: Sequence[Unit], world: sim.World | None = None,
            local: crews.Local | None = None, fill: bool = True, log: bool = True,
            airports: Mapping[str, Airport] | None = None) -> CrewDay:
    """The day under `plan` with `units` at the start (the truth by default); `local` reads a station's clock (the
    hub's by default), `airports` hold every station a unit may end at or be wanted at (the task's by default)."""
    world = sim.truth(task) if world is None else world
    pool = Pool(task, units, local or (lambda s, m: m), plan, fill, log, airports or task.airports)
    run = sim.execute(task, plan, world, pool)
    spent = pool.spent()
    return CrewDay(run, dict(pool.crewed), {i: tuple(v) for i, v in pool.riders.items()}, tuple(sorted(pool.uncrewed)),
                   {k: sum(v[k] for v in spent.values()) for k in ("deadhead", "hotel", "end")}, pool.unit_log(spent),
                   pool.status)


def audit(task: Task, units: Sequence[Unit], plan: Mapping, deps: Mapping[int, int], blocks: Mapping[int, int],
          local: crews.Local | None = None, airports: Mapping[str, Airport] | None = None,
          ends: Mapping[int, str] | None = None) -> list[str]:
    """A plan's crews and riders over fixed departures and blocks (and where each flight ended, for a diversion),
    checked move by move against the sim's rules in departure order (no aircraft): the moves the sim would refuse."""
    pool = Pool(task, units, local or (lambda s, m: m), plan, False, False, airports or task.airports, ends or {})
    out = []
    for i in sorted(deps, key=lambda i: (deps[i], i)):
        f, t = task.flight(i), deps[i]
        entry = plan.get(i, {})
        picks = {}
        for kind, uid in entry.get("crew", {}).items():
            how = pool.operate(pool.state[uid], f, t)
            if how is None:
                out.append(f"{f.flight} {f.orig}-{f.dest} at {t}: {kind} {uid}")
            else:
                picks[kind] = (uid, how)
        pool.picks[i] = picks
        pool.fly(f, t, t + blocks[i], entry)
        out += [f"{f.flight} {f.orig}-{f.dest} at {t}: rider {u}" for u in entry.get("deadhead", ())
                if u not in pool.riders.get(i, ())]
    return out


class Pool:
    def __init__(self, task: Task, units: Sequence[Unit], local: crews.Local, plan: Mapping, fill: bool, log: bool,
                 airports: Mapping[str, Airport], ends: Mapping[int, str] | None = None):
        self.task, self.local, self.fill, self.log = task, local, fill, log
        self.airports, self.diverted = airports, ends or {}
        self.reports = dict(plan.get("reports", {}))
        self.uncalled: dict[str, tuple[int, int]] = {}
        self.held: dict[int, list[str]] = defaultdict(list)
        want = {int(k): v for k, v in plan.items() if k != "reports"}
        self.named = {(kind, int(i)): u for i, v in want.items() for kind, u in v.get("crew", {}).items()}
        self.scripted = set(self.named.values()) | {u for v in want.values() for u in v.get("deadhead", ())} | \
            {k.split("#")[0] for k in self.reports}
        self.state = {}
        for u in units:
            s = State(u, u.station, u.ready, u.report, u.hour, u.rap if u.reserve == "short" else None, u.flown,
                      u.minutes, u.landed, list(u.trip), list(u.deadheads), moved=u.landed is not None)
            if u.reserve == "short" and u.report is None:
                s.earliest = crews.on_hour(max(u.rap, task.start) + u.lead)
                self.uncalled[u.id] = (s.earliest, s.ready)
            if u.report is not None:
                s.duties.append(duty(u.report, u.at or None, "on duty"))
            self.state[u.id] = s
        self.at: dict[tuple, set[str]] = defaultdict(set)
        for u in units:
            self.at[(u.kind, u.group, u.station)].add(u.id)
        self.planned = {(u.kind, i): u.id for u in units for i in u.trip}
        self.watch: dict[str, set[int]] = defaultdict(set)
        for (_, i), uid in [*self.planned.items(), *self.named.items()]:
            self.watch[uid].add(i)
        seats: dict[str, int] = {}
        for t in task.tails:
            seats[t.family] = min(seats.get(t.family, t.seats), t.seats)
        self.free = {f.id: max(0, seats[f.family] - f.pax) for f in task.flights}
        self.picks: dict[int, dict[str, tuple]] = {}
        self.ends = crews.Ends(units, airports, task.date)
        self.waiting: dict[tuple, set[int]] = defaultdict(set)
        self.wake: list[tuple[int, int]] = []
        self.crewed: dict[int, dict[str, str]] = defaultdict(dict)
        self.riders: dict[int, list[str]] = defaultdict(list)
        self.uncrewed: list[int] = []
        self.done: set[int] = set()
        self.status: dict[int, list[tuple]] = defaultdict(list)
        if log:
            for f in task.flights:
                self.judge(f, task.start)

    def board(self, f: Flight, t: int, entry: Mapping, left: bool) -> int | None:
        """t if f's crews are there now (held for `fly`), else the minute to try again (None: when woken)."""
        group = crews.TYPE[f.family]
        picks, later = {}, []
        for kind in crews.KINDS:
            got, retry = self.choose(kind, group, f, t, entry.get("crew", {}).get(kind))
            if got is not None:
                picks[kind] = got
            elif retry is not None:
                later.append(retry)
        if left or len(picks) == len(crews.KINDS):
            self.picks[f.id] = picks
            return t
        for kind in crews.KINDS:
            if kind not in picks:
                self.waiting[(kind, group, f.orig)].add(f.id)
        return max(later) if later else None

    def choose(self, kind: str, group: str, f: Flight, t: int, name: str | None) -> tuple:
        """((unit, how it starts), None) for the unit that takes f at t, or (None, the minute to try again or None)."""
        if name:
            s = self.state[name]
            if s.station != f.orig:
                return None, None
            if s.ready > t:
                return None, s.ready
            how = self.operate(s, f, t)
            return ((name, how), None) if how else (None, self.later(s, f, t))
        if not self.fill:
            return None, None
        everyone = self.at[(kind, group, f.orig)]
        mine = self.planned.get((kind, f.id))
        spare = [u for u in everyone - self.scripted if u != mine and not self.state[u].trip
                 and self.available(self.state[u], f)]
        spare.sort(key=lambda u: (self.state[u].ready, u))
        if mine in everyone and self.available(self.state[mine], f):
            spare.insert(0, mine)
        for u in spare:
            s = self.state[u]
            if s.ready <= t and (how := self.operate(s, f, t)):
                return (u, how), None
        waits = [w for u in spare if (w := self.later(self.state[u], f, t)) is not None]
        reserve = self.reserve(kind, group, f, t)
        if reserve is not None and (not waits or reserve[1] < min(waits)):
            s = self.state[reserve[0]]
            s.claim, s.call, s.earliest = f.id, max(t, s.unit.rap, self.task.start), reserve[1] - crews.BRIEF
            s.ready = reserve[1]
            self.held[f.id].append(reserve[0])
            return None, reserve[1]
        return None, min(waits) if waits else None

    def available(self, s: State, f: Flight) -> bool:
        """Whether the default rule may give s flight f: not held for another flight, and no uncalled short-call
        reserve unless held for f."""
        if s.claim is not None:
            return s.claim == f.id
        return not (s.unit.reserve == "short" and s.report is None)

    def reserve(self, kind: str, group: str, f: Flight, t: int) -> tuple[str, int] | None:
        """An uncalled short-call reserve of the base, unnamed, and the earliest it could leave if called now."""
        best = None
        for u in sorted(self.at[(kind, group, f.orig)] - self.scripted):
            s = self.state[u]
            if s.unit.reserve != "short" or s.report is not None or s.claim is not None:
                continue
            call = max(t, s.unit.rap, self.task.start)
            report = crews.on_hour(call + s.unit.lead)
            if report - s.unit.lead > s.unit.until:
                continue
            go = report + crews.BRIEF
            if best is None or go < best[1]:
                best = (u, go)
        return best

    def operate(self, s: State, f: Flight, t: int) -> tuple | None:
        """How s may operate f leaving at t: ("on",) in its period, ("report", r, hour) or ("rest", r, hour) starting
        one; None if it may not."""
        u = s.unit
        if s.station != f.orig or s.ready > t:
            return None
        own = s.period == 1 and f.id in u.trip
        if u.id + "#2" in self.reports and s.period == 1 and (start := self.begin(s, t)) is not None:
            return start[:3] if self.fits(u, start[1], start[2], 0, 0, f, t, False, start[3]) else None
        if s.report is not None and not self.over(s, t) and \
                self.fits(u, s.report, s.hour, s.flown, s.minutes, f, t, own, s.rap):
            return ("on",)
        start = self.begin(s, t)
        if start is not None and self.fits(u, start[1], start[2], 0, 0, f, t, False, start[3]):
            return start[:3]
        return None

    def begin(self, s: State, t: int) -> tuple | None:
        """The period s would start to leave at t: ("report"|"rest", report, hour, short-call period start)."""
        u = s.unit
        if u.reserve == "short" and s.report is None:
            r = self.reports.get(u.id, (t - crews.BRIEF) // 60 * 60)
            if r % 60 or r < s.earliest or r - u.lead > u.until or not r + crews.BRIEF <= t < r + crews.BRIEF + 60:
                return None
            return "report", r, crews.local_hour(self.local, u.home, r), u.rap
        release = s.release(self.task.start)
        if s.period != 1 or release is None:
            return None
        r = self.reports.get(u.id + "#2", (t - crews.BRIEF) // 60 * 60)
        if r % 60 or r < release + crews.rest(u.kind) or not r + crews.BRIEF <= t < r + crews.BRIEF + 60:
            return None
        return "rest", r, crews.local_hour(self.local, s.station, r), None

    def later(self, s: State, f: Flight, t: int) -> int | None:
        """The next minute after t at which s, there, might take f: when it is free, reports or is rested."""
        u = s.unit
        if s.ready > t:
            return s.ready
        if u.reserve == "short" and s.report is None:
            r = self.reports.get(u.id, max(s.earliest, (t - crews.BRIEF) // 60 * 60))
            go = r + crews.BRIEF
            return go if go > t else None
        release = s.release(self.task.start)
        if s.period != 1 or release is None:
            return None
        r = self.reports.get(u.id + "#2", crews.on_hour(release + crews.rest(u.kind)))
        go = max(r + crews.BRIEF, crews.on_hour(release + crews.rest(u.kind)) + crews.BRIEF)
        return go if go > t else None

    def fits(self, u: Unit, report: int, hour: int, flown: int, minutes: int, f: Flight, t: int, own: bool,
             rap: int | None) -> bool:
        if u.kind == "pilot":
            return crews.pilot_fits(report, hour, flown, minutes, t, f.sched_block, rap)
        return crews.cabin_fits(report, t, f.sched_block, own)

    def over(self, s: State, t: int) -> bool:
        """An airport-standby reserve that has not moved may leave only until its shift ends."""
        u = s.unit
        return u.reserve == "standby" and not s.moved and u.until is not None and t > u.until

    def ride(self, s: State, f: Flight, t: int) -> tuple | None:
        u = s.unit
        if s.station != f.orig or s.ready > t:
            return None
        start = self.begin(s, t)
        if s.report is None or (start is not None and start[0] == "rest"):
            return start[:3] if start is not None else None
        if not self.over(s, t) and (u.kind == "pilot" or (s.period == 1 and f.id in u.deadheads)
                                    or crews.cabin_fits(s.report, t, f.sched_block)):
            return ("on",)
        return None

    def fly(self, f: Flight, t: int, arr: int, entry: Mapping) -> None:
        picks = self.picks.pop(f.id, {})
        for kind in crews.KINDS:
            if kind not in picks:
                self.uncrewed.append(f.id)
                break
        touched = set()
        for kind, (uid, how) in picks.items():
            s = self.state[uid]
            self.start(s, how, f.orig)
            self.move(s, f, t, arr, True)
            self.crewed[f.id][kind] = uid
            touched.add(uid)
        room = self.free[f.id]
        riders = list(entry.get("deadhead", ()))
        if self.fill:
            riders += sorted(u for u, s in self.state.items() if f.id in s.deadheads and u not in self.scripted
                             and u not in riders)
        for uid in riders:
            s = self.state[uid]
            how = self.ride(s, f, t)
            if how is None or s.unit.size > room:
                continue
            room -= s.unit.size
            self.start(s, how, f.orig)
            self.move(s, f, t, arr, False)
            self.riders[f.id].append(uid)
            touched.add(uid)
        for kind in crews.KINDS:
            uid = self.planned.get((kind, f.id))
            if uid is not None and f.id in self.state[uid].trip:
                self.state[uid].trip.remove(f.id)
                touched.add(uid)
        self.release(f.id, t)
        self.done.add(f.id)
        if self.log:
            self.status[f.id].append((t, "flown" if f.id not in self.uncrewed else "flown without a legal crew",
                                      None))
            self.rejudge(touched, t)

    def release(self, i: int, t: int | None = None) -> None:
        """The short-call reserves held for flight i, which flew (at t) or was cancelled: one that never reported is as
        if never called, and a flight waiting at its base tries again at t."""
        for uid in self.held.pop(i, ()):
            s = self.state[uid]
            if s.claim == i:
                s.claim = None
                if s.report is None:
                    s.call = None
                    s.earliest, s.ready = self.uncalled[s.unit.id]
                    u = s.unit
                    for j in self.waiting.pop((u.kind, u.group, s.station), ()) if t is not None else ():
                        self.wake.append((j, t))

    def start(self, s: State, how: tuple, station: str) -> None:
        if how[0] == "on":
            return
        _, r, hour = how
        u = s.unit
        if how[0] == "rest":
            release = s.release(self.task.start)
            if s.duties:
                s.duties[-1]["release"] = release
            if s.station != u.home:
                s.hotels.append({"station": s.station, "from": release, "to": r})
            s.period, s.rap = 2, None
        else:
            s.call = s.call if s.call is not None else r - u.lead
            s.rap = u.rap
        s.report, s.hour, s.flown, s.minutes, s.landed = r, hour, 0, 0, None
        s.duties.append(duty(r, station, "rest" if how[0] == "rest" else "call"))

    def move(self, s: State, f: Flight, t: int, arr: int, op: bool) -> None:
        u = s.unit
        dest = self.diverted.get(f.id, f.dest)
        self.at[(u.kind, u.group, s.station)].discard(u.id)
        s.station, s.ready, s.landed, s.moved = dest, arr + crews.CONNECT, arr, True
        if op:
            s.flown, s.minutes = s.flown + 1, s.minutes + (arr - t)
        else:
            if f.id in s.deadheads:
                s.deadheads.remove(f.id)
        if not s.duties:
            s.duties.append(duty(s.report, u.at or None, "on duty"))
        s.duties[-1]["legs" if op else "deadheads"].append((f.id, t, arr))
        s.duties[-1]["release"] = arr + crews.DEBRIEF
        self.at[(u.kind, u.group, dest)].add(u.id)
        for i in self.waiting.pop((u.kind, u.group, dest), ()):
            self.wake.append((i, s.ready))

    def woken(self) -> list[tuple[int, int]]:
        out, self.wake = self.wake, []
        return out

    def cancel(self, i: int, decided: int) -> None:
        touched = set()
        for kind in crews.KINDS:
            uid = self.planned.get((kind, i))
            if uid is not None and i in self.state[uid].trip:
                self.state[uid].trip.remove(i)
                touched.add(uid)
        self.release(i)
        self.done.add(i)
        if self.log:
            self.status[i].append((decided, "cancelled", None))
            self.rejudge(touched, decided)

    def rejudge(self, units: set[str], t: int) -> None:
        for uid in units:
            for i in sorted(self.watch[uid]):
                if i not in self.done:
                    self.judge(self.task.flight(i), t)

    def judge(self, f: Flight, t: int) -> None:
        """f's crew status from minute t, logged when it changes."""
        worst = (LEGAL, None)
        for kind in crews.KINDS:
            uid = self.named.get((kind, f.id)) or self.planned.get((kind, f.id))
            got = (NO_CREW, None) if uid is None else self.outlook(self.state[uid], f)
            if got[0] == NO_CREW or (got[0] == TIMES_OUT and (worst[0] == LEGAL or got[1] < worst[1])):
                worst = got
            if worst[0] == NO_CREW:
                break
        log = self.status[f.id]
        if not log or log[-1][1:] != worst:
            log.append((t, *worst))

    def outlook(self, s: State, f: Flight) -> tuple[str, int | None]:
        """Whether s, following its trip to f, can take it: legal, timing out at its last legal departure, or not."""
        u = s.unit
        trip = s.trip
        if f.id in trip:
            before = trip[:trip.index(f.id)]
            path = [self.task.flight(i) for i in before]
            station = path[0].orig if path else f.orig
            chain = [*path, f]
            for a, b in zip(chain, chain[1:], strict=False):
                if a.dest != b.orig:
                    return NO_CREW, None
        else:
            before, path, station = [], [], f.orig
        if s.station != station:
            return NO_CREW, None
        if s.report is None:
            return LEGAL, None
        own = s.period == 1 and f.id in u.trip
        if u.kind == "pilot":
            minutes = s.minutes + sum(g.sched_block for g in path) + f.sched_block
            if minutes > crews.flight_time(s.hour):
                return NO_CREW, None
            last = crews.pilot_limit(s.report, s.hour, s.flown + len(path) + 1, s.rap) - f.sched_block
        elif own:
            return LEGAL, None
        else:
            last = s.report + crews.CABIN_DUTY - crews.DEBRIEF - f.sched_block
        if last < max(f.sched_dep, s.ready):
            return NO_CREW, None
        return TIMES_OUT, last

    def spent(self) -> dict[str, dict[str, float]]:
        """Each unit's crew cost: its deadheads off its trip, its nights away from base, its share of the end of the
        day."""
        d = self.task.date
        end = self.ends.cost({uid: s.station for uid, s in self.state.items()})
        out = {}
        for uid, s in self.state.items():
            u = s.unit
            planned = set(u.deadheads)
            dh = sum(crews.deadhead(u.kind, u.group, arr - t, d) for duty in s.duties for i, t, arr in
                     duty["deadheads"] if i not in planned)
            out[uid] = {"deadhead": dh, "hotel": sum(crews.hotel(h["station"], d) * u.size for h in s.hotels),
                        "end": end[uid]}
        return out

    def unit_log(self, spent: Mapping[str, dict[str, float]]) -> dict[str, dict]:
        out = {}
        for uid, s in self.state.items():
            if not s.duties and s.call is None:
                continue
            out[uid] = {"kind": s.unit.kind, "group": s.unit.group, "home": s.unit.home, "reserve": s.unit.reserve,
                        "call": s.call, "duties": s.duties, "hotels": s.hotels, "station": s.station,
                        "cost": spent[uid]}
        return out


def duty(report: int | None, at: str | None, begins: str) -> dict:
    """A duty period in the log: its report, where, how it began (on duty at the start, a reserve's call, after
    rest), its legs and deadheads (flight, departure, arrival) and its release."""
    return {"report": report, "at": at, "begins": begins, "legs": [], "deadheads": [], "release": None}


def clock(minute: int | None) -> str | None:
    """A task minute as HH:MM on the hub's clock, +1 for the next day."""
    if minute is None:
        return None
    day, m = divmod(minute, 1440)
    return f"{m // 60:02d}:{m % 60:02d}" + (f"+{day}" if day else "")


def statuses(day: CrewDay) -> dict[int, list[dict]]:
    return {i: [{"at": clock(t), "status": st, "until": clock(until)} for t, st, until in log]
            for i, log in sorted(day.status.items())}

