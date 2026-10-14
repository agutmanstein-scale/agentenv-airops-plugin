"""Stage one replay moment at a hub for the 3D viewer: every aircraft on the ground or moving at the hub in a window of
the task date, JetBlue's from a recorded episode's executed plan and every other carrier's from BTS on-time on its real
times, typed from the FAA registry, each parked at a display-only stand; the METARs in force and the runways in use.
Needs the hub's stage (scripts/build_stage.py); written to cache/stage/<HUB>/moment-<hub>-<yyyymmdd>.json.

usage: python scripts/stage_moment.py [--episode PATH] [--window 16:50-18:10]
"""

import argparse
import csv
import gzip
import heapq
import io
import json
import math
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import airportsdata
import pandas as pd
import shapely
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

from agentenv_airops.build import bts
from agentenv_airops.build.fleet import registry_models

ROOT = Path(__file__).resolve().parents[1]
EPISODE = ROOT / "cache/hub/dataset/viewer/episodes/sol-v1.openai-gpt-6.1-sol.b6-bos-20250216-1500.json"
CARRIER = "B6"
TERMINAL = {"DL": "A", "AA": "B", "MQ": "B", "UA": "B", "AS": "B", "AC": "B", "WN": "B", "B6": "C", "NK": "E",
            "F9": "E", "G4": "E", "HA": "E", "SY": "E"}
REPUBLIC = ((4000, "UA"), (5000, "AA"), (10000, "DL"))
"""Republic (YX) flies for United, American and Delta: the partner by flight-number block, upper bound exclusive."""
REGIONAL = {"YX", "MQ", "OO", "9E", "OH", "QX", "G7", "C5", "ZW", "PT"}
OVERFLOW = ["E", "B", "A"]
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
FALLBACK = ("narrowbody", 37.6, 35.8)
FALLBACK_REGIONAL = ("regional", 31.7, 26.0)
B6_TYPES = {"A220": ("a220", 38.7, 35.1), "E190": ("e190", 36.2, 28.7), "A320": ("a320", 37.6, 35.8),
            "A321": ("a321", 44.5, 35.8)}
APPROACH, CLIMB, TURN_GAP, CANCEL_WAIT = 6, 2, 5, 30
LONG_RUNWAY = 2000.0
FLOWS = {"BOS": {"33L": "27", "27": "33L", "22L": "22R", "4R": "9"}}
"""Departure runway paired with the arrival runway in the hub's usual flows."""
FEATURE_OUT = (1020, 1040)
WIDE_ROOM = 55.0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--episode", type=Path, default=EPISODE)
    p.add_argument("--window", default="16:50-18:10", help="Local hh:mm-hh:mm on the task date.")
    p.add_argument("--pack", type=Path, default=ROOT / "data" / "b6-v1" / "tasks.jsonl")
    p.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs")
    p.add_argument("--stage", type=Path, default=ROOT / "cache" / "stage")
    args = p.parse_args(argv)
    ep = json.loads(args.episode.read_text())
    task = ep["task"]
    hub, date, tz = task["hub"], task["date"], task["tz"]
    window = tuple(clock(v) for v in args.window.split("-"))
    stage = json.loads(gzip.decompress((args.stage / hub / "stage.json.gz").read_bytes()))
    record = next(t for line in args.pack.read_text().splitlines()
                  if (t := json.loads(line))["task_id"] == task["task_id"])
    month = date[:7]
    rows = movements(bts.ontime_path(args.inputs, month), hub, date, tz)
    taxi = taxi_medians(rows[rows.carrier == CARRIER])
    names = {k: v["city"] for k, v in airportsdata.load("IATA").items()}
    jetblue, notes = jetblue_aircraft(ep, record, rows, taxi, names, window, hub)
    others = other_aircraft(rows[rows.carrier != CARRIER], args.inputs / "faa" / "ReleasableAircraft.zip", names,
                            window)
    aircraft = jetblue + others
    unplaced = assign_stands(aircraft, stage, window)
    aircraft = [a for a in aircraft if a["stand"]]
    for a in aircraft:
        a.pop("ground")
    weather = metars(args.inputs / "iem" / "metar" / f"{hub}_{month.replace('-', '_')}.csv", date, tz, window)
    in_use = runways_in_use(stage, weather, window, hub)
    featured = feature(aircraft, stage, in_use["departures"])
    moment = {"hub": hub, "date": date, "tz": tz, "window": list(window), "weather": weather,
              "runways_in_use": in_use, "aircraft": aircraft, "featured": featured}
    out = args.stage / hub / f"moment-{hub.lower()}-{date.replace('-', '')}.json"
    out.write_text(json.dumps(moment, ensure_ascii=False, separators=(",", ":")) + "\n")
    legs = [g for a in aircraft for g in a["legs"]]
    print(f"{hub} {date} {args.window}: {len(aircraft)} aircraft ({sum(a['jetblue'] for a in aircraft)} JetBlue), "
          f"{len(legs)} legs, {len(weather)} METARs, runways {in_use}, featured {featured}, "
          f"{len(unplaced)} without a stand {unplaced} -> {out} ({out.stat().st_size} B)")
    for n in notes:
        print("note:", n)
    return 0


def clock(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def movements(path: Path, hub: str, date: str, tz: str) -> pd.DataFrame:
    """The day's flown BOS departures and arrivals, one row each with times in minutes from local midnight."""
    strings = ["FlightDate", "Reporting_Airline", "Tail_Number", "Flight_Number_Reporting_Airline", "Origin", "Dest",
               "CRSDepTime", "CRSArrTime", "DepTime"]
    numbers = ["DepDelay", "ArrDelay", "Cancelled", "Diverted", "CRSElapsedTime", "DivReachedDest", "DivArrDelay",
               "TaxiOut", "TaxiIn"]
    with zipfile.ZipFile(path) as z, z.open(next(n for n in z.namelist() if n.endswith(".csv"))) as f:
        df = pd.read_csv(f, usecols=strings + numbers, dtype={c: str for c in strings}, low_memory=False)
    df = df[(df.FlightDate == date) & ((df.Origin == hub) | (df.Dest == hub)) & (df.Cancelled == 0)]
    df = bts.with_utc(df)
    zero = bts.epoch_minutes(datetime.fromisoformat(date).replace(tzinfo=ZoneInfo(tz)))
    out = []
    for r in df.itertuples():
        base = {"carrier": r.Reporting_Airline, "tail": r.Tail_Number if isinstance(r.Tail_Number, str) else "",
                "number": str(r.Flight_Number_Reporting_Airline)}
        if r.Origin == hub and not math.isnan(r.act_dep):
            out.append(base | {"kind": "dep", "other": r.Dest, "sched": r.sched_dep - zero, "out": r.act_dep - zero,
                               "taxi": r.TaxiOut})
        if r.Dest == hub and not math.isnan(r.act_arr) and (r.Diverted == 0 or r.DivReachedDest == 1):
            out.append(base | {"kind": "arr", "other": r.Origin, "sched": r.sched_arr - zero, "in": r.act_arr - zero,
                               "taxi": r.TaxiIn})
    return pd.DataFrame(out)


def taxi_medians(rows: pd.DataFrame) -> dict[str, dict[int | None, float]]:
    """Median taxi-out and taxi-in minutes by local hour of pushback / gate arrival, and the day's under None."""
    out = {}
    for kind, at in (("dep", "out"), ("arr", "in")):
        r = rows[(rows.kind == kind) & rows.taxi.notna()]
        hours = (r[at] // 60 % 24).astype(int)
        out[kind] = {None: float(r.taxi.median())} | {int(h): float(g.median()) for h, g in r.taxi.groupby(hours)}
    return out


def jetblue_aircraft(ep: dict, record: dict, rows: pd.DataFrame, taxi: dict, names: dict, window: tuple, hub: str
                     ) -> tuple[list[dict], list[str]]:
    """The episode's tails at the hub in the window on Sol's executed plan (and the frozen legs before it), each
    cancelled hub departure of the window tagged on a JetBlue aircraft parked there at its scheduled time."""
    tails = {t["tail"]: t for t in record["tails"]}
    real = {(r["number"], r["kind"]): r for r in rows[rows.carrier == CARRIER].to_dict("records")}
    legs: dict[str, list[dict]] = {}
    for f in ep["frozen"]:
        for kind in ("dep", "arr"):
            if (f["orig"] if kind == "dep" else f["dest"]) != hub:
                continue
            number = f["flight"].split()[1]
            r = real.get((number, kind))
            leg = b6_leg(f["flight"], kind, f["dest"] if kind == "dep" else f["orig"], names, "flown",
                         r["sched"] if r else None, f["dep"] if kind == "dep" else f["arr"],
                         r["taxi"] if r and not math.isnan(r["taxi"]) else None, taxi)
            legs.setdefault(f["tail"], []).append(leg)
    cancelled, notes = [], []
    for s, x in zip(ep["schedule"], ep["executed"], strict=True):
        for kind in ("dep", "arr"):
            if (s["orig"] if kind == "dep" else s["dest"]) != hub:
                continue
            other = s["dest"] if kind == "dep" else s["orig"]
            sched = s["sched_dep"] if kind == "dep" else s["sched_arr"]
            if x["status"] != "flown":
                if kind == "dep" and window[0] <= sched <= window[1]:
                    cancelled.append((s, b6_leg(s["flight"], kind, other, names, "cancelled", sched, None, None, taxi)))
                elif window[0] <= sched <= window[1]:
                    notes.append(f"{s['flight']} from {other} due {hhmm(sched)} cancelled: no aircraft to show")
                continue
            at = x["dep"] if kind == "dep" else x["arr"]
            hold = x["not_before"] if kind == "dep" and x["not_before"] and x["not_before"] > sched else None
            legs.setdefault(x["tail"], []).append(b6_leg(s["flight"], kind, other, names, "flown", sched, at, None,
                                                         taxi, hold))
    out = []
    for tail, ls in sorted(legs.items()):
        ground, chosen = presence(ls, window)
        if chosen:
            t = tails[tail]
            cls, length, span = b6_type(t)
            out.append({"key": tail, "tail": tail, "carrier": CARRIER, "jetblue": True, "class": cls,
                        "type": t["type"], "length": length, "span": span, "stand": None, "legs": chosen,
                        "ground": ground, "family": t["family"]})
    for s, leg in cancelled:
        host = cancel_host(out, s, leg["sched"])
        if host is None:
            notes.append(f"{leg['flight']} cancelled at {hhmm(leg['sched'])}: no JetBlue aircraft parked to carry it")
            continue
        host["legs"] = sorted(host["legs"] + [leg], key=leg_time)
        if host["tail"] != s["tail"]:
            notes.append(f"{leg['flight']} cancelled at {hhmm(leg['sched'])} shown on {host['tail']} "
                         f"(scheduled {s['tail']}, not parked at {hub})")
    for a in out:
        a.pop("family")
    return out, notes


def b6_leg(flight: str, kind: str, other: str, names: dict, status: str, sched: float | None, at: float | None,
           taxi_real: float | None, taxi: dict, hold: float | None = None) -> dict:
    leg = {"flight": flight, "kind": kind, "other": other, "city": names.get(other, other), "status": status,
           "sched": num(sched), "out": None, "off": None, "on": None, "in": None}
    if at is not None:
        t = taxi_real if taxi_real is not None else median_at(taxi["dep" if kind == "dep" else "arr"], at)
        if kind == "dep":
            leg["out"], leg["off"] = num(at), num(at + t)
        else:
            leg["on"], leg["in"] = num(at - t), num(at)
    leg["tag"] = tag(leg, hold)
    return leg


def median_at(table: dict, t: float) -> float:
    return table.get(int(t // 60 % 24), table[None])


def tag(leg: dict, hold: float | None) -> str:
    head = f"{leg['flight']} → {leg['city']}" if leg["kind"] == "dep" else f"{leg['flight']} from {leg['city']}"
    if leg["status"] == "cancelled":
        return f"{head} · cancelled"
    if hold is not None:
        return f"{head} · held until {hhmm(hold)}"
    at = leg["out"] if leg["kind"] == "dep" else leg["in"]
    late = round(at - leg["sched"]) if leg["sched"] is not None else 0
    return f"{head} · {'on time' if late <= 0 else 'delayed ' + duration(late)}"


def duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p)


def hhmm(t: float) -> str:
    t = int(round(t)) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def num(v: float | None) -> float | int | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    v = round(float(v), 1)
    return int(v) if v.is_integer() else v


def leg_time(leg: dict) -> float:
    for k in ("out", "in", "sched"):
        if leg[k] is not None:
            return leg[k]
    return 0.0


def b6_type(t: dict) -> tuple[str, float, float]:
    if t["family"] == "A32X":
        return B6_TYPES["A321" if "A321" in t["type"].replace("-", "") or "321" in t["type"] else "A320"]
    return B6_TYPES[t["family"]]


def presence(legs: list[dict], window: tuple) -> tuple[list[list[float]], list[dict]]:
    """The aircraft's ground intervals at the hub (in to out, open-ended at either side) and the legs to show: those
    moving in the window and those bounding a ground interval that overlaps it."""
    w0, w1 = window
    flown = sorted((g for g in legs if g["status"] == "flown"), key=leg_time)
    ground, chosen, pending = [], [], None
    for g in flown:
        if g["kind"] == "arr":
            if pending is not None:
                ground.append([pending["in"], g["on"] - APPROACH, pending, None])
            pending = g
        else:
            ground.append([pending["in"] if pending else -math.inf, g["out"], pending, g])
            pending = None
    if pending is not None:
        ground.append([pending["in"], math.inf, pending, None])
    keep = []
    for start, end, arr, dep in ground:
        moving = (arr is not None and arr["on"] - APPROACH <= w1 and arr["in"] >= w0) or (
            dep is not None and dep["out"] <= w1 and dep["off"] + CLIMB >= w0)
        if moving or (start <= w1 and end >= w0):
            keep.append([start, end])
            chosen += [g for g in (arr, dep) if g is not None and g not in chosen]
    return keep, sorted(chosen, key=leg_time)


def cancel_host(aircraft: list[dict], s: dict, at: float) -> dict | None:
    """The aircraft scheduled for the cancelled flight when it is parked then, else a parked one with no pushback
    within CANCEL_WAIT minutes, the same family first, then the longest wait."""

    def wait(a: dict) -> float:
        nxt = [g["out"] for g in a["legs"] if g["kind"] == "dep" and g["out"] is not None and g["out"] >= at]
        return min(nxt) - at if nxt else math.inf

    parked = [a for a in aircraft if any(g0 <= at <= g1 for g0, g1 in a["ground"])
              and not any(g["status"] == "cancelled" for g in a["legs"])]
    own = [a for a in parked if a["tail"] == s["tail"]]
    idle = [a for a in parked if wait(a) >= CANCEL_WAIT]
    return own[0] if own else max(idle, key=lambda a: (a["family"] == s["family"], wait(a), a["tail"]), default=None)


def other_aircraft(rows: pd.DataFrame, registry: Path, names: dict, window: tuple) -> list[dict]:
    models = registry_models(registry, {t for t in rows["tail"] if t})
    legs: dict[str, list[dict]] = {}
    flight: dict[str, tuple[str, int]] = {}
    for r in rows.to_dict("records"):
        key = r["tail"] or f"{r['carrier']}{r['number']}"
        leg = {"flight": f"{r['carrier']} {r['number']}", "kind": r["kind"], "other": r["other"],
               "city": names.get(r["other"], r["other"]), "status": "flown", "sched": num(r["sched"]), "out": None,
               "off": None, "on": None, "in": None}
        t = r["taxi"] if not math.isnan(r["taxi"]) else (15 if r["kind"] == "dep" else 7)
        if r["kind"] == "dep":
            leg["out"], leg["off"] = num(r["out"]), num(r["out"] + t)
        else:
            leg["on"], leg["in"] = num(r["in"] - t), num(r["in"])
        legs.setdefault(key, []).append(leg)
        flight.setdefault(key, (r["carrier"], int(r["number"])))
    out = []
    for key, ls in sorted(legs.items()):
        ground, chosen = presence(ls, window)
        if not chosen:
            continue
        carrier, number = flight[key]
        model = models.get(key, "")
        cls, length, span = classify(model, carrier)
        out.append({"key": key, "tail": key if key != f"{carrier}{number}" else None, "carrier": carrier,
                    "jetblue": False, "class": cls, "type": model or None, "length": length, "span": span,
                    "stand": None, "legs": chosen, "ground": ground, "terminal": terminal_of(carrier, number)})
    return out


def classify(model: str, carrier: str) -> tuple[str, float, float]:
    m = model.upper().replace("AIRBUS ", "")
    for pattern, cls, length, span in TYPES:
        if re.search(pattern, m):
            return cls, length, span
    return FALLBACK_REGIONAL if carrier in REGIONAL else FALLBACK


def terminal_of(carrier: str, number: int) -> str:
    if carrier == "YX":
        carrier = next(c for bound, c in REPUBLIC if number < bound)
    return TERMINAL.get(carrier, "E")


def assign_stands(aircraft: list[dict], stage: dict, window: tuple) -> list[str]:
    """Greedy, JetBlue first then widebodies then by arrival: the first stand of the aircraft's terminal (then the
    overflow terminals, then remote stands) where its parked outline clears the terminals and every aircraft parked
    near it at an overlapping time."""
    stands = {s["ref"]: s for s in stage["stands"]}
    built = unary_union([Polygon(t["outer"], t["holes"]) for t in stage["terminals"] + [
        dict(h, holes=[]) for h in stage["hangars"]]]).buffer(-1.0)
    room = rooms(stage["stands"])
    placed: list[tuple[dict, Polygon, list]] = []
    xy = {r: s["xy"] for r, s in stands.items()}
    w0, w1 = window
    order = sorted(aircraft, key=lambda a: (not a["jetblue"], a["class"] != "widebody",
                                            min(g[0] for g in a["ground"]), a["key"]))
    unplaced = []
    for a in order:
        span = [[max(g0, w0 - 60) - TURN_GAP, min(g1, w1 + 60) + TURN_GAP] for g0, g1 in a["ground"]]
        home = "C" if a["jetblue"] else a.pop("terminal")
        pools = ["C", *OVERFLOW, "remote"] if a["jetblue"] else [home, *OVERFLOW, "remote", "C"]
        for pool in dict.fromkeys(pools):
            cands = [r for r, s in stands.items() if s["terminal"] == pool]
            wide = a["class"] == "widebody"
            cands.sort(key=lambda r: (-room[r], natural(r)) if wide else (room[r] >= WIDE_ROOM, natural(r)))
            found = None
            for r in cands:
                shape = outline(stands[r], a)
                if shape.intersects(built):
                    continue
                if any(overlaps(span, o_span) and (o["stand"] == r or (
                        math.dist(xy[r], xy[o["stand"]]) < 150 and shape.intersects(o_shape)))
                       for o, o_shape, o_span in placed):
                    continue
                found = r
                break
            if found:
                a["stand"] = found
                placed.append((a, outline(stands[found], a), span))
                break
        else:
            unplaced.append(a["key"])
    return unplaced


def rooms(stands: list[dict]) -> dict[str, float]:
    """Distance from each stand to its nearest neighbour that is not a variant of it (A19 / A19A)."""
    base = {s["ref"]: re.match(r"[A-Z]*\d+", s["ref"]).group() for s in stands}
    return {s["ref"]: min((math.dist(s["xy"], o["xy"]) for o in stands if base[o["ref"]] != base[s["ref"]]),
                          default=math.inf) for s in stands}


def outline(stand: dict, a: dict) -> Polygon:
    """The parked aircraft's plan view, a cross of fuselage, wings and tailplane, nose gear on the stand point."""
    L, S = a["length"], a["span"]
    w = max(3.0, 0.11 * S)
    nose, tail = 0.12 * L, -0.88 * L
    fuselage = shapely.box(-w / 2, tail, w / 2, nose)
    wing = Polygon([(-w / 2, -0.22 * L), (-S / 2, -0.50 * L), (-S / 2, -0.58 * L), (-w / 2, -0.48 * L),
                    (w / 2, -0.48 * L), (S / 2, -0.58 * L), (S / 2, -0.50 * L), (w / 2, -0.22 * L)])
    t = 0.18 * S
    tailplane = Polygon([(-w / 2, -0.78 * L), (-t, -0.88 * L), (-t, -0.92 * L), (t, -0.92 * L), (t, -0.88 * L),
                         (w / 2, -0.78 * L)])
    plan = unary_union([fuselage, wing, tailplane])
    return translate(rotate(plan, -stand["heading"], origin=(0, 0)), *stand["xy"])


def overlaps(a: list, b: list) -> bool:
    return any(x0 < y1 and y0 < x1 for x0, x1 in a for y0, y1 in b)


def natural(ref: str) -> list:
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", ref)]


def metars(path: Path, date: str, tz: str, window: tuple) -> list[dict]:
    """Each routine METAR in force during the window: the last one issued before it opens and every one inside."""
    zero = datetime.fromisoformat(date).replace(tzinfo=ZoneInfo(tz))
    rows = []
    for r in csv.DictReader(io.StringIO(path.read_text())):
        when = datetime.fromisoformat(r["valid"]).replace(tzinfo=ZoneInfo("UTC"))
        t = round((when - zero).total_seconds() / 60)
        rows.append((t, r))
    rows.sort(key=lambda v: v[0])
    before = [v for v in rows if v[0] <= window[0]][-1:]
    inside = [v for v in rows if window[0] < v[0] <= window[1]]
    return [metar(t, r) for t, r in before + inside]


def metar(t: int, r: dict) -> dict:
    clouds = [[r[f"skyc{i}"], int(float(r[f"skyl{i}"]))] for i in range(1, 5) if r[f"skyc{i}"] and r[f"skyl{i}"]]
    ceiling = min((base for cover, base in clouds if cover in ("BKN", "OVC", "VV")), default=None)
    m = re.search(r" (M?\d{2})/(M?\d{2}) ", r["metar"])
    temp, dew = ((int(v.replace("M", "-")) for v in m.groups()) if m else (None, None))
    whole = {k: int(float(r[k])) if r[k] else None for k in ("drct", "sknt", "gust")}
    return {"t": t, "metar": r["metar"].strip(), "wind_dir": whole["drct"], "wind_kt": whole["sknt"] or 0,
            "gust_kt": whole["gust"], "vis_sm": float(r["vsby"]) if r["vsby"] else None, "wx": r["wxcodes"].split(),
            "clouds": clouds, "ceiling_ft": ceiling, "temp_c": temp, "dew_c": dew}


def runways_in_use(stage: dict, weather: list[dict], window: tuple, hub: str) -> dict:
    """Arrivals on the long runway end with the most headwind in the METAR in force at mid-window, departures on the
    end the hub pairs with it in that flow (else the same end)."""
    mid = sum(window) / 2
    w = [m for m in weather if m["t"] <= mid][-1]
    ends = [(w["wind_kt"] * math.cos(math.radians((w["wind_dir"] or 0) - end["heading"])), end["id"])
            for r in stage["runways"] if math.dist(r["ends"][0]["xy"], r["ends"][1]["xy"]) >= LONG_RUNWAY
            for end in r["ends"]]
    arr = max(ends)[1]
    return {"departures": FLOWS.get(hub, {}).get(arr, arr), "arrivals": arr}


def feature(aircraft: list[dict], stage: dict, runway: str) -> dict | None:
    """The JetBlue departure flown with pushback in FEATURE_OUT from a Terminal C stand with the longest taxi to the
    departure runway."""
    g = stage["taxi_graph"]
    stand_term = {s["ref"]: s["terminal"] for s in stage["stands"]}
    adj: dict[int, list[tuple[int, float]]] = {}
    for a, b, length, kind in g["edges"]:
        cost = length * (3 if kind == "runway" else 1)
        adj.setdefault(a, []).append((b, cost))
        adj.setdefault(b, []).append((a, cost))
    dist = dijkstra(adj, g["thresholds"][runway])
    best = None
    for a in aircraft:
        if not a["jetblue"] or stand_term.get(a["stand"]) != "C":
            continue
        for leg in a["legs"]:
            if leg["kind"] == "dep" and leg["status"] == "flown" and FEATURE_OUT[0] <= leg["out"] <= FEATURE_OUT[1]:
                score = dist.get(g["stand_nodes"][a["stand"]], 0)
                if best is None or score > best[0]:
                    best = (score, {"tail": a["tail"], "flight": leg["flight"], "out": leg["out"], "off": leg["off"]})
    return best[1] if best else None


def dijkstra(adj: dict, src: int) -> dict[int, float]:
    dist, todo = {src: 0.0}, [(0.0, src)]
    while todo:
        d, u = heapq.heappop(todo)
        if d > dist[u]:
            continue
        for v, c in adj.get(u, ()):
            if d + c < dist.get(v, math.inf):
                dist[v] = d + c
                heapq.heappush(todo, (d + c, v))
    return dist


if __name__ == "__main__":
    sys.exit(main())
