"""The b6-v1 pack: one task per (day, hub) window, from pinned public inputs (see DESIGN.md for the task schema).

Scope: JetBlue's tails that fly a leg touching the hub inside the window; their legs from `start` (window start - 1 h)
to the end of the day are the flights, those flown before `start` are frozen. The data-only clauses keep a window
whose every hub movement has a typed tail, whose airline cancellations in scope are at least 80% weather or NAS
(codes B, C over A, B, C) and whose scope has 30 to 80 tails. `reference` is left empty for scripts/references.py."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import airportsdata
import pandas as pd

from . import advisories, bts, fetch, fleet, weather, windows

CARRIER = "B6"
DMAX = 360
NEWS_BEFORE = 720
"""Advisories signed up to this many minutes before `start` are delivered at the first watch."""
METAR_BEFORE = 180
MIN_TAILS, MAX_TAILS = 30, 80
MIN_WEATHER_SHARE = 0.8
MIN_DISRUPTION = {"eval": 5 * windows.CANCEL_WEIGHT, "train": 2 * windows.CANCEL_WEIGHT}
"""Real disruption: an eval window holds at least five weather or NAS cancellations' worth at the hub, a train window
two."""
LIFT_PCT = 75
LIFT_WINDOW = 90
CONSTRAINED = ("BOS", "EWR", "JFK", "LGA")
KINDS = ("advisory", "taf", "metar", "outlook")
SPLITS = ("eval", "control", "train")
EVAL_DAYS = ("2022-12-16", "2022-12-23", "2022-12-24", "2023-06-16", "2023-06-17", "2023-06-18", "2023-06-19",
             "2023-06-24", "2023-06-25", "2023-06-26", "2023-06-27", "2023-06-28", "2024-01-06", "2024-01-07",
             "2024-01-09", "2024-01-16", "2024-01-17", "2024-01-19")
"""The candidate storm days of the flight-records research (section 5), each tried at every hub."""
CONTROL_DAYS = (("2023-06-10", ("JFK",)), ("2024-01-04", ("JFK", "BOS")), ("2024-01-24", ("JFK", "BOS")))
"""Normal days with no cancellations: the first hub of each whose window passes the clauses."""
MONTHS = ("2022-12", "2023-06", "2024-01")


@dataclass(frozen=True)
class Hub:
    afd: str
    office: str
    speaker: str


HUBS = {"JFK": Hub("AFDOKX", "OKX", "NWS New York forecast discussion"),
        "BOS": Hub("AFDBOX", "BOX", "NWS Boston forecast discussion")}


@dataclass
class Day:
    date: str
    hub: str
    tz: str
    base: int
    prev: pd.DataFrame
    cur: pd.DataFrame
    nxt: pd.DataFrame
    around: pd.DataFrame
    fleet: dict
    lf: dict
    turn: dict[str, int]
    turn_all: int


@dataclass
class Scope:
    window: tuple[int, int]
    start: int
    tails: list[str]
    untailed: int
    typed: bool
    dec: pd.DataFrame
    frozen: pd.DataFrame
    codes: dict[str, int]

    @property
    def weather_share(self) -> float:
        a, b, c = (self.codes.get(k, 0) for k in "ABC")
        return (b + c) / (a + b + c) if a + b + c else 1.0

    @property
    def clauses(self) -> dict[str, bool]:
        return {"tails_known": self.untailed == 0 and self.typed,
                "weather_causes": self.weather_share >= MIN_WEATHER_SHARE,
                "size": MIN_TAILS <= len(self.tails) <= MAX_TAILS}

    @property
    def ok(self) -> bool:
        return all(self.clauses.values())


def fetch_inputs(inputs: Path, months: list[str], hubs: list[str]) -> None:
    for month in months:
        bts.fetch_ontime(inputs, month)
        fleet.fetch_t100(inputs, month)
        for year in fleet.b43_years(month):
            fleet.fetch_b43(inputs, year)
        for hub in hubs:
            weather.fetch_metars(inputs, hub, month)
            weather.fetch_afos(inputs, f"TAF{hub}", month)
            weather.fetch_afos(inputs, HUBS[hub].afd, month)
        for day in month_days(month):
            advisories.fetch_day(inputs, day, set(hubs))
    fleet.fetch_registry(inputs)


def build(inputs: Path, derived: Path, months: list[str], hubs: list[str]) -> tuple[list[dict], list[dict]]:
    """Every task of the pack, sorted, and a row per (day, hub) tried, with its window or why there is none."""
    df = bts.load([bts.ontime_path(inputs, m) for m in months], derived)
    carrier = df[df.Reporting_Airline == CARRIER]
    by_date = dict(tuple(carrier.groupby("FlightDate")))
    near = df[df.Origin.isin(CONSTRAINED) | df.Dest.isin(CONSTRAINED)]
    near_by_date = dict(tuple(near.groupby("FlightDate")))
    fleets = {m: fleet.fleet(CARRIER, m, set(carrier[carrier.FlightDate.str.startswith(m)].Tail_Number.dropna()),
                             inputs) for m in months}
    lfs = {m: fleet.load_factors(CARRIER, m, inputs) for m in months}
    sources = Sources(inputs)
    dates = sorted(d for d in by_date if shift(d, -1) in by_date and shift(d, 1) in by_date)
    eval_days, control = set(EVAL_DAYS), dict(CONTROL_DAYS)
    tasks, tried = [], []
    for d in dates:
        split = "eval" if d in eval_days else "control" if d in control else "train"
        for hub in [h for h in control[d] if h in hubs] if split == "control" else hubs:
            day = context(d, hub, by_date, near_by_date, fleets[d[:7]], lfs[d[:7]])
            task, row = attempt(day, split, inputs, sources)
            tried.append(row)
            if task:
                tasks.append(task)
                if split == "control":
                    break
    tasks.sort(key=lambda t: (SPLITS.index(t["split"]), t["task_id"]))
    return tasks, tried


def attempt(day: Day, split: str, inputs: Path, sources: "Sources") -> tuple[dict | None, dict]:
    x = day.cur
    disruption = windows.hourly(x, day.hub)
    scopes: dict[tuple[int, int], Scope] = {}

    def allowed(a: int, b: int) -> bool:
        scopes[a, b] = scope(day, a * 60, b * 60)
        return scopes[a, b].ok

    chosen = windows.choose(disruption, windows.movements(x, day.hub), allowed)
    row = {"date": day.date, "hub": day.hub, "split": split, "window": None, "disruption": 0, "reason": ""}
    if chosen is None:
        return None, {**row, "reason": "no window passes the data-only clauses"}
    a, b, score = chosen
    row.update(window=[a * 60, b * 60], disruption=round(score))
    if split == "control" and scopes[a, b].codes:
        return None, {**row, "reason": "cancellations on a control day"}
    if split != "control" and score < MIN_DISRUPTION[split]:
        return None, {**row, "reason": f"disruption below {MIN_DISRUPTION[split]}"}
    task = make_task(day, scopes[a, b], split, inputs, sources)
    return task, {**row, "task_id": task["task_id"]}


def context(d: str, hub: str, by_date: dict, near_by_date: dict, aircraft: dict, lf: dict) -> Day:
    tz = bts.airports()[hub]["tz"]
    base = bts.epoch_minutes(pd.Timestamp(d).tz_localize(tz).tz_convert("UTC").to_pydatetime())
    frames = [local(by_date[shift(d, k)], base) for k in (-1, 0, 1)]
    around = local(pd.concat([near_by_date[shift(d, k)] for k in (-1, 0, 1) if shift(d, k) in near_by_date]), base)
    turn, turn_all = turns(frames[1], aircraft)
    return Day(d, hub, tz, base, *frames, around, aircraft, lf, turn, turn_all)


def local(df: pd.DataFrame, base: int) -> pd.DataFrame:
    return df.assign(sd=df.sched_dep - base, sa=df.sched_arr - base, ad=df.act_dep - base, aa=df.act_arr - base,
                     end=end_station(df),
                     lb_exo=(df.DepDelay.clip(lower=0) - df.LateAircraftDelay.fillna(0)).clip(lower=0))


def end_station(df: pd.DataFrame) -> pd.Series:
    """Where the aircraft ended the leg: the destination, else its last diversion airport."""
    reached = (df.Diverted == 0) | (df.DivReachedDest == 1)
    div = df.Div2Airport.where(df.Div2Airport.notna() & (df.Div2Airport != ""), df.Div1Airport)
    return df.Dest.where(reached, div)


def flown(df: pd.DataFrame) -> pd.Series:
    return (df.Cancelled == 0) & ((df.Diverted == 0) | (df.DivReachedDest == 1))


def turns(x: pd.DataFrame, aircraft: dict) -> tuple[dict[str, int], int]:
    """The 5th percentile of the day's actual ground times between connecting flown legs, by family and overall."""
    legs = x[flown(x) & x.Tail_Number.notna()].sort_values(["Tail_Number", "ad"])
    nxt = legs.groupby("Tail_Number")
    ok = (legs.end == nxt.Origin.shift(-1)) & nxt.Origin.shift(-1).notna()
    ground = (nxt.ad.shift(-1) - legs.aa)[ok]
    fam = legs.Tail_Number[ok].map(lambda t: aircraft[t].family if t in aircraft else "")
    by: dict[str, list[int]] = {}
    for f, g in zip(fam, ground, strict=True):
        by.setdefault(f, []).append(int(g))
    every = sorted(v for f, vs in by.items() for v in vs)
    return ({f: p5(v) for f, v in sorted(by.items()) if f}, p5(every))


def p5(values: list[int]) -> int:
    v = sorted(values)
    return v[int(0.05 * len(v))]


def scope(day: Day, w0: int, w1: int) -> Scope:
    x = day.cur
    touch = (((x.Origin == day.hub) & (x.sd >= w0) & (x.sd < w1)) | ((x.Dest == day.hub) & (x.sa >= w0) & (x.sa < w1)))
    tails = sorted(set(x[touch].Tail_Number.dropna()))
    xs = x[x.Tail_Number.isin(tails)]
    start = w0 - 60
    departed = xs.Cancelled == 0
    dec = xs[(departed & (xs.ad >= start)) | (~departed & (xs.sd >= start))]
    codes = dec[dec.Cancelled == 1].CancellationCode.value_counts()
    return Scope((w0, w1), start, tails, int(x[touch].Tail_Number.isna().sum()), set(tails) <= set(day.fleet),
                 dec, xs[departed & (xs.ad < start)], {k: int(v) for k, v in sorted(codes.items())})


def make_task(day: Day, sc: Scope, split: str, inputs: Path, sources: "Sources") -> dict:
    flights = make_flights(day, sc)
    tails = make_tails(day, sc)
    frozen = [{"flight": f"{CARRIER} {int(r.Flight_Number_Reporting_Airline)}", "orig": r.Origin, "dest": r.Dest,
               "dep": int(r.ad), "arr": None if pd.isna(r.aa) else int(r.aa), "tail": r.Tail_Number}
              for r in sc.frozen.sort_values(["ad", "Tail_Number"]).itertuples()]
    caps, nominal = make_caps(day, sc)
    horizon = max(f["sched_dep"] + f["dmax"] for f in flights)
    news, used = make_news(day, sc.start, horizon, inputs)
    codes = {f["orig"] for f in flights} | {f["dest"] for f in flights} | {r["dest"] for r in frozen} | {
        r["orig"] for r in frozen} | {t[k] for t in tails for k in ("pos", "end_req", "actual_end")}
    w0 = sc.window[0]
    return {
        "task_id": f"{CARRIER.lower()}-{day.hub.lower()}-{day.date.replace('-', '')}-{w0 // 60:02d}{w0 % 60:02d}",
        "split": split, "carrier": CARRIER, "hub": day.hub, "date": day.date, "tz": day.tz, "window": list(sc.window),
        "start": sc.start, "turn": day.turn, "turn_all": day.turn_all, "dmax": DMAX, "airports": airports(codes),
        "tails": tails, "flights": flights, "caps": caps, "nominal": nominal, "news": news, "frozen": frozen,
        "reference": {}, "sources": sources.of(day, sc, used)}


def make_flights(day: Day, sc: Scope) -> list[dict]:
    dec = sc.dec.assign(num=sc.dec.Flight_Number_Reporting_Airline.astype(int), reached=flown(sc.dec))
    dec = dec.sort_values(["sd", "num", "Origin"])
    pool = day.cur[day.cur.Cancelled == 0]
    out = []
    for r in dec.itertuples():
        a = day.fleet[r.Tail_Number]
        departed = r.Cancelled == 0
        lb = int(math.ceil(r.lb_exo)) if departed else lift(pool, r.Origin, r.sd)
        reached = bool(r.reached)
        block = int(r.aa - r.ad) if reached else int(r.CRSElapsedTime)
        out.append({"id": len(out), "flight": f"{CARRIER} {r.num}", "orig": r.Origin, "dest": r.Dest,
                    "sched_dep": int(r.sd), "sched_arr": int(r.sa), "block": block,
                    "pax": fleet.pax(a.seats, day.lf, r.Origin, r.Dest), "family": a.family, "lb": lb,
                    "dmax": max(DMAX, sc.start - int(r.sd) + 60),
                    "actual": {"cancelled": int(not reached), "cancel_code": "" if departed else r.CancellationCode,
                               "dep": int(r.ad) if departed else None, "arr": int(r.aa) if reached else None,
                               "end": r.end if departed else None, "tail": r.Tail_Number}})
    return out


def lift(pool: pd.DataFrame, origin: str, sd: float) -> int:
    """A cancelled flight's exogenous delay: the 75th percentile of flights that left the same origin within 90 min."""
    at = pool[pool.Origin == origin]
    near = at[(at.sd - sd).abs() <= LIFT_WINDOW].lb_exo
    near = near if len(near) >= 3 else at.lb_exo
    return int(math.ceil(near.quantile(LIFT_PCT / 100))) if len(near) else 0


def make_tails(day: Day, sc: Scope) -> list[dict]:
    out = []
    for t in sc.tails:
        a = day.fleet[t]
        turn = day.turn.get(a.family, day.turn_all)
        mine = day.cur[day.cur.Tail_Number == t]
        legs = sc.frozen[sc.frozen.Tail_Number == t].sort_values("ad")
        prev = day.prev[(day.prev.Tail_Number == t) & (day.prev.Cancelled == 0)].sort_values("ad")
        if len(legs):
            last = legs.iloc[-1]
            pos = last.end
            arr = last.aa if pd.notna(last.aa) else last.ad + last.CRSElapsedTime + 60
            ready = int(arr) + turn
        elif len(prev):
            last = prev.iloc[-1]
            pos = last.end
            ready = max(0, int(last.aa) + turn) if pd.notna(last.aa) else 0
        else:
            pos, ready = mine.sort_values("sd").Origin.iloc[0], 0
        today = mine[mine.Cancelled == 0].sort_values("ad")
        actual_end = today.end.iloc[-1] if len(today) else pos
        nxt = day.nxt[day.nxt.Tail_Number == t]
        nxt_flown = nxt[nxt.Cancelled == 0].sort_values("ad")
        end_req = (nxt_flown.Origin.iloc[0] if len(nxt_flown) else nxt.sort_values("sd").Origin.iloc[0] if len(nxt)
                   else actual_end)
        out.append({"tail": t, "family": a.family, "type": a.type, "seats": a.seats, "pos": pos, "ready": ready,
                    "end_req": end_req, "actual_end": actual_end})
    return out


def make_caps(day: Day, sc: Scope) -> tuple[dict, dict]:
    """Truth caps: the carrier's ration-by-schedule share of what every carrier moved at the airport in each local
    hour, less the carrier's own movements outside the scope, never below the scope's own; nominal: the scope's
    scheduled movements. Both for the constrained airports the scope uses, from the first watch's hour to 23."""
    dec = sc.dec
    mine = set(dec.index)
    used = sorted(({*dec.Origin, *dec.Dest}) & set(CONSTRAINED))
    hours = range(sc.start // 60, 24)
    caps: dict[str, dict[str, int]] = {}
    nominal: dict[str, dict[str, int]] = {}
    for a in used:
        for kind, place, s_col, a_col in (("arr", "Dest", "sa", "aa"), ("dep", "Origin", "sd", "ad")):
            at = day.around[day.around[place] == a]
            ours = at.Reporting_Airline == CARRIER
            in_scope = at.index.isin(mine)
            sched_h, act_h = at[s_col] // 60, at[a_col] // 60
            scope_sched = dec[dec[place] == a][s_col] // 60
            key = f"{a}:{kind}"
            caps[key], nominal[key] = {}, {}
            for h in hours:
                sched, real = sched_h == h, act_h == h
                share = (sched & ours).sum() / sched.sum() if sched.sum() else 0.0
                rbs = int(share * real.sum() + 0.5)
                own = int((real & in_scope).sum())
                caps[key][str(h)] = max(own, rbs - int((real & ours & ~in_scope).sum()))
                nominal[key][str(h)] = int((scope_sched == h).sum())
    return caps, nominal


def make_news(day: Day, start: int, horizon: int, inputs: Path) -> tuple[list[dict], dict]:
    hub, month = HUBS[day.hub], day.date[:7]
    lo = start - NEWS_BEFORE
    days = utc_days(day.base + lo, day.base + horizon)
    records = [r for d in days for r in advisories.load_day(inputs, d, day.hub)]
    taf = weather.afos_path(inputs, f"TAF{day.hub}", month)
    afd = weather.afos_path(inputs, hub.afd, month)
    metar = weather.metar_path(inputs, day.hub, month)
    news = (advisories.news(records, day.hub, day.base, lo, horizon)
            + weather.taf_news(read(taf), month, day.hub, day.base, start, horizon)
            + weather.metar_news(read(metar), day.hub, day.base, start - METAR_BEFORE, horizon)
            + weather.outlook_news(read(afd), month, hub.office, hub.speaker, day.base, start, horizon))
    news.sort(key=lambda n: (n["at"], KINDS.index(n["kind"]), n["id"]))
    return news, {"advisories": days, "taf": taf, "metar": metar, "outlook": afd}


def airports(codes: set[str]) -> dict:
    data = bts.airports()
    return {c: {"name": data[c]["name"], "lat": round(data[c]["lat"], 4), "lon": round(data[c]["lon"], 4),
                "tz": data[c]["tz"]} for c in sorted(codes)}


class Sources:
    """The sha256 of each pinned input, computed once per build."""

    def __init__(self, inputs: Path):
        self.inputs = inputs
        self.cache: dict[str, str] = {}

    def file(self, path: Path) -> str:
        rel = str(path.relative_to(self.inputs))
        if rel not in self.cache:
            self.cache[rel] = fetch.sha256(path)
        return self.cache[rel]

    def advisories(self, day: str, hub: str) -> str:
        key = f"atcscc/{day}/{hub}"
        if key not in self.cache:
            self.cache[key] = advisories.day_digest(advisories.load_day(self.inputs, day, hub))
        return self.cache[key]

    def of(self, day: Day, sc: Scope, used: dict) -> dict:
        month = day.date[:7]
        out = {"bts_ontime": self.file(bts.ontime_path(self.inputs, month)),
               "b43": {str(y): self.file(fleet.b43_path(self.inputs, y)) for y in sorted(
                   {int(day.fleet[t].source.split()[-1]) for t in sc.tails if day.fleet[t].source.startswith("B-43")})},
               "t100": self.file(fleet.t100_path(self.inputs, month))}
        if any(day.fleet[t].source == "FAA registry" for t in sc.tails):
            out["faa_registry"] = self.file(fleet.registry_path(self.inputs))
        out["advisories"] = {d: self.advisories(d, day.hub) for d in used["advisories"]}
        out.update({k: self.file(used[k]) for k in ("taf", "metar", "outlook")})
        out["airportsdata"] = airportsdata.__version__
        return out


def summary(task: dict) -> dict:
    flights = task["flights"]
    cancelled = [f for f in flights if f["actual"]["cancelled"]]
    codes: dict[str, int] = {}
    for f in cancelled:
        if f["actual"]["cancel_code"]:
            codes[f["actual"]["cancel_code"]] = codes.get(f["actual"]["cancel_code"], 0) + 1
    adv = [n for n in task["news"] if n["kind"] == "advisory"]
    return {"task_id": task["task_id"], "split": task["split"], "window": task["window"], "tails": len(task["tails"]),
            "flights": len(flights), "cancelled": len(cancelled), "codes": dict(sorted(codes.items())),
            "diverted": sum(1 for f in flights if f["actual"]["cancelled"] and not f["actual"]["cancel_code"]),
            "programs": sum(1 for n in adv if n["data"]["kind"] == "gdp"),
            "ground_stops": sum(1 for n in adv if n["data"]["kind"] == "gs"),
            "news": {k: sum(1 for n in task["news"] if n["kind"] == k) for k in KINDS}}


def write(tasks: list[dict], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / "tasks.jsonl"
    fetch.save(path, "".join(json.dumps(t, separators=(",", ":")) + "\n" for t in tasks).encode())
    return path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def utc_days(lo: int, hi: int) -> list[str]:
    first = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(minutes=lo)
    last = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(minutes=hi)
    return [(first.date() + timedelta(days=k)).isoformat() for k in range((last.date() - first.date()).days + 1)]


def month_days(month: str) -> list[str]:
    year, m = (int(v) for v in month.split("-"))
    first = date(year, m, 1)
    return [(first + timedelta(days=k)).isoformat() for k in range(31) if (first + timedelta(days=k)).month == m]


def shift(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()
