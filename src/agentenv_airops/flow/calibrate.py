"""The world's calibration and its two guards, on train day-hubs only; nothing here reads the FAA's or an airline's
decisions, only the weather, the schedule and what BTS saw land.

Excess air time is a landed flight's AirTime less its baseline: the median AirTime of the same day's flights on its
route that landed 06:00-10:00 local when there are MIN_FLIGHTS of them, else the route's median over the train dates
of its month. An hour is saturated from 07:00 to 23:00 local with MIN_WHEELS_ON BTS wheels-on and a median excess of
SATURATED_EXCESS minutes; an hour with a GDP or ground stop of the hub in force counts only when every 15-minute bin of
it has that median (an airborne queue all hour, so throughput is the runway's, not the program's). Observed
throughput is the hour's BTS wheels-on times the month's counted over BTS-landed arrivals.

The reductions are fitted on the saturated train hours, both hubs pooled: each factor on a 0-0.9 grid in 0.05 steps,
jointly, by the least log-squared error of modelled capacity against observed throughput (ties to the smaller
factors); a factor present in fewer than MIN_HOURS saturated hours stays 0.

Guard capacity: modelled capacity within CAPACITY_BAND of observed throughput in at least CAPACITY_SHARE of the
saturated train hours without a GDP. Guard airborne: the world's queue run on each train day-hub with every flight
released at its recorded wheels-off and reaching the queue a baseline later; per hour of that arrival (07:00-23:00,
no GDP and no ground stop, MIN_GUARD_ARRIVALS BTS flights that landed), the flights' mean modelled holding against
their mean observed excess: Pearson correlation at least CORRELATION over the pooled hours, and the median ratio
within RATIO over the hours whose mean observed excess is RATIO_MIN_EXCESS minutes or more.
"""

import itertools
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from . import capacity, queue
from .capacity import Reductions
from .demand import Medians
from .world import World

FIRST_HOUR, LAST_HOUR = 7, 23
MORNING = (360, 600)
MIN_FLIGHTS = 3
MIN_WHEELS_ON = 10
SATURATED_EXCESS = 10
GRID = tuple(round(0.05 * k, 2) for k in range(19))
MIN_HOURS = 10
CAPACITY_BAND, CAPACITY_SHARE = 0.20, 0.80
MIN_GUARD_ARRIVALS = 5
RATIO_MIN_EXCESS = 5
CORRELATION, RATIO = 0.5, (0.67, 1.5)
FACTORS = ("storm", "vicinity", "frozen")


@dataclass(frozen=True)
class Hour:
    date: str
    hub: str
    hour: int
    gdp: bool
    gs: bool
    wheels_on: int
    throughput: float
    excess: float | None
    bins: tuple[float | None, ...]
    saturated: bool
    base: tuple[float, ...]
    storm: tuple[float, ...]
    vicinity: tuple[bool, ...]
    frozen: tuple[bool, ...]
    category: str
    config: str

    def capacity(self, r: Reductions) -> float:
        return sum(b * (1 - max(r.storm * s, r.vicinity * v, r.frozen * z))
                   for b, s, v, z in zip(self.base, self.storm, self.vicinity, self.frozen, strict=True)) / 4


def route_months(worlds: Iterable[World]) -> dict[tuple[str, str, str], float]:
    """(origin, hub, month) -> median AirTime of the train worlds' landed BTS arrivals."""
    acc = defaultdict(list)
    for w in worlds:
        for a in w.arrivals:
            if a.air_time is not None:
                acc[a.orig, w.hub, w.date[:7]].append(a.air_time)
    return {k: float(np.median(v)) for k, v in acc.items()}


def baselines(world: World, route_month: Mapping[tuple[str, str, str], float]) -> dict[int, float]:
    """Each flown BTS arrival's baseline AirTime (the module's rule)."""
    morning = defaultdict(list)
    for a in world.arrivals:
        if a.air_time is not None and MORNING[0] <= a.wheels_on < MORNING[1]:
            morning[a.orig].append(a.air_time)
    out = {}
    for a in world.arrivals:
        if a.unseen or a.cancelled or a.wheels_off is None:
            continue
        m = morning[a.orig]
        base = float(np.median(m)) if len(m) >= MIN_FLIGHTS else route_month.get((a.orig, world.hub, world.date[:7]))
        if base is not None:
            out[a.id] = base
    return out


def spans(world: World) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """The GDP and the ground-stop intervals of the hub in force."""
    gdp = [(max(r.since, r.adv.start), min(r.until, r.adv.end + 1)) for r in world.runs]
    return [(a, b) for a, b in gdp if b > a], [(p.start, p.end) for p in world.stops]


def overlaps(intervals: Iterable[tuple[int, int]], lo: int, hi: int) -> bool:
    return any(a < hi and b > lo for a, b in intervals)


def hours(world: World, base: Mapping[int, float]) -> list[Hour]:
    gdp, gs = spans(world)
    landed = [(a.wheels_on, a.air_time - base[a.id]) for a in world.arrivals
              if a.air_time is not None and a.id in base]
    out = []
    for h in range(FIRST_HOUR, LAST_HOUR):
        lo = 60 * h
        here = [e for t, e in landed if lo <= t < lo + 60]
        quarters = tuple(statistics.median(q) if (q := [e for t, e in landed if lo + 15 * k <= t < lo + 15 * k + 15])
                         else None for k in range(4))
        median = statistics.median(here) if here else None
        program = overlaps(gdp, lo, lo + 60) or overlaps(gs, lo, lo + 60)
        queued = (all(q is not None and q >= SATURATED_EXCESS for q in quarters) if program
                  else median is not None and median >= SATURATED_EXCESS)
        idx = range(4 * h, 4 * h + 4)
        rates = [capacity.rate(world.hub, world.timeline[k].obs, world.departures[h]) for k in idx]
        out.append(Hour(world.date, world.hub, h, overlaps(gdp, lo, lo + 60), overlaps(gs, lo, lo + 60), len(here),
                        len(here) * world.scale, median, quarters, len(here) >= MIN_WHEELS_ON and queued,
                        tuple(r[0] for r in rates), tuple(world.timeline[k].storm for k in idx),
                        tuple(world.timeline[k].obs.vicinity for k in idx),
                        tuple(world.timeline[k].obs.frozen for k in idx), rates[0][1], rates[0][2]))
    return out


def present(h: Hour) -> dict[str, bool]:
    return {"storm": any(s > 0 for s in h.storm), "vicinity": any(h.vicinity), "frozen": any(h.frozen)}


def fit(sample: Sequence[Hour]) -> dict:
    """The reductions of least log-squared error over the saturated hours, and the sample behind them."""
    sat = [h for h in sample if h.saturated]
    counts = {f: sum(present(h)[f] for h in sat) for f in FACTORS}
    grids = [GRID if counts[f] >= MIN_HOURS else (0.0,) for f in FACTORS]
    base = np.array([h.base for h in sat])
    s, v, z = (np.array([getattr(h, f) for h in sat], dtype=float) for f in FACTORS)
    thru = np.array([h.throughput for h in sat])
    best = None
    for f in itertools.product(*grids):
        cap = (base * (1 - np.maximum(np.maximum(f[0] * s, f[1] * v), f[2] * z))).sum(axis=1) / 4
        loss = round(float((np.log(cap / thru) ** 2).sum()), 9)
        if best is None or (loss, f) < best:
            best = (loss, f)
    loss, f = best
    zero = float((np.log(base.sum(axis=1) / 4 / thru) ** 2).sum())
    return {"reductions": Reductions(*f), "loss": loss, "loss_without": round(zero, 4), "hours": len(sat),
            "present": counts, "fixed_at_zero": [k for k in FACTORS if counts[k] < MIN_HOURS],
            "by_hub": {hub: sum(h.hub == hub for h in sat) for hub in ("JFK", "BOS")},
            "program_hours": sum(h.gdp or h.gs for h in sat)}


def guard_capacity(sample: Sequence[Hour], r: Reductions) -> dict:
    rows = [h for h in sample if h.saturated and not h.gdp]
    ratios = [h.capacity(r) / h.throughput for h in rows]
    ok = [abs(x - 1) <= CAPACITY_BAND for x in ratios]
    share = sum(ok) / len(ok) if ok else None
    by_hub = {hub: {"hours": sum(h.hub == hub for h in rows),
                    "within": sum(o for h, o in zip(rows, ok, strict=True) if h.hub == hub)} for hub in ("JFK", "BOS")}
    return {"hours": len(rows), "within": sum(ok), "share": share, "threshold": CAPACITY_SHARE,
            "pass": share is not None and share >= CAPACITY_SHARE, "by_hub": by_hub,
            "median_ratio": statistics.median(ratios) if ratios else None,
            "ratio_p10_p90": [float(np.percentile(ratios, 10)), float(np.percentile(ratios, 90))] if ratios else None}


def holding(world: World, base: Mapping[int, float]) -> list[dict]:
    """The world's queue with every flight at its recorded wheels-off plus its baseline: per landed BTS flight, the
    arrival minute, the modelled holding and the observed excess."""
    etas = []
    for a in world.arrivals:
        if a.unseen:
            etas.append((a.eta0, a.key, a.id))
        elif a.id in base:
            etas.append((int(round(a.wheels_off + base[a.id])), a.key, a.id))
    landed = queue.land(etas, world.slots)
    at = {i: e for e, _, i in etas}
    return [{"id": a.id, "eta": at[a.id],
             "held": queue.DIVERT_AFTER if landed[a.id] is None else landed[a.id] - at[a.id],
             "excess": a.air_time - base[a.id]}
            for a in world.arrivals if a.air_time is not None and a.id in base]


def airborne_hours(world: World, base: Mapping[int, float]) -> list[dict]:
    gdp, gs = spans(world)
    by = defaultdict(list)
    for f in holding(world, base):
        by[f["eta"] // 60].append(f)
    out = []
    for h in range(FIRST_HOUR, LAST_HOUR):
        fs = by.get(h, [])
        lo = 60 * h
        if len(fs) >= MIN_GUARD_ARRIVALS and not overlaps(gdp, lo, lo + 60) and not overlaps(gs, lo, lo + 60):
            out.append({"date": world.date, "hub": world.hub, "hour": h, "flights": len(fs),
                        "modelled": statistics.mean(f["held"] for f in fs),
                        "observed": statistics.mean(f["excess"] for f in fs)})
    return out


def guard_airborne(rows: Sequence[dict]) -> dict:
    m = np.array([r["modelled"] for r in rows])
    o = np.array([r["observed"] for r in rows])
    corr = float(np.corrcoef(m, o)[0, 1]) if len(rows) > 2 and m.std() > 0 and o.std() > 0 else None
    ratios = [r["modelled"] / r["observed"] for r in rows if r["observed"] >= RATIO_MIN_EXCESS]
    ratio = statistics.median(ratios) if ratios else None
    return {"hours": len(rows), "correlation": corr, "correlation_threshold": CORRELATION,
            "ratio_hours": len(ratios), "median_ratio": ratio, "ratio_band": list(RATIO),
            "pass": corr is not None and corr >= CORRELATION and ratio is not None and RATIO[0] <= ratio <= RATIO[1],
            "mean_modelled": float(m.mean()) if len(m) else None, "mean_observed": float(o.mean()) if len(o) else None}


def train_capacity(worlds: Sequence[World]) -> dict[str, dict]:
    """Per hub, the median landings an hour over the given (calibrated train) worlds: by local hour of day and over
    07:00-23:00, for forecasts that fall back on a typical day."""
    out = {}
    for hub in sorted({w.hub for w in worlds}):
        by = defaultdict(list)
        for w in worlds:
            if w.hub == hub:
                for h in range(24):
                    by[h].append(sum(w.caps[4 * h:4 * h + 4]))
        out[hub] = {"by_hour": [statistics.median(by[h]) for h in range(24)],
                    "day": statistics.median(v for h in range(FIRST_HOUR, LAST_HOUR) for v in by[h])}
    return out


def with_reductions(world: World, r: Reductions) -> World:
    return replace(world, reductions=r, caps=tuple(capacity.bins(world.hub, world.timeline, world.departures, r)))


def save(path: Path, med: Medians, r: Reductions, extra: Mapping) -> None:
    doc = {"reductions": asdict(r), "medians": {k: [[*key, v] if isinstance(key, tuple) else [key, v]
                                                     for key, v in getattr(med, k).items()]
                                                for k in ("air", "air_any", "taxi_in", "taxi_in_hub", "taxi_out")},
           "line": [[*k, *v] for k, v in med.line.items()], "taxi_out_all": med.taxi_out_all, **extra}
    path.write_text(json.dumps(doc, indent=1, default=str) + "\n")


def load(path: Path) -> tuple[Medians, Reductions]:
    """The medians and reductions a calibration JSON holds, for building worlds."""
    doc = json.loads(path.read_text())
    m = doc["medians"]

    def table(rows: list, n: int) -> dict:
        return {tuple(r[:n]) if n > 1 else r[0]: r[n] for r in rows}

    med = Medians(table(m["air"], 3), table(m["air_any"], 2), {(a, b): (c, d) for a, b, c, d in doc["line"]},
                  table(m["taxi_in"], 2), table(m["taxi_in_hub"], 1), table(m["taxi_out"], 1), doc["taxi_out_all"])
    return med, Reductions(**doc["reductions"])


def summary(rows: Sequence[Hour]) -> dict:
    sat = [h for h in rows if h.saturated]
    return {"hours": len(rows), "saturated": len(sat), "saturated_program": sum(h.gdp or h.gs for h in sat),
            "by_category": dict(Counter(h.category for h in sat))}
