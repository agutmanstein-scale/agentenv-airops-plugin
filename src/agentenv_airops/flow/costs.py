"""What a minute costs in the Command Center model, in passenger-minutes, at the FAA's rates for every aircraft class.

The FAA's "Economic Values for FAA Investment and Regulatory Decisions", Section 4, Table 4-7 (YE June 2023, Part 121
passenger carriers, cost per block hour) gives each class's fuel and oil, maintenance, crew, fixed and total cost;
programs.cost.per_minute takes them to 2025 passenger-minutes per aircraft-minute. A minute held at the gate costs
crew and fixed (`ground`); a minute held in the air costs the total (`air`). Passengers cost their minutes of arrival
delay against the schedule. A diversion costs its held minutes and DIVERSION_BLOCK more at the total rate, and
DIVERSION_PAX minutes per passenger in place of an arrival delay, since it never lands.

A flight's class comes from T-100 by carrier, route and month: the aircraft type with the most departures performed,
its model through B-43's type codes (the first three digits of B-43's code are T-100's), and the model's class by a
pinned rule (body, maximum takeoff weight, seats); regional jets and turboprops split by the route's seats per
departure.
"""

import csv
import functools
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..build import fleet
from ..programs.cost import per_minute

TABLE_4_7 = {
    "widebody 580k lb or more": (7248, 2041, 2469, 1343, 13100),
    "widebody under 580k lb": (5376, 1523, 2271, 1024, 10195),
    "narrowbody 165k lb or more": (2663, 954, 1378, 656, 5650),
    "narrowbody under 165k lb": (2322, 1004, 1336, 587, 5248),
    "regional jet 61-99 seats": (1480, 667, 904, 279, 3329),
    "regional jet under 61 seats": (1102, 862, 902, 392, 3258),
    "turboprop over 60 seats": (893, 1339, 1044, 552, 3828),
    "turboprop 20-60 seats": (700, 1033, 719, 814, 3266),
    "turboprop under 20 seats": (639, 866, 539, 351, 2395),
    "piston": (346, 427, 207, 516, 1496)}
"""Dollars per block hour: fuel and oil, maintenance, crew, total fixed, total (Table 4-7, YE June 2023)."""
CLASSES = tuple(TABLE_4_7)
DIVERSION_PAX = 180
DIVERSION_BLOCK = 120
B43_YEARS = (2021, 2022, 2023, 2024, 2025)


@dataclass(frozen=True)
class Rates:
    ground: int
    air: int


RATES = {c: Rates(per_minute(v[2] + v[3]), per_minute(v[4])) for c, v in TABLE_4_7.items()}


def classify(model: str, seats: float) -> str | None:
    """A model's Table 4-7 class, by its B-43 name and the route's seats per departure; None when unknown."""
    m = model.upper().replace(" ", "")
    if re.search(r"777|747|A35|35\d-|A34|34\d-|A38|MD-?11", m):
        return "widebody 580k lb or more"
    if re.search(r"767|787|A33|33\d-|A30\d|A310|DC-?10|L-?1011", m):
        return "widebody under 580k lb"
    if re.search(r"(ERJ|EMB)-?19\d|E19\d|BD-?500|A22\d|717|MD-?[89]\d|A31[89]|31[89]-|737-[3-7]", m):
        return "narrowbody under 165k lb"
    if re.search(r"757|A32\d|32\d-|737", m):
        return "narrowbody 165k lb or more"
    if re.search(r"CL-?600|CRJ|ERJ|EMB-?1[3-7]\d|E1[3-7]\d", m):
        return "regional jet 61-99 seats" if seats >= 61 else "regional jet under 61 seats"
    if re.search(r"ATR|DHC|DASH|SAAB|SF-?340|340|EMB-?120|1900|B190|208|PC-?12|BEECH|KINGAIR|JETSTREAM|J31", m):
        return ("turboprop over 60 seats" if seats > 60 else "turboprop 20-60 seats" if seats >= 20
                else "turboprop under 20 seats")
    if re.search(r"PA-?\d|BN-?2|^40[24]|^310|^414|^421|P2012", m):
        return "piston"
    return None


@functools.cache
def models(inputs: Path) -> dict[str, str]:
    """T-100's aircraft type code -> the B-43 model most tails of that code carry, over B43_YEARS."""
    by: dict[str, Counter] = {}
    for year in B43_YEARS:
        text = fleet.b43_path(inputs, year).read_text(encoding="utf-8-sig")
        for r in csv.DictReader(io.StringIO(text)):
            code = r["AIRCRAFT_TYPE"].strip()
            by.setdefault(code[:3], Counter())[r["MODEL"].strip()] += 1
    return {code: c.most_common(1)[0][0] for code, c in by.items()}


@dataclass(frozen=True)
class Aircraft:
    cls: str
    seats: float
    pax: float
    source: str


def rank(cls: str) -> int:
    return sorted(CLASSES, key=lambda c: RATES[c].air).index(cls)


@functools.lru_cache(maxsize=16)
def route_aircraft(inputs: Path, month: str, dest: str) -> tuple[dict[tuple[str, str], Aircraft], dict[str, Aircraft]]:
    """(carrier, origin) -> Aircraft for scheduled passenger service into `dest` in the month, and carrier -> its
    median Aircraft over all its routes of the month (departure-weighted), the fallback."""
    year, m = (int(v) for v in month.split("-"))
    rows = [r for r in csv.DictReader(io.StringIO(fleet.t100_path(inputs, month).read_text(encoding="utf-8-sig")))
            if r["CLASS"] == "F" and (int(r["YEAR"]), int(r["MONTH"])) == (year, m)
            and float(r["DEPARTURES_PERFORMED"]) > 0]
    names = models(inputs)
    routes: dict[tuple, list] = {}
    for r in rows:
        routes.setdefault((r["UNIQUE_CARRIER"], r["ORIGIN"], r["DEST"]), []).append(r)
    found: dict[tuple, Aircraft] = {}
    for key, rs in routes.items():
        n = sum(float(r["DEPARTURES_PERFORMED"]) for r in rs)
        seats = sum(float(r["SEATS"]) for r in rs) / n
        pax = sum(float(r["PASSENGERS"]) for r in rs) / n
        top = max(rs, key=lambda r: (float(r["DEPARTURES_PERFORMED"]), r["AIRCRAFT_TYPE"]))
        own = float(top["SEATS"]) / float(top["DEPARTURES_PERFORMED"])
        cls = classify(names.get(top["AIRCRAFT_TYPE"].strip(), ""), own)
        if cls:
            found[key] = (Aircraft(cls, seats, pax, "T-100 route-month"), n)
    routes_in = {(c, o): a for (c, o, d), (a, _) in found.items() if d == dest}
    carriers = {}
    for carrier in sorted({c for c, _, _ in found}):
        mine = sorted(((a, n) for (c, _, _), (a, n) in found.items() if c == carrier), key=lambda x: rank(x[0].cls))
        cls = weighted_median([(rank(a.cls), n) for a, n in mine])
        seats = weighted_median(sorted((a.seats, n) for a, n in mine))
        load = sum(a.pax * n for a, n in mine) / sum(a.seats * n for a, n in mine)
        carriers[carrier] = Aircraft(sorted(CLASSES, key=lambda c: RATES[c].air)[int(cls)], seats, seats * load,
                                     "T-100 carrier median")
    return routes_in, carriers


def weighted_median(pairs: list[tuple[float, float]]) -> float:
    """The value at half the total weight of (value, weight) pairs sorted by value."""
    pairs = sorted(pairs)
    half, acc = sum(w for _, w in pairs) / 2, 0.0
    for v, w in pairs:
        acc += w
        if acc >= half:
            return v
    raise ValueError("no weight")
