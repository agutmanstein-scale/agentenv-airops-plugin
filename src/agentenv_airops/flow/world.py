"""One hub's storm day as the Command Center model's world: every arrival, the weather's capacity per 15-minute bin,
one landing queue, and the FAA's cost of any set of releases.

A policy hands `evaluate` a wheels-off release (an EDCT) per flight, never before its d0; a flight without one leaves
at its d0, and the unseen stream always reaches the queue at its own time. Each flight reaches the queue tau after
release and lands by queue.land; recorded cancellations never fly. Its cost (passenger-minutes): the gate minutes past
d0 at its ground rate, the minutes it held in the air at its air rate, and its passengers' arrival delay against the
schedule (landing plus taxi-in); a diversion costs its held minutes and costs.DIVERSION_BLOCK more at the air rate and
costs.DIVERSION_PAX minutes per passenger, and never lands. The system cost is every arrival's but the recorded
cancellations', the unseen stream's included and also reported apart. The queue runs until it is empty, over HOURS of
bins from local midnight.
"""

import functools
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..programs import candidates
from ..programs.advisories import Run, Stop
from . import capacity, costs, demand, queue, weather
from .capacity import Reductions
from .demand import Arrival, Medians
from .weather import Bin, Obs

HOURS = demand.HOURS
BINS = HOURS * 60 // queue.BIN


@dataclass(frozen=True)
class World:
    date: str
    hub: str
    base: int
    arrivals: tuple[Arrival, ...]
    timeline: tuple[Bin, ...]
    departures: tuple[float, ...]
    reductions: Reductions
    caps: tuple[int, ...]
    runs: tuple[Run, ...]
    stops: tuple[Stop, ...]
    scale: float
    observations: tuple[Obs, ...] = field(repr=False)

    @functools.cached_property
    def slots(self) -> list[int]:
        return queue.slots(self.caps)

    def obs_at(self, t: int) -> Obs:
        """The latest METAR or SPECI observed at or before minute t."""
        return weather.in_force(self.observations, t)

    def capacity(self, timeline: tuple[Bin, ...]) -> list[int]:
        """Landings per bin for another weather timeline (a forecast), at this day's departure demand and reductions."""
        return capacity.bins(self.hub, timeline, self.departures, self.reductions)


@dataclass(frozen=True)
class Flown:
    release: int
    eta: int
    landing: int | None
    held: int
    ground: int
    air: int
    pax: int

    @property
    def cost(self) -> int:
        return self.ground + self.air + self.pax


@dataclass(frozen=True)
class Outcome:
    flights: dict[int, Flown]
    totals: dict


def build(inputs: Path, derived: Path, d: str, hub: str, med: Medians, reductions: Reductions) -> World:
    timeline = weather.timeline(inputs, hub, d, BINS)
    departures = demand.departures(inputs, derived, d, hub)
    runs, stops = demand.programs(inputs, d, hub)
    return World(d, hub, candidates.midnight(d), demand.arrivals(inputs, derived, d, hub, med), timeline, departures,
                 reductions, tuple(capacity.bins(hub, timeline, departures, reductions)), runs, stops,
                 demand.scale(inputs, derived, hub, d[:7]), weather.observations(inputs, hub, d))


def build_job(job: tuple) -> World:
    """build over one picklable tuple, for a process pool."""
    return build(*job)


def evaluate(world: World, releases: Mapping[int, int] | None = None,
             divert_after: int = queue.DIVERT_AFTER) -> Outcome:
    releases = releases or {}
    flying = [a for a in world.arrivals if not a.cancelled]
    rel = {}
    for a in flying:
        r = releases.get(a.id, a.d0)
        if r < a.d0 or (a.unseen and r != a.d0):
            raise ValueError(f"release {r} of flight {a.id} is before its d0 {a.d0} or holds the unseen stream")
        rel[a.id] = r
    landed = queue.land([(rel[a.id] + a.tau, a.key, a.id) for a in flying], world.slots, divert_after)
    out = {}
    for a in flying:
        eta = rel[a.id] + a.tau
        at = landed[a.id]
        held = divert_after if at is None else at - eta
        ground = a.rates.ground * (rel[a.id] - a.d0)
        if at is None:
            air = a.rates.air * (held + costs.DIVERSION_BLOCK)
            pax = a.pax * costs.DIVERSION_PAX
        else:
            air = a.rates.air * held
            pax = a.pax * max(0, at + a.taxi_in - a.sched_arr)
        out[a.id] = Flown(rel[a.id], eta, at, held, ground, air, pax)
    return Outcome(out, totals(world, out))


def totals(world: World, flights: Mapping[int, Flown]) -> dict:
    parts = {"ground": 0, "air": 0, "pax": 0}
    unseen = {"ground": 0, "air": 0, "pax": 0}
    diverted = held = 0
    for a in world.arrivals:
        f = flights.get(a.id)
        if f is None:
            continue
        for k in parts:
            parts[k] += getattr(f, k)
            if a.unseen:
                unseen[k] += getattr(f, k)
        diverted += f.landing is None
        held += f.held
    cancelled = [a for a in world.arrivals if a.cancelled]
    return {"system": sum(parts.values()), **parts, "unseen": sum(unseen.values()), "unseen_parts": unseen,
            "diversions": diverted, "held_minutes": held, "flights": len(flights),
            "cancelled": {"flights": len(cancelled), "pax": sum(a.pax for a in cancelled)}}


def bands(world: World, releases: Mapping[int, int] | None = None) -> dict[int, dict]:
    """The diversions and system cost with diversion after 45 and after 90 minutes of holding."""
    out = {}
    for k in (45, 90):
        t = evaluate(world, releases, k).totals
        out[k] = {"diversions": t["diversions"], "system": t["system"]}
    return out
