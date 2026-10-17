"""A storm-day task as one JSON line: a real storm date, every JetBlue flight and aircraft of it inside the FAA's
programs at JFK and BOS (programs.network), or one hub window of it (programs.candidates).

Times are minutes from 00:00 hub-local on `date` (`base` is that midnight's UTC epoch minute), as in v1. A task carries
everything the truth, the forecast and the references read, and nothing they would have to fetch:
- flights: the task's legs from `start`, with the scheduled and the truth block, pax capped at the smallest
  seats of the family's tails in the task (`pax_raw` kept), the exogenous delay `lb` with NAS delay removed for a
  flight controlled or GS-held on its recorded departure, the out-and-back `pair`, the `deadline`,
  JetBlue's recorded outcome and, when the task was built with the crew model (`bases`, the crew bases; empty when
  off), its crew: the pairing, its FAR 117 limit, the crew deadline and the end of the legal rest (repairs.crews);
- tails: split family, position and ready time at the start, their legs, busy blocks and Moves on the leg before
  them, the turns JetBlue flew between their legs, and, unless the task carries pooled `needs`, tomorrow's need;
- programs per hub (both hubs in every task): the advisories as parsed, the in-sample unseen arrivals per hour of
  each GDP or proposed advisory and the airport's scale, the truth segments with JetBlue's slot pool, the ground-stop
  pieces, the VMC called arrival rate, and the demand: every BTS-scheduled arrival at the hub over the task, so the
  forecast RBS can run at any watch and the other carriers' controlled arrivals carry their BTS NAS delay for the
  fidelity clause.

A task may carry v3's realism `layers` (Layers, programs.layers builds them from pinned public inputs); a task that
sets any is written as model version LAYERED, one without (None, or Layers() with nothing set) is v2's task byte
for byte. `version` is the v2 model's: LAYERED is read back as VERSION and written from the layers alone.

With a finite reserve pool at a crew base (`Layers.reserves`, docs/v3/DESIGN.md §3.4) a departure from that base gets
its pairing's crew deadline as an outstation departure has it (the report + the FAR 117 limit + EXTENSION, less its
scheduled block, its rest REST after it: `Task.crew`), and may fly past it only on a reserve crew the plan calls
(`reserve`, granted while the base's pool lasts: sim.granted). A base the pool leaves out keeps Edgar's unlimited
reserves.
"""

import json
import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

from ..core.model import Airport
from .advisories import HUBS, Advisory, Scope, Stop, nm

VERSION = 3
LAYERED = 4
EXTENSION = 120
REST = 600
LAGS = {"advisory": 0, "metar": 5, "speci": 5, "taf": 10}


@dataclass(frozen=True)
class Outcome:
    """What JetBlue's flight did (BTS): `dep` when it left, `arr` and `end` when it reached a station."""

    cancelled: bool
    diverted: bool
    code: str
    dep: int | None
    arr: int | None
    end: str | None
    tail: str

    @classmethod
    def from_dict(cls, d: Mapping) -> "Outcome":
        return cls(d["cancelled"], d["diverted"], d["code"], d["dep"], d["arr"], d["end"], d["tail"])

    def to_dict(self) -> dict:
        return {"cancelled": self.cancelled, "diverted": self.diverted, "code": self.code, "dep": self.dep,
                "arr": self.arr, "end": self.end, "tail": self.tail}

    @property
    def flown(self) -> bool:
        return self.arr is not None


@dataclass(frozen=True)
class Crew:
    """A flight's crew (repairs.crews): its `pairing` reports at `report` at `station` for `segments` legs under a
    FAR 117 Table B limit of `fdp` minutes; `deadline` is the last minute the flight may leave for its crew to block in
    within that limit and the 117.19 extension (None from a base, whose reserves take over), `rest` the first minute
    it may leave after their legal rest."""

    pairing: str
    report: int
    station: str
    segments: int
    fdp: int
    deadline: int | None
    rest: int | None

    @classmethod
    def from_dict(cls, d: Mapping) -> "Crew":
        return cls(d["pairing"], d["report"], d["station"], d["segments"], d["fdp"], d["deadline"], d["rest"])

    def to_dict(self) -> dict:
        return {"pairing": self.pairing, "report": self.report, "station": self.station, "segments": self.segments,
                "fdp": self.fdp, "deadline": self.deadline, "rest": self.rest}


@dataclass(frozen=True)
class Flight:
    """`controlled` is the GDP advisory whose segment governed the flight on its recorded departure (the scheduled one
    when cancelled), `gs_held` the ground-stop chain that held it; `exempt` marks a flight into a hub whose origin no
    program of that hub covers; `crew` is None without the crew model."""

    id: int
    flight: str
    orig: str
    dest: str
    sched_dep: int
    sched_arr: int
    sched_block: int
    block: int
    pax: int
    pax_raw: int
    family: str
    lb: int
    nas_removed: int
    controlled: str | None
    gs_held: str | None
    exempt: bool
    pair: int | None
    deadline: int
    actual: Outcome
    crew: Crew | None = None

    @classmethod
    def from_dict(cls, d: Mapping) -> "Flight":
        return cls(d["id"], d["flight"], d["orig"], d["dest"], d["sched_dep"], d["sched_arr"], d["sched_block"],
                   d["block"], d["pax"], d["pax_raw"], d["family"], d["lb"], d["nas_removed"], d["controlled"],
                   d["gs_held"], d["exempt"], d["pair"], d["deadline"], Outcome.from_dict(d["actual"]),
                   Crew.from_dict(d["crew"]) if d.get("crew") else None)

    def to_dict(self) -> dict:
        return {"id": self.id, "flight": self.flight, "orig": self.orig, "dest": self.dest,
                "sched_dep": self.sched_dep, "sched_arr": self.sched_arr, "sched_block": self.sched_block,
                "block": self.block, "pax": self.pax, "pax_raw": self.pax_raw, "family": self.family, "lb": self.lb,
                "nas_removed": self.nas_removed, "controlled": self.controlled, "gs_held": self.gs_held,
                "exempt": self.exempt, "pair": self.pair, "deadline": self.deadline, "actual": self.actual.to_dict(),
                "crew": self.crew.to_dict() if self.crew else None}

    @property
    def number(self) -> int:
        return int(self.flight.split()[-1])

    def legal(self, t: int) -> int:
        """The first minute from t its crew may take it: t, or the end of their rest when t falls after the crew
        deadline and before it."""
        c = self.crew
        return c.rest if c and c.deadline is not None and c.deadline < t < c.rest else t


@dataclass(frozen=True)
class Move:
    """A chain break's fixed, uncosted repositioning, bound to the aircraft that flies `leg`, the tail's leg before
    the break (its destination `orig`, its scheduled arrival `leave`): landed and turned, that aircraft is ready at
    `dest` at `ready` or then, whichever is later. Legs the plan cancels into a break were never flown, so they join
    its repositioning; a leg cancelled after it moves nothing (sim.routes)."""

    leg: int
    orig: str
    dest: str
    leave: int
    ready: int

    @classmethod
    def from_dict(cls, d: Mapping) -> "Move":
        return cls(d["leg"], d["orig"], d["dest"], d["leave"], d["ready"])

    def to_dict(self) -> dict:
        return {"leg": self.leg, "orig": self.orig, "dest": self.dest, "leave": self.leave, "ready": self.ready}


@dataclass(frozen=True)
class Busy:
    """A long daytime same-station gap in a tail's schedule, which likely hides an international leg BTS lacks: the
    aircraft that flies `leg` is busy after it lands until `ready`, or to the end of its turn if that is later. A
    cancelled `leg` has no block, so nothing is taken or added."""

    leg: int
    ready: int

    @classmethod
    def from_dict(cls, d: Mapping) -> "Busy":
        return cls(d["leg"], d["ready"])

    def to_dict(self) -> dict:
        return {"leg": self.leg, "ready": self.ready}


@dataclass(frozen=True)
class Connection:
    """A turn JetBlue flew: the aircraft that flew `leg` next flew `next`, `ground` minutes after it reached the gate
    (BTS gate times, the tail's consecutive flown legs)."""

    leg: int
    next: int
    ground: int

    @classmethod
    def from_list(cls, v: list) -> "Connection":
        return cls(v[0], v[1], v[2])

    def to_list(self) -> list:
        return [self.leg, self.next, self.ground]


@dataclass(frozen=True)
class Tail:
    """`pos` and `ready` already close a break before the first leg (the tail reappears at its origin, ready at its
    scheduled departure less the turn); `legs` are its flights in order, a whole-network tail's the legs it flew in the
    order it flew them, a hub window's in scheduled order with cancelled ones; `end_req` is the last destination of
    those legs, tomorrow's need when the task carries no pooled `needs`."""

    tail: str
    family: str
    type: str
    seats: int
    pos: str
    ready: int
    legs: tuple[int, ...]
    busy: tuple[Busy, ...]
    repositions: tuple[Move, ...]
    end_req: str
    actual_end: str
    connections: tuple[Connection, ...] = ()

    @classmethod
    def from_dict(cls, d: Mapping) -> "Tail":
        return cls(d["tail"], d["family"], d["type"], d["seats"], d["pos"], d["ready"], tuple(d["legs"]),
                   tuple(Busy.from_dict(b) for b in d["busy"]), tuple(Move.from_dict(m) for m in d["repositions"]),
                   d["end_req"], d["actual_end"], tuple(Connection.from_list(c) for c in d.get("connections", ())))

    def to_dict(self) -> dict:
        return {"tail": self.tail, "family": self.family, "type": self.type, "seats": self.seats, "pos": self.pos,
                "ready": self.ready, "legs": list(self.legs), "busy": [b.to_dict() for b in self.busy],
                "repositions": [m.to_dict() for m in self.repositions], "end_req": self.end_req,
                "actual_end": self.actual_end, "connections": [c.to_list() for c in self.connections]}


@dataclass(frozen=True)
class Arrival:
    """One BTS-scheduled arrival at a hub, any carrier: `dep` is its recorded departure (None when cancelled), `nas`
    its BTS NASDelay, `flight` the task flight it is (None outside the task), `controlled` the GDP advisory whose
    segment governed it by the schedule-based test on its recorded departure, `diverted` whether it flew but landed
    elsewhere (BTS gives such a flight no NAS delay)."""

    carrier: str
    number: int
    orig: str
    sched_dep: int
    sched_arr: int
    dep: int | None
    cancelled: bool
    nas: int
    flight: int | None
    controlled: str | None
    diverted: bool = False

    @classmethod
    def from_list(cls, v: list) -> "Arrival":
        return cls(v[0], v[1], v[2], v[3], v[4], v[5], bool(v[6]), v[7], v[8], v[9], bool(v[10]))

    def to_list(self) -> list:
        return [self.carrier, self.number, self.orig, self.sched_dep, self.sched_arr, self.dep, int(self.cancelled),
                self.nas, self.flight, self.controlled, int(self.diverted)]

    @property
    def departs(self) -> int:
        """The departure the build's tests read: the recorded one, the scheduled one for a cancelled flight."""
        return self.sched_dep if self.dep is None else self.dep

    @property
    def key(self) -> tuple:
        """Ration-by-schedule order."""
        return self.sched_arr, self.carrier, self.number, self.orig


@dataclass(frozen=True)
class Segment:
    """One GDP advisory as it ran: it governs departures in [since, until) whose scheduled arrival is in
    [start, end]; `slots` are JetBlue's pool, the slot minutes RBS gave its task-scope, program-scope, non-exempt
    flights (`holders`), and `e` the unseen arrivals per hour it was rationed with."""

    id: str
    hub: str
    since: int
    until: int
    start: int
    end: int
    e: float
    slots: tuple[int, ...]
    holders: tuple[int, ...]

    @classmethod
    def from_dict(cls, d: Mapping) -> "Segment":
        return cls(d["id"], d["hub"], d["since"], d["until"], d["start"], d["end"], d["e"], tuple(d["slots"]),
                   tuple(d["holders"]))

    def to_dict(self) -> dict:
        return {"id": self.id, "hub": self.hub, "since": self.since, "until": self.until, "start": self.start,
                "end": self.end, "e": self.e, "slots": list(self.slots), "holders": list(self.holders)}


@dataclass(frozen=True)
class Programs:
    """A hub's programs: `e_fit` is each GDP or proposed advisory's unseen arrivals per hour at the airport's
    train-fitted `scale` on its stated AVERAGE DELAY (unseen.table), the airport's median `e_median` where the form
    can't match."""

    hub: str
    aar: int
    scale: float
    e_median: float
    e_fit: dict[str, float]
    advisories: tuple[Advisory, ...]
    segments: tuple[Segment, ...]
    stops: tuple[Stop, ...]
    demand: tuple[Arrival, ...]

    @classmethod
    def from_dict(cls, d: Mapping) -> "Programs":
        return cls(d["hub"], d["aar"], d["scale"], d["e_median"], dict(d["e_fit"]),
                   tuple(Advisory.from_dict(a) for a in d["advisories"]),
                   tuple(Segment.from_dict(s) for s in d["segments"]), tuple(Stop.from_dict(s) for s in d["stops"]),
                   tuple(Arrival.from_list(a) for a in d["demand"]))

    def to_dict(self) -> dict:
        return {"hub": self.hub, "aar": self.aar, "scale": self.scale, "e_median": self.e_median,
                "e_fit": dict(self.e_fit), "advisories": [a.to_dict() for a in self.advisories],
                "segments": [s.to_dict() for s in self.segments], "stops": [s.to_dict() for s in self.stops],
                "demand": [a.to_list() for a in self.demand]}

    def e(self, advisory: str) -> float:
        return self.e_fit.get(advisory, self.e_median)

    @property
    def e_bar(self) -> float:
        """The airport's median unseen arrivals per hour at its scale: the nominal cap's e."""
        return self.e_median


@dataclass(frozen=True)
class Layers:
    """The world's realism layers beyond the FAA's programs, hours and minutes from the task's midnight:
    JetBlue's departure cap per hour at a hub (`dep_caps`) and arrival cap per hour at an outstation (`arr_caps`),
    ramp closures per station as (start, end, dep_cap, arr_cap): JetBlue's departures from the station leaving in
    [start, end) total at most dep_cap, and its arrivals there inside it at most arr_cap; the reserve crews per crew
    base, and whether a cancelled flight's slot is released to compression (`release`). A hub's arrivals answer to
    its programs and nominal cap, so an arrival cap at a hub is a ValueError.

    The desk's news beyond the advisories (docs/v3/DESIGN.md §4): the hubs' weather as (minute, kind, station, text),
    kind "metar", "speci" or "taf", the minute its observation or issue (layers.weather), and the delivery lag per
    product kind in minutes (`lags`, LAGS by default: advisory 0, metar 5, speci 5, taf 10), each item reaching the
    desk at its minute plus its kind's lag. Both are written only when set, so a task without them reads and writes
    as before."""

    dep_caps: dict[str, dict[int, int]] = field(default_factory=dict)
    arr_caps: dict[str, dict[int, int]] = field(default_factory=dict)
    closures: dict[str, tuple[tuple[int, int, int, int], ...]] = field(default_factory=dict)
    reserves: dict[str, int] = field(default_factory=dict)
    release: bool = False
    weather: tuple[tuple[int, str, str, str], ...] = ()
    lags: dict[str, int] = field(default_factory=lambda: dict(LAGS))

    def __post_init__(self):
        hubs = sorted(set(self.arr_caps) & set(HUBS))
        if hubs:
            raise ValueError(f"arrival caps at hub(s) {', '.join(hubs)}: a hub's arrivals answer to its programs "
                             "and nominal cap")

    @classmethod
    def from_dict(cls, d: Mapping) -> "Layers":
        return cls({s: {int(h): c for h, c in by.items()} for s, by in d["dep_caps"].items()},
                   {s: {int(h): c for h, c in by.items()} for s, by in d["arr_caps"].items()},
                   {s: tuple((a, b, c, e) for a, b, c, e in spans) for s, spans in d["closures"].items()},
                   dict(d["reserves"]), d["release"], tuple((m, k, s, x) for m, k, s, x in d.get("weather", ())),
                   dict(d.get("lags", LAGS)))

    def to_dict(self) -> dict:
        out = {"dep_caps": {s: {str(h): c for h, c in sorted(by.items())} for s, by in self.dep_caps.items()},
               "arr_caps": {s: {str(h): c for h, c in sorted(by.items())} for s, by in self.arr_caps.items()},
               "closures": {s: [list(x) for x in spans] for s, spans in self.closures.items()},
               "reserves": dict(self.reserves), "release": self.release}
        if self.weather:
            out["weather"] = [list(x) for x in self.weather]
        if self.lags != LAGS:
            out["lags"] = dict(self.lags)
        return out


@dataclass(frozen=True)
class Task:
    """`hubs` are a whole-network task's storm hubs (`hub` the one whose window starts it), `needs` tomorrow's
    aircraft per family and station; a hub window has neither, its needs being its tails' `end_req`. `layers` None
    (or Layers() with nothing set) is v2's world."""

    task_id: str
    split: str
    carrier: str
    hub: str
    date: str
    tz: str
    base: int
    window: tuple[int, int]
    start: int
    slide: int
    dmax: int
    cutoff: int
    turn: dict[str, int]
    airports: dict[str, Airport]
    artcc: dict[str, str]
    tails: tuple[Tail, ...]
    flights: tuple[Flight, ...]
    programs: dict[str, Programs]
    clauses: dict
    sources: dict
    version: int = VERSION
    bases: tuple[str, ...] = ()
    hubs: tuple[str, ...] = ()
    needs: dict[str, dict[str, int]] = field(default_factory=dict)
    layers: Layers | None = None

    @classmethod
    def from_dict(cls, d: Mapping) -> "Task":
        return cls(d["task_id"], d["split"], d["carrier"], d["hub"], d["date"], d["tz"], d["base"],
                   tuple(d["window"]), d["start"], d["slide"], d["dmax"], d["cutoff"], dict(d["turn"]),
                   {c: Airport.from_dict(c, a) for c, a in d["airports"].items()}, dict(d["artcc"]),
                   tuple(Tail.from_dict(t) for t in d["tails"]), tuple(Flight.from_dict(f) for f in d["flights"]),
                   {h: Programs.from_dict(p) for h, p in d["programs"].items()}, dict(d["clauses"]),
                   dict(d["sources"]), VERSION if d["version"] == LAYERED else d["version"],
                   tuple(d.get("bases", ())), tuple(d.get("hubs", ())),
                   {fam: dict(by) for fam, by in d.get("needs", {}).items()},
                   Layers.from_dict(d["layers"]) if "layers" in d else None)

    def to_dict(self) -> dict:
        layered = self.layered
        out = {"version": LAYERED if layered else self.version, "task_id": self.task_id,
               "split": self.split, "carrier": self.carrier,
               "hub": self.hub, "date": self.date, "tz": self.tz, "base": self.base, "window": list(self.window),
               "start": self.start, "slide": self.slide, "dmax": self.dmax, "cutoff": self.cutoff,
               "turn": dict(self.turn), "airports": {c: a.to_dict() for c, a in self.airports.items()},
               "artcc": dict(self.artcc), "tails": [t.to_dict() for t in self.tails],
               "flights": [f.to_dict() for f in self.flights],
               "programs": {h: p.to_dict() for h, p in self.programs.items()}, "clauses": self.clauses,
               "sources": self.sources, "bases": list(self.bases), "hubs": list(self.hubs),
               "needs": {fam: dict(by) for fam, by in self.needs.items()}}
        return out | {"layers": self.layers.to_dict()} if layered else out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @cached_property
    def by_id(self) -> dict[int, Flight]:
        return {f.id: f for f in self.flights}

    def flight(self, flight_id: int) -> Flight:
        return self.by_id[flight_id]

    def turn_for(self, family: str) -> int:
        return self.turn[family]

    def back(self, f: Flight, arr: int) -> int:
        """When the aircraft that flew f, landing at `arr`, is ready again: after its turn or a busy block on f."""
        return max(arr + self.turn_for(f.family), self._busy.get(f.id, arr))

    def turned(self, f: Flight, arr: int) -> int:
        """When the aircraft that flew f, landing at `arr`, is ready for the flight JetBlue turned it into (`quick`)."""
        return max(arr + self.quick[f.id][1], self._busy.get(f.id, arr))

    @cached_property
    def quick(self) -> dict[int, tuple[int, int]]:
        """Each flight JetBlue turned into its next one faster than the family's turn, to that flight and the ground
        time: that turn may be as fast as JetBlue's, any other waits the family's turn."""
        return {c.leg: (c.next, c.ground) for t in self.tails for c in t.connections
                if c.ground < self.turn_for(t.family)}

    @cached_property
    def _busy(self) -> dict[int, int]:
        return {b.leg: b.ready for t in self.tails for b in t.busy}

    @cached_property
    def moved(self) -> dict[int, Move]:
        """Each leg a Move is bound to, to its Move."""
        return {m.leg: m for t in self.tails for m in t.repositions}

    def earliest(self, f: Flight, lb: int) -> int:
        """Nothing in scope leaves before the first watch; an early departure (lb < 0) leaves before its schedule."""
        return max(f.sched_dep + lb, self.start)

    def auto_at(self, f: Flight, reserve: bool = False) -> int:
        """When f auto-cancels if it can't leave: at its crew deadline, never before the first watch, if its crew's
        rest would end past its deadline, else at its deadline; on a `reserve` crew (one granted) at its deadline."""
        c = f.crew if reserve else self.crew(f)
        if c and c.deadline is not None and c.deadline < f.deadline < c.rest:
            return max(c.deadline, self.start)
        return f.deadline

    def reservable(self, f: Flight) -> bool:
        """Whether f is a departure from a crew base with a finite reserve pool, past whose crew deadline it flies only
        on a reserve crew."""
        return (self.layers is not None and f.crew is not None and f.crew.deadline is None
                and f.orig in self.layers.reserves)

    def crew(self, f: Flight) -> Crew | None:
        """f's crew as the world holds it without a reserve: a departure from a base with a finite pool gets its
        pairing's crew deadline and rest as an outstation departure has them (repairs.crews)."""
        return self._crews.get(f.id, f.crew)

    @cached_property
    def _crews(self) -> dict[int, Crew]:
        out = {}
        for f in self.flights:
            if self.reservable(f):
                d = f.crew.report + f.crew.fdp + EXTENSION - f.sched_block
                out[f.id] = replace(f.crew, deadline=d, rest=d + REST)
        return out

    def legal(self, f: Flight, t: int, reserve: bool = False) -> int:
        """Flight.legal with the crew the world holds without a reserve (Task.crew); on a `reserve` crew, f's own."""
        c = f.crew if reserve else self.crew(f)
        return c.rest if c and c.deadline is not None and c.deadline < t < c.rest else t

    def late(self, f: Flight, t: int) -> bool:
        """Whether f, leaving at t, flies on a reserve crew: a reservable flight past its crew deadline before its rest
        ends."""
        return self.reservable(f) and self.legal(f, t) != t

    @property
    def lb(self) -> dict[int, int]:
        return {f.id: f.lb for f in self.flights}

    @cached_property
    def needed(self) -> Counter:
        """Tomorrow's needs: aircraft per (family, station) at the end of the day."""
        if self.needs:
            return Counter({(fam, s): n for fam, by in self.needs.items() for s, n in by.items()})
        return Counter((t.family, t.end_req) for t in self.tails)

    @property
    def layered(self) -> bool:
        """Whether the task sets any of v3's layers (written as LAYERED)."""
        return self.layers is not None and self.layers != Layers()

    def lag(self, kind: str) -> int:
        """The minutes a news item of `kind` takes to reach the desk (Layers.lags; LAGS without layers)."""
        return (self.layers.lags if self.layers else LAGS).get(kind, 0)

    @property
    def storms(self) -> tuple[str, ...]:
        """The hubs whose programs the task is about: its storm hubs, or its hub."""
        return self.hubs or (self.hub,)

    def covers(self, scope: Scope, hub: str, origin: str) -> bool:
        """Whether a program at `hub` with `scope` covers departures from `origin`."""
        return self._coverage(scope, hub, origin)

    @cached_property
    def _coverage(self) -> Callable[[Scope, str, str], bool]:
        return coverage(self.airports, self.artcc)


def coverage(airports: Mapping[str, Airport], artcc: Mapping[str, str]) -> Callable[[Scope, str, str], bool]:
    """covers(scope, hub, origin) over these airports, distances cached."""
    distance: dict[tuple[str, str], float] = {}

    def covers(scope: Scope, hub: str, origin: str) -> bool:
        if (hub, origin) not in distance:
            a, b = airports.get(hub), airports.get(origin)
            distance[hub, origin] = nm((a.lat, a.lon), (b.lat, b.lon)) if a and b else math.inf
        return scope.covers(origin, artcc.get(origin), distance[hub, origin])

    return covers


def load_tasks(path: str | Path) -> list[Task]:
    p = Path(path)
    p = p / "tasks.jsonl" if p.is_dir() else p
    return [Task.from_dict(json.loads(line)) for line in p.read_text().splitlines() if line.strip()]


def write_tasks(tasks: list[Task], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(t.to_json() + "\n" for t in tasks))
    return path
