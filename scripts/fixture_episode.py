"""Build a fixture episode for the viewer before the env exists: a real storm day (a research instance from BTS data),
replanned watch by watch by the reference follower (the time-line MIP re-solved on the day as known), with the FAA
Command Center advisories and the hub's TAFs and METARs as issued, executed on the truth under DESIGN.md's rules and
costs, and graded against the hindsight optimum. Writes cache/episodes/<task_id>.fixture.json (and index.json) and a
trimmed copy for the tests.

usage: python scripts/fixture_episode.py [--instance inst_B6_20230626_JFK_14-20.json] [--research ~/airops-research]
           [--downloads DIR]   # the research downloads: flights/work/otp_*.parquet and weather/data (offline fallback)
"""

import argparse
import copy
import csv
import heapq
import importlib
import io
import json
import math
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import airportsdata
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = Path.home() / "airops-research"
CANCEL = 523
FERRY_FIXED, FERRY_PER_MILE = 92 * 30, 92 / 8
LOOK_LB, LOOK_CAP, FREEZE, STEP = 120, 240, 60, 60
LATE = 15
METRO = {"N90", "ZNY"}
IEM = "https://mesonet.agron.iastate.edu/cgi-bin"
NOUNS = {"advisory": ("advisory", "advisories"), "taf": ("TAF", "TAFs"), "metar": ("METAR", "METARs"),
         "outlook": ("outlook", "outlooks")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--instance", type=Path, default=RESEARCH / "model/out/inst_B6_20230626_JFK_14-20.json")
    p.add_argument("--research", type=Path, default=RESEARCH, help="airops-research checkout (model/scripts, atc/).")
    p.add_argument("--downloads", type=Path, help="Research downloads: the BTS parquet for the morning's flown legs "
                   "(flights/work/otp_YYYY_M.parquet) and KEWR TAF/METAR samples if IEM is unreachable.")
    p.add_argument("--out", type=Path, default=ROOT / "cache" / "episodes")
    p.add_argument("--small", type=Path, default=ROOT / "tests" / "viewer" / "fixtures" / "episode_small.json")
    args = p.parse_args(argv)

    sys.path.insert(0, str(args.research / "model" / "scripts"))
    C, TB = importlib.import_module("common"), importlib.import_module("timeband")
    C.K_CANCEL, C.FERRY_FIXED, C.FERRY_PER_MILE = CANCEL, FERRY_FIXED, FERRY_PER_MILE
    inst = json.loads(args.instance.read_text())
    task = make_task(inst)
    check_against_research(inst, task, args)
    news = advisories(task, args.research / "atc" / "advisories") + weather(task, args.downloads)
    news.sort(key=lambda n: (n["at"], n["id"]))
    task["news"] = news

    hindsight = TB.solve_tb(inst, 1, 300, lp=False, mip_gap=1e-4)
    floor = TB.solve_tb(inst, 1, 120, floor=True, lp=False)
    print(f"hindsight {hindsight['total_cost']} ({hindsight['status']}, {hindsight['wall_s']} s), "
          f"floor {floor['total_cost']}")
    follower = rolling(inst, task, C, TB, "persist")
    blind = rolling(inst, task, C, TB, "blind")
    truth_lb = {f["id"]: f["lb"] for f in task["flights"]}
    caps = task["caps"]
    final = follower["plans"][-1]
    run = execute(task, final, truth_lb, caps)
    refs = {
        "floor": floor["total_cost"], "hindsight": hindsight["total_cost"], "anchor": hindsight["total_cost"],
        "follower": run["cost"]["total"],
        "blind": execute(task, blind["plans"][-1], truth_lb, caps)["cost"]["total"],
        "naive": execute(task, {}, truth_lb, caps)["cost"]["total"],
        "airline": execute(task, airline_plan(task), truth_lb, caps)["cost"]["total"],
    }
    refs["gap_pct"] = round(100 * (refs["follower"] - refs["anchor"]) / refs["anchor"], 2)
    refs["qualifies"] = True
    for k in ("follower", "blind", "naive", "airline"):
        refs[f"reward_{k}"] = round(reward(refs[k], refs["anchor"], refs["floor"]), 4)
    day = datetime.fromisoformat(task["date"])
    otp = args.downloads / "flights" / "work" / f"otp_{day.year}_{day.month}.parquet" if args.downloads else None
    episode = make_episode(task, follower, run, refs, otp)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{task['task_id']}.fixture.json"
    path.write_text(json.dumps(episode, separators=(",", ":")))
    write_index(args.out, path, episode)
    args.small.parent.mkdir(parents=True, exist_ok=True)
    small = trim(episode)
    args.small.write_text(json.dumps(small, indent=1) + "\n")
    (args.small.parent / "index.json").unlink(missing_ok=True)
    write_index(args.small.parent, args.small, small)
    print(f"wrote {path} ({path.stat().st_size / 1e3:.0f} kB), {args.small}")
    print(json.dumps(refs, indent=1))
    return 0


def make_task(inst: dict) -> dict:
    meta = inst["meta"]
    ap = airportsdata.load("IATA")
    w0, w1 = (int(h) * 60 for h in meta["window"].split("-"))
    nominal: dict[str, Counter] = defaultdict(Counter)
    flights = []
    for f in inst["flights"]:
        nominal[f"{f['orig']}:dep"][str(f["sched_dep"] // 60)] += 1
        nominal[f"{f['dest']}:arr"][str(f["sched_arr"] // 60)] += 1
        flights.append({**{k: f[k] for k in ("id", "orig", "dest", "sched_dep", "sched_arr", "block", "pax", "family",
                                              "lb", "dmax")},
                        "flight": f"{f['flight'][:2]} {f['flight'][2:]}",
                        "actual": {**f["actual"], "cancel_code": f["cancel_code"]}})
    caps = {k: {h: v["cap"] for h, v in hours.items()} for k, hours in inst["caps"].items()}
    return {
        "task_id": f"{meta['carrier'].lower()}-{meta['hub'].lower()}-{meta['day'].replace('-', '')}-{w0 // 60:02d}00",
        "split": "eval", "carrier": meta["carrier"], "hub": meta["hub"], "date": meta["day"], "tz": meta["tz"],
        "window": [w0, w1], "start": meta["cutoff_min"], "turn": inst["turn"], "turn_all": meta["turn_p5_all"],
        "dmax": meta["dmax"],
        "airports": {a: {"name": ap[a]["name"] if a in ap else a, "lat": lat, "lon": lon,
                         "tz": ap[a]["tz"] if a in ap else meta["tz"]}
                     for a, (lat, lon) in sorted(inst["coords"].items())},
        "tails": [{k: t[k] for k in ("tail", "family", "seats", "pos", "ready", "end_req", "actual_end")}
                  for t in inst["tails"]],
        "flights": flights, "caps": caps,
        "nominal": {k: dict(sorted(v.items(), key=lambda kv: int(kv[0]))) for k, v in sorted(nominal.items())},
    }


def check_against_research(inst: dict, task: dict, args) -> None:
    """The research's hindsight plan, executed here under its own costs, must cost what its MIP said."""
    found = sorted(args.instance.parent.glob(f"tb_{inst['meta']['day'].replace('-', '')}_{inst['meta']['hub']}_"
                                             f"{inst['meta']['window']}_s1.json"))
    if not found:
        return
    tb = json.loads(found[0].read_text())
    plan = {int(i): {"cancel": bool(v["cancel"]), "not_before": v["dep"]} for i, v in tb["solution"].items()}
    res = execute(task, plan, {f["id"]: f["lb"] for f in task["flights"]}, task["caps"], cancel=600,
                  ferry=(10000, 20))
    print(f"research hindsight {found[0].name}: MIP {tb['total_cost']}, executed here {res['cost']['total']}")


def rolling(inst: dict, task: dict, C, TB, mode: str) -> dict:
    """The follower: the MIP re-solved each watch on the day as known (persistence or blind forecast), flights planned
    to leave before the watch + FREEZE frozen; each watch's MIP plan reduced to cancels and the holds that bind."""
    F = {f["id"]: f for f in inst["flights"]}
    fixed, mip, out = {}, None, {"watches": [], "plans": [], "mip": []}
    t = task["start"]
    while True:
        if mip:
            for i, v in mip.items():
                if i in fixed:
                    continue
                if (v["cancel"] and F[i]["sched_dep"] < t + FREEZE) or (not v["cancel"] and v["dep"] < t + FREEZE):
                    fixed[i] = {"cancel": v["cancel"], "dep": v["dep"]}
        if len(fixed) == len(F):
            break
        lb, caps = C.forecast_lb(inst, t, mode), C.forecast_caps(inst, t, mode)
        res = TB.solve_tb(inst, 1, 60, lb=lb, caps=caps, fixed=fixed, earliest=(t + FREEZE) if mip else 0, lp=False,
                          mip_gap=1e-3)
        mip = res["solution"]
        caps = {f"{a}:{kind}": {str(h): c for h, c in hours.items()} for (a, kind), hours in caps.items()}
        out["plans"].append(minimal(task, mip, lb, caps))
        out["mip"].append(mip)
        out["watches"].append({"t": t, "frozen": sorted(fixed), "mip_cost": res["total_cost"]})
        t += STEP
    print(f"{mode}: {len(out['watches'])} watches")
    return out


def known_lb(task: dict, t: int) -> dict[int, int]:
    return {f["id"]: f["lb"] if f["sched_dep"] <= t + LOOK_LB else 0 for f in task["flights"]}


def known_caps(task: dict, t: int) -> dict[str, dict[str, int]]:
    out = {}
    for key in sorted(set(task["caps"]) | set(task["nominal"])):
        truth, nominal = task["caps"].get(key, {}), task["nominal"].get(key, {})
        hours = sorted({*truth, *nominal}, key=int)
        row = {h: truth.get(h) if 60 * int(h) <= t + LOOK_CAP else nominal.get(h) for h in hours}
        out[key] = {h: v for h, v in row.items() if v is not None}
    return out


def minimal(task: dict, mip: dict, lb: dict, caps: dict) -> dict:
    """A MIP plan as the env takes it: its cancels, plus a hold on each flight that would otherwise leave earlier
    than planned on the planner's own forecast."""
    plan = {i: {"cancel": True, "not_before": None} for i, v in mip.items() if v["cancel"]}
    for _ in range(12):
        res = execute(task, plan, lb, caps)
        early = [i for i, v in mip.items() if not v["cancel"] and i not in plan
                 and res["flights"][i]["status"] == "flown" and res["flights"][i]["dep"] < v["dep"]]
        if not early:
            break
        for i in early:
            plan[i] = {"cancel": False, "not_before": mip[i]["dep"]}
    return plan


def execute(task: dict, plan: dict, lb: dict, caps: dict, cancel: int = CANCEL,
            ferry: tuple[float, float] = (FERRY_FIXED, FERRY_PER_MILE)) -> dict:
    """DESIGN.md 'Execution': flights in order of their earliest candidate departure take the first-ready aircraft of
    their family at their origin, move to the next hour with room when a cap is full, are auto-cancelled past dmax;
    then ferries to the required end positions."""
    F = {f["id"]: f for f in task["flights"]}
    pools: dict[tuple, list] = defaultdict(list)
    for t in task["tails"]:
        pools[(t["family"], t["pos"])].append([t["ready"], t["tail"]])
    used: Counter = Counter()
    out: dict[int, dict] = {}
    heap = []
    for i, f in F.items():
        p = plan.get(i) or {}
        if p.get("cancel"):
            out[i] = {"id": i, "status": "cancelled", "tail": None, "dep": None, "arr": None, "arr_delay": None}
            continue
        heapq.heappush(heap, (max(f["sched_dep"] + lb[i], p.get("not_before") or 0), i))

    def full(airport: str, kind: str, minute: int) -> bool:
        cap = caps.get(f"{airport}:{kind}", {}).get(str(minute // 60))
        return cap is not None and used[(airport, kind, minute // 60)] >= cap

    while heap:
        cand, i = heapq.heappop(heap)
        f = F[i]
        last = f["sched_dep"] + f["dmax"]
        pool = pools[(f["family"], f["orig"])]
        if not pool:
            later = max(cand + 1, heap[0][0]) if heap else last + 1
            if later > last:
                out[i] = {"id": i, "status": "auto_cancelled", "tail": None, "dep": None, "arr": None,
                          "arr_delay": None}
            else:
                heapq.heappush(heap, (later, i))
            continue
        k = min(range(len(pool)), key=lambda j: (pool[j][0], pool[j][1]))
        dep = max(cand, pool[k][0])
        if dep <= last and (full(f["orig"], "dep", dep) or full(f["dest"], "arr", dep + f["block"])):
            dep = 60 * (dep // 60 + 1)
            while dep <= last and (full(f["orig"], "dep", dep) or full(f["dest"], "arr", dep + f["block"])):
                dep += 60
            if dep <= last:
                heapq.heappush(heap, (dep, i))
                continue
        if dep > last:
            out[i] = {"id": i, "status": "auto_cancelled", "tail": None, "dep": None, "arr": None, "arr_delay": None}
            continue
        _, tail = pool.pop(k)
        arr = dep + f["block"]
        used[(f["orig"], "dep", dep // 60)] += 1
        used[(f["dest"], "arr", arr // 60)] += 1
        turn = task["turn"].get(f["family"], task["turn_all"])
        pools[(f["family"], f["dest"])].append([arr + turn, tail])
        out[i] = {"id": i, "status": "flown", "tail": tail, "dep": dep, "arr": arr,
                  "arr_delay": max(0, arr - f["sched_arr"])}
    ferries = reposition(task, pools, ferry)
    pax = {i: F[i]["pax"] for i in F}
    cost = {"pax_delay": sum(pax[i] * r["arr_delay"] for i, r in out.items() if r["status"] == "flown"),
            "cancel": cancel * sum(pax[i] for i, r in out.items() if r["status"] != "flown"),
            "ferry": round(sum(x["cost"] for x in ferries))}
    cost["total"] = sum(cost.values())
    return {"flights": out, "ferries": ferries, "cost": cost}


def reposition(task: dict, pools: dict, ferry: tuple[float, float]) -> list[dict]:
    """The cheapest end-of-day ferries per family: the aircraft left over at each station assigned to the required
    positions short of one, by minimum total cost."""
    need = Counter((t["family"], t["end_req"]) for t in task["tails"])
    out = []
    for fam in sorted({t["family"] for t in task["tails"]}):
        stations = {a for f, a in need if f == fam} | {a for f, a in pools if f == fam and pools[(f, a)]}
        spare, short = [], []
        for a in sorted(stations):
            n = len(pools.get((fam, a), [])) - need[(fam, a)]
            spare += [(a, tail) for _, tail in sorted(pools[(fam, a)])[-n:]] if n > 0 else []
            short += [a] * max(0, -n)
        if not spare:
            continue
        costs = [[ferry[0] + ferry[1] * miles(task, a, b) for b in short] for a, _ in spare]
        for r, c in enumerate(assignment(costs)):
            out.append({"family": fam, "tail": spare[r][1], "orig": spare[r][0], "dest": short[c],
                        "cost": round(costs[r][c])})
    return out


def assignment(cost: list[list[float]]) -> list[int]:
    """Hungarian algorithm on a square cost matrix: the column assigned to each row."""
    n = len(cost)
    u, v, match, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        match[0], j0 = i, 0
        minv, used = [math.inf] * (n + 1), [False] * (n + 1)
        while match[j0]:
            used[j0], i0, delta, j1 = True, match[j0], math.inf, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
        while j0:
            j1 = way[j0]
            match[j0], j0 = match[j1], j1
    rows = [0] * n
    for j in range(1, n + 1):
        rows[match[j] - 1] = j - 1
    return rows


def miles(task: dict, a: str, b: str) -> float:
    p, q = task["airports"][a], task["airports"][b]
    la1, la2, dl = math.radians(p["lat"]), math.radians(q["lat"]), math.radians(q["lon"] - p["lon"])
    c = math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(dl)
    return 3958.8 * math.acos(max(-1.0, min(1.0, c)))


def airline_plan(task: dict) -> dict:
    return {f["id"]: {"cancel": True, "not_before": None} if f["actual"]["cancelled"]
            else {"cancel": False, "not_before": f["actual"]["dep"]} for f in task["flights"]}


def reward(cost: float, anchor: float, floor: float) -> float:
    g = max(0, cost - anchor) / (anchor - floor + 0.02 * anchor)
    return 0.2 + 0.8 * math.exp(-g / 0.5)


def make_episode(task: dict, follower: dict, run: dict, refs: dict, otp: Path | None) -> dict:
    F = {f["id"]: f for f in task["flights"]}
    news = task["news"]
    watches, steps, prev = [], [], {}
    decided: dict[int, int] = {}
    for k, (w, plan) in enumerate(zip(follower["watches"], follower["plans"], strict=True)):
        t = w["t"]
        lo = -10**9 if k == 0 else follower["watches"][k - 1]["t"]
        arrived = [n for n in news if lo < n["at"] <= t]
        frozen = set(w["frozen"])
        changes = []
        for i in sorted(set(plan) | set(prev)):
            if i in frozen:
                continue
            a, b = prev.get(i), plan.get(i)
            if a == b:
                continue
            changes.append(change(F[i], a, b))
            decided[i] = t
        lb, caps = known_lb(task, t), known_caps(task, t)
        proj = execute(task, plan, lb, caps)
        rows = [row(F[i], r, plan.get(i)) for i, r in sorted(proj["flights"].items())]
        counters = tally(task, rows)
        watches.append({"t": t, "news": [strip(n) for n in arrived], "caps_known": caps, "changes": changes,
                        "est_cost": proj["cost"]["total"], "cost_known": proj["cost"], "counters": counters,
                        "frozen": len(frozen), "projected": rows})
        steps += transcript(task, t, arrived, changes, proj, counters, k, len(follower["watches"]))
        prev = plan
    final = follower["plans"][-1]
    executed = []
    for i, r in sorted(run["flights"].items()):
        e = row(F[i], r, final.get(i))
        e["decided"] = decided.get(i, task["start"]) if r["status"] == "cancelled" else (
            F[i]["sched_dep"] + F[i]["dmax"] if r["status"] == "auto_cancelled" else decided.get(i))
        executed.append(e)
    radar_index = ROOT / "cache" / "radar" / task["task_id"] / "index.json"
    radar = None
    if radar_index.is_file():
        r = json.loads(radar_index.read_text())
        radar = {"bounds": r["bounds"], "projection": r["projection"], "step": r["step"],
                 "frames": [{"t": f["t"], "url": f"../radar/{task['task_id']}/{f['url']}"} for f in r["frames"]],
                 "attribution": r["attribution"]}
    twin_index = ROOT / "cache" / "twin" / "index.json"
    twin = None
    if twin_index.is_file() and task["hub"] in json.loads(twin_index.read_text()):
        tw = json.loads(twin_index.read_text())[task["hub"]]
        twin = {"hub": task["hub"], "url": f"../twin/{tw['url']}", "sha256": tw["sha256"],
                "attribution": "Hub twin " + tw["attribution"]}
    keep = ("task_id", "carrier", "hub", "date", "tz", "window", "start", "turn", "turn_all", "dmax", "airports",
            "tails")
    return {
        "version": 1,
        "task": {k: task[k] for k in keep},
        "model": "follower", "model_label": "Reference follower (MIP re-solved each watch)", "run": "fixture",
        "reward": refs["reward_follower"], "cost": run["cost"], "reference": refs,
        "schedule": [{k: f[k] for k in ("id", "flight", "orig", "dest", "sched_dep", "sched_arr", "family", "pax")}
                     | {"tail": f["actual"]["tail"], "airline": airline(f)} for f in task["flights"]],
        "executed": executed, "ferries": run["ferries"], "frozen": morning(task, otp),
        "watches": watches, "steps": steps, "news": [strip(n) for n in news],
        "caps": task["caps"], "nominal": task["nominal"], "radar": radar, "twin": twin,
        "sources": {"flights": "BTS On-Time Reporting Carrier On-Time Performance, June 2023",
                    "advisories": "FAA ATCSCC advisories database (fly.faa.gov)",
                    "weather": "NWS TAFs and METARs via the Iowa Environmental Mesonet",
                    "note": "Fixture built by scripts/fixture_episode.py from the AirOps research instance; the "
                            "planner is the reference follower, not a model."},
    }


def change(f: dict, a: dict | None, b: dict | None) -> dict:
    a = a or {"cancel": False, "not_before": None}
    b = b or {"cancel": False, "not_before": None}
    if b["cancel"] and not a["cancel"]:
        action = "cancel"
    elif a["cancel"] and not b["cancel"]:
        action = "restore"
    elif b["not_before"] is None:
        action = "release"
    else:
        action = "hold"
    return {"id": f["id"], "flight": f["flight"], "orig": f["orig"], "dest": f["dest"], "action": action,
            "cancel": b["cancel"], "not_before": b["not_before"], "was": a}


def row(f: dict, r: dict, p: dict | None) -> dict:
    nb = (p or {}).get("not_before")
    held = r["status"] == "flown" and nb is not None and nb > f["sched_dep"] + f["lb"]
    return {"id": f["id"], "tail": r["tail"], "status": r["status"], "dep": r["dep"], "arr": r["arr"],
            "arr_delay": r["arr_delay"], "not_before": nb, "held": held}


def tally(task: dict, rows: list[dict]) -> dict:
    pax = {f["id"]: f["pax"] for f in task["flights"]}
    flown = [r for r in rows if r["status"] == "flown"]
    return {"cancelled": sum(r["status"] == "cancelled" for r in rows),
            "auto_cancelled": sum(r["status"] == "auto_cancelled" for r in rows),
            "held": sum(r["held"] for r in rows), "delayed": sum(r["arr_delay"] >= LATE for r in flown),
            "pax_delay": sum(pax[r["id"]] * r["arr_delay"] for r in flown),
            "pax_disrupted": sum(pax[r["id"]] for r in rows if r["status"] != "flown" or r["arr_delay"] >= LATE)}


def transcript(task, t, arrived, changes, proj, counters, k, n) -> list[dict]:
    kinds = Counter(a["kind"] for a in arrived)
    news = ", ".join(f"{v} {NOUNS[kk][v > 1]}" for kk, v in sorted(kinds.items())) or "no news"
    steps = [{"t": t, "tool": "get_situation", "args": {},
              "result": f"Watch {k + 1} at {hhmm(t)}: {news}. {counters['cancelled']} cancelled, "
                        f"{counters['held']} held; cost as known {proj['cost']['total']:,} pax-min."}]
    if changes:
        plan = {str(c["id"]): {"cancel": c["cancel"], "not_before": c["not_before"]} for c in changes}
        steps.append({"t": t, "tool": "submit_plan", "args": {"plan": plan},
                      "result": {"accepted": len(changes), "est_cost": proj["cost"]["total"], **counters}})
    if k < n - 1:
        steps.append({"t": t, "tool": "advance", "args": {}, "result": f"Clock moves to {hhmm(t + STEP)}."})
    return steps


def airline(f: dict) -> dict:
    a = f["actual"]
    return {"cancelled": bool(a["cancelled"]), "dep": a["dep"], "arr": a["arr"], "cancel_code": a["cancel_code"]}


def morning(task: dict, otp: Path | None) -> list[dict]:
    """Legs the in-scope tails flew before the first watch, from BTS (empty without the parquet)."""
    if not otp or not otp.is_file():
        return []
    df = pd.read_parquet(otp, columns=["FlightDate", "Reporting_Airline", "Tail_Number",
                                       "Flight_Number_Reporting_Airline", "Origin", "Dest", "Cancelled",
                                       "sched_dep_utc", "sched_arr_utc", "act_dep_utc", "act_arr_utc"])
    tails = {t["tail"] for t in task["tails"]}
    x = df[(df.FlightDate == task["date"]) & (df.Reporting_Airline == task["carrier"]) & df.Tail_Number.isin(tails)
           & (df.Cancelled == 0)]
    base = datetime.fromisoformat(task["date"]).replace(tzinfo=ZoneInfo(task["tz"]))
    m = {c: ((x[c] - base).dt.total_seconds() // 60) for c in ("sched_dep_utc", "sched_arr_utc", "act_dep_utc",
                                                                "act_arr_utc")}
    out = []
    for idx, r in x.iterrows():
        dep, arr = int(m["act_dep_utc"][idx]), int(m["act_arr_utc"][idx])
        if dep < task["start"]:
            out.append({"flight": f"{task['carrier']} {int(r.Flight_Number_Reporting_Airline)}", "orig": r.Origin,
                        "dest": r.Dest, "sched_dep": int(m["sched_dep_utc"][idx]),
                        "sched_arr": int(m["sched_arr_utc"][idx]), "dep": dep, "arr": arr, "tail": r.Tail_Number})
    return sorted(out, key=lambda f: (f["tail"], f["dep"]))


def advisories(task: dict, root: Path) -> list[dict]:
    """ATCSCC advisories for the task's airports and the New York metro, as issued, in hub-local minutes."""
    tz = ZoneInfo(task["tz"])
    base = datetime.fromisoformat(task["date"]).replace(tzinfo=tz)
    out = []
    for day in (base.astimezone(UTC).date(), (base + timedelta(days=1)).astimezone(UTC).date()):
        path = root / f"{day:%Y-%m-%d}" / "_advisories.json"
        if not path.is_file():
            continue
        for r in json.loads(path.read_text()):
            head = r.get("header") or ""
            parts = head.split()
            if len(parts) < 5 or not r.get("signature"):
                continue
            element = parts[3]
            airport = element.split("/")[0]
            if airport not in task["airports"] and airport not in METRO:
                continue
            signed = datetime.strptime(r["signature"], "%y/%m/%d %H:%M").replace(tzinfo=UTC)
            at = int((signed - base).total_seconds() // 60)
            if not task["start"] - 420 <= at <= task["window"][1] + 360:
                continue
            title = " ".join(parts[5:])
            out.append({"id": f"adv-{day:%m%d}-{r['advn']:03d}", "at": at, "kind": "advisory",
                        "speaker": "FAA Command Center", "text": f"{head}\n{r.get('message', '').strip()}",
                        "data": {"element": element, "airport": airport, "title": title, "program": program(title),
                                 "effective": r.get("effective"), "url": r["url"]}})
    return out


def program(title: str) -> str:
    t = title.upper()
    if "GROUND STOP" in t or " GS" in f" {t}":
        return "gs_cnx" if "CNX" in t or "CANCEL" in t else "gs"
    if "GROUND DELAY" in t:
        return "gdp_cnx" if "CNX" in t else "gdp_proposed" if "PROPOSED" in t else "gdp"
    return "other"


def weather(task: dict, downloads: Path | None) -> list[dict]:
    """The hub's TAFs and METARs as issued (IEM), cached under cache/weather/; KEWR samples if offline."""
    station = "K" + task["hub"]
    cache = ROOT / "cache" / "weather" / task["task_id"]
    cache.mkdir(parents=True, exist_ok=True)
    tz = ZoneInfo(task["tz"])
    base = datetime.fromisoformat(task["date"]).replace(tzinfo=tz)
    s, e = (base + timedelta(minutes=task["start"] - 420)).astimezone(UTC), (base + timedelta(days=1, hours=6))
    e = e.astimezone(UTC)
    taf_url = (f"{IEM}/afos/retrieve.py?pil=TAF{task['hub']}&sdate={s:%Y-%m-%dT%H:%MZ}&edate={e:%Y-%m-%dT%H:%MZ}"
               f"&fmt=text&limit=9999")
    metar_url = (f"{IEM}/request/asos.py?station={task['hub']}&data=metar&sts={s:%Y-%m-%dT%H:%MZ}"
                 f"&ets={e:%Y-%m-%dT%H:%MZ}&tz=Etc/UTC&format=onlycomma&report_type=3&report_type=4")
    taf_text = fetch(taf_url, cache / f"{station}_taf.txt")
    metar_text = fetch(metar_url, cache / f"{station}_metar.csv")
    if (not taf_text or not metar_text) and downloads:
        station = "KEWR"
        taf_text = (downloads / "weather/data/taf/afos_tafewr_20230626_27.txt").read_text()
        metar_text = (downloads / "weather/data/metar/kewr_20230626_28.csv").read_text()
    out = []
    for block in (taf_text or "").replace("\r", "").split("\x01"):
        lines = [ln.rstrip() for ln in block.strip().splitlines() if ln.strip()]
        start = next((k for k, ln in enumerate(lines) if ln.startswith(station + " ")), None)
        if start is None:
            continue
        stamp = lines[start].split()[1]
        at = minute_of(base, s, stamp)
        if at is None:
            continue
        body = "\n".join(lines[start - 1 if lines[start - 1].startswith("TAF") else start:]).rstrip("=")
        out.append({"id": f"taf-{stamp}", "at": at, "kind": "taf", "speaker": "NWS New York (OKX)", "text": body,
                    "data": {"station": station}})
    for r in csv.DictReader(io.StringIO(metar_text or "")):
        if not r.get("metar") or r.get("station", "").lstrip("K") != station[1:]:
            continue
        valid = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        out.append({"id": f"metar-{valid:%d%H%M}", "at": int((valid - base).total_seconds() // 60), "kind": "metar",
                    "speaker": f"{station} observation", "text": r["metar"], "data": {"station": station}})
    return [n for n in out if task["start"] - 420 <= n["at"] <= task["window"][1] + 360]


def fetch(url: str, path: Path) -> str | None:
    if path.is_file() and path.stat().st_size:
        return path.read_text()
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
    except OSError:
        return None
    path.write_text(text)
    return text


def minute_of(base: datetime, near: datetime, stamp: str) -> int | None:
    """A TAF's ddhhmmZ issue time in minutes from the hub-local midnight of ``base``."""
    try:
        day, hour, minute = int(stamp[:2]), int(stamp[2:4]), int(stamp[4:6])
    except ValueError:
        return None
    for month_shift in (0, 1):
        d = near.replace(day=1) + timedelta(days=31 * month_shift)
        try:
            when = d.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            continue
        if abs((when - near).total_seconds()) < 3 * 86400:
            return int((when - base).total_seconds() // 60)
    return None


def strip(n: dict) -> dict:
    return {k: n[k] for k in ("id", "at", "kind", "speaker", "text", "data") if k in n}


def hhmm(m: int) -> str:
    return f"{m // 60 % 24:02d}:{m % 60:02d}" + ("+1" if m >= 1440 else "")


def write_index(out: Path, path: Path, episode: dict) -> None:
    index_path = out / "index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {"version": 1, "episodes": []}
    entry = {"url": path.name, "task_id": episode["task"]["task_id"], "hub": episode["task"]["hub"],
             "date": episode["task"]["date"], "model": episode["model"], "model_label": episode.get("model_label"),
             "run": episode["run"], "reward": episode["reward"], "cost": episode["cost"]["total"],
             "anchor": episode["reference"]["anchor"]}
    index["episodes"] = [e for e in index["episodes"] if e["url"] != path.name] + [entry]
    index_path.write_text(json.dumps(index, indent=1) + "\n")


def trim(episode: dict) -> dict:
    """A small, self-contained copy: six aircraft (and those their flights touch), their flights, the first three
    watches, no radar or twin."""
    e = copy.deepcopy(episode)
    first = {t["tail"] for t in e["task"]["tails"][:6]}
    flown_on = {x["id"]: x["tail"] for x in e["executed"]}
    keep = {s["id"] for s in e["schedule"] if s["tail"] in first or flown_on[s["id"]] in first}
    tails = first | {flown_on[i] for i in keep if flown_on[i]} | {s["tail"] for s in e["schedule"] if s["id"] in keep}
    e["task"]["tails"] = [t for t in e["task"]["tails"] if t["tail"] in tails]
    used = {a for s in e["schedule"] if s["id"] in keep for a in (s["orig"], s["dest"])} | {e["task"]["hub"]}
    e["task"]["airports"] = {a: v for a, v in e["task"]["airports"].items() if a in used}
    e["schedule"] = [s for s in e["schedule"] if s["id"] in keep]
    e["executed"] = [x for x in e["executed"] if x["id"] in keep]
    e["frozen"] = [x for x in e["frozen"] if x["tail"] in tails]
    e["ferries"] = []
    e["watches"] = e["watches"][:3]
    last = e["watches"][-1]["t"]
    for w in e["watches"]:
        w["projected"] = [x for x in w["projected"] if x["id"] in keep]
        w["changes"] = [c for c in w["changes"] if c["id"] in keep]
        w["news"] = w["news"][:4]
    e["steps"] = [s for s in e["steps"] if s["t"] <= last]
    e["news"] = [n for w in e["watches"] for n in w["news"]]
    e["radar"], e["twin"] = None, None
    e["run"] = "fixture-small"
    return e


if __name__ == "__main__":
    sys.exit(main())
