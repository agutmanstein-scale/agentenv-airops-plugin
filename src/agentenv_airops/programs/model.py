"""The v2 task: one JetBlue hub window on a real storm day inside the FAA's programs at JFK and BOS, as one JSON line.

Times are minutes from 00:00 hub-local on `date` (`base` is that midnight's UTC epoch minute), as in v1. A task carries
everything the truth, the forecast and the references read, and nothing they would have to fetch:
- flights: the scope's legs from `start`, with the scheduled and the truth block, pax capped at the smallest
  seats of the family's tails in the task (`pax_raw` kept), the exogenous delay `lb` with NAS delay removed for a
  flight controlled or GS-held on its recorded departure, the out-and-back `pair`, the `deadline` and
  JetBlue's recorded outcome;
- tails: split family, the schedule's position and ready time, their legs in scheduled order, same-station gaps as
  busy blocks and chain breaks as fixed repositionings (scope closure), and tomorrow's need;
- programs per hub (both hubs in every task): the advisories as parsed, the in-sample unseen arrivals per hour of
  each GDP or proposed advisory and the airport's scale, the truth segments with JetBlue's slot pool, the ground-stop
  pieces, the VMC called arrival rate, and the demand: every BTS-scheduled arrival at the hub over the task, so the
  forecast RBS can run at any watch and the other carriers' controlled arrivals carry their BTS NAS delay for the
  fidelity clause.
"""

import json
import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..core.model import Airport
from .advisories import Advisory, Scope, Stop, nm

VERSION = 2


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
class Flight:
    """`controlled` is the GDP advisory whose segment governed the flight on its recorded departure (the scheduled one
    when cancelled), `gs_held` the ground-stop chain that held it; `exempt` marks a flight into a hub whose origin no
    program of that hub covers."""

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

    @classmethod
    def from_dict(cls, d: Mapping) -> "Flight":
        return cls(d["id"], d["flight"], d["orig"], d["dest"], d["sched_dep"], d["sched_arr"], d["sched_block"],
                   d["block"], d["pax"], d["pax_raw"], d["family"], d["lb"], d["nas_removed"], d["controlled"],
                   d["gs_held"], d["exempt"], d["pair"], d["deadline"], Outcome.from_dict(d["actual"]))

    def to_dict(self) -> dict:
        return {"id": self.id, "flight": self.flight, "orig": self.orig, "dest": self.dest,
                "sched_dep": self.sched_dep, "sched_arr": self.sched_arr, "sched_block": self.sched_block,
                "block": self.block, "pax": self.pax, "pax_raw": self.pax_raw, "family": self.family, "lb": self.lb,
                "nas_removed": self.nas_removed, "controlled": self.controlled, "gs_held": self.gs_held,
                "exempt": self.exempt, "pair": self.pair, "deadline": self.deadline, "actual": self.actual.to_dict()}

    @property
    def number(self) -> int:
        return int(self.flight.split()[-1])


@dataclass(frozen=True)
class Move:
    """A tail away from the schedule's flying, fixed and uncosted. Decoupled: an aircraft of its family is ready at
    `dest` at `ready`, and one leaves `orig` at the first moment from `leave` that one is there. A repositioning
    closes a chain break (`ready` before `leave` where BTS shows a swap: the cancelled legs keep the tail they were
    assigned, the flown ones the tail that flew them); a same-station Move is a busy block, a long daytime gap that
    likely hides an international leg BTS lacks."""

    orig: str
    dest: str
    leave: int
    ready: int

    @classmethod
    def from_dict(cls, d: Mapping) -> "Move":
        return cls(d["orig"], d["dest"], d["leave"], d["ready"])

    def to_dict(self) -> dict:
        return {"orig": self.orig, "dest": self.dest, "leave": self.leave, "ready": self.ready}


@dataclass(frozen=True)
class Tail:
    """`pos` and `ready` already close a break before the first leg (the tail reappears at its origin, ready at its
    scheduled departure less the turn); `legs` are its flights in scheduled order, cancelled ones included."""

    tail: str
    family: str
    type: str
    seats: int
    pos: str
    ready: int
    legs: tuple[int, ...]
    busy: tuple[Move, ...]
    repositions: tuple[Move, ...]
    end_req: str
    actual_end: str

    @classmethod
    def from_dict(cls, d: Mapping) -> "Tail":
        return cls(d["tail"], d["family"], d["type"], d["seats"], d["pos"], d["ready"], tuple(d["legs"]),
                   tuple(Move.from_dict(m) for m in d["busy"]), tuple(Move.from_dict(m) for m in d["repositions"]),
                   d["end_req"], d["actual_end"])

    def to_dict(self) -> dict:
        return {"tail": self.tail, "family": self.family, "type": self.type, "seats": self.seats, "pos": self.pos,
                "ready": self.ready, "legs": list(self.legs), "busy": [m.to_dict() for m in self.busy],
                "repositions": [m.to_dict() for m in self.repositions], "end_req": self.end_req,
                "actual_end": self.actual_end}


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
class Task:
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

    @classmethod
    def from_dict(cls, d: Mapping) -> "Task":
        return cls(d["task_id"], d["split"], d["carrier"], d["hub"], d["date"], d["tz"], d["base"],
                   tuple(d["window"]), d["start"], d["slide"], d["dmax"], d["cutoff"], dict(d["turn"]),
                   {c: Airport.from_dict(c, a) for c, a in d["airports"].items()}, dict(d["artcc"]),
                   tuple(Tail.from_dict(t) for t in d["tails"]), tuple(Flight.from_dict(f) for f in d["flights"]),
                   {h: Programs.from_dict(p) for h, p in d["programs"].items()}, dict(d["clauses"]),
                   dict(d["sources"]), d["version"])

    def to_dict(self) -> dict:
        return {"version": self.version, "task_id": self.task_id, "split": self.split, "carrier": self.carrier,
                "hub": self.hub, "date": self.date, "tz": self.tz, "base": self.base, "window": list(self.window),
                "start": self.start, "slide": self.slide, "dmax": self.dmax, "cutoff": self.cutoff,
                "turn": dict(self.turn), "airports": {c: a.to_dict() for c, a in self.airports.items()},
                "artcc": dict(self.artcc), "tails": [t.to_dict() for t in self.tails],
                "flights": [f.to_dict() for f in self.flights],
                "programs": {h: p.to_dict() for h, p in self.programs.items()}, "clauses": self.clauses,
                "sources": self.sources}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @cached_property
    def by_id(self) -> dict[int, Flight]:
        return {f.id: f for f in self.flights}

    def flight(self, flight_id: int) -> Flight:
        return self.by_id[flight_id]

    def turn_for(self, family: str) -> int:
        return self.turn[family]

    def earliest(self, f: Flight, lb: int) -> int:
        """Nothing in scope leaves before the first watch; an early departure (lb < 0) leaves before its schedule."""
        return max(f.sched_dep + lb, self.start)

    @property
    def lb(self) -> dict[int, int]:
        return {f.id: f.lb for f in self.flights}

    @property
    def needed(self) -> Counter:
        """Tomorrow's needs: aircraft per (station, family) at the end of the day."""
        return Counter((t.end_req, t.family) for t in self.tails)

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
