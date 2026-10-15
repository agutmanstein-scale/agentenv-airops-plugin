"""Every arrival the Command Center model lands at a hub on a local date: each BTS-scheduled arrival of any carrier,
and the unseen stream of everything BTS never reports.

BTS rows are the arrivals scheduled 00:00-23:59 local, cancelled ones kept as demand that leaves the list NOTICE before
its scheduled departure and never flies. A flight's earliest wheels-off d0 is its scheduled departure plus its
exogenous departure delay (step 0's rule for removing NAS delay: DepDelay less NASDelay when a hub GDP controlled it
or a hub ground stop held it as they ran, never earlier than its recorded early departure, floored at EARLY) plus its
origin's median taxi-out; it reaches the hub's queue tau after wheels-off and its gate taxi_in after landing. Class,
seats and passengers come from T-100 (costs.route_aircraft), the carrier's month median where its route is missing.

The unseen stream (labelled `unseen`) is the airport's counted arrivals per day less the month's average BTS arrivals
that landed, spread over the day's hours like its BTS scheduled arrivals (cumulative rounding), evenly inside an hour;
each has the day's median BTS class and passengers, arrives at the queue taxi_in before its scheduled arrival and is
exempt from ground holding. The hub's departure demand per hour, which JFK's rates trade against, is its BTS scheduled
departures (cancelled ones too: the schedule as published) scaled by the same unseen share.

Medians are calibration: tau by origin, hub and season (median AirTime of train-date flights that landed outside the
hub's programs; the route's all-season median, then a line in distance fitted on the hub's train routes, where fewer
than MIN_FLIGHTS), taxi-in by carrier and hub and taxi-out by origin, all over train dates only.
"""

import functools
import math
import os
import statistics
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..build import bts, fetch, pack
from ..programs import advisories, candidates, rbs
from ..programs.advisories import HUBS, NONCONTIGUOUS
from ..programs.model import coverage
from ..programs.repairs import EARLY
from . import costs, sources

NOTICE = rbs.NOTICE
DAY = 1440
HOURS = 48
MIN_FLIGHTS = 3
EXTRAS = ("TaxiOut", "TaxiIn", "AirTime")
UNSEEN = "~unseen"


@dataclass(frozen=True)
class Arrival:
    """One arrival at the hub, minutes from local midnight. `d0` is the earliest wheels-off, `tau` the unimpeded air
    time, `leaves` when a recorded cancellation leaves the demand list; the recorded fields are BTS's (None for the
    unseen stream and for flights that did not land)."""

    id: int
    carrier: str
    number: int
    orig: str
    artcc: str | None
    nm: float | None
    sched_dep: int
    sched_arr: int
    cls: str
    seats: float
    pax: int
    cancelled: bool
    unseen: bool
    d0: int
    tau: int
    taxi_out: int
    taxi_in: int
    removed: bool
    wheels_off: int | None
    wheels_on: int | None
    air_time: float | None
    diverted: bool
    nas: int
    source: str

    @property
    def key(self) -> tuple:
        return self.sched_arr, self.carrier, self.number, self.orig

    @property
    def leaves(self) -> int | None:
        return self.sched_dep - NOTICE if self.cancelled else None

    @property
    def eta0(self) -> int:
        return self.d0 + self.tau

    @property
    def noncontiguous(self) -> bool:
        return self.artcc in NONCONTIGUOUS

    @property
    def exempt(self) -> bool:
        """Exempt at 00:00 local: the unseen stream, origins outside the contiguous US, flights airborne by then."""
        return self.unseen or self.noncontiguous or self.d0 < 0

    @property
    def rates(self) -> costs.Rates:
        return costs.RATES[self.cls]


@dataclass(frozen=True)
class Medians:
    air: Mapping[tuple[str, str, str], float]
    air_any: Mapping[tuple[str, str], float]
    line: Mapping[tuple[str, str], tuple[float, float]]
    taxi_in: Mapping[tuple[str, str], float]
    taxi_in_hub: Mapping[str, float]
    taxi_out: Mapping[str, float]
    taxi_out_all: float

    def tau(self, orig: str, hub: str, season: str, nm: float) -> int:
        v = self.air.get((orig, hub, season), self.air_any.get((orig, hub)))
        if v is None:
            a, b = self.line[hub, season]
            v = a + b * nm
        return int(round(v))

    def taxi(self, carrier: str, hub: str) -> int:
        return int(round(self.taxi_in.get((carrier, hub), self.taxi_in_hub[hub])))

    def out(self, orig: str) -> int:
        return int(round(self.taxi_out.get(orig, self.taxi_out_all)))


@functools.lru_cache(maxsize=4)
def month(inputs: Path, derived: Path, m: str) -> pd.DataFrame:
    """The month's BTS rows to or from JFK or BOS with their TaxiOut, TaxiIn and AirTime."""
    x = bts.read_ontime(bts.ontime_path(inputs, m), derived)
    x = pd.concat([x, extras(inputs, derived, m)], axis=1)
    return x[x.Origin.isin(HUBS) | x.Dest.isin(HUBS)].reset_index(drop=True)


def extras(inputs: Path, derived: Path, m: str) -> pd.DataFrame:
    """EXTRAS in the zip's row order (read_ontime's), cached beside it under the zip's sha256."""
    path = bts.ontime_path(inputs, m)
    cached = derived / f"flow_extras_{fetch.sha256(path)[:16]}.parquet"
    if not cached.exists():
        with zipfile.ZipFile(path) as z, z.open(next(n for n in z.namelist() if n.endswith(".csv"))) as f:
            got = pd.read_csv(f, usecols=list(EXTRAS))[list(EXTRAS)]
        derived.mkdir(parents=True, exist_ok=True)
        part = cached.with_name(f"{cached.name}.{os.getpid()}.part")
        got.to_parquet(part, index=False)
        part.replace(cached)
    return pd.read_parquet(cached)


@functools.lru_cache(maxsize=8)
def frame(inputs: Path, derived: Path, d: str) -> pd.DataFrame:
    """The hub rows of FlightDate d - 1 to d + 1 in minutes from d's local midnight, with wheels-off and wheels-on."""
    days = [pack.shift(d, k) for k in (-1, 0, 1)]
    x = pd.concat([p[p.FlightDate.isin(days)] for p in (month(inputs, derived, m) for m in candidates.months_of(d))],
                  ignore_index=True)
    x = pack.local(x[x.sched_dep.notna()], candidates.midnight(d))
    landed = (x.Cancelled == 0) & (x.Diverted == 0)
    return x.assign(wheels_off=(x.ad + x.TaxiOut).where(x.Cancelled == 0),
                    wheels_on=(x.aa - x.TaxiIn).where(landed), landed=landed)


@functools.lru_cache(maxsize=32)
def flown_per_day(inputs: Path, derived: Path, hub: str, m: str) -> float:
    """The month's average BTS arrivals per day that landed at the hub."""
    x = month(inputs, derived, m)
    x = x[x.FlightDate.str.startswith(m)]
    return float(((x.Dest == hub) & (x.Cancelled == 0) & (x.Diverted == 0)).sum() / x.FlightDate.nunique())


def unseen_per_day(inputs: Path, derived: Path, hub: str, m: str) -> float:
    return max(0.0, sources.counted_per_day(hub, m) - flown_per_day(inputs, derived, hub, m))


def scale(inputs: Path, derived: Path, hub: str, m: str) -> float:
    """Counted over BTS-landed arrivals: what a BTS count is multiplied by to count every arrival."""
    return 1 + unseen_per_day(inputs, derived, hub, m) / flown_per_day(inputs, derived, hub, m)


def programs(inputs: Path, d: str, hub: str) -> tuple[tuple, tuple]:
    """The hub's binding programs as they ran around d (runs, stop pieces)."""
    return candidates.programs_of(inputs, d, hub)


def covers_of(inputs: Path, codes: Iterable[str]) -> tuple[dict[str, str], rbs.Covers]:
    codes = sorted(set(codes))
    artcc = {c: a for c in codes if (a := advisories.artcc(inputs, c))}
    return artcc, coverage(candidates.airports_of(codes), artcc)


def distance(orig: str, hub: str) -> float:
    a, b = bts.airports()[orig], bts.airports()[hub]
    return advisories.nm((a["lat"], a["lon"]), (b["lat"], b["lon"]))


def arrivals(inputs: Path, derived: Path, d: str, hub: str, med: Medians) -> tuple[Arrival, ...]:
    """Every arrival of the day at the hub, BTS rows then the unseen stream, in key order, ids by position."""
    x = frame(inputs, derived, d)
    at = x[(x.Dest == hub) & (x.sa >= 0) & (x.sa < DAY)]
    runs, stops = programs(inputs, d, hub)
    artcc, covers = covers_of(inputs, set(at.Origin) | {hub})
    routes, carriers = costs.route_aircraft(inputs, d[:7], hub)
    season = candidates.season(d)
    rows = []
    for r in at.itertuples():
        cancelled = r.Cancelled == 1
        departs = int(r.sd) if cancelled else int(r.ad)
        removed = (rbs.governing(runs, covers, r.Origin, int(r.sa), departs) is not None
                   or rbs.gs_held(stops, covers, r.Origin, int(r.sd)) is not None)
        ac = routes.get((r.Reporting_Airline, r.Origin)) or carriers[r.Reporting_Airline]
        nm = distance(r.Origin, hub)
        taxi_out = med.out(r.Origin)
        rows.append(dict(
            carrier=r.Reporting_Airline, number=int(r.Flight_Number_Reporting_Airline), orig=r.Origin,
            artcc=artcc.get(r.Origin), nm=round(nm, 1), sched_dep=int(r.sd), sched_arr=int(r.sa), cls=ac.cls,
            seats=round(ac.seats, 1), pax=int(round(ac.pax)), cancelled=cancelled, unseen=False,
            d0=int(r.sd) + (0 if cancelled else lb(r, removed)) + taxi_out, tau=med.tau(r.Origin, hub, season, nm),
            taxi_out=taxi_out, taxi_in=med.taxi(r.Reporting_Airline, hub), removed=removed,
            wheels_off=None if pd.isna(r.wheels_off) else int(round(r.wheels_off)),
            wheels_on=None if pd.isna(r.wheels_on) else int(round(r.wheels_on)),
            air_time=None if pd.isna(r.AirTime) or not r.landed else float(r.AirTime), diverted=r.Diverted == 1,
            nas=0 if pd.isna(r.NASDelay) else int(r.NASDelay), source=ac.source))
    rows += unseen(rows, unseen_per_day(inputs, derived, hub, d[:7]), med.taxi_in_hub[hub])
    rows.sort(key=lambda a: (a["sched_arr"], a["carrier"], a["number"], a["orig"]))
    return tuple(Arrival(id=k, **a) for k, a in enumerate(rows))


def lb(r, removed: bool) -> int:
    """The exogenous departure delay: DepDelay less NAS delay where a hub program held the flight, never before its
    recorded early departure (floored at EARLY)."""
    dep = 0.0 if pd.isna(r.DepDelay) else float(r.DepDelay)
    nas = 0.0 if pd.isna(r.NASDelay) else float(r.NASDelay)
    return int(math.ceil(max(dep - nas * removed, max(min(dep, 0.0), EARLY))))


def unseen(rows: Sequence[dict], per_day: float, taxi_in: float) -> list[dict]:
    """The unseen stream over the day's BTS scheduled-arrival profile, with the day's median class and pax."""
    profile = np.bincount([a["sched_arr"] // 60 for a in rows], minlength=24)[:24]
    ranks = sorted(costs.rank(a["cls"]) for a in rows)
    cls = sorted(costs.CLASSES, key=lambda c: costs.RATES[c].air)[ranks[(len(ranks) - 1) // 2]]
    pax = int(statistics.median_low(a["pax"] for a in rows))
    taxi = int(round(taxi_in))
    out, total, k = [], 0.0, 0
    for h in range(24):
        before = math.floor(total + 0.5)
        total += per_day * profile[h] / profile.sum()
        n = math.floor(total + 0.5) - before
        for j in range(n):
            arr = 60 * h + (2 * j + 1) * 60 // (2 * n)
            out.append(dict(carrier=UNSEEN, number=k, orig="", artcc=None, nm=None, sched_dep=arr - taxi,
                            sched_arr=arr, cls=cls, seats=float(pax), pax=pax, cancelled=False, unseen=True,
                            d0=arr - taxi, tau=0, taxi_out=0, taxi_in=taxi, removed=False, wheels_off=None,
                            wheels_on=None, air_time=None, diverted=False, nas=0, source="unseen: day median"))
            k += 1
    return out


def departures(inputs: Path, derived: Path, d: str, hub: str) -> tuple[float, ...]:
    """The hub's departure demand per hour from local midnight, HOURS of them (past the cache, the last hour's)."""
    x = frame(inputs, derived, d)
    sd = x[(x.Origin == hub) & (x.sd >= 0)].sd
    counts = np.bincount((sd // 60).astype(int), minlength=HOURS)[:HOURS].astype(float)
    k = 1 + unseen_per_day(inputs, derived, hub, d[:7]) / float(counts[:24].sum())
    return tuple(float(c * k) for c in counts)


def medians(inputs: Path, derived: Path, dates: Iterable[str]) -> Medians:
    """The calibration medians over the given (train) dates."""
    air, taxi_in, taxi_out = defaultdict(list), defaultdict(list), defaultdict(list)
    for d in sorted(set(dates)):
        x = frame(inputs, derived, d)
        x = x[x.FlightDate == d]
        for v in x[x.TaxiOut.notna()].itertuples():
            taxi_out[v.Origin].append(v.TaxiOut)
        for hub in HUBS:
            spans = candidates.in_force(*programs(inputs, d, hub))
            at = x[(x.Dest == hub) & x.landed]
            for v in at.itertuples():
                taxi_in[v.Reporting_Airline, hub].append(v.TaxiIn)
                h = int(v.wheels_on // 60)
                if not any(a < 60 * h + 60 and b > 60 * h for a, b in spans):
                    air[v.Origin, hub, candidates.season(d)].append(v.AirTime)
    med = {k: float(np.median(v)) for k, v in air.items() if len(v) >= MIN_FLIGHTS}
    pooled = defaultdict(list)
    for (o, h, _), v in air.items():
        pooled[o, h] += v
    line = {}
    for hub in HUBS:
        for s in ("summer", "winter"):
            pts = [(distance(o, h), v) for (o, h, ss), v in med.items() if h == hub and ss == s]
            line[hub, s] = fit_line(pts)
    hub_in = {h: float(np.median([t for (_, x), v in taxi_in.items() if x == h for t in v])) for h in HUBS}
    every_out = [t for v in taxi_out.values() for t in v]
    return Medians(med, {k: float(np.median(v)) for k, v in pooled.items() if len(v) >= MIN_FLIGHTS}, line,
                   {k: float(np.median(v)) for k, v in taxi_in.items() if len(v) >= MIN_FLIGHTS}, hub_in,
                   {k: float(np.median(v)) for k, v in taxi_out.items() if len(v) >= MIN_FLIGHTS},
                   float(np.median(every_out)))


def fit_line(pts: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Least squares a + b x."""
    xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    b, a = np.polyfit(xs, ys, 1)
    return float(a), float(b)
