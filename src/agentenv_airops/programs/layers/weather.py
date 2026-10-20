"""The hubs' weather as the desk reads it (docs/v3/DESIGN.md §4): every routine METAR, SPECI and TAF of the task's
hubs over its span, as news items (minute, kind, station, text) for Layers.weather.

A METAR or SPECI is dated by its observation (IEM ASOS `valid`), a TAF by its AFOS issue time; minutes are from the
task's midnight. Each reaches the desk at that minute plus its kind's lag (Layers.lags: metar 5, speci 5, taf 10 by
default, advisories 0), the time the product takes to be filed and spread. The span starts LEAD_OBS before the first
watch for the observations and LEAD_TAF for the TAFs (the TAF cycle is six hours), so the first watch has the latest of
each, and ends at the last deadline. The SPECI archive holds some reports the routine one has too; a SPECI with the same
minute and text as a routine METAR is dropped. Only the observation's raw text is carried: the desk reads it as a
dispatcher reads the wire.
"""

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from ...build import bts
from ...build import weather as v1
from ...flow import sources
from ..model import Task

LEAD_OBS = 90
LEAD_TAF = 360
Item = tuple[int, str, str, str]


def build(task: Task, inputs: Path) -> tuple[Item, ...]:
    """The task's hubs' METARs, SPECIs and TAFs over its span, oldest first (minute, kind, hub, station order); a
    month file the cache doesn't hold is skipped."""
    hi = max(f.deadline for f in task.flights)
    months = _months(task.base + task.start - LEAD_TAF, task.base + hi)
    out: dict[tuple[int, str, str], Item] = {}
    for hub in task.storms:
        for kind, path in [(k, p) for m in months for k, p in (("metar", v1.metar_path(inputs, hub, m)),
                                                                 ("speci", sources.speci_path(inputs, hub, m)))]:
            for minute, text in _observations(path, hub, task.base):
                if task.start - LEAD_OBS <= minute <= hi:
                    out.setdefault((minute, hub, text), (minute, kind, hub, text))
        for m in months:
            path = v1.afos_path(inputs, f"TAF{hub}", m)
            if not path.exists():
                continue
            for issued, body in v1.products(path.read_text(encoding="latin-1"), m):
                minute = bts.epoch_minutes(issued) - task.base
                if task.start - LEAD_TAF <= minute <= hi:
                    out.setdefault((minute, hub, body), (minute, "taf", hub, body))
    return tuple(sorted(out.values(), key=lambda x: (x[0], x[1], x[2], x[3])))


def _observations(path: Path, hub: str, base: int) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    out = []
    for r in csv.DictReader(io.StringIO(path.read_text())):
        text = (r["metar"] or "").strip()
        if r["station"] == hub and text:
            observed = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
            out.append((bts.epoch_minutes(observed) - base, text))
    return out


def _months(lo: int, hi: int) -> list[str]:
    """The UTC months epoch minutes lo to hi touch, as YYYY-MM."""
    first, last = (datetime.fromtimestamp(60 * x, UTC) for x in (lo, hi))
    out, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
