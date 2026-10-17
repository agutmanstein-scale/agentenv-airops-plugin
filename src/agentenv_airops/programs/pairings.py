"""Trips over the published schedule: per crew kind and type group, the duties that fly every leg of the date as
scheduled (flown and cancelled alike, never the flown day), by set partitioning with column generation.

The builder covers the date and the two days before it at once. A duty reports REPORT before its first departure, at a
base or at an outstation where a duty of the earlier days ended at least the rest before (one row per outstation and
report time) or where an aircraft of its group overnights before the first day (one crew per aircraft): crews lay over
wherever a duty ends (labelled; the overnight-aircraft rule alone leaves west-coast turns and dawn red-eyes without a
legal trip). Its legs connect at a station in CONNECT at least, as crews connect in the crew sim and the crew replay,
staying with the aircraft or not, and no sit is longer than MAX_SIT. It may begin by deadheading from a base and end by
deadheading to one, and it ends anywhere (a night away from where it reported is a hotel). It is legal as scheduled: a
pilot duty within Table B for its report's local hour at the report station and its segments and within Table A, without
the extension (kept for the unforeseen); a cabin duty within 14 h from report to release, its deadheads included. The
objective counts crews first, then hotels, deadhead minutes, aircraft changes (crews prefer to stay with their aircraft)
and duty minutes. The date's duties are those with a leg departing on the date, so a duty spanning midnight belongs to
the date whose legs it flies; the earlier days' duties only feed its outstation starts.
"""

import bisect
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import highspy
import numpy as np
import pandas as pd

from ..build import pack
from . import candidates, crews, network

CREW_COST = 10_000
HOTEL_COST = 500
DEADHEAD_COST = 2
CHANGE_COST = 50
DUTY_COST = 1
UNCOVERED = 1_000_000
ROUNDS = 200
EARLY_KEY = 10_000_000
PER_START = 3
MAX_SIT = 240


@dataclass(frozen=True)
class Leg:
    """A scheduled leg: `key` its row, `hour` the local hour at its origin of a report REPORT before it, `after`
    the tail's next leg."""

    key: int
    flight: str
    group: str
    orig: str
    dest: str
    sd: int
    sa: int
    block: int
    tail: str
    hour: int
    after: int | None


@dataclass(frozen=True)
class Duty:
    kind: str
    group: str
    legs: tuple[int, ...]
    deadheads: tuple[int, ...]
    start: str
    end: str
    report: int
    release: int
    hour: int
    cost: int


@dataclass
class Built:
    kind: str
    group: str
    duties: list[Duty]
    uncovered: list[int]
    lp: float
    ip: float
    columns: int
    rounds: int
    seconds: float

    @property
    def gap_pct(self) -> float:
        return 100 * (self.ip - self.lp) / self.ip if self.ip else 0.0


def schedule(ctx: candidates.Context, inputs, derived) -> pd.DataFrame:
    """The carrier's legs of the date and the two days before it as scheduled, on the date's clock, keyed uniquely
    (the date's and the day before's by their row in the task's day, the earliest by an offset)."""
    day = ctx.day
    raw = candidates.frame(inputs, derived, pack.shift(day.date, -1))
    early = raw[(raw.FlightDate == pack.shift(day.date, -2)) & (raw.Reporting_Airline == pack.CARRIER)]
    early = pack.local(early, day.base)
    early.index = early.index + EARLY_KEY
    return pd.concat([early, day.prev, day.cur])


def legs_of(day: pack.Day, x: pd.DataFrame, families: Mapping[str, str]) -> list[Leg]:
    """Every typed leg of `x` as scheduled, with its tail's next leg."""
    x = x[x.Tail_Number.isin(day.fleet) & x.Tail_Number.isin(families)].sort_values(["Tail_Number", "sd"])
    nxt = {}
    for _, g in x.groupby("Tail_Number"):
        rows = list(g.itertuples())
        for a, b in zip(rows, rows[1:], strict=False):
            nxt[a.Index] = b.Index
    out = [Leg(int(r.Index), f"{pack.CARRIER} {int(r.Flight_Number_Reporting_Airline)}",
               crews.TYPE[families[r.Tail_Number]], r.Origin, r.Dest, int(r.sd), int(r.sa), int(r.CRSElapsedTime),
               r.Tail_Number, crews.local_hour(lambda s, m: network.local(day, s, m), r.Origin,
                                               int(r.sd) - crews.REPORT), nxt.get(r.Index))
           for r in x.itertuples()]
    return sorted(out, key=lambda leg: (leg.sd, leg.key))


def build_all(legs: Sequence[Leg], d: str, time_limit: float = 600.0) -> dict[tuple[str, str], Built]:
    """Every crew kind and type group's trips of date d."""
    return {(kind, g): build(legs, kind, g, crews.bases(kind, d), time_limit=time_limit)
            for kind in crews.KINDS for g in crews.GROUPS if any(leg.group == g for leg in legs)}


def build(legs: Sequence[Leg], kind: str, group: str, bases: Sequence[str], time_limit: float = 600.0,
          first_end: int = -1440) -> Built:
    """Duties over `legs` (the date and the days before it, the first ending at `first_end`); the date's are the
    trips."""
    t0 = time.monotonic()
    mine = [leg for leg in legs if leg.group == group]
    pricer = Pricer(legs, mine, kind, group, bases)
    master = Master(mine, overnights(legs, group, bases, first_end), bases, crews.rest(kind))
    master.add(pricer.seed())
    rounds = 0
    while rounds < ROUNDS and time.monotonic() - t0 < time_limit:
        rounds += 1
        legs_dual, lay, _ = master.solve_lp()
        new = pricer.price(legs_dual, lay)
        if not new:
            break
        master.add(new)
    lp = master.solve_lp()[2]
    ip, chosen = master.solve_ip(max(60.0, time_limit - (time.monotonic() - t0)))
    duties = [master.columns[j] for j in chosen]
    covered = {k for d in duties for k in d.legs}
    sd = {leg.key: leg.sd for leg in legs}
    today = [d for d in duties if max(sd[k] for k in d.legs) >= 0]
    return Built(kind, group, today, sorted(leg.key for leg in mine if leg.key not in covered and leg.sd >= 0),
                 lp, ip, len(master.columns), rounds, round(time.monotonic() - t0, 1))


def overnights(legs: Sequence[Leg], group: str, bases: Sequence[str], before: int) -> dict[str, int]:
    """Per outstation, the aircraft of the group the schedule has there when the first day starts: a crew each."""
    first: dict[str, Leg] = {}
    for leg in sorted(legs, key=lambda g: g.sd):
        if leg.group == group and leg.sd < before and leg.tail not in first:
            first[leg.tail] = leg
    out: dict[str, int] = defaultdict(int)
    for leg in first.values():
        if leg.orig not in bases:
            out[leg.orig] += 1
    return dict(out)


class Master:
    """min sum c_j x_j + UNCOVERED u: each leg flown by one duty or uncovered; a duty that reports at an outstation
    takes a crew there, from the aircraft overnighting there before the first day or from a duty that ended there at
    least `rest` earlier (one row per outstation and report time)."""

    def __init__(self, legs: Sequence[Leg], starts: Mapping[str, int], bases: Sequence[str], rest: int):
        self.bases, self.rest = set(bases), rest
        self.row = {leg.key: n for n, leg in enumerate(legs)}
        times: dict[str, set[int]] = defaultdict(set)
        for leg in legs:
            if leg.orig not in self.bases:
                times[leg.orig].add(leg.sd - crews.REPORT)
        self.lay = {s: sorted(ts) for s, ts in times.items()}
        n = len(legs)
        self.lay_row = {}
        lo = [1.0] * n
        for s, ts in sorted(self.lay.items()):
            for t in ts:
                self.lay_row[(s, t)] = n + len(self.lay_row)
                lo.append(-float(starts.get(s, 0)))
        hi = [1.0] * n + [highspy.kHighsInf] * len(self.lay_row)
        self.h = h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("threads", 1)
        h.addRows(len(lo), np.array(lo), np.array(hi), 0, np.zeros(len(lo), dtype=np.int32),
                  np.array([], dtype=np.int32), np.array([]))
        for k in range(n):
            h.addCol(UNCOVERED, 0.0, 1.0, 1, np.array([k], dtype=np.int32), np.array([1.0]))
        self.slack = n
        self.columns: list[Duty] = []
        self.seen: set[tuple] = set()

    def coefs(self, d: Duty) -> dict[int, float]:
        out = {self.row[k]: 1.0 for k in d.legs}
        if d.start not in self.bases:
            for t in self.lay.get(d.start, ()):
                if t >= d.report:
                    out[self.lay_row[(d.start, t)]] = -1.0
        if d.end not in self.bases:
            for t in self.lay.get(d.end, ()):
                if t >= d.release + self.rest:
                    out[self.lay_row[(d.end, t)]] = out.get(self.lay_row[(d.end, t)], 0.0) + 1.0
        return out

    def add(self, duties: Sequence[Duty]) -> None:
        for d in duties:
            key = (d.legs, d.deadheads, d.start, d.end)
            if key in self.seen:
                continue
            self.seen.add(key)
            c = self.coefs(d)
            self.h.addCol(float(d.cost), 0.0, highspy.kHighsInf, len(c), np.array(list(c), dtype=np.int32),
                          np.array(list(c.values())))
            self.columns.append(d)

    def solve_lp(self) -> tuple[dict[int, float], dict[str, tuple[list[int], np.ndarray]], float]:
        self.h.run()
        dual = np.asarray(self.h.getSolution().row_dual)
        legs = {k: dual[r] for k, r in self.row.items()}
        lay = {}
        for s, ts in self.lay.items():
            y = np.array([dual[self.lay_row[(s, t)]] for t in ts])
            lay[s] = (ts, np.cumsum(y[::-1])[::-1])
        return legs, lay, self.h.getInfo().objective_function_value

    def solve_ip(self, time_limit: float) -> tuple[float, list[int]]:
        h = self.h
        n = h.getNumCol()
        h.changeColsIntegrality(n, np.arange(n, dtype=np.int32), np.array([highspy.HighsVarType.kInteger] * n))
        h.setOptionValue("time_limit", float(time_limit))
        h.setOptionValue("mip_rel_gap", 1e-6)
        h.run()
        x = h.getSolution().col_value
        return h.getInfo().objective_function_value, [j for j in range(len(self.columns)) if x[self.slack + j] > 0.5]


def after(lay: Mapping[str, tuple[list[int], np.ndarray]], s: str, t: int) -> float:
    """The sum of the layover duals at s from t on."""
    if s not in lay:
        return 0.0
    ts, tail = lay[s]
    k = bisect.bisect_left(ts, t)
    return float(tail[k]) if k < len(ts) else 0.0


class Pricer:
    """Duties of least reduced cost, from each first operated leg (and a positioning deadhead before it): a depth-first
    search over connecting legs with dominance on segments and flight time."""

    def __init__(self, legs: Sequence[Leg], mine: Sequence[Leg], kind: str, group: str, bases: Sequence[str]):
        self.kind, self.group, self.bases = kind, group, set(bases)
        self.mine = mine
        self.by_orig: dict[str, list[Leg]] = defaultdict(list)
        for leg in mine:
            self.by_orig[leg.orig].append(leg)
        self.times = {s: [leg.sd for leg in ls] for s, ls in self.by_orig.items()}
        self.into: dict[str, list[Leg]] = defaultdict(list)
        self.home: dict[str, list[Leg]] = defaultdict(list)
        for leg in sorted(legs, key=lambda g: g.sd):
            if leg.orig in self.bases:
                self.into[leg.dest].append(leg)
            if leg.dest in self.bases:
                self.home[leg.orig].append(leg)
        self.homes = {s: [leg.sd for leg in ls] for s, ls in self.home.items()}
        self.rate = crews.rate(kind, group)
        self.rest = crews.rest(kind)

    def seed(self) -> list[Duty]:
        return [d for leg in self.mine for d in self.price_from(leg, None, {}, {}, keep=1)]

    def price(self, legs: Mapping[int, float], lay: Mapping) -> list[Duty]:
        out = []
        for leg in self.mine:
            out += self.price_from(leg, None, legs, lay)
            for dh in self.into.get(leg.orig, ()):
                if dh.sa + crews.CONNECT <= leg.sd <= dh.sa + MAX_SIT and dh.key != leg.key:
                    out += self.price_from(leg, dh, legs, lay)
        return out

    def price_from(self, first: Leg, dh: Leg | None, pi: Mapping[int, float], lay: Mapping,
                   keep: int = PER_START) -> list[Duty]:
        where = dh.orig if dh else first.orig
        lead = dh or first
        report, hour = lead.sd - crews.REPORT, lead.hour
        if not self.legal(report, hour, 1, first.sa):
            return []
        flying = crews.flight_time(hour)
        base = CREW_COST + ((DEADHEAD_COST + self.rate) * dh.block if dh else 0)
        shift = -after(lay, where, report) if where not in self.bases else 0.0
        found = []
        stack = [(first, 1, first.block, base, pi.get(first.key, 0.0), (first.key,))]
        kept: dict[tuple[int, int], list[tuple[int, float, float]]] = defaultdict(list)
        while stack:
            leg, n, ft, cost, gain, path = stack.pop()
            labels = kept[(leg.key, n)]
            if any(f <= ft and c - g <= cost - gain + 1e-9 for f, c, g in labels):
                continue
            labels.append((ft, cost, gain))
            for c, d in self.ends_at(leg, report, hour, cost, path, dh, where):
                credit = after(lay, d.end, d.release + self.rest) if d.end not in self.bases else 0.0
                found.append((c - gain - shift - credit, d))
            if n >= 7:
                continue
            for nxt in self.next_legs(leg):
                if ft + nxt.block > flying or not self.legal(report, hour, n + 1, nxt.sa):
                    continue
                step = DUTY_COST * (nxt.sa - leg.sa) + CHANGE_COST * (leg.after != nxt.key)
                stack.append((nxt, n + 1, ft + nxt.block, cost + step, gain + pi.get(nxt.key, 0.0),
                              path + (nxt.key,)))
        found.sort(key=lambda p: p[0])
        return [d for rc, d in found[:keep] if rc < -1e-6 or not pi]

    def legal(self, report: int, hour: int, n: int, sa: int) -> bool:
        if self.kind == "pilot":
            return sa - report <= crews.fdp(hour, n)
        return sa + crews.DEBRIEF - report <= crews.CABIN_DUTY

    def next_legs(self, leg: Leg) -> list[Leg]:
        ls, ts = self.by_orig.get(leg.dest, []), self.times.get(leg.dest, [])
        lo = bisect.bisect_left(ts, leg.sa)
        hi = bisect.bisect_right(ts, leg.sa + MAX_SIT)
        return [g for g in ls[lo:hi] if g.sd >= leg.sa + crews.CONNECT]

    def ends_at(self, leg: Leg, report: int, hour: int, cost: float, path: tuple[int, ...], dh: Leg | None,
                where: str) -> list[tuple[float, Duty]]:
        first = (dh.key,) if dh else ()
        release = leg.sa + crews.DEBRIEF
        c = cost + DUTY_COST * (release - report) + HOTEL_COST * (leg.dest != where or leg.dest not in self.bases)
        out = [(c, Duty(self.kind, self.group, path, first, where, leg.dest, report, release, hour, round(c)))]
        if leg.dest not in self.bases:
            ts = self.homes.get(leg.dest, [])
            k = bisect.bisect_left(ts, leg.sa + crews.CONNECT)
            if k < len(ts) and ts[k] <= leg.sa + MAX_SIT:
                back = self.home[leg.dest][k]
                release = back.sa + crews.DEBRIEF
                if self.kind == "pilot" or release - report <= crews.CABIN_DUTY:
                    c = (cost + DUTY_COST * (release - report) + (DEADHEAD_COST + self.rate) * back.block
                         + HOTEL_COST * (back.dest != where))
                    out.append((c, Duty(self.kind, self.group, path, first + (back.key,), where, back.dest, report,
                                        release, hour, round(c))))
        return out
