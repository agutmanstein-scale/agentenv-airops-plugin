"""v4's crew layer (docs/v4/DESIGN.md §1): pairings legal on schedule, cut in place of Edgar's proxy (repairs.pairings).

Edgar's pairings chain each tail's legs from a base departure to the next base arrival with no duty limit, so a long
chain through outstations exceeds 14 CFR 117 on schedule and its later legs can never fly. Here each tail's scheduled
legs of the day (cancelled ones and those before the window included, as repairs.crews takes them), in order, are cut
into duties:
- a duty starts at the tail's first leg, at every departure from a crew base, and after a ground gap of REST or more
  (the 10 h rest of 117.25(e), an overnight);
- it takes the tail's next leg while the duty, that leg its last, would still end on schedule `buffer` minutes inside
  its Table B limit (repairs.fdp, for the report REPORT minutes before its first departure, at the report's local hour
  at its first origin, and the duty's segments with that leg) and hold at most `max_segments` legs; otherwise a new
  duty starts at that leg (a crew change at a base, an overnight crew at an outstation). A first leg longer than its
  limit alone is still a duty of its own.
A duty is a pairing, and its legs get Edgar's records (model.Crew): its report, station, segments and limit; an
outstation departure must block in by the report + the limit + EXTENSION (117.19(a)(1)), so its crew deadline is that
less its scheduled block, its rest REST after it; a departure from a base has none in the record (reserves crew it),
and under a finite reserve pool there (Layers.reserves, v3's) Task.crew gives it the same deadline and rest. So every
duty is legal on schedule with `buffer` and the extension to spare, and the crew binds only when delays push a duty
past its limit, as real crews time out; the sim and the hindsight MIP enforce it as they do Edgar's.

A build's crew mode is a Rule: Edgar's ('edgar', repairs.crews) or v4's ('v4', `crews` at its buffer and max_segments,
fitted on train dates by layers.calibrate.crew_fit; defaults BUFFER and MAX_SEGMENTS, the spike's).
"""

from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .. import repairs
from ..model import EXTENSION, REST, Crew

MODES = ("edgar", "v4")
BUFFER, MAX_SEGMENTS = 60, 4


@dataclass(frozen=True)
class Rule:
    """How a build cuts pairings: `mode` 'edgar' (repairs.crews; `buffer` and `max_segments` unused) or 'v4'."""

    mode: str = "edgar"
    buffer: int = BUFFER
    max_segments: int = MAX_SEGMENTS

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"crew mode {self.mode!r} is none of {MODES}")

    def crews(self, legs: pd.DataFrame, base: int, bases: Collection[str],
              zones: Mapping[str, str]) -> dict[Hashable, Crew]:
        if self.mode == "edgar":
            return repairs.crews(legs, base, bases, zones)
        return crews(legs, base, bases, zones, self.buffer, self.max_segments)

    def to_dict(self) -> dict:
        v4 = self.mode == "v4"
        return {"crew": self.mode, "buffer": self.buffer if v4 else None,
                "max_segments": self.max_segments if v4 else None}


EDGAR = Rule()


def crews(legs: pd.DataFrame, base: int, bases: Collection[str], zones: Mapping[str, str], buffer: int = BUFFER,
          max_segments: int = MAX_SEGMENTS) -> dict[Hashable, Crew]:
    """The crew of each of `legs` by row label, as repairs.crews takes them (Tail_Number, Origin, Dest, local sd and
    CRSElapsedTime; `base` their midnight's UTC epoch minute, `zones` each origin's time zone), on duties cut legal on
    schedule."""
    out = {}
    for tail, x in legs.sort_values(["Tail_Number", "sd"]).groupby("Tail_Number"):
        for k, duty in enumerate(duties(list(x.itertuples()), base, bases, zones, buffer, max_segments)):
            first = duty[0]
            report = int(first.sd) - repairs.REPORT
            limit = repairs.fdp(hour(base, report, zones[first.Origin]), len(duty))
            for r in duty:
                d = None if r.Origin in bases else report + limit + EXTENSION - int(r.CRSElapsedTime)
                out[r.Index] = Crew(f"{tail}/{k}", report, first.Origin, len(duty), limit, d,
                                    None if d is None else d + REST)
    return out


def duties(legs: Sequence, base: int, bases: Collection[str], zones: Mapping[str, str], buffer: int = BUFFER,
           max_segments: int = MAX_SEGMENTS) -> list[list]:
    """A tail's legs (rows with Origin, Dest, sd and CRSElapsedTime) in scheduled order cut into duties legal on
    schedule."""
    out: list[list] = []
    for r in legs:
        if out and not (r.Origin in bases or int(r.sd) - end(out[-1][-1]) >= REST or len(out[-1]) >= max_segments):
            report = int(out[-1][0].sd) - repairs.REPORT
            limit = repairs.fdp(hour(base, report, zones[out[-1][0].Origin]), len(out[-1]) + 1)
            if end(r) + buffer <= report + limit:
                out[-1].append(r)
                continue
        out.append([r])
    return out


def end(r) -> int:
    """A leg's scheduled arrival, its departure plus its scheduled block."""
    return int(r.sd) + int(r.CRSElapsedTime)


def hour(base: int, minute: int, zone: str) -> int:
    """The local hour in `zone` of `minute` past the UTC epoch minute `base`."""
    return datetime.fromtimestamp(60 * (base + minute), ZoneInfo(zone)).hour
