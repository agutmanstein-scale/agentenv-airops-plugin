"""Ramp closures at JFK and BOS from the METARs, as a cut in JetBlue's departure rate and a gate hold
(docs/v3/DESIGN.md §3.2).

An ASOS reports TS in present weather when it detects lightning within 5 nm of the field (VCTS is 5 to 10 nm, LTG DSNT
beyond: neither closes the ramp), and the remarks carry the thunderstorm's begin and end minutes (TSBmm or TSBhhmm,
TSEmm or TSEhhmm). Read oldest first, the routine METARs and the SPECIs give each closure: from a begin to its end; a
report with TS in present weather and none open opens one at the report; a report without TS closes one still open at
the report, unless its remarks end it first; the last report closes one still open. An end remark older than the open
closure's start belongs to an earlier storm (a SPECI's "TSE13B24" while a closure is open from :22) and is ignored.
Overlapping or touching intervals merge. The SPECIs, when the cache holds them, give the onset and end to the minute;
without them the routine reports alone, whose remarks carry the same minutes an hour later at most.

Lightning doesn't stop the ramp: during such intervals every carrier together still pushed back at about 75% of its
rate in the same minutes just before (the spike: JFK 411 vs 563, BOS 449 vs 584). `fit_rate` measures that ratio per
hub on train dates, leaving out a closure whose minutes just before reach into another closure, as they hold no
normal rate. `build` turns each closure over a task into JetBlue's cap there (§3.2): `rate` x JetBlue's normal hourly
departures from the hub (the task's flights scheduled to leave it in the clock hours the closure touches, per hour) x
the closure's length in hours, rounded to the nearest flight, and never below what those flights actually pushed back
inside it. The floor keeps the recorded pushbacks inside a closure within its cap; the gate hold can still make a
recorded turn inside a closure infeasible (an aircraft that landed inside and pushed back again before its end), so the
recorded day is not feasible by construction. `intervals` are in UTC epoch minutes, closures in minutes from the
task's midnight.
"""

import csv
import io
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from ...build import bts
from ..candidates import midnight
from ..model import Task

EVENT = re.compile(r"\bTS((?:[BE]\d{2,4})+)\b")
PART = re.compile(r"([BE])(\d{2,4})")
Interval = tuple[int, int]


def intervals(hub: str, metars: Iterable[Path], specis: Iterable[Path] = ()) -> list[Interval]:
    """The hub's closures, [start, end) in UTC epoch minutes, from its routine METAR and SPECI CSVs (IEM ASOS); a
    path that doesn't exist is skipped."""
    rows = [r for p in (*metars, *specis) if p.exists() for r in csv.DictReader(io.StringIO(p.read_text()))
            if r["station"] == hub and (r["metar"] or "").strip()]
    rows.sort(key=lambda r: r["valid"])
    out: list[Interval] = []
    start, now = None, None
    for r in rows:
        report = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        now = bts.epoch_minutes(report)
        _, _, remarks = r["metar"].partition(" RMK ")
        for m in EVENT.finditer(remarks):
            for kind, digits in PART.findall(m.group(1)):
                when = bts.epoch_minutes(_at(report, digits))
                if kind == "B" and start is None:
                    start = when
                elif kind == "E" and start is not None and when >= start:
                    out.append((start, max(when, start + 1)))
                    start = None
        here = any("TS" in c and not c.startswith("VC") for c in (r["wxcodes"] or "").split())
        if here and start is None:
            start = now
        elif not here and start is not None:
            out.append((start, max(now, start + 1)))
            start = None
    if start is not None:
        out.append((start, max(now, start + 1)))
    merged: list[Interval] = []
    for a, b in sorted(out):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _at(report: datetime, digits: str) -> datetime:
    """A remark's minute (mm, or hhmm), the latest such time at or before its report."""
    if len(digits) == 4:
        t = report.replace(hour=int(digits[:2]), minute=int(digits[2:]))
        return t - timedelta(days=1) if t > report else t
    t = report.replace(minute=int(digits[-2:]))
    return t - timedelta(hours=1) if t > report else t


def build(task: Task, spans: Mapping[str, Sequence[Interval]],
          rate: Mapping[str, float]) -> dict[str, tuple[tuple[int, int, int], ...]]:
    """The task's ramp closures per hub, (start, end, cap) from its midnight: each of `spans` (UTC epoch minutes, per
    hub) that overlaps the task's span, from its first watch to the end of the hour after its last deadline. A hub
    closed over the task without a `rate` (`fit_rate` gives none to a hub with nothing pushed back before its
    closures) is a ValueError: its cap can't be priced, and dropping the closure would hide the gap."""
    hi = 60 * (max(f.deadline for f in task.flights) // 60 + 2)
    out = {}
    for hub, xs in spans.items():
        mine = [f for f in task.flights if f.orig == hub]
        shut = []
        for a, b in ((a - task.base, b - task.base) for a, b in xs):
            if b <= task.start or a >= hi:
                continue
            if hub not in rate:
                raise ValueError(f"{hub} closes {a}-{b} on {task.date} but has no fitted rate")
            lo, top = 60 * (a // 60), 60 * -(-b // 60)
            hourly = sum(lo <= f.sched_dep < top for f in mine) * 60 / (top - lo)
            flew = sum(f.actual.dep is not None and a <= f.actual.dep < b for f in mine)
            shut.append((a, b, max(flew, math.floor(rate[hub] * hourly * (b - a) / 60 + 0.5))))
        if shut:
            out[hub] = tuple(shut)
    return out


def fit_rate(dates: Iterable[str], frame: pd.DataFrame, spans: Mapping[str, Sequence[Interval]]) -> dict[str, float]:
    """Per hub, every carrier's pushbacks inside its closures that start on `dates` (local days) over its pushbacks in
    the same number of minutes just before each, leaving out a closure whose minutes before reach into another of the
    hub's closures. `frame` holds every carrier's BTS flights once each (`Origin`, and `act_dep` in UTC epoch minutes,
    NaN when none); a hub with no pushback before any of its closures has no rate."""
    days = [(midnight(d), midnight(d) + 1440) for d in dates]
    out = {}
    for hub, xs in spans.items():
        pushed = frame.act_dep[frame.Origin == hub].dropna()
        inside = before = 0
        for a, b in xs:
            if any(lo <= a < hi for lo, hi in days) and not any(c < a and d > 2 * a - b for c, d in xs):
                inside += int(((pushed >= a) & (pushed < b)).sum())
                before += int(((pushed >= 2 * a - b) & (pushed < a)).sum())
        if before:
            out[hub] = inside / before
    return out
