"""The hub's TAFs as the Command Center model reads them (P6): each product as issued (AFOS issue time), its validity,
its prevailing groups (the base and each FM, which replaces everything; a BECMG's elements from the end of its period)
and its alternatives (TEMPO, PROB30 or PROB40, and a BECMG during its period), each a change of the elements it lists
over the prevailing conditions. A state reads into weather.Obs, so the same capacity function scores a forecast and
the truth: sustained wind and gust, the visibility (P6SM as 6), the ceiling (lowest broken, overcast or vertical
visibility), a thunderstorm at the field (TS outside VC), one nearby (VCTS or a CB layer) and frozen precipitation
(SN, SG, PL, FZRA, FZDZ). Past its validity a TAF holds its last prevailing state and offers no alternative.
"""

import functools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..build import bts
from ..build import weather as v1
from ..build.advisories import near
from ..programs import candidates
from .weather import FROZEN, Obs

WIND = re.compile(r"^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT$")
VIS = re.compile(r"^([PM])?(?:(\d+)|(\d)/(\d))SM$")
SKY = re.compile(r"^(?:(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?|VV(\d{3})|SKC|CLR|NSC)$")
WX = re.compile(r"^(?:[+-]|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)*"
                r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*$")
TIME = re.compile(r"^(\d{2})(\d{2})/(\d{2})(\d{2})$")
CHANGE = re.compile(r"^(FM\d{6}|TEMPO|PROB\d{2}|BECMG)$")
ALTERNATIVE = ("TEMPO", "PROB", "BECMG")


@dataclass(frozen=True)
class Group:
    kind: str
    start: int
    end: int | None
    elements: Mapping[str, object]


@dataclass(frozen=True)
class Taf:
    issued: int
    start: int
    end: int
    base: Mapping[str, object]
    groups: tuple[Group, ...]


@functools.lru_cache(maxsize=16)
def load(inputs: Path, hub: str, d: str) -> tuple[Taf, ...]:
    """Every TAF of the months d - 1 to d + 1 fall in, minutes from d's local midnight, oldest first."""
    base = candidates.midnight(d)
    out = []
    for m in candidates.months_of(d):
        for issued, body in v1.products(v1.afos_path(inputs, f"TAF{hub}", m).read_text(encoding="latin-1"), m):
            taf = parse(body, issued, base)
            if taf is not None:
                out.append(taf)
    return tuple(sorted(out, key=lambda t: t.issued))


def parse(body: str, issued: datetime, base: int) -> Taf | None:
    """One TAF product (the text after its PIL line); None when it has no valid period (NIL, a cancellation)."""
    tokens = body.replace("=", " ").split()
    valid = next((i for i, w in enumerate(tokens) if TIME.match(w)), None)
    if valid is None:
        return None

    def at(day: str, hour: str, minute: str = "00") -> int:
        return bts.epoch_minutes(near(issued, int(day), int(hour), int(minute))) - base

    v = TIME.match(tokens[valid]).groups()
    chunks: list[tuple[str, list[str]]] = [("BASE", [])]
    rest = tokens[valid + 1:]
    k = 0
    while k < len(rest):
        w = rest[k]
        if CHANGE.match(w):
            head = [w]
            if w.startswith("PROB") and k + 1 < len(rest) and rest[k + 1] == "TEMPO":
                k += 1
            if not w.startswith("FM"):
                head.append(rest[k + 1])
                k += 1
            chunks.append((" ".join(head), []))
        else:
            chunks[-1][1].append(w)
        k += 1
    groups = []
    for head, words in chunks[1:]:
        parts = head.split()
        if parts[0].startswith("FM"):
            s = parts[0][2:]
            groups.append(Group("FM", at(s[0:2], s[2:4], s[4:6]), None, elements(words)))
        elif TIME.match(parts[-1]):
            d1, h1, d2, h2 = TIME.match(parts[-1]).groups()
            kind = "PROB" if parts[0].startswith("PROB") else parts[0]
            groups.append(Group(kind, at(d1, h1), at(d2, h2), elements(words)))
    return Taf(bts.epoch_minutes(issued) - base, at(v[0], v[1]), at(v[2], v[3]), elements(chunks[0][1]),
               tuple(groups))


def elements(words: Sequence[str]) -> dict[str, object]:
    """The elements a group lists: wind (direction or None, knots, gust), visibility, sky (ceiling, CB) and weather."""
    out: dict[str, object] = {}
    wx, sky, whole = [], [], None
    for w in words:
        if m := WIND.match(w):
            out["wind"] = (None if m.group(1) == "VRB" else float(m.group(1)), float(m.group(2)),
                           float(m.group(3)) if m.group(3) else None)
        elif w.isdigit() and len(w) == 1:
            whole = float(w)
        elif m := VIS.match(w):
            value = float(m.group(2)) if m.group(2) else int(m.group(3)) / int(m.group(4))
            out["vis"] = value + (whole or 0.0)
            whole = None
        elif m := SKY.match(w):
            height = m.group(2) or m.group(4)
            sky.append((m.group(1) or ("VV" if m.group(4) else None), None if height is None else 100 * int(height),
                        m.group(3) == "CB"))
        elif w == "NSW":
            out["wx"] = ()
        elif w and WX.match(w) and not w.startswith("WS"):
            wx.append(w)
    if sky:
        ceiling = min((h for c, h, _ in sky if c in ("BKN", "OVC", "VV") and h is not None), default=None)
        out["sky"] = (ceiling, any(cb for _, _, cb in sky))
    if wx:
        out["wx"] = tuple(wx)
    return out


def state(taf: Taf, t: int) -> tuple[Obs, list[Obs]]:
    """The prevailing conditions at minute t and the alternatives in force then."""
    if t >= taf.end:
        return obs(prevailing(taf, taf.end - 1), t), []
    t = max(t, taf.start)
    now = prevailing(taf, t)
    alts = [obs({**now, **g.elements}, t) for g in taf.groups
            if g.kind in ALTERNATIVE and g.start <= t < g.end]
    return obs(now, t), alts


def prevailing(taf: Taf, t: int) -> dict[str, object]:
    now = dict(taf.base)
    for g in taf.groups:
        if g.kind == "FM" and g.start <= t:
            now = dict(g.elements)
        elif g.kind == "BECMG" and g.end <= t:
            now.update(g.elements)
    return now


def obs(e: Mapping[str, object], t: int) -> Obs:
    wind_dir, kt, gust = e.get("wind", (None, 0.0, None))
    ceiling, cb = e.get("sky", (None, False))
    wx = e.get("wx", ())
    ts = any("TS" in w and not w.startswith("VC") for w in wx)
    vicinity = not ts and (any(w.startswith("VCTS") for w in wx) or cb)
    precip = [w for w in wx if not w.startswith("VC") and w.lstrip("+-")[:4] not in ("BLSN", "DRSN")]
    frozen = any(f in w for w in precip for f in FROZEN)
    return Obs(t, wind_dir, kt, gust, ceiling, e.get("vis"), ts, vicinity, frozen)


def in_force(tafs: Sequence[Taf], t: int) -> Taf | None:
    """The latest TAF or amendment issued by minute t."""
    issued = [x for x in tafs if x.issued <= t]
    return issued[-1] if issued else None
