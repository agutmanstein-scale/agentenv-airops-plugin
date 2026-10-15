"""A hub's arrival capacity per 15-minute bin from its weather, by the FAA Airport Capacity Profiles' facility-reported
rates, reduced for storms and frozen precipitation by factors calibrated on train days.

Category: visual, marginal or instrument by ceiling and visibility at the profile's thresholds (JFK 2014 p. 2: visual
at 2000 ft and 4 SM; BOS 2019 p. 2: 3000 ft and 3 SM; instrument below 1000 ft or 3 SM at both).

JFK trades arrivals for departures in every category. Visual runs between the facility points 52 arrivals at 32
departures (arrival priority, 13L,22L|13R) and 38 at 52 (departure priority, 22L|22R,31L), a slope of -0.7 arrivals per
departure; marginal and instrument hold their facility points (48 at 38 and 48 at 36, 4R,4L|4L,31L) and lose 0.7
arrivals per departure past them. Departure demand counts to 52, the profile's highest facility departure rate, and
the rates are kept visual >= marginal >= instrument at equal demand. The wind only labels JFK's configuration (the
profile gives one per category).

BOS flies the first wind-feasible flow of north, south and crosswind, choosing afresh each bin (no switching cost). A
configuration is feasible when every arrival runway has a crosswind of at most 20 kt and a tailwind of at most 5 kt on
the sustained wind (FAA Order 8400.9; true alignments from NASR); calm or variable wind leaves every runway feasible.
Within a flow and category the rate is the usage-weighted mean (usage times the category's share, profile pp. 21-23)
of the feasible configurations' facility arrival rates listed for that category: north 61 visual (4L,4R|4L,4R,9) and 32
marginal and instrument (4R|4L,4R,9); south 48 (22L,27|22R, visual and marginal) and 32 (22L|22R, visual alternate and
instrument); crosswind 44 visual (27,32|33L). A category no feasible configuration lists takes 32, the single-runway
instrument rate, and the flow's rates are kept visual >= marginal >= instrument. With no feasible flow the one with the
least wind excess flies.

The reduction is the largest of f_storm x storm intensity (weather.Bin.storm), f_vicinity for a thunderstorm or CB
nearby, and f_frozen for frozen precipitation; the hourly rate times one less the reduction gives a quarter of itself
to each bin, and bins are integers by cumulative rounding from the first bin, so a day's landings match its rates.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import sources
from .weather import Bin, Obs

VISUAL, MARGINAL, INSTRUMENT = "visual", "marginal", "instrument"
CATEGORIES = (VISUAL, MARGINAL, INSTRUMENT)
VISUAL_AT = {"JFK": (2000, 4.0), "BOS": (3000, 3.0)}
INSTRUMENT_BELOW = (1000, 3.0)
JFK_VISUAL = ((32, 52), (52, 38))
JFK_FLOOR = {MARGINAL: (38, 48), INSTRUMENT: (36, 48)}
SLOPE = -0.7
MAX_DEPARTURES = 52
RUNWAYS = {"JFK": {"04": 31, "13": 121, "22": 211, "31": 301}, "BOS": {"04": 20, "22": 200, "27": 257, "32": 305}}
"""True alignment of each runway direction's arrival end, NASR APT_RWY_END (01 Oct 2026 cycle)."""
FALLBACK = 32
FLOWS = ("north", "south", "crosswind")


@dataclass(frozen=True)
class Config:
    name: str
    flow: str
    arrivals: tuple[str, ...]
    rates: Mapping[str, tuple[float, float]]
    """category -> (facility arrival rate, usage weight)"""


BOS = (Config("4L,4R|4L,4R,9", "north", ("04",), {VISUAL: (61, 14 * 1.00)}),
       Config("4R|4L,4R,9", "north", ("04",), {MARGINAL: (32, 1.0), INSTRUMENT: (32, 1.0)}),
       Config("22L,27|22R", "south", ("22", "27"), {VISUAL: (48, 6 * 0.96), MARGINAL: (48, 6 * 0.04)}),
       Config("22L|22R", "south", ("22",), {VISUAL: (32, 12 * 0.85), INSTRUMENT: (32, 12 * 0.07)}),
       Config("27,32|33L", "crosswind", ("27", "32"), {VISUAL: (44, 10 * 0.99)}))
"""BOS profile 2019 pp. 21-23: facility-reported rates per flow and category, each configuration's share of hours
times its share of them in the category."""


@dataclass(frozen=True)
class Reductions:
    storm: float = 0.0
    vicinity: float = 0.0
    frozen: float = 0.0

    def of(self, b: Bin) -> float:
        return max(self.storm * b.storm, self.vicinity * b.obs.vicinity, self.frozen * b.obs.frozen)


def category(hub: str, ceiling: float | None, vis: float | None) -> str:
    c = math.inf if ceiling is None else ceiling
    v = math.inf if vis is None else vis
    if c < INSTRUMENT_BELOW[0] or v < INSTRUMENT_BELOW[1]:
        return INSTRUMENT
    return VISUAL if c >= VISUAL_AT[hub][0] and v >= VISUAL_AT[hub][1] else MARGINAL


def components(obs: Obs, heading: float) -> tuple[float, float]:
    """(headwind, crosswind) on a runway of true `heading`; calm or variable wind is zero."""
    if obs.wind_dir is None or not obs.wind_kt:
        return 0.0, 0.0
    a = math.radians(obs.wind_dir - heading)
    return obs.wind_kt * math.cos(a), abs(obs.wind_kt * math.sin(a))


def excess(obs: Obs, hub: str, runways: Sequence[str]) -> float:
    """How far the wind is outside the limits on the worst of `runways`, in knots (0 when feasible)."""
    worst = 0.0
    for r in runways:
        head, cross = components(obs, RUNWAYS[hub][r])
        worst = max(worst, cross - sources.CROSSWIND_KT, -head - sources.TAILWIND_KT)
    return worst


def jfk_rates(departures: float) -> dict[str, float]:
    d = min(departures, MAX_DEPARTURES)
    (d1, a1), (d2, a2) = JFK_VISUAL
    out = {VISUAL: a1 if d <= d1 else a2 if d >= d2 else a1 + SLOPE * (d - d1)}
    for cat, (dk, ak) in JFK_FLOOR.items():
        out[cat] = ak if d <= dk else ak + SLOPE * (d - dk)
    return monotone(out)


def monotone(r: Mapping[str, float]) -> dict[str, float]:
    m = min(r[MARGINAL], r[VISUAL])
    return {VISUAL: r[VISUAL], MARGINAL: m, INSTRUMENT: min(r[INSTRUMENT], m)}


def bos_rates(obs: Obs) -> tuple[str, dict[str, float]]:
    """The flow BOS flies under `obs` and its rate per category."""
    feasible = {f: [c for c in BOS if c.flow == f and excess(obs, "BOS", c.arrivals) <= 0] for f in FLOWS}
    flow = next((f for f in FLOWS if feasible[f]), None)
    if flow is None:
        flow = min(FLOWS, key=lambda f: (min(excess(obs, "BOS", c.arrivals) for c in BOS if c.flow == f),
                                         FLOWS.index(f)))
        feasible[flow] = [c for c in BOS if c.flow == flow]
    out = {}
    for cat in CATEGORIES:
        listed = [c.rates[cat] for c in feasible[flow] if cat in c.rates and c.rates[cat][1] > 0]
        out[cat] = sum(r * w for r, w in listed) / sum(w for _, w in listed) if listed else FALLBACK
    return flow, monotone(out)


def jfk_label(obs: Obs) -> str:
    """The arrival direction the wind favours at JFK: the most headwind among the wind-feasible ones."""
    ok = [(components(obs, h)[0], r) for r, h in RUNWAYS["JFK"].items() if excess(obs, "JFK", (r,)) <= 0]
    return f"arrivals {max(ok)[1]}" if ok else "no feasible direction"


def rate(hub: str, obs: Obs, departures: float) -> tuple[float, str, str]:
    """The hourly arrival rate before reductions, the category and the configuration or flow."""
    cat = category(hub, obs.ceiling_ft, obs.vis_sm)
    if hub == "JFK":
        return jfk_rates(departures)[cat], cat, jfk_label(obs)
    flow, rates = bos_rates(obs)
    return rates[cat], cat, flow


def hourly(hub: str, b: Bin, departures: float, reductions: Reductions) -> float:
    """The bin's arrival rate per hour after its reduction."""
    return rate(hub, b.obs, departures)[0] * (1 - reductions.of(b))


def bins(hub: str, timeline: Sequence[Bin], departures: Sequence[float], reductions: Reductions) -> list[int]:
    """Integer landings per bin by cumulative rounding; `departures` per hour, the last one held past its end."""
    out, total = [], 0.0
    for k, b in enumerate(timeline):
        h = min(k * sources.BIN // 60, len(departures) - 1)
        before = math.floor(total + 0.5)
        total += hourly(hub, b, departures[h], reductions) * sources.BIN / 60
        out.append(math.floor(total + 0.5) - before)
    return out
