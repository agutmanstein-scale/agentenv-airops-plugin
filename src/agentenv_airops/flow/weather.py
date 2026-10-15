"""The hub's weather as the Command Center model reads it: every routine METAR and SPECI as an observation, held until
the next one, and the radar's share of the terminal area in heavy precipitation, both per 15-minute bin.

An observation carries the sustained wind (true degrees, knots), the gust, the ceiling (lowest broken, overcast or
vertical-visibility layer) and the visibility, and three flags: a thunderstorm at the field (TS in the present weather
outside VC), one nearby (VCTS, a CB layer, or CB, LTG or TS in the remarks) and frozen precipitation (SN, SG, PL,
FZRA, FZDZ; not blowing or drifting snow). A bin takes the observation in force at its middle minute and its radar
frame's share, the previous bin's where the frame is missing. A TAF read into the same Obs shape scores through the
same capacity function.
"""

import csv
import functools
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..build import bts
from ..build import weather as v1
from ..build.weather import number
from ..programs import candidates
from . import sources

BIN = sources.BIN
FROZEN = ("SN", "SG", "PL", "FZRA", "FZDZ")
REMARK_STORM = re.compile(r"\b(CB\w*|LTG\w*|TS(?!NO)\w*)\b")
STORM_FULL = 0.10
"""A 40 nm disc this share or more at 40 dBZ counts as a full storm at or near the field; less counts pro rata."""


@dataclass(frozen=True)
class Obs:
    at: int
    wind_dir: float | None
    wind_kt: float
    gust_kt: float | None
    ceiling_ft: float | None
    vis_sm: float | None
    ts: bool
    vicinity: bool
    frozen: bool
    speci: bool = False


@dataclass(frozen=True)
class Bin:
    """A 15-minute bin's weather: the observation in force and the radar's 40 dBZ share of the disc (None when no
    frame was ever found)."""

    obs: Obs
    share: float | None

    @property
    def storm(self) -> float:
        """The storm intensity at or near the field, 0 to 1: 1 with TS at the field, else the radar share over
        STORM_FULL, capped at 1."""
        if self.obs.ts:
            return 1.0
        return min(1.0, (self.share or 0.0) / STORM_FULL)


def observation(r: dict, base: int, speci: bool = False) -> Obs:
    at = bts.epoch_minutes(datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)) - base
    layers = [(r[f"skyc{i}"], number(r[f"skyl{i}"])) for i in range(1, 5)]
    ceiling = min((h for c, h in layers if c in ("BKN", "OVC", "VV") and h is not None), default=None)
    codes = (r["wxcodes"] or "").split()
    body, _, remarks = r["metar"].partition(" RMK ")
    ts = any("TS" in c and not c.startswith("VC") for c in codes)
    cb = any(re.fullmatch(r"(FEW|SCT|BKN|OVC|VV)\d{3}(CB|TCU)?", w) and w.endswith("CB") for w in body.split())
    vicinity = not ts and (any(c.startswith("VCTS") for c in codes) or cb or bool(REMARK_STORM.search(remarks)))
    precip = [c for c in codes if not c.startswith("VC") and c.lstrip("+-")[:4] not in ("BLSN", "DRSN")]
    frozen = any(f in c for c in precip for f in FROZEN)
    return Obs(at, number(r["drct"]), number(r["sknt"]) or 0.0, number(r["gust"]), ceiling, number(r["vsby"]), ts,
               vicinity, frozen, speci)


@functools.lru_cache(maxsize=64)
def observations(inputs: Path, hub: str, d: str) -> tuple[Obs, ...]:
    """Every METAR and SPECI of the months d - 1 to d + 1 fall in, in minutes from d's local midnight, oldest first."""
    base = candidates.midnight(d)
    out = []
    for m in candidates.months_of(d):
        for path, speci in ((v1.metar_path(inputs, hub, m), False), (sources.speci_path(inputs, hub, m), True)):
            for r in csv.DictReader(io.StringIO(path.read_text())):
                if r["metar"].strip():
                    out.append(observation(r, base, speci))
    return tuple(sorted(out, key=lambda o: (o.at, o.speci)))


def in_force(obs: Sequence[Obs], t: float) -> Obs:
    """The latest observation at or before t (the first one before any)."""
    lo, hi = 0, len(obs)
    while lo < hi:
        mid = (lo + hi) // 2
        if obs[mid].at <= t:
            lo = mid + 1
        else:
            hi = mid
    return obs[max(lo - 1, 0)]


def timeline(inputs: Path, hub: str, d: str, n: int) -> tuple[Bin, ...]:
    """The first n bins from d's local midnight."""
    obs = observations(inputs, hub, d)
    frames = sources.load_radar(inputs, d)
    base = candidates.midnight(d)
    out, last = [], None
    for b in range(n):
        f = frames.get(base + BIN * b + 5)
        if f and f["shares"] is not None:
            last = f["shares"][hub]["40"]
        out.append(Bin(in_force(obs, BIN * b + BIN // 2), last))
    return tuple(out)
