"""Realistic mode (spike, throwaway): a b6-v3 day in a world closer to the real one, beside the current mode, which it
leaves untouched (design: 2026-10-27-airops-realistic-mode-design.md).

The realistic world is the day's truth with:
- v4's layers (docs/v4/DESIGN.md §3.1-§3.3 on Alec's fork): JetBlue's departure caps at the hubs and arrival caps at
  the outstations, from every carrier's BTS pushbacks and arrivals, and the hubs' ramp closures from their METARs and
  SPECIs at v4's measured departure rate inside a closure (JFK 411/563, BOS 449/584). v3's crew desk keeps its own
  reserves, so v4's reserve pools are left out;
- gates: at most `gates[station]` JetBlue aircraft on the ground at or inbound to a station at once, the most the
  pack's 70 recorded days ever had there (sim.World.gates); a flight whose station is full waits at its origin;
- carrier breakdowns: each leg's aircraft breaks an hour before its scheduled departure with JetBlue's fair-weather
  code-A cancellation rate, seeded by the date, and is out for the rest of the day (sim.World.broken);
- aircraft swaps only at the hubs: at an outstation a flight takes the aircraft that flew into it on JetBlue's day
  when that aircraft is there (sim.World.swaps), as an outstation rarely has a spare; the current mode swaps freely
  anywhere, which absorbs the delay a late aircraft carries down its rotation;
- tomorrow: the end-of-day aircraft must cover the next day's first departures (BTS, d + 1) at each needed station;
  each is priced as a ferry when it's elsewhere plus its first flight's passengers' delay when it gets there late,
  never more than cancelling that flight, in place of v3's untimed end-of-day ferries.
The latest departure stays at the pack's 15 h: JetBlue's own records go past it (0.1% of its flown flights left over
18.8 h late across the pack's days), so the data give no tighter limit. No wait on the tarmac is modelled: a flight
held by its destination's programs or gates waits at the gate, so the DOT's 3-h rule can't bind.
"""

import gzip
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import replace
from functools import cache
from pathlib import Path

import numpy as np
import pandas as pd

from .build import bts
from .core.cost import FERRY_BASE_MIN, FERRY_MILES_PER_MIN, K_CANCEL, miles
from .core.sim import FLOWN, _assign
from .programs import candidates, crews, pax, sim
from .programs.cost import RATES, ferry_cost
from .programs.layers import config, fairday
from .programs.model import Task

CACHE = Path.home() / "Code" / "airops-realistic-cache"
INPUTS, DERIVED = CACHE / "inputs", CACHE / "derived"
RATE = {"JFK": round(411 / 563, 3), "BOS": round(449 / 584, 3)}
BREAK_LEAD = 60
SWAPS = frozenset({"JFK", "BOS"})


def gates(dates_dir: Path) -> dict[str, int]:
    """The most JetBlue aircraft of a pack day's tails on the ground at or inbound to each station at once, over every
    recorded day in `dates_dir`: a tail counts at its start position, then at each flown leg's end from its pushback."""
    out: Counter = Counter()
    for p in sorted(dates_dir.glob("*.json.gz")):
        t = json.loads(gzip.decompress(p.read_bytes()))["task"]
        by = {f["id"]: f["actual"] for f in t["flights"]}
        ev = []
        for tl in t["tails"]:
            ev.append((-math.inf, 1, tl["pos"]))
            cur = tl["pos"]
            for i in sorted((i for i in tl["legs"] if by[i]["dep"] is not None and by[i]["end"]),
                            key=lambda i: by[i]["dep"]):
                ev += [(by[i]["dep"], -1, cur), (by[i]["dep"], 1, by[i]["end"])]
                cur = by[i]["end"]
        now, top = Counter(), Counter()
        for _, d, s in sorted(ev, key=lambda e: (e[0], e[1])):
            now[s] += d
            top[s] = max(top[s], now[s])
        for s, n in top.items():
            out[s] = max(out[s], n)
    return dict(sorted(out.items()))


@cache
def breakdown_rate(months: tuple[str, ...]) -> float:
    """JetBlue's code-A cancellations per scope flight, pooled over the fair days (no B or C cancellation) of
    `months`."""
    d = pd.concat([fairday.days(bts.read_ontime(bts.ontime_path(INPUTS, m), DERIVED), ("JFK", "BOS"))
                   for m in months], ignore_index=True)
    fair = d[(d.B == 0) & (d.C == 0) & (d.flights > 0)]
    return float(fair.A.sum() / fair.flights.sum())


def layered(task: Task) -> Task:
    """The task with v4's departure caps, outstation arrival caps and ramp closures (no reserve pools)."""
    cfg = config.Config(rate=RATE)
    spans = config.spans(INPUTS, tuple(candidates.months_of(task.date)))
    return replace(task, layers=config.build(task, cfg, candidates.frame(INPUTS, DERIVED, task.date), spans))


def broken(task: Task, rate: float) -> dict[str, int]:
    """Each tail that breaks, to the minute it does: every leg draws, in flight-id order from a seed the date fixes,
    whether its scheduled tail breaks BREAK_LEAD before it; a tail breaks once, at its first draw."""
    rng = random.Random(int(hashlib.sha256(task.date.encode()).hexdigest()[:12], 16))
    tail_of = {i: t.tail for t in task.tails for i in t.legs}
    out: dict[str, int] = {}
    for f in sorted(task.flights, key=lambda f: f.id):
        if rng.random() < rate and f.id in tail_of and tail_of[f.id] not in out:
            out[tail_of[f.id]] = max(task.start, f.sched_dep - BREAK_LEAD)
    return out


def world(task: Task, gate_caps: dict[str, int], rate: float) -> sim.World:
    """The realistic truth of a layered task."""
    return replace(sim.truth(task), gates=gate_caps, broken=broken(task, rate), swaps=SWAPS)


def first_departures(task: Task) -> dict[tuple[str, str], list[int]]:
    """Per (family, station) the task needs an aircraft at tomorrow, the scheduled departures (minutes from the task's
    midnight) of JetBlue's first flights of that family from it on d + 1, one per aircraft needed, earliest first; a
    need with no flight found gets none (it's then priced as v3's ferry, untimed)."""
    x = candidates.frame(INPUTS, DERIVED, task.date)
    day = (pd.Timestamp(task.date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    x = x[(x.Reporting_Airline == "B6") & (x.FlightDate.astype(str).str[:10] == day)]
    family = {t.tail: t.family for t in task.tails}
    out = {}
    for (fam, s), n in task.needed.items():
        y = x[(x.Origin == s) & x.Tail_Number.map(family).eq(fam)]
        out[(fam, s)] = sorted(int(v - task.base) for v in y.sched_dep)[:n]
    return out


def tomorrow(task: Task, run: sim.Execution, firsts: dict[tuple[str, str], list[int]], airline: bool = False) -> int:
    """The cost of covering tomorrow's first departures with the day's end-of-day aircraft: per family a min-cost
    matching of aircraft (where and when each is ready) to needs (station, departure), each pair the ferry when the
    aircraft is elsewhere plus the first flight's passengers' delay when it is ready late, at most cancelling it;
    `airline`: the first flight's lateness at the aircraft's own crew and fixed cost (RATES gate) in place of its
    passengers' delay."""
    pax = defaultdict(list)
    for f in task.flights:
        pax[f.family].append(f.pax)
    seats = {fam: round(sum(v) / len(v)) for fam, v in pax.items()}
    family = {t.tail: t.family for t in task.tails}
    ready = {}
    for t in task.tails:
        legs = [i for i in run.legs.get(t.tail, ()) if run.flights[i].status == FLOWN]
        ready[t.tail] = (run.flights[legs[-1]].arr + task.turn_for(t.family)) if legs else t.ready
    total = 0
    for fam in sorted(set(family.values())):
        planes = sorted(p for p in run.ends if family.get(p) == fam)
        needs = [(s, d) for (g, s), n in sorted(task.needed.items()) if g == fam
                 for d in (firsts.get((g, s), []) + [None] * n)[:n]]
        size = max(len(planes), len(needs))
        matrix = [[0] * size for _ in range(size)]
        for a, p in enumerate(planes):
            for b, (s, d) in enumerate(needs):
                e = run.ends[p]
                move = 0 if e == s else FERRY_BASE_MIN + miles(task.airports[e], task.airports[s]) / FERRY_MILES_PER_MIN
                late = 0 if d is None else max(0, ready[p] + move - d)
                wait = RATES[fam].gate * late if airline else min(seats[fam] * late, seats[fam] * K_CANCEL)
                matrix[a][b] = ferry_cost(task, fam, e, s) + wait
        total += sum(matrix[a][b] for a, b in enumerate(_assign(matrix)))
    return total


def rated(task: Task, run: sim.Execution, total: float, firsts: dict) -> dict:
    """The day's costs in both modes' terms: v3's total, and realistic, v3's total with its untimed ferries replaced by
    tomorrow's cost."""
    tmr = tomorrow(task, run, firsts)
    return {"v3": total, "ferry": run.cost.ferry, "tomorrow": tmr, "realistic": total - run.cost.ferry + tmr}


@cache
def fare_line(year: int) -> tuple[float, float]:
    """JetBlue's one-way fare against distance in `year` (the nearest year with data): a least-squares line through its
    average fare in every top-1,000 city-pair market where it is the largest or the low-fare carrier (DOT Consumer
    Airfare Report, Table 1)."""
    d = pd.read_csv(INPUTS / "dot_airfare" / "table1.csv", usecols=["Year", "nsmiles", "carrier_lg", "fare_lg",
                                                                     "carrier_low", "fare_low"])
    b6 = pd.concat([d[d.carrier_lg == "B6"].assign(f=lambda x: x.fare_lg),
                    d[d.carrier_low == "B6"].assign(f=lambda x: x.fare_low)])
    y = min(b6.Year.unique(), key=lambda v: abs(v - year))
    x = b6[b6.Year == y]
    a, b = np.linalg.lstsq(np.vstack([np.ones(len(x)), x.nsmiles]).T, x.f, rcond=None)[0]
    return float(a), float(b)


def od_fare(task: Task, legs: tuple[int, ...]) -> float:
    """The fare of an itinerary over `legs`, by its origin-to-destination distance, in passenger-minutes."""
    a, b = fare_line(int(task.date[:4]))
    o, d = task.flight(legs[0]).orig, task.flight(legs[-1]).dest
    return crews.value(a + b * miles(task.airports[o], task.airports[d]), task.date)


def airline(task: Task, day, firsts: dict, passengers: pax.Passengers) -> dict:
    """JetBlue's own cost of a played day before its lost revenue is priced, in passenger-minutes: its aircraft's
    minutes (gate, overrun, less what its cancellations saved, with that saving apart), its crews' deadheads, hotels
    and end of day, tomorrow
    at the aircraft's own cost; with the passenger counts and the fares that lost revenue is priced on (the report's
    JetBlue cost): the fares of the refunded, of the stranded to a later day, and of every passenger booked on a
    flight cancelled (each itinerary's fare by od_fare)."""
    run = day.run
    c = run.cost
    group = {g.id: g for g in passengers.groups}
    fares = {g.id: od_fare(task, g.legs) for g in passengers.groups}
    rev = Counter()
    for part in run.pax.parts:
        rev[part.kind] += part.pax * fares[part.group]
    gone = {i for i, r in run.flights.items() if r.status != FLOWN}
    return {"aircraft": c.gate + c.overrun - c.saved, "saved": c.saved, "crew": day.crew_cost,
            "tomorrow": tomorrow(task, run, firsts, airline=True),
            "cancelled_pax": sum(f.pax for f in task.flights if f.id in gone),
            "cancelled_rev": sum(g.pax * fares[g.id] for g in group.values() if gone.intersection(g.legs)),
            "refunded_rev": rev[pax.REFUNDED], "stranded_rev": rev[pax.STRANDED], **run.pax.outcomes()}


def score(cost: float, anchor: float, k: float = 2.0) -> float:
    """The realistic reward: 0.5 at JetBlue's replayed cost (`anchor`), toward 1 as cost falls to 0 and toward the
    0.2 floor as it grows, as a function of cost / anchor alone so days compare."""
    return 0.2 + 0.8 / (1 + 5 / 3 * (max(cost, 0) / anchor) ** k)
