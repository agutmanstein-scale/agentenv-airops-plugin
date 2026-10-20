"""Passengers (v3): who is booked on a storm day's flights and where they are going, what the day does to them, and
what that costs. The rules are the registered step 0's (cost P).

Bookings (`book`). A flight's booked passengers are its `pax` (seats x T-100 route-month load factor, model.Flight).
At JetBlue's connecting stations (CONNECT) some of a route's departing passengers arrived on an earlier JetBlue
flight: the flows per origin, connecting station and destination of the date's survey period (`Flows.load`: the DB1B
quarter up to 2025 Q2, then the DB1C month; data/pax/b6-transfers.csv), each its share of the route's departing
passengers in the survey times the route's booked passengers that day. A route-day's connecting passengers are split
over origins by largest remainder, and an origin's over the day's scheduled JetBlue connections onto the route from
that origin (scheduled connection from the station's MCT to DWELL minutes) in proportion to DB1C's B6-to-B6
connection-time distribution at the station (`Dwell`, data/pax/b6-dwell.csv), never more than either flight has left:
a passenger connects at most once (three-coupon itineraries are 0.04 to 0.2% of B6's), so an itinerary has one leg or
two. Routes in (connecting station, destination) order, origins in name order. Everyone else on a flight is local.
Deterministic. Not modelled: international connections (the survey and the BTS on-time network are domestic only),
interline connections, spill.

The day (`fly`), a pass over a finished day: passengers never change what aircraft and crews do. Crews keep seat
priority, so a flight's seats for passengers are its aircraft's seats less the people of the crew units riding it (the
family's smallest seats for a flight that never flew); the crew sim is unchanged. A group flies its booked legs; it is
disrupted when a leg it holds is cancelled (at the decision, or when it reaches the leg's origin if that is later) or
when it misses a connection (it arrives later than the outbound's departure less the station's MCT; at the outbound's
pushback). Rebooking, first come, first served by scheduled departure: disruptions in time order, ties by the
scheduled departure of the flight they were on, then group id; at its minute a disrupted group gives up its seats on
the legs it can no longer fly and takes, in order of scheduled departure (then scheduled arrival, fewer legs, ids), the
JetBlue itineraries to its final destination the schedule sells from where it is, direct or through one connecting
station at its MCT, every leg not cancelled by then and leaving at or after the group is ready (at its origin, the
station's MCT after the decision and never before its booked departure; at a connection, its arrival plus the MCT), as
many of the group as each has seats for; a rebooked flight cancelled later, or a connection missed again, disrupts it
again. Who has no seat that day takes, in the order stranded, the first seats of the next day's published BTS schedule
(`Seat`, its seats left after its own T-100 loads; with none given, today's schedule a day later, its seats not
limited), and spends the night at a hotel where it is.

Cost P (cost.priced, in passenger-minutes at the FAA's $1.07): every v2 term but two. Each passenger's delay is charged
at the final destination, rebooked or the next day's arrival included, in place of v2's delay at every leg; a cancelled
flight's booked passengers cost 66 each (K_CANCEL's refund-exposure part: a 0.3 take-up of the $235 DB1B median fare)
in place of 523, their real delay replacing its 457 minutes; and every passenger stranded overnight costs one GSA
lodging night for the city (crews.lodging). JetBlue owes no compensation, meals or hotel on a storm day (force majeure),
so none is charged.

The MIP's passengers (`Proxy`, mip.Network with `pax`): a flight's copies carry the delay of the passengers it takes to
their destination, connecting passengers included; every booked connection a binary that is 1 when its inbound flew
and its outbound was cancelled or left within the MCT of the inbound's arrival; and every way a group can be disrupted
(its first leg cancelled; or, its inbound flown, its connection broken) rebooks it onto the first OPTIONS itineraries
the schedule sells after the flight it lost, at their scheduled arrival, within each flight's seats as booked (the
family's smallest seats less its passengers, none on a cancelled one), or strands it to the next day's first
itinerary and a lodging night. It departs from the day's pass in four things: a disrupted connecting passenger also
keeps the delay of the outbound it was booked on, in place of the rebooked flights' own delays, which it does not see;
it does not count the seats freed by passengers disrupted off a flight; and it assigns seats optimally where the pass
serves first come first. `Proxy.cost` is a day under it (the assignment an LP), what the MIP's objective is for that
day; `optimal` is a day's passengers rebooked optimally over its actual flights and seats instead of first come first
served.
"""

import csv
import heapq
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date as Date
from importlib.resources import files
from pathlib import Path

import highspy
import numpy as np

from ..core.sim import FLOWN
from . import crews
from .cost import RATES, REFUND, flown_price, late
from .model import Flight, Task

CONNECT = ("JFK", "BOS", "FLL", "MCO", "SJU")
MCT = {"JFK": 45, "BOS": 45}
MCT_ELSEWHERE = 40
DWELL = 360
OPTIONS = 6
DATA = files("agentenv_airops") / "data" / "pax"
FLEW, REBOOKED, STRANDED = "flew", "rebooked", "stranded"
INF = highspy.kHighsInf


def mct(station: str) -> int:
    """DB1C's scheduled minimums: B6-to-B6 connections hardly ever under 45 minutes at JFK and BOS, 40 elsewhere."""
    return MCT.get(station, MCT_ELSEWHERE)


@dataclass(frozen=True)
class Group:
    id: int
    legs: tuple[int, ...]
    pax: int

    @classmethod
    def from_list(cls, v: list) -> "Group":
        return cls(v[0], tuple(v[1]), v[2])

    def to_list(self) -> list:
        return [self.id, list(self.legs), self.pax]


@dataclass(frozen=True)
class Seat:
    """A flight of the next day's published schedule, in minutes from the task date's midnight, and its seats left."""

    flight: str
    orig: str
    dest: str
    dep: int
    arr: int
    free: int

    @classmethod
    def from_list(cls, v: list) -> "Seat":
        return cls(*v)

    def to_list(self) -> list:
        return [self.flight, self.orig, self.dest, self.dep, self.arr, self.free]


@dataclass(frozen=True)
class Passengers:
    groups: tuple[Group, ...]
    tomorrow: tuple[Seat, ...] = ()
    source: str = ""

    def booked(self) -> Counter:
        out: Counter = Counter()
        for g in self.groups:
            for k in g.legs:
                out[k] += g.pax
        return out

    def to_dict(self) -> dict:
        return {"source": self.source, "groups": [g.to_list() for g in self.groups],
                "tomorrow": [s.to_list() for s in self.tomorrow]}

    @classmethod
    def from_dict(cls, d: Mapping) -> "Passengers":
        return cls(tuple(Group.from_list(g) for g in d["groups"]), tuple(Seat.from_list(s) for s in d["tomorrow"]),
                   d["source"])


@dataclass(frozen=True)
class Flows:
    """Per route (connecting station, destination) of a survey period, the share of its departing passengers that
    arrived on a JetBlue flight from each origin ("*": from any, spread over the inbound flights by passengers)."""

    shares: Mapping[tuple[str, str], Mapping[str, float]]
    label: str = ""

    def scaled(self, k: float) -> "Flows":
        return Flows({r: {o: k * v for o, v in by.items()} for r, by in self.shares.items()}, f"{self.label} x{k:g}")

    @classmethod
    def synthetic(cls, task: Task, share: float) -> "Flows":
        return cls({(f.orig, f.dest): {"*": share} for f in task.flights if f.orig in CONNECT}, f"synthetic {share:g}")

    @classmethod
    def load(cls, date: str, path: Path | str | None = None) -> "Flows":
        with open(str(path or DATA / "b6-transfers.csv"), newline="") as fh:
            rows = list(csv.DictReader(fh))
        p = period(date, {r["period"] for r in rows})
        shares: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        source = ""
        for r in rows:
            if r["period"] == p and r["orig"] != "*" and r["dest"] != "*":
                shares[(r["hub"], r["dest"])][r["orig"]] = int(r["transfers"]) / int(r["departing"])
                source = r["source"]
        return cls(dict(shares), f"{source} {p}")


def period(date: str, periods: Iterable[str]) -> str:
    """The survey period of a date: its DB1B quarter up to 2025 Q2, then its DB1C month; where that is missing, the
    period whose middle is nearest."""
    y, m = int(date[:4]), int(date[5:7])
    periods = sorted(periods)
    own = f"{y}Q{(m - 1) // 3 + 1}" if (y, m) <= (2025, 6) else f"{y}-{m:02d}"
    if own in periods:
        return own
    day = Date.fromisoformat(date)
    return min(periods, key=lambda p: abs((_middle(p) - day).days))


def _middle(p: str) -> Date:
    if "Q" in p:
        y, q = p.split("Q")
        return Date(int(y), 3 * int(q) - 1, 15)
    return Date(int(p[:4]), int(p[5:7]), 15)


@dataclass(frozen=True)
class Dwell:
    """DB1C's B6-to-B6 transfers per scheduled connection time at each connecting station, pooled over its months,
    in bins [lo, hi); a station without its own takes every station's."""

    bins: Mapping[str, tuple[tuple[int, int, int], ...]]

    def weight(self, hub: str, minutes: int) -> float:
        for lo, hi, n in self.bins.get(hub) or self.bins["*"]:
            if lo <= minutes < hi:
                return n / (hi - lo)
        return 0.0

    @classmethod
    def uniform(cls) -> "Dwell":
        return cls({"*": ((0, DWELL + 1, 1),)})

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Dwell":
        with open(str(path or DATA / "b6-dwell.csv"), newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r["period"] == "all"]
        bins: dict[str, list] = defaultdict(list)
        pooled: Counter = Counter()
        for r in rows:
            lo, hi, n = int(r["lo"]), int(r["hi"]), int(r["transfers"])
            bins[r["hub"]].append((lo, hi, n))
            pooled[(lo, hi)] += n
        bins["*"] = [(lo, hi, n) for (lo, hi), n in sorted(pooled.items())]
        return cls({h: tuple(sorted(v)) for h, v in bins.items()})


def book(task: Task, flows: Flows, dwell: Dwell, tomorrow: Iterable[Seat] = ()) -> Passengers:
    left = {f.id: f.pax for f in task.flights}
    inbound: dict[str, list[Flight]] = defaultdict(list)
    outbound: dict[tuple[str, str], list[Flight]] = defaultdict(list)
    for f in sorted(task.flights, key=lambda f: (f.sched_dep, f.id)):
        if f.dest in CONNECT:
            inbound[f.dest].append(f)
        if f.orig in CONNECT:
            outbound[(f.orig, f.dest)].append(f)
    pairs: Counter = Counter()
    for (h, d), outs in sorted(outbound.items()):
        by = {o: v for o, v in flows.shares.get((h, d), {}).items() if o != d and v > 0}
        load = sum(f.pax for f in outs)
        for o, n in sorted(split(round(sum(by.values()) * load), by).items()):
            ins = [i for i in inbound[h] if (i.orig == o if o != "*" else i.orig != d)]
            weight = {(i.id, j.id): dwell.weight(h, j.sched_dep - i.sched_arr) * (i.pax if o == "*" else 1)
                      for i in ins for j in outs if mct(h) <= j.sched_dep - i.sched_arr <= DWELL}
            pairs.update(allot(n, weight, left))
    legs = [(p, n) for p, n in pairs.items() if n] + [((i,), n) for i, n in left.items() if n > 0]
    return Passengers(tuple(Group(k, legs, n) for k, (legs, n) in enumerate(sorted(legs))), tuple(tomorrow),
                      flows.label)


def split(n: int, weight: Mapping) -> dict:
    """n over the keys by weight, by largest remainder, ties to the smaller key."""
    total = sum(weight.values())
    if n <= 0 or total <= 0:
        return {}
    share = {k: n * w / total for k, w in weight.items()}
    out = {k: int(s) for k, s in share.items()}
    for k in sorted(share, key=lambda k: (int(share[k]) - share[k], k))[:n - sum(out.values())]:
        out[k] += 1
    return {k: v for k, v in out.items() if v}


def allot(n: int, weight: Mapping[tuple[int, int], float], left: dict[int, int]) -> Counter:
    """n over the flight pairs by weight, by largest remainder, none past what either flight has `left` (taken)."""
    out: Counter = Counter()
    while n > 0:
        live = {p: w for p, w in weight.items() if w > 0 and left[p[0]] > 0 and left[p[1]] > 0}
        if not live:
            break
        got = 0
        for p, k in sorted(split(n, live).items()):
            k = min(k, left[p[0]], left[p[1]])
            left[p[0]] -= k
            left[p[1]] -= k
            out[p] += k
            got += k
        if not got:
            break
        n -= got
    return out


@dataclass(frozen=True)
class Part:
    """Some of a group's passengers and how their day ended: `legs` the flights they flew, `arr` their minute at the
    destination (past 1440 the next day), `at` where they spent the night when they did not get there that day."""

    group: int
    pax: int
    kind: str
    legs: tuple[int | str, ...]
    arr: int
    delay: int
    at: str | None

    def to_dict(self) -> dict:
        return {"group": self.group, "pax": self.pax, "kind": self.kind, "legs": list(self.legs), "arr": self.arr,
                "delay": self.delay, "at": self.at}


@dataclass(frozen=True)
class Day:
    parts: tuple[Part, ...]
    events: tuple[dict, ...]
    delay: int
    lodging: int

    def outcomes(self) -> dict[str, int]:
        c: Counter = Counter()
        for p in self.parts:
            c[p.kind] += p.pax
        return {k: c[k] for k in (FLEW, REBOOKED, STRANDED)}

    def misconnected(self) -> int:
        return sum(e["pax"] for e in self.events if e["kind"] == "misconnect")

    def to_dict(self) -> dict:
        return {"delay": self.delay, "lodging": self.lodging, "outcomes": self.outcomes(),
                "misconnected": self.misconnected(), "parts": [p.to_dict() for p in self.parts],
                "events": list(self.events)}


class Timetable:
    """A schedule as sold: itineraries of one flight, or two through a connecting station at its MCT."""

    def __init__(self, flights: Iterable):
        self.od: dict[tuple[str, str], list] = defaultdict(list)
        for f in sorted(flights, key=lambda f: (_dep(f), _arr(f), _key(f))):
            self.od[(f.orig, f.dest)].append(f)

    def itineraries(self, s: str, d: str, ok: Callable, then: Callable) -> list[tuple]:
        """The itineraries from s to d whose first flight passes `ok` and second `then`, by scheduled departure, then
        scheduled arrival, fewer flights and the flights."""
        out = [(_dep(f), _arr(f), 1, (_key(f),), (f,)) for f in self.od.get((s, d), ()) if ok(f)]
        for h in CONNECT:
            if h in (s, d) or not self.od.get((s, h)) or not self.od.get((h, d)):
                continue
            seconds = [g for g in self.od[(h, d)] if then(g)]
            for f in self.od[(s, h)]:
                if ok(f):
                    out += [(_dep(f), _arr(g), 2, (_key(f), _key(g)), (f, g)) for g in seconds
                            if _dep(g) >= _arr(f) + mct(h)]
        return [legs for *_, legs in sorted(out, key=lambda x: x[:4])]


def _dep(f) -> int:
    return f.sched_dep if isinstance(f, Flight) else f.dep


def _arr(f) -> int:
    return f.sched_arr if isinstance(f, Flight) else f.arr


def _key(f) -> int | str:
    return f.id if isinstance(f, Flight) else f.flight


class Tomorrow:
    """The next day's schedule and its seats left, first come first served; without one, today's a day later with
    seats not limited. Who finds no seat arrives two days after the scheduled minute."""

    def __init__(self, task: Task, seats: tuple[Seat, ...]):
        self.flights = seats or tuple(Seat(str(f.id), f.orig, f.dest, f.sched_dep + 1440, f.sched_arr + 1440, -1)
                                      for f in task.flights)
        self.table = Timetable(self.flights)
        self.free = {s.flight: s.free for s in self.flights}

    def first(self, s: str, d: str) -> int | None:
        legs = self.table.itineraries(s, d, lambda f: True, lambda f: True)
        return _arr(legs[0][-1]) if legs else None

    def take(self, s: str, d: str, n: int) -> list[tuple[tuple[str, ...], int, int]]:
        """Seats for n from s to d: (flights, how many, arrival) in order, as many as there are."""
        out = []
        for legs in self.table.itineraries(s, d, lambda f: True, lambda f: True):
            m = n if all(self.free[f.flight] < 0 for f in legs) else \
                min(n, *(self.free[f.flight] for f in legs if self.free[f.flight] >= 0))
            if m <= 0:
                continue
            for f in legs:
                if self.free[f.flight] >= 0:
                    self.free[f.flight] -= m
            out.append((tuple(f.flight for f in legs), m, _arr(legs[-1])))
            n -= m
            if not n:
                break
        return out


def seats(task: Task, runs: Mapping, riders: Mapping[int, int]) -> dict[int, int]:
    """Each flight's seats for passengers: its aircraft's (the family's smallest if it never flew) less its riders."""
    by_tail = {t.tail: t.seats for t in task.tails}
    least = smallest(task)
    return {f.id: (by_tail[runs[f.id].tail] if runs[f.id].status == FLOWN else least[f.family])
            - riders.get(f.id, 0) for f in task.flights}


def smallest(task: Task) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in task.tails:
        out[t.family] = min(out.get(t.family, t.seats), t.seats)
    return out


def fly(task: Task, passengers: Passengers, runs: Mapping, riders: Mapping[int, int] | None = None) -> Day:
    """The day's passengers on its flights `runs` (sim.FlightRun by id), `riders` the crew people riding each."""
    return _Pass(task, passengers, runs, riders or {}).run()


class _Pass:
    def __init__(self, task: Task, passengers: Passengers, runs: Mapping, riders: Mapping[int, int]):
        self.task, self.groups, self.runs = task, passengers.groups, runs
        self.table = Timetable(task.flights)
        self.next = Tomorrow(task, passengers.tomorrow)
        self.cap = seats(task, runs, riders)
        self.hold = passengers.booked()
        self.heap: list = []
        self.parts: list[Part] = []
        self.events: list[dict] = []

    def run(self) -> Day:
        for g in self.groups:
            self.walk(g, g.pax, g.legs, (), None, self.task.start)
        while self.heap:
            self.disrupted(*heapq.heappop(self.heap)[-1])
        parts = tuple(sorted(self.parts, key=lambda p: (p.group, p.kind, p.arr, tuple(map(str, p.legs)))))
        date = self.task.date
        lodging = sum(x.pax * crews.lodging(x.at, date) for x in parts if x.kind == STRANDED)
        return Day(parts, tuple(sorted(self.events, key=lambda e: (e["t"], e["group"], e["kind"]))),
                   sum(x.pax * x.delay for x in parts), round(lodging))

    def walk(self, g: Group, n: int, legs: tuple[int, ...], flew: tuple[int, ...], prev: int | None,
             now: int) -> None:
        """n of g on `legs` from the first's origin, there from `prev` (their arrival; None at their origin)."""
        for x, k in enumerate(legs):
            f, r = self.task.flight(k), self.runs[k]
            if r.status != FLOWN:
                self.push(max(r.decided, now if prev is None else prev), g, n, f, legs[x + 1:], flew, prev)
                return
            if prev is not None and r.dep < prev + mct(f.orig):
                self.push(max(r.dep, now), g, n, f, legs[x + 1:], flew, prev)
                self.events.append({"t": r.dep, "kind": "misconnect", "at": f.orig, "flight": k, "from": flew[-1],
                                    "pax": n, "group": g.id})
                return
            flew, prev = (*flew, k), r.arr
        self.done(g, n, FLEW if flew == g.legs else REBOOKED, flew, prev, None)

    def push(self, t: int, g: Group, n: int, f: Flight, *rest) -> None:
        heapq.heappush(self.heap, (t, f.sched_dep, g.id, len(self.heap), (t, g, n, f, *rest)))

    def disrupted(self, t: int, g: Group, n: int, was: Flight, rest: tuple[int, ...], flew: tuple[int, ...],
                  prev: int | None) -> None:
        for k in rest:
            self.hold[k] -= n
        task, runs, s = self.task, self.runs, was.orig
        dest = task.flight(g.legs[-1]).dest
        ready = max(t + mct(s), task.flight(g.legs[0]).sched_dep) if prev is None else max(t, prev + mct(s))

        def ok(f: Flight) -> bool:
            r = runs[f.id]
            return r.dep >= ready if r.status == FLOWN else r.decided > t and f.sched_dep >= ready

        def then(f: Flight) -> bool:
            r = runs[f.id]
            return r.dep >= ready if r.status == FLOWN else r.decided > t

        for legs in self.table.itineraries(s, dest, ok, then):
            ids = tuple(f.id for f in legs)
            m = min(n, *(self.cap[k] - self.hold[k] for k in ids))
            if m <= 0:
                continue
            for k in ids:
                self.hold[k] += m
            self.events.append({"t": t, "kind": "rebook", "at": s, "flight": was.id, "to": ids[0], "pax": m,
                                "group": g.id})
            self.walk(g, m, ids, flew, prev, t)
            n -= m
            if not n:
                return
        sched = task.flight(g.legs[-1]).sched_arr
        self.events.append({"t": t, "kind": "hotel", "at": s, "flight": was.id, "pax": n, "group": g.id})
        for legs, m, arr in self.next.take(s, dest, n):
            self.done(g, m, STRANDED, (*flew, *legs), arr, s)
            n -= m
        if n:
            self.done(g, n, STRANDED, flew, sched + 2880, s)

    def done(self, g: Group, n: int, kind: str, flew: tuple, arr: int, at: str | None) -> None:
        self.parts.append(Part(g.id, n, kind, flew, arr, max(0, arr - self.task.flight(g.legs[-1]).sched_arr), at))


@dataclass(frozen=True)
class Link:
    """A booked connection: `pax` from flight i onto j at `hub`."""

    i: int
    j: int
    hub: str
    pax: int


@dataclass(frozen=True)
class Mode:
    """One way a group is disrupted: its first leg `cut` (cancelled or auto-cancelled), or its `link` broken with its
    inbound flown; `options` the itineraries it is rebooked onto and each one's price per passenger, `night` the price
    per passenger of the night away."""

    group: int
    pax: int
    cut: int | None
    link: tuple[int, int] | None
    options: tuple[tuple[tuple[int, ...], int], ...]
    night: int


class Proxy:
    """The passengers as the MIP prices them (see the module docstring)."""

    def __init__(self, task: Task, passengers: Passengers):
        self.task = task
        table, nxt = Timetable(task.flights), Tomorrow(task, passengers.tomorrow)
        booked = passengers.booked()
        least = smallest(task)
        self.free = {f.id: max(0, least[f.family] - booked[f.id]) for f in task.flights}
        self.term: Counter = Counter()
        links: Counter = Counter()
        modes = []
        for g in passengers.groups:
            first, last = task.flight(g.legs[0]), task.flight(g.legs[-1])
            self.term[last.id] += g.pax
            modes.append(self._mode(table, nxt, g, first, last, first.id, None))
            if len(g.legs) == 2:
                links[(first.id, last.id)] += g.pax
                modes.append(self._mode(table, nxt, g, last, last, None, (first.id, last.id)))
        self.links = tuple(Link(i, j, task.flight(j).orig, n) for (i, j), n in sorted(links.items()))
        self.modes = tuple(modes)

    def _mode(self, table: Timetable, nxt: Tomorrow, g: Group, lost: Flight, last: Flight, cut: int | None,
              link: tuple[int, int] | None) -> Mode:
        def ok(f: Flight) -> bool:
            return f.sched_dep >= lost.sched_dep and f.id not in g.legs

        options = tuple((tuple(f.id for f in legs), max(0, legs[-1].sched_arr - last.sched_arr))
                        for legs in table.itineraries(lost.orig, last.dest, ok, ok)[:OPTIONS])
        a = nxt.first(lost.orig, last.dest)
        night = max(0, (last.sched_arr + 2880 if a is None else a) - last.sched_arr) + \
            crews.lodging(lost.orig, self.task.date)
        return Mode(g.id, g.pax, cut, link, options, round(night))

    def flown(self, f: Flight, dep: int, arr: int) -> int:
        return flown_price(f, dep, arr, self.term.get(f.id, 0))

    def price(self, f: Flight, decided: int, flat: bool = False) -> int:
        """A cancellation's price before rebooking: its passengers' refund exposure and notice, less the saving."""
        return f.pax * (REFUND + (0 if flat else late(f, decided))) - RATES[f.family].saved * f.sched_block

    def broken(self, link: Link | tuple[int, int], runs: Mapping) -> bool:
        """Whether a booked connection broke with its inbound flown: its outbound cancelled, or gone within the MCT."""
        i, j = (link.i, link.j) if isinstance(link, Link) else link
        a, b = runs[i], runs[j]
        return a.status == FLOWN and (b.status != FLOWN or b.dep < a.arr + mct(self.task.flight(j).orig))

    def cost(self, run, flat: bool = False) -> float:
        """A day (sim.Execution) under the proxy, its passengers rebooked as the LP would: the MIP's objective."""
        task, runs = self.task, run.flights
        total = float(run.cost.ferry)
        for f in task.flights:
            r = runs[f.id]
            total += self.flown(f, r.dep, r.arr) if r.status == FLOWN else self.price(f, r.decided, flat)
        return total + self.assign(runs)[0]

    def hit(self, mode: Mode, runs: Mapping) -> bool:
        if mode.cut is not None:
            return runs[mode.cut].status != FLOWN
        return self.broken(mode.link, runs)

    def assign(self, runs: Mapping) -> tuple[float, dict[tuple[int, int], float], dict[int, float]]:
        """The rebooking LP of a day: each disrupted mode's passengers over its options within the seats as booked of
        the flights that flew, or stranded; its cost, and the passengers per (mode, option) and stranded per mode."""
        cols, cost, rows = [], [], []
        seats: dict[int, dict[int, float]] = defaultdict(dict)
        for m, mode in enumerate(self.modes):
            if not self.hit(mode, runs):
                continue
            row = {}
            for o, (legs, price) in enumerate(mode.options):
                if all(runs[k].status == FLOWN for k in legs):
                    c = len(cols)
                    cols.append((m, o))
                    cost.append(price)
                    row[c] = 1.0
                    for k in legs:
                        seats[k][c] = 1.0
            c = len(cols)
            cols.append((m, None))
            cost.append(mode.night)
            row[c] = 1.0
            rows.append((row, mode.pax, mode.pax))
        rows += [(r, -INF, self.free[k]) for k, r in sorted(seats.items())]
        x = _lp(cost, [float(self.modes[m].pax) for m, _ in cols], rows) if cols else np.zeros(0)
        r = {(m, o): v for (m, o), v in zip(cols, x, strict=True) if o is not None and v > 1e-9}
        s = {m: v for (m, o), v in zip(cols, x, strict=True) if o is None and v > 1e-9}
        return float(np.dot(cost, x)) if cols else 0.0, r, s


def _lp(cost: list[float], ub: list[float], rows: list[tuple[dict[int, float], float, float]]) -> np.ndarray:
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("threads", 1)
    n = len(cost)
    h.addVars(n, np.zeros(n), np.array(ub, dtype=float))
    h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(cost, dtype=float))
    starts, idx, val = [], [], []
    for coefs, _, _ in rows:
        starts.append(len(idx))
        idx += list(coefs)
        val += list(coefs.values())
    h.addRows(len(rows), np.array([r[1] for r in rows], dtype=float), np.array([r[2] for r in rows], dtype=float),
              len(idx), np.array(starts, dtype=np.int32), np.array(idx, dtype=np.int32), np.array(val, dtype=float))
    h.run()
    return np.array(h.getSolution().col_value)


def optimal(task: Task, passengers: Passengers, runs: Mapping, riders: Mapping[int, int] | None = None) -> Day:
    """The day's passengers rebooked optimally instead of first come first served: every group disrupted off its
    booked legs is assigned, over the day's flights as they flew (each leg leaving at or after it is ready, every
    connection at the MCT on the actual minutes) and the next day's schedule, at the least total delay and lodging
    within each flight's seats less its riders and the booked passengers it carried (an LP, so parts may be
    fractional); who finds no seat at all arrives two days late."""
    cap = seats(task, runs, riders or {})
    table, nxt = Timetable(task.flights), Tomorrow(task, passengers.tomorrow)
    carried: Counter = Counter()
    parts, lost = [], []
    for g in passengers.groups:
        prev, flew, hit = None, (), None
        for k in g.legs:
            f, r = task.flight(k), runs[k]
            if r.status != FLOWN or (prev is not None and r.dep < prev + mct(f.orig)):
                t = max(r.decided, task.start if prev is None else prev) if r.status != FLOWN else r.dep
                ready = max(t + mct(f.orig), task.flight(g.legs[0]).sched_dep) if prev is None else \
                    max(t, prev + mct(f.orig))
                hit = (f.orig, ready, flew)
                break
            carried[k] += g.pax
            flew, prev = (*flew, k), r.arr
        if hit is None:
            parts.append(Part(g.id, g.pax, FLEW, flew, prev, max(0, prev - task.flight(g.legs[-1]).sched_arr), None))
        else:
            lost.append((g, *hit))
    cols: list[tuple[int, tuple, int, float]] = []
    rows, room = [], defaultdict(dict)
    for n, (g, s, ready, _) in enumerate(lost):
        last = task.flight(g.legs[-1])

        def ok(f: Flight, ready=ready) -> bool:
            return runs[f.id].status == FLOWN and runs[f.id].dep >= ready

        night = crews.lodging(s, task.date)
        today = [x for x in table.itineraries(s, last.dest, ok, ok)
                 if len(x) == 1 or runs[x[1].id].dep >= runs[x[0].id].arr + mct(x[0].dest)]
        row = {}
        for x in today:
            row[len(cols)] = 1.0
            for f in x:
                room[(0, f.id)][len(cols)] = 1.0
            cols.append((n, tuple(f.id for f in x), max(0, runs[x[-1].id].arr - last.sched_arr), 0.0))
        for x in nxt.table.itineraries(s, last.dest, lambda f: True, lambda f: True):
            row[len(cols)] = 1.0
            for f in x:
                if nxt.free[f.flight] >= 0:
                    room[(1, f.flight)][len(cols)] = 1.0
            cols.append((n, tuple(f.flight for f in x), max(0, x[-1].arr - last.sched_arr), night))
        row[len(cols)] = 1.0
        cols.append((n, (), 2880, night))
        rows.append((row, g.pax, g.pax))
    rows += [(r, -INF, float(cap[k] - carried[k] if not away else nxt.free[k]))
             for (away, k), r in sorted(room.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))]
    x = _lp([d + h for _, _, d, h in cols], [float(lost[n][0].pax) for n, *_ in cols], rows) if cols else []
    delay = sum(p.pax * p.delay for p in parts)
    lodging = 0.0
    for v, (n, legs, d, h) in zip(x, cols, strict=True):
        if v <= 1e-9:
            continue
        g, s, _, flew = lost[n]
        sched = task.flight(g.legs[-1]).sched_arr
        parts.append(Part(g.id, v, STRANDED if h else REBOOKED, (*flew, *legs), sched + d, d, s if h else None))
        delay += v * d
        lodging += v * h
    return Day(tuple(parts), (), round(delay), round(lodging))
