"""The 3D viewer's traffic at a hub over an episode (web/stage): every carrier's movements there on their real BTS
times, typed from the FAA registry, JetBlue's aircraft the task leaves out among them, and the task's aircraft on the
plan the run flew; each visit parked at a stand, the METARs decoded with a plain-English line, the runways in use and
the moments worth filming. Stands, taxi routes and runways in use are display-only inferences. ``traffic`` is the
run-independent part scripts/prebake_traffic.py bakes under cache/traffic/; ``sidecar`` lays a run over it. Standard
library only, so the viewer runs without the build extra."""

import csv
import io
import math
import re
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import date as Date
from datetime import timedelta
from pathlib import Path

from .core.model import Task

CARRIER = "B6"
HOME = {"BOS": "C", "JFK": "T5"}
TERMINALS = {
    "BOS": {"DL": "A", "9E": "A", "OO": "A", "AA": "B", "MQ": "B", "OH": "B", "UA": "B", "AS": "B", "AC": "B",
            "WN": "B", "B6": "C", "NK": "E", "F9": "E", "G4": "E", "HA": "E", "SY": "E"},
    "JFK": {"DL": "T4", "9E": "T4", "OO": "T4", "HA": "T4", "AA": "T8", "MQ": "T8", "OH": "T8", "AS": "T7", "UA": "T7",
            "F9": "T7", "B6": "T5"},
}
"""Each BTS carrier's terminal over 2023-2025; display only."""
REPUBLIC = ((4000, "UA"), (5000, "AA"), (10000, "DL"))
"""Republic (YX) flies for United, American and Delta: the partner by flight-number block, upper bound exclusive."""
REGIONAL = {"YX", "MQ", "OO", "9E", "OH", "QX", "G7", "C5", "ZW", "PT"}
TYPES = [
    (r"^ERJ ?170-1|^EMB-170", "regional", 29.9, 26.0), (r"^ERJ ?170-2|^EMB-175", "regional", 31.7, 26.0),
    (r"^ERJ ?190|^EMB-190", "e190", 36.2, 28.7), (r"^EMB-145|^ERJ ?145", "regional_t", 29.9, 20.0),
    (r"^CL-600-2B19", "regional_t", 26.8, 21.2), (r"^CL-600-2C1", "regional_t", 32.3, 23.2),
    (r"^CL-600-2D", "regional_t", 36.2, 24.9), (r"^BD-500-1A10|^A220-1", "a220", 35.0, 35.1),
    (r"^BD-500-1A11|^A220-3", "a220", 38.7, 35.1), (r"^A319", "a320", 33.8, 35.8), (r"^A320", "a320", 37.6, 35.8),
    (r"^A321", "a321", 44.5, 35.8), (r"^737-[78]$", "narrowbody", 39.5, 35.9), (r"^737-9$", "narrowbody", 42.2, 35.9),
    (r"^737-7", "narrowbody", 33.6, 35.8), (r"^737-8", "narrowbody", 39.5, 35.8), (r"^737-9", "narrowbody", 42.1, 35.8),
    (r"^757-2", "narrowbody", 47.3, 38.1), (r"^757-3", "narrowbody", 54.4, 38.1), (r"^767-3", "widebody", 54.9, 47.6),
    (r"^767-4", "widebody", 61.4, 51.9), (r"^777-2", "widebody", 63.7, 60.9), (r"^777-3", "widebody", 73.9, 64.8),
    (r"^787-8", "widebody", 56.7, 60.1), (r"^787-9", "widebody", 62.8, 60.1), (r"^787-10", "widebody", 68.3, 60.1),
    (r"^A330-2", "widebody", 58.8, 60.3), (r"^A330-[39]", "widebody", 63.7, 60.3), (r"^A350-9", "widebody", 66.8, 64.8),
]
FALLBACK, FALLBACK_REGIONAL = ("narrowbody", 37.6, 35.8), ("regional", 31.7, 26.0)
B6_TYPES = {"A220": ("a220", 38.7, 35.1), "E190": ("e190", 36.2, 28.7), "A320": ("a320", 37.6, 35.8),
            "A321": ("a321", 44.5, 35.8)}
FLOWS = {"BOS": (("33L", "27"), ("27", "33L"), ("22L", "22R"), ("4R", "9"), ("15R", "15R")),
         "JFK": (("31R", "31L"), ("13L", "13R"), ("4R", "4L"), ("22L", "22R"))}
"""(arrivals, departures) of each hub's usual flows, the first the default in calm air."""
TAXI = {"dep": 15.0, "arr": 7.0}
REAL_TAXI, MARGIN, HORIZON, GAP = 10, 20, 120, 45
PAD_IN, PAD_OUT, NEAR, WIDE_ROOM = 5, 10, 150.0, 55.0
CANCEL_WAIT, STRAY, LONG_RUNWAY, TAILWIND, SWING, CALM, CHANGE = 30, 60, 2000.0, 5, 10, 4, 60
STOP_GAP, QUEUE, DAY_HOURS, SPREAD, MAX_FEATURED, STORY = 30, 3, (360, 1380), 20, 12, 180
KT_MPH = 1.15078
COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()
PHENOMENA = {"DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains", "IC": "ice crystals",
             "PL": "ice pellets", "GR": "hail", "GS": "small hail", "UP": "precipitation", "BR": "mist", "FG": "fog",
             "FU": "smoke", "VA": "volcanic ash", "DU": "dust", "SA": "sand", "HZ": "haze", "PY": "spray",
             "PO": "dust whirls", "SQ": "squalls", "FC": "funnel cloud", "SS": "sandstorm", "DS": "duststorm"}
DRY = {"BR", "FG", "FU", "VA", "DU", "SA", "HZ", "PY", "PO", "SQ", "FC", "SS", "DS"}
DESCRIBED = {"MI": "shallow {}", "PR": "partial {}", "BC": "patches of {}", "DR": "drifting {}", "BL": "blowing {}",
             "FZ": "freezing {}"}
SKY = {None: "Clear", "FEW": "Few clouds", "SCT": "Partly cloudy", "BKN": "Mostly cloudy", "OVC": "Overcast",
       "VV": "Overcast"}
WX = re.compile(r"(-|\+|VC)?((?:MI|PR|BC|DR|BL|SH|TS|FZ)*)((?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO"
                r"|SQ|FC|SS|DS)*)")
WIND = re.compile(r"(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT")
VIS = re.compile(r"(M|P)?(?:(\d+)/(\d+)|(\d+))SM")
CLOUD = re.compile(r"(FEW|SCT|BKN|OVC|VV)(\d{3})")
TEMP = re.compile(r"(M?\d{2})/(M?\d{2})?")
COLUMNS = ("FlightDate", "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline", "Origin", "Dest",
           "CRSDepTime", "CRSArrTime", "CRSElapsedTime", "DepDelay", "ArrDelay", "Cancelled", "Diverted",
           "DivReachedDest", "DivArrDelay")

Leg = dict
Visit = dict


def traffic(task: Task, inputs: Path) -> dict:
    """What an episode of ``task`` shows that no run changes: the task's flights at the hub and the legs it froze,
    every other aircraft's visits there from the first watch to the latest any plan can reach, JetBlue's hourly taxi
    times, the METARs and the ground stops. ``inputs`` is cache/inputs (BTS on-time zips and the FAA registry)."""
    hub, tails = task.hub, {t.tail for t in task.tails}
    flights = [f for f in task.flights if hub in (f.orig, f.dest)]
    horizon = [task.start, max([f.sched_arr if f.dest == hub else f.sched_dep for f in flights] + [task.start])
               + task.dmax + HORIZON]
    rows, cities = movements(inputs, hub, task.date)
    ours = [r for r in rows if r["carrier"] == CARRIER and r["tail"] in tails]
    rest = [r for r in rows if not (r["carrier"] == CARRIER and r["tail"] in tails)]
    models = registry_models(inputs / "faa" / "ReleasableAircraft.zip", {r["tail"] for r in rest if r["tail"]})
    taxi = medians(rows)
    by_key: dict[str, list[Leg]] = {}
    numbers: dict[str, tuple[str, int]] = {}
    for r in sorted(rest, key=lambda r: r["at"]):
        key = r["tail"] or f"{r['carrier']}{r['number']}"
        by_key.setdefault(key, []).append(real_leg(r, cities, taxi))
        numbers.setdefault(key, (r["carrier"], r["number"]))
    aircraft = []
    for key, legs in sorted(by_key.items()):
        visits = [v for v in pair(legs) if overlaps(v, horizon)]
        if not visits:
            continue
        carrier, number = numbers[key]
        model = models.get(key, "")
        cls, length, span = classify(model, carrier)
        aircraft.append({"key": key, "tail": key if key != f"{carrier}{number}" else None, "carrier": carrier,
                         "jetblue": carrier == CARRIER, "task": False, "class": cls, "type": model or None,
                         "length": length, "span": span, "terminal": terminal_of(hub, carrier, number),
                         "visits": visits})
    real: dict[str, list[dict]] = {}
    for r in sorted(ours, key=lambda r: r["at"]):
        real.setdefault(f"{r['flight']} {r['kind']}", []).append({k: r[k] for k in ("at", "taxi", "sched")})
    return {"task_id": task.task_id, "hub": hub, "date": task.date, "tz": task.tz, "horizon": horizon,
            "flights": [{"id": f.id, "flight": f.flight, "orig": f.orig, "dest": f.dest, "sched_dep": f.sched_dep,
                         "sched_arr": f.sched_arr, "family": f.family, "tail": f.actual.tail} for f in flights],
            "frozen": [dict(f) for f in task.frozen if hub in (f["orig"], f["dest"])],
            "tails": [{"tail": t.tail, "family": t.family, "type": t.type, "pos": t.pos} for t in task.tails],
            "cities": {c: cities.get(c, c) for c in sorted({c for f in flights for c in (f.orig, f.dest)} | {*cities})},
            "real": real, "taxi": taxi, "aircraft": aircraft,
            "weather": [metar(n.at, n.text) for n in sorted(task.news, key=lambda n: n.at) if n.kind == "metar"],
            "stops": stops(task)}


def sidecar(task: Mapping, executed: Sequence[Mapping], traffic: Mapping, stage: Mapping,
            crew: Mapping[int, list] | None = None) -> dict:
    """The traffic sidecar of web/stage for a run: ``task`` is the episode's task, ``executed`` its executed rows (the
    plan flown on the truth), ``traffic`` what ``traffic`` baked for the task and ``stage`` the hub's stage; ``crew``,
    by flight id, the crew status its departure tag shows over time (occ.episode.crew_tags). The span runs from the
    first watch to the run's last movement at the hub; every visit in it gets a stand."""
    if traffic["task_id"] != task["task_id"] or stage["hub"] != task["hub"]:
        raise ValueError(f"traffic for {traffic['task_id']} and the {stage['hub']} stage don't match {task['task_id']}")
    ours, cancelled = task_aircraft(task, executed, traffic, crew or {})
    moves = [t for a in ours for v in a["visits"] for t in times(v)] + [cancelled_at(c[1]) for c in cancelled]
    span = [task["start"], round(max(moves + [task["start"]]) + MARGIN)]
    host(ours, cancelled)
    aircraft = ours + [{k: v for k, v in a.items() if k != "visits"} | {"visits": [clip(v, span) for v in a["visits"]
                                                                                    if overlaps(v, span)]}
                       for a in traffic["aircraft"]]
    assign(aircraft, stage, span)
    aircraft = [{k: v for k, v in a.items() if k not in ("terminal", "family")}
                | {"visits": [v for v in a["visits"] if v.get("stand")]} for a in aircraft]
    aircraft = [a for a in aircraft if a["visits"]]
    weather = in_force(traffic["weather"], span)
    return {"hub": task["hub"], "date": task["date"], "tz": task["tz"], "span": span, "weather": weather,
            "runways": runways(weather, stage, span), "aircraft": aircraft,
            "featured": featured(aircraft, traffic["stops"], span, task["window"])}


def task_aircraft(task: Mapping, executed: Sequence[Mapping], traffic: Mapping,
                  crew: Mapping[int, list] | None = None) -> tuple[list[dict], list]:
    """The task's tails at the hub on the run's flown legs and the frozen ones, tagged, each departure with its crew
    status from ``crew``; and its cancelled legs there, each with the flight's family and planned tail, for ``host``
    to hang on a parked aircraft."""
    hub, cities, start = task["hub"], traffic["cities"], task["start"]
    flights = {f["id"]: f for f in traffic["flights"]}
    legs: dict[str, list[Leg]] = {}
    cancelled = []
    for f in traffic["frozen"]:
        for kind, other, at in (("dep", f["dest"], f["dep"]), ("arr", f["orig"], f["arr"])):
            if (f["orig"] if kind == "dep" else f["dest"]) == hub:
                real = nearest(traffic, f["flight"], kind, at) or {}
                legs.setdefault(f["tail"], []).append(
                    task_leg(f["flight"], kind, other, cities, real.get("sched"), at, None, traffic))
    for x in executed:
        f = flights.get(x["id"])
        if f is None:
            continue
        for kind in ("dep", "arr"):
            if (f["orig"] if kind == "dep" else f["dest"]) != hub:
                continue
            other, sched = (f["dest"], f["sched_dep"]) if kind == "dep" else (f["orig"], f["sched_arr"])
            status = {"crew": crew[x["id"]]} if kind == "dep" and crew and x["id"] in crew else {}
            if x["status"] != "flown":
                leg = task_leg(f["flight"], kind, other, cities, sched, None, None, traffic)
                cancelled.append((f, leg | {"decided": num(x.get("decided"))} | status))
                continue
            hold = x.get("not_before") if kind == "dep" and x.get("held") else None
            at = x["dep"] if kind == "dep" else x["arr"]
            legs.setdefault(x["tail"], []).append(
                task_leg(f["flight"], kind, other, cities, sched, at, hold, traffic) | status)
    out = []
    for t in traffic["tails"]:
        mine = sorted(legs.get(t["tail"], []), key=leg_time)
        visits = pair(mine) or ([{"from": None, "to": None, "arr": None, "dep": None}] if t["pos"] == hub else [])
        visits = [clip(v, [start, math.inf]) for v in visits if overlaps(v, [start, math.inf])]
        if not visits:
            continue
        cls, length, span = b6_type(t)
        out.append({"key": t["tail"], "tail": t["tail"], "carrier": CARRIER, "jetblue": True, "task": True,
                    "class": cls, "type": t["type"], "length": length, "span": span, "terminal": HOME[hub],
                    "family": t["family"], "visits": visits})
    return out, sorted(cancelled, key=lambda c: (cancelled_at(c[1]), c[1]["flight"]))


def task_leg(flight: str, kind: str, other: str, cities: Mapping, sched: float | None, at: float | None,
             hold: float | None, traffic: Mapping) -> Leg:
    leg = blank(flight, kind, other, cities, "flown" if at is not None else "cancelled", sched)
    if at is not None:
        real = nearest(traffic, flight, kind, at)
        taxi = real["taxi"] if real and real["taxi"] is not None and abs(real["at"] - at) <= REAL_TAXI else None
        timed(leg, at, taxi if taxi is not None else median_at(traffic["taxi"], kind, at))
    leg["tag"], leg["tone"] = tag(leg, hold)
    return leg


def nearest(traffic: Mapping, flight: str, kind: str, at: float) -> dict | None:
    """The flight's real movement at the hub nearest ``at``."""
    return min(traffic["real"].get(f"{flight} {kind}", []), key=lambda r: abs(r["at"] - at), default=None)


def blank(flight: str, kind: str, other: str, cities: Mapping, status: str, sched: float | None) -> Leg:
    return {"flight": flight, "kind": kind, "other": other, "city": cities.get(other, other), "status": status,
            "sched": num(sched), "out": None, "off": None, "on": None, "in": None, "tag": None, "tone": None}


def timed(leg: Leg, at: float, taxi: float) -> Leg:
    if leg["kind"] == "dep":
        leg["out"], leg["off"] = num(at), num(at + taxi)
    else:
        leg["on"], leg["in"] = num(at - taxi), num(at)
    return leg


def tag(leg: Leg, hold: float | None) -> tuple[str, str]:
    head = f"{leg['flight']} → {leg['city']}" if leg["kind"] == "dep" else f"{leg['flight']} from {leg['city']}"
    if leg["status"] == "cancelled":
        return f"{head} · cancelled", "cancel"
    if hold is not None:
        return f"{head} · held until {hhmm(hold)}", "hold"
    if leg["sched"] is None:
        return head, "ok"
    late = round(at_gate(leg) - leg["sched"])
    return (f"{head} · on time", "ok") if late <= 0 else (f"{head} · delayed {duration(late)}", "delay")


def host(aircraft: list[dict], cancelled: list) -> None:
    """Each cancelled leg on a task aircraft parked at the hub when it is cancelled (cancelled_at), else on the one
    parked nearest then within STRAY minutes: the flight's planned tail first, then one of its family with no pushback
    within CANCEL_WAIT minutes, then any, the longest wait first. The leg takes the visit's free end, else the visit
    splits there into two at the same stand."""
    for f, leg in cancelled:
        t = cancelled_at(leg)
        near = [(a, i, v, d) for a in aircraft for i, v in enumerate(a["visits"]) if (d := apart(v, t)) <= STRAY]
        if not near:
            continue

        def rank(c, f=f, t=t):
            a, _, v, d = c
            wait = math.inf if v["to"] is None else v["to"] - t
            busy = any(g and g["status"] == "cancelled" for w in a["visits"] for g in (w["arr"], w["dep"]))
            return (d == 0, a["tail"] == f["tail"], a["family"] == f["family"] and wait >= CANCEL_WAIT, not busy,
                    -d, min(wait, 240), a["key"])

        a, i, v, _ = max(near, key=rank)
        slot = leg["kind"]
        if v[slot] is None:
            v[slot] = leg
            continue
        lo = -math.inf if v["from"] is None else v["from"]
        hi = math.inf if v["to"] is None else v["to"]
        cut = min(max(t, lo + 1), hi - 1)
        if not lo < cut < hi:
            continue
        first, second = dict(v), dict(v)
        first["to"], second["from"] = cut, cut
        if slot == "dep":
            first["dep"], second["arr"] = leg, None
        else:
            first["dep"], second["arr"] = None, leg
        a["visits"][i:i + 1] = [first, second]


def cancelled_at(leg: Leg) -> float:
    """When a cancelled leg shows as cancelled: its scheduled time, or later when the run's plan or the env cancelled
    it after that (``decided``, the env's 6 hours after schedule for a flight that couldn't leave)."""
    return max(leg["sched"], leg.get("decided") or leg["sched"])


def apart(v: Visit, t: float) -> float:
    """Minutes from ``t`` to the visit, 0 inside it."""
    lo = -math.inf if v["from"] is None else v["from"]
    hi = math.inf if v["to"] is None else v["to"]
    return max(lo - t, t - hi, 0)


def pair(legs: Sequence[Leg]) -> list[Visit]:
    """An aircraft's ground visits at the hub from its legs there in time order: each arrival with the departure after
    it; a first departure with none before it was there from the start, an arrival with none after it stays. Where the
    record skips a movement, the aircraft leaves its stand GAP minutes before landing again, or turns up at one
    GAP minutes before pushing back."""
    out, pending, prev = [], None, None
    for g in legs:
        if g["kind"] == "arr":
            if pending is not None:
                out.append({"from": pending["in"], "to": max(pending["in"], g["on"] - GAP), "arr": pending,
                            "dep": None})
            pending = g
        elif pending is not None or prev is None:
            out.append({"from": pending["in"] if pending else None, "to": g["out"], "arr": pending, "dep": g})
            pending = None
        else:
            out.append({"from": max(prev["off"], g["out"] - GAP), "to": g["out"], "arr": None, "dep": g})
        prev = g
    if pending is not None:
        out.append({"from": pending["in"], "to": None, "arr": pending, "dep": None})
    return out


def overlaps(v: Visit, span: Sequence[float]) -> bool:
    a = -math.inf if v["from"] is None else v["from"]
    b = math.inf if v["to"] is None else v["to"]
    return a <= span[1] and b >= span[0]


def clip(v: Visit, span: Sequence[float]) -> Visit:
    """The visit within ``span``: an arrival before it and a departure after it are left out, its ends open there."""
    v = dict(v)
    if v["from"] is not None and v["from"] < span[0]:
        v["from"], v["arr"] = None, None
    if v["to"] is not None and v["to"] > span[1]:
        v["to"], v["dep"] = None, None
    return v


def times(v: Visit) -> list[float]:
    return [t for g in (v["arr"], v["dep"]) if g for t in (g["in"], g["off"]) if t is not None]


def assign(aircraft: list[dict], stage: Mapping, span: Sequence[float]) -> list[str]:
    """Every visit a stand, greedily in order of arrival: the task's aircraft first, then JetBlue's others, the
    widebodies, the rest. An aircraft takes a stand of its terminal, else the remote stand nearest it, else another
    terminal's, where its parked outline clears the buildings and every aircraft parked nearby at an overlapping time
    (PAD_IN before it and PAD_OUT after). Returns the keys of the visits no stand could take."""
    stands = {s["ref"]: s for s in stage["stands"] if s["ref"] in stage["taxi_graph"]["stand_nodes"]}
    geo = Geometry(stage, stands)
    by_terminal: dict[str, list[str]] = {}
    for ref, s in stands.items():
        by_terminal.setdefault(s["terminal"], []).append(ref)
    centre = {k: mean([stands[r]["xy"] for r in refs]) for k, refs in by_terminal.items()}
    order: dict[tuple[str, bool], list[str]] = {}
    held: dict[str, list[tuple[float, float, tuple, str]]] = {r: [] for r in stands}
    items = []
    for a in aircraft:
        rank = 0 if a["task"] else 1 if a["jetblue"] else 2 if a["class"] == "widebody" else 3
        for block in blocks(a["visits"]):
            lo = span[0] if block[0]["from"] is None else block[0]["from"]
            hi = span[1] if block[-1]["to"] is None else block[-1]["to"]
            items.append((rank, lo, a["key"], a, block, (lo - PAD_IN, hi + PAD_OUT)))
    unplaced = []
    for _, _, _, a, block, (lo, hi) in sorted(items, key=lambda i: i[:3]):
        dims = (a["length"], a["span"])
        wide = a["class"] == "widebody"
        home = a["terminal"] if a["terminal"] in by_terminal else None
        key = (home, wide)
        if key not in order:
            order[key] = candidates(stands, by_terminal, centre, geo.room, home, wide)
        for ref in order[key]:
            if geo.blocked(ref, dims) or any(lo < b and a0 < hi for a0, b, _, _ in held[ref]):
                continue
            if any(lo < b and a0 < hi and geo.clash(ref, dims, other, d)
                   for other in geo.near[ref] for a0, b, d, _ in held[other]):
                continue
            for v in block:
                v["stand"] = ref
            held[ref].append((lo, hi, dims, a["key"]))
            break
        else:
            unplaced.append(a["key"])
    for a in aircraft:
        a["visits"] = [{"stand": v.get("stand"), **{k: v[k] for k in ("from", "to", "arr", "dep")}}
                       for v in a["visits"]]
    return sorted(unplaced)


def blocks(visits: Sequence[Visit]) -> list[list[Visit]]:
    """The visits in runs the aircraft spends at one stand: a visit that starts as the one before it ends, with no
    movement between them (a cancelled leg split the stay)."""
    out: list[list[Visit]] = []
    for v in visits:
        last = out[-1][-1] if out else None
        if (last is not None and last["to"] is not None and v["from"] == last["to"]
                and not moved(last["dep"]) and not moved(v["arr"])):
            out[-1].append(v)
        else:
            out.append([v])
    return out


def moved(g: Leg | None) -> bool:
    return g is not None and g["status"] == "flown"


def candidates(stands: Mapping, by_terminal: Mapping, centre: Mapping, room: Mapping, home: str | None,
               wide: bool) -> list[str]:
    def fit(ref: str):
        return (-room[ref], natural(ref)) if wide else (room[ref] >= WIDE_ROOM, natural(ref))

    own = sorted(by_terminal.get(home, []), key=fit)
    c = centre.get(home) or mean([s["xy"] for s in stands.values()])
    near = sorted((r for r in stands if r not in own), key=lambda r: (stands[r]["terminal"] != "remote",
                                                                      math.dist(stands[r]["xy"], c), natural(r)))
    return own + near


class Geometry:
    """Parked outlines at the stands, each a few convex parts (fuselage, wings, tailplane): whether one meets the
    terminals and hangars, and whether two at neighbouring stands meet, both cached by stand and size."""

    def __init__(self, stage: Mapping, stands: Mapping):
        self.stands = stands
        rings = [t["outer"] for t in stage["terminals"]] + [h["outer"] for h in stage.get("hangars", [])]
        self.rings = [(r, box(r)) for r in rings]
        base = {r: re.sub(r"(\d)[A-Z]+$", r"\1", r) for r in stands}
        xy = {r: s["xy"] for r, s in stands.items()}
        self.room = {r: min((math.dist(xy[r], xy[o]) for o in stands if base[o] != base[r]), default=math.inf)
                     for r in stands}
        self.near = {r: [o for o in stands if o != r and math.dist(xy[r], xy[o]) < NEAR] for r in stands}
        self._parts: dict = {}
        self._blocked: dict = {}
        self._clash: dict = {}

    def parts(self, ref: str, dims: tuple) -> list[list[tuple[float, float]]]:
        if (ref, dims) not in self._parts:
            s = self.stands[ref]
            self._parts[ref, dims] = [placed(p, s["xy"], s["heading"]) for p in plan(*dims)]
        return self._parts[ref, dims]

    def blocked(self, ref: str, dims: tuple) -> bool:
        if (ref, dims) not in self._blocked:
            parts = self.parts(ref, dims)
            self._blocked[ref, dims] = any(boxes_meet(box(p), b) and polygon_meets(p, r)
                                           for p in parts for r, b in self.rings)
        return self._blocked[ref, dims]

    def clash(self, a: str, da: tuple, b: str, db: tuple) -> bool:
        k = (a, da, b, db)
        if k not in self._clash:
            self._clash[k] = self._clash[b, db, a, da] = any(convex_meet(p, q) for p in self.parts(a, da)
                                                             for q in self.parts(b, db))
        return self._clash[k]


def plan(length: float, span: float) -> list[list[tuple[float, float]]]:
    """The aircraft from above, nose wheel at the origin pointing +y: fuselage, the two wings and the tailplane, each
    convex."""
    L, S = length, span
    w = max(3.0, 0.11 * S)
    t = 0.18 * S
    fuselage = [(-w / 2, -0.88 * L), (w / 2, -0.88 * L), (w / 2, 0.12 * L), (-w / 2, 0.12 * L)]
    wing = [(w / 2, -0.48 * L), (S / 2, -0.58 * L), (S / 2, -0.50 * L), (w / 2, -0.22 * L)]
    tail = [(-w / 2, -0.78 * L), (-t, -0.88 * L), (-t, -0.92 * L), (t, -0.92 * L), (t, -0.88 * L), (w / 2, -0.78 * L)]
    return [fuselage, wing, [(-x, y) for x, y in reversed(wing)], tail]


def placed(part: list, xy: Sequence[float], heading: float) -> list[tuple[float, float]]:
    h = math.radians(heading)
    c, s = math.cos(h), math.sin(h)
    return [(xy[0] + x * c + y * s, xy[1] - x * s + y * c) for x, y in part]


def box(points: Sequence) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def boxes_meet(a: tuple, b: tuple) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def convex_meet(p: Sequence, q: Sequence) -> bool:
    """Separating-axis test of two convex polygons."""
    for poly in (p, q):
        for (x0, y0), (x1, y1) in zip(poly, [*poly[1:], poly[0]], strict=True):
            nx, ny = y0 - y1, x1 - x0
            a = [nx * x + ny * y for x, y in p]
            b = [nx * x + ny * y for x, y in q]
            if max(a) < min(b) or max(b) < min(a):
                return False
    return True


def polygon_meets(convex: Sequence, ring: Sequence) -> bool:
    """Whether a convex polygon meets a simple polygon: an edge crossing, or one inside the other."""
    edges = list(zip(ring, [*ring[1:], ring[0]], strict=True))
    for a, b in zip(convex, [*convex[1:], convex[0]], strict=True):
        if any(crosses(a, b, c, d) for c, d in edges):
            return True
    return inside(convex[0], ring) or convex_contains(convex, ring[0])


def crosses(a, b, c, d) -> bool:
    def side(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def inside(p: Sequence[float], ring: Sequence) -> bool:
    x, y, odd = p[0], p[1], False
    for (x0, y0), (x1, y1) in zip(ring, [*ring[1:], ring[0]], strict=True):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            odd = not odd
    return odd


def convex_contains(convex: Sequence, p: Sequence[float]) -> bool:
    signs = {(b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) > 0
             for a, b in zip(convex, [*convex[1:], convex[0]], strict=True)}
    return len(signs) == 1


def in_force(weather: Sequence[Mapping], span: Sequence[float]) -> list[dict]:
    """The METARs in force over the span: the last one issued before it opens and every one inside."""
    before = [w for w in weather if w["t"] <= span[0]][-1:]
    return [dict(w) for w in before + [w for w in weather if span[0] < w["t"] <= span[1]]] or [dict(w)
                                                                                                 for w in weather[:1]]


def runways(weather: Sequence[Mapping], stage: Mapping, span: Sequence[float]) -> list[dict]:
    """The flow in use through the span: at each METAR the hub's flow whose arrival end takes the most headwind of the
    long runways, changed only when the wind blows over TAILWIND knots down the one in use or the best takes SWING
    knots more headwind than it, and at most every CHANGE minutes."""
    ends = {e["id"]: e["heading"] for r in stage["runways"] for e in r["ends"]
            if math.dist(r["ends"][0]["xy"], r["ends"][1]["xy"]) >= LONG_RUNWAY}
    flows = [f for f in FLOWS.get(stage["hub"], ()) if f[0] in ends and f[1] in ends] or [
        (e, e) for e in sorted(ends)[:1]]

    def head(flow, w) -> float:
        return w["wind_kt"] * math.cos(math.radians(w["wind_dir"] - ends[flow[0]]))

    out: list[dict] = []
    flow = None
    for w in weather:
        t = max(span[0], w["t"])
        windy = w["wind_dir"] is not None and w["wind_kt"] >= CALM
        best = max(flows, key=lambda f: head(f, w)) if windy else None
        if flow is None:
            flow = best or flows[0]
        elif (best and best != flow and (head(flow, w) < -TAILWIND or head(best, w) - head(flow, w) > SWING)
              and t - out[-1]["t"] >= CHANGE):
            flow = best
        else:
            continue
        out.append({"t": round(t), "arrivals": flow[0], "departures": flow[1]})
    return out or [{"t": span[0], "arrivals": flows[0][0], "departures": flows[0][1]}]


def featured(aircraft: Sequence[Mapping], stop_periods: Sequence[Sequence[int]], span: Sequence[float],
             window: Sequence[int]) -> list[dict]:
    """Moments worth filming on the task's aircraft, best first, no two of one aircraft or within SPREAD minutes: the
    first JetBlue landing after a ground stop at the hub and the first takeoff after half an hour with none while
    QUEUE aircraft pushed back, a held flight finally pushing back, a cancelled flight's aircraft sitting at the gate,
    the longest delays; those in the task's window or the STORY minutes after it first."""
    found = []
    legs = [(a, g) for a in aircraft if a["task"] for v in a["visits"] for g in (v["arr"], v["dep"]) if g]
    landings = sorted((g["on"], a["key"], g["flight"]) for a, g in legs if g["kind"] == "arr" and g["on"] is not None)
    for _, end in stop_periods:
        after = [m for m in landings if end <= m[0] <= span[1]]
        if after and after[0][0] - end <= 120:
            found.append((300 + min(after[0][0] - end, 60), after[0][1], after[0][2], "first_after_stop", after[0][0]))
    deps = [g for a in aircraft for v in a["visits"] if (g := v["dep"]) and g["off"] is not None]
    offs = sorted(g["off"] for g in deps)
    ours = sorted((g["off"], a["key"], g["flight"]) for a, g in legs if g["kind"] == "dep" and g["off"] is not None)
    for prev, nxt in zip(offs, offs[1:], strict=False):
        queued = sum(prev < g["out"] < nxt for g in deps)
        if nxt - prev >= STOP_GAP and queued >= QUEUE and DAY_HOURS[0] <= prev <= DAY_HOURS[1]:
            mine = [m for m in ours if m[0] == nxt]
            if mine:
                found.append((300 + min(nxt - prev, 120), mine[0][1], mine[0][2], "first_after_stop", nxt))
    for a, g in legs:
        if g["tone"] == "hold":
            found.append((200 + min(g["out"] - g["sched"], 300) / 3, a["key"], g["flight"], "held", g["out"]))
        elif g["tone"] == "cancel" and g["kind"] == "dep":
            found.append((150, a["key"], g["flight"], "cancelled", cancelled_at(g)))
        elif g["tone"] == "delay" and (late := at_gate(g) - g["sched"]) >= 60:
            found.append((100 + min(late, 300) / 3, a["key"], g["flight"], "delayed",
                          g["out"] if g["kind"] == "dep" else g["on"]))
    out: list[dict] = []
    story = (window[0], window[1] + STORY)
    found = [(score + (STORY if story[0] <= t <= story[1] else 0), *rest, t) for score, *rest, t in found]
    for _, key, flight, reason, t in sorted(found, key=lambda f: (-f[0], f[4], f[1])):
        if span[0] <= t <= span[1] and all(o["key"] != key and abs(o["t"] - t) >= SPREAD for o in out):
            out.append({"key": key, "flight": flight, "reason": reason, "t": num(t)})
    return out[:MAX_FEATURED]


def movements(inputs: Path, hub: str, day: str) -> tuple[list[dict], dict[str, str]]:
    """The flown departures and arrivals at ``hub`` of flights dated ``day`` and the next, times in minutes from
    ``day``'s local midnight; and the city of each airport they name. An arrival's day comes from its scheduled block
    time, so no origin time zone is needed."""
    d0 = Date.fromisoformat(day)
    days = {(d0 + timedelta(k)).isoformat(): k for k in (0, 1)}
    out, cities = [], {}
    for month in sorted({d[:7] for d in days}):
        path = inputs / "bts" / f"ontime_{month.replace('-', '_')}.zip"
        if not path.is_file():
            if month == day[:7]:
                raise FileNotFoundError(path)
            continue
        for r in bts_rows(path, hub, days):
            for code, city in ((r["Origin"], r.get("OriginCityName")), (r["Dest"], r.get("DestCityName"))):
                if city:
                    cities.setdefault(code, town(city))
            if r["Cancelled"] != 0:
                continue
            k = days[r["FlightDate"]] * 1440
            base = {"carrier": r["Reporting_Airline"], "tail": r["Tail_Number"], "number": int(r[
                "Flight_Number_Reporting_Airline"]), "flight": f"{r['Reporting_Airline']} "
                                                              f"{r['Flight_Number_Reporting_Airline']}"}
            dep, arr = clock(r["CRSDepTime"]), clock(r["CRSArrTime"])
            if r["Origin"] == hub and r["DepDelay"] is not None:
                sched = k + dep
                out.append(base | {"kind": "dep", "other": r["Dest"], "sched": sched, "at": sched + r["DepDelay"],
                                   "taxi": r.get("TaxiOut")})
            reached = r["Diverted"] == 0 or r["DivReachedDest"] == 1
            delay = r["ArrDelay"] if r["Diverted"] == 0 else r["DivArrDelay"]
            if r["Dest"] == hub and reached and delay is not None and r["CRSElapsedTime"] is not None:
                sched = k + arr + 1440 * round((dep + r["CRSElapsedTime"] - arr) / 1440)
                out.append(base | {"kind": "arr", "other": r["Origin"], "sched": sched, "at": sched + delay,
                                   "taxi": r.get("TaxiIn")})
    return out, cities


def town(city: str) -> str:
    """BTS's "West Palm Beach/Palm Beach, FL" as "West Palm Beach"; "Dallas/Fort Worth, TX" as "Dallas/Fort Worth"."""
    name = city.rsplit(",", 1)[0].strip()
    first, _, second = name.partition("/")
    return first if second and second in first else name


def bts_rows(path: Path, hub: str, days: Mapping[str, int]) -> Iterable[dict]:
    """The rows of a BTS on-time zip on ``days`` touching ``hub``, numbers as floats (None when blank)."""
    with zipfile.ZipFile(path) as z, z.open(next(n for n in z.namelist() if n.endswith(".csv"))) as f:
        lines = io.TextIOWrapper(f, "utf-8", errors="replace", newline="")
        header = next(csv.reader([next(lines)]))
        wanted = [c for c in (*COLUMNS, "TaxiOut", "TaxiIn", "OriginCityName", "DestCityName") if c in header]
        at = {c: header.index(c) for c in wanted}
        numbers = {"CRSElapsedTime", "DepDelay", "ArrDelay", "Cancelled", "Diverted", "DivReachedDest", "DivArrDelay",
                   "TaxiOut", "TaxiIn"}
        for row in csv.reader(line for line in lines if hub in line):
            r = {c: row[i] for c, i in at.items()}
            if r["FlightDate"] not in days or hub not in (r["Origin"], r["Dest"]):
                continue
            yield {c: (float(v) if v.strip() else None) if c in numbers else v.strip() for c, v in r.items()}


def real_leg(r: Mapping, cities: Mapping, taxi: Mapping) -> Leg:
    leg = blank(r["flight"], r["kind"], r["other"], cities, "flown", r["sched"])
    return timed(leg, r["at"], r["taxi"] if r["taxi"] is not None else median_at(taxi, r["kind"], r["at"]))


def medians(rows: Sequence[Mapping]) -> dict[str, dict[str, float]]:
    """Median taxi-out and taxi-in minutes at the hub by local hour of pushback / gate arrival, the day's under
    "day"."""
    out = {}
    for kind in ("dep", "arr"):
        got = [(int(r["at"] // 60 % 24), r["taxi"]) for r in rows if r["kind"] == kind and r["taxi"] is not None]
        hours: dict[str, list[float]] = {}
        for h, v in got:
            hours.setdefault(str(h), []).append(v)
        out[kind] = {"day": median([v for _, v in got]) if got else TAXI[kind]} | {
            h: median(v) for h, v in sorted(hours.items(), key=lambda kv: int(kv[0]))}
    return out


def median_at(taxi: Mapping, kind: str, t: float) -> float:
    table = taxi[kind]
    return table.get(str(int(t // 60 % 24)), table["day"])


def registry_models(path: Path, tails: set[str]) -> dict[str, str]:
    """The model of each tail in the FAA registry's current (MASTER) or deregistered (DEREG) aircraft, current first;
    a tail BTS records without its N is looked up with it."""
    marks = {t if t.startswith("N") else f"N{t}": t for t in tails}
    found: dict[str, str] = {}
    if not tails or not path.is_file():
        return found
    with zipfile.ZipFile(path) as z:
        ref = {r[0].strip(): r[2].strip() for r in csv.reader(io.TextIOWrapper(z.open("ACFTREF.txt"), "utf-8-sig"))}
        for name in ("MASTER.txt", "DEREG.txt"):
            for r in csv.reader(io.TextIOWrapper(z.open(name), "utf-8-sig", errors="replace")):
                tail = marks.get("N" + r[0].strip())
                if tail and tail not in found:
                    found[tail] = ref.get(r[2].strip(), "")
    return {t: m for t, m in found.items() if m}


def classify(model: str, carrier: str) -> tuple[str, float, float]:
    m = model.upper().replace("AIRBUS ", "")
    for pattern, cls, length, span in TYPES:
        if re.search(pattern, m):
            return cls, length, span
    return FALLBACK_REGIONAL if carrier in REGIONAL else FALLBACK


def b6_type(t: Mapping) -> tuple[str, float, float]:
    if t["family"] == "A32X":
        return B6_TYPES["A321" if "321" in t["type"] else "A320"]
    return B6_TYPES.get(t["family"], FALLBACK)


def terminal_of(hub: str, carrier: str, number: int) -> str:
    if carrier == "YX":
        carrier = next((c for bound, c in REPUBLIC if number < bound), "DL")
    return TERMINALS.get(hub, {}).get(carrier, "remote")


def stops(task: Task) -> list[list[int]]:
    """The ground stops on flights to the hub from the FAA's advisories, each cut short by its cancellation."""
    periods, ends = [], []
    for n in sorted(task.news, key=lambda n: n.at):
        if n.kind != "advisory" or n.data.get("element") != task.hub:
            continue
        if n.data.get("kind") == "gs" and n.data.get("gs_period"):
            periods.append(list(n.data["gs_period"]))
        elif n.data.get("kind") == "gs_cnx":
            ends.append((n.data.get("event_time") or n.data.get("cnx_period") or [n.at])[0])
    merged: list[list[int]] = []
    for a, b in sorted(periods):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [[a, min([b] + [e for e in ends if a < e < b])] for a, b in merged]


def metar(t: int, text: str) -> dict:
    """A METAR as numbers: wind (true degrees, knots), visibility (statute miles), weather codes, cloud layers (feet),
    the ceiling (the lowest broken, overcast or obscured layer), temperature and dew point; and a plain-English line."""
    tokens = text.split(" RMK")[0].split()
    w = {"t": t, "metar": text.strip(), "wind_dir": None, "wind_kt": 0, "gust_kt": None, "vis_sm": None, "wx": [],
         "clouds": [], "ceiling_ft": None, "temp_c": None, "dew_c": None}
    vis = None
    for i, tok in enumerate(tokens):
        if i == 0 or re.fullmatch(r"\d{6}Z|METAR|SPECI|AUTO|COR", tok):
            continue
        if m := WIND.fullmatch(tok):
            w["wind_dir"] = None if m.group(1) == "VRB" else int(m.group(1))
            w["wind_kt"], w["gust_kt"] = int(m.group(2)), int(m.group(3)) if m.group(3) else None
        elif m := VIS.fullmatch(tok):
            whole = int(tokens[i - 1]) if i > 1 and m.group(2) and tokens[i - 1].isdigit() else 0
            value = whole + int(m.group(2)) / int(m.group(3)) if m.group(2) else float(m.group(4))
            text_vis = (f"{whole} " if whole else "") + (f"{m.group(2)}/{m.group(3)}" if m.group(2) else m.group(4))
            w["vis_sm"] = round(value, 2)
            vis = {"M": f"under {text_vis}", "P": f"over {text_vis}"}.get(m.group(1) or "", text_vis)
        elif m := CLOUD.match(tok):
            w["clouds"].append([m.group(1), int(m.group(2)) * 100])
        elif m := TEMP.fullmatch(tok):
            w["temp_c"] = int(m.group(1).replace("M", "-"))
            w["dew_c"] = int(m.group(2).replace("M", "-")) if m.group(2) else None
        elif len(tok) >= 2 and (m := WX.fullmatch(tok)) and (m.group(2) or m.group(3)):
            w["wx"].append(tok)
    w["ceiling_ft"] = min((ft for cover, ft in w["clouds"] if cover in ("BKN", "OVC", "VV")), default=None)
    w["plain"] = plain(w, vis)
    return w


def plain(w: Mapping, vis: str | None) -> str:
    """"Light rain · visibility 8 mi · cloud at 700 ft · wind NNW 9 mph"."""
    words = " and ".join(phrase(c) for c in w["wx"])
    rank = ("FEW", "SCT", "BKN", "OVC", "VV")
    cover = max((c for c, _ in w["clouds"]), key=rank.index, default=None)
    parts = [(words[:1].upper() + words[1:]) if words else SKY[cover]]
    if vis:
        parts.append(f"visibility {vis} mi")
    if w["ceiling_ft"] is not None:
        parts.append(f"cloud at {w['ceiling_ft']:,} ft")
    mph = round(w["wind_kt"] * KT_MPH)
    if w["wind_kt"] == 0:
        parts.append("calm")
    else:
        where = "variable" if w["wind_dir"] is None else COMPASS[round(w["wind_dir"] / 22.5) % 16]
        gust = f" gusting {round(w['gust_kt'] * KT_MPH)}" if w["gust_kt"] else ""
        parts.append(f"wind {where} {mph} mph{gust}")
    return " · ".join(parts)


def phrase(code: str) -> str:
    """One weather group in words: -RA "light rain", +TSRA "thunderstorm with heavy rain", VCSH "showers nearby"."""
    sign, described, rest = WX.fullmatch(code).groups()
    found = [rest[i:i + 2] for i in range(0, len(rest), 2)]
    noun = " and ".join(PHENOMENA[p] for p in found)
    wet = any(p not in DRY for p in found)
    level = {"-": "light ", "+": "heavy "}.get(sign or "", "") if wet else ""
    descriptors = [described[i:i + 2] for i in range(0, len(described), 2)]
    for d in descriptors:
        if d in DESCRIBED and noun:
            noun = DESCRIBED[d].format(noun)
    if "SH" in descriptors:
        noun = f"{noun} showers" if noun else "showers"
    if "TS" in descriptors:
        words = f"thunderstorm with {level}{noun}" if noun else "thunderstorm"
    else:
        words = f"{level}{noun}"
    return f"{words} nearby" if sign == "VC" else words


def leg_time(g: Leg) -> float:
    for k in ("out", "in", "on", "sched"):
        if g[k] is not None:
            return g[k]
    return 0.0


def at_gate(g: Leg) -> float:
    return g["out"] if g["kind"] == "dep" else g["in"]


def clock(hhmm_text: str) -> int:
    v = int(float(hhmm_text or 0))
    return v // 100 * 60 + v % 100


def hhmm(t: float) -> str:
    t = int(round(t)) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p)


def num(v: float | None) -> float | int | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = round(float(v), 1)
    return int(v) if v.is_integer() else v


def median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def mean(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def natural(ref: str) -> list:
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", ref)]
