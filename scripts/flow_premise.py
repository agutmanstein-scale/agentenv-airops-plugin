"""The two-seat premise check (the Command Center model): the draw, the world and its guards (part 1), the policies and
the pre-registered run (part 2). No LLM is called.

    python scripts/flow_premise.py draw --out DIR
    python scripts/flow_premise.py pin --out DIR [--workers 8]
    python scripts/flow_premise.py calibrate --out DIR [--workers 12]
    python scripts/flow_premise.py worlds --out DIR [--workers 12]
    python scripts/flow_premise.py verify --out DIR [--workers 12]
    python scripts/flow_premise.py policies --out DIR [--workers 12]
    python scripts/flow_premise.py c3 --out DIR [--workers 12] [--prove-s 3600] [--watch-s 1800]
    python scripts/flow_premise.py summarize --out DIR

draw writes DIR/days.json: the 20 day-hubs (seed 2026) and the train day-hubs, from step 0's built candidates. It runs
before anything else is computed, and never again. pin fetches and pins what the world adds to step 0's inputs: the
ATR 2024 and FAA Order 8400.9 PDFs, the SPECIs of the 13 months and the radar of every drawn and train date.
calibrate reads train day-hubs only: the medians, the reductions and both guards, into DIR/calibration.json,
DIR/guards.json, DIR/hours.jsonl and DIR/airborne.jsonl. worlds builds the 20 drawn day-hubs' worlds with that
calibration and writes their sizes, capacity and naive day to DIR/worlds.json (no policy is run).

verify reads train day-hubs only: the TAF verification table and the class capacities, into DIR/verify.json. policies
prices naive, H, LB, the FAA's programs as they ran, the split row, reactive and TAF-based on each drawn day-hub, a
JSON each under DIR/raw/. c3 plays JetBlue's desk against the TAF-based programs on each drawn day-hub: joint naive,
joint hindsight and the four channel settings, a JSON per run under DIR/raw/ as it finishes, a finished one kept on a
rerun. summarize computes C1-C3 as pre-registered into DIR/premise2.jsonl and DIR/premise2_draft.md.
"""

import argparse
import json
import statistics
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from agentenv_airops.build import pack
from agentenv_airops.flow import (
    bars,
    calibrate,
    days,
    demand,
    desk,
    faa,
    forecast,
    hindsight,
    joint,
    policies,
    sources,
    taf,
    world,
)
from agentenv_airops.flow.capacity import Reductions

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
STEP0 = ROOT / "cache" / "step0" / "full"
C3_RUNS = ("hindsight", "off", "a", "b", "ab", "naive")
POLICIES = ("naive", "hindsight", "faa", "split", "reactive", "taf")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="stage", required=True)
    d = sub.add_parser("draw")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--seed", type=int, default=days.SEED)
    p = sub.add_parser("pin")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=8)
    for name in ("calibrate", "worlds", "verify", "policies", "c3", "summarize"):
        c = sub.add_parser(name)
        c.add_argument("--out", type=Path, required=True)
        c.add_argument("--workers", type=int, default=12)
        if name == "c3":
            c.add_argument("--prove-s", type=float, default=3600.0)
            c.add_argument("--watch-s", type=float, default=1800.0)
    args = ap.parse_args()
    if args.stage == "draw":
        draw(args.out, args.seed)
    elif args.stage == "pin":
        pin(args.out, args.workers)
    elif args.stage == "calibrate":
        fit(args.out, args.workers)
    elif args.stage == "worlds":
        worlds(args.out, args.workers)
    elif args.stage == "verify":
        verify(args.out, args.workers)
    elif args.stage == "policies":
        run_policies(args.out, args.workers)
    elif args.stage == "c3":
        run_c3(args.out, args.workers, args.prove_s, args.watch_s)
    else:
        summarize(args.out)


def draw(out: Path, seed: int) -> None:
    path = out / "days.json"
    if path.exists():
        raise SystemExit(f"{path} exists: the draw runs once")
    source = STEP0 / "candidates.jsonl"
    keys, built = days.read(source)
    doc = days.write(path, days.make(keys, built, seed), seed, str(source.relative_to(ROOT)))
    print(json.dumps({k: doc[k] for k in ("quotas", "eligible", "held_out_events")}, indent=1))
    print("drawn", [(r["date"], r["hub"], r["season"], r["event"]) for r in doc["drawn"]])
    print("train", len(doc["train"]))


def pin(out: Path, workers: int) -> None:
    drawn, train = days.load(out / "days.json")
    print("documents", [str(p.relative_to(INPUTS)) for p in sources.pin_documents(INPUTS)], flush=True)
    print("specis", len(sources.fetch_specis(INPUTS, pack.MONTHS)), flush=True)
    dates = sorted({d for d, _ in drawn + train})
    paths = sources.fetch_radar(INPUTS, dates, workers)
    missing = sum(f["shares"] is None for p in paths for f in json.loads(p.read_text())["frames"])
    print("radar", len(paths), "dates", missing, "bins without a frame", flush=True)


def build(keys: list[tuple[str, str]], med: demand.Medians, r: Reductions, workers: int) -> list[world.World]:
    jobs = [(INPUTS, DERIVED, d, hub, med, r) for d, hub in keys]
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(world.build_job, jobs))


def fit(out: Path, workers: int) -> None:
    clock = {}
    t = time.time()
    _, train = days.load(out / "days.json")
    med = demand.medians(INPUTS, DERIVED, [d for d, _ in train])
    clock["medians_s"] = round(time.time() - t, 1)
    t = time.time()
    built = build(train, med, Reductions(), workers)
    clock["train_worlds_s"] = round(time.time() - t, 1)
    t = time.time()
    route_month = calibrate.route_months(built)
    base = [calibrate.baselines(w, route_month) for w in built]
    rows = [h for w, b in zip(built, base, strict=True) for h in calibrate.hours(w, b)]
    found = calibrate.fit(rows)
    r = found["reductions"]
    cap = calibrate.guard_capacity(rows, r)
    fitted = [calibrate.with_reductions(w, r) for w in built]
    air_rows = [h for w, b in zip(fitted, base, strict=True) for h in calibrate.airborne_hours(w, b)]
    air = calibrate.guard_airborne(air_rows)
    clock["calibrate_and_guards_s"] = round(time.time() - t, 1)
    info = {k: v for k, v in found.items() if k != "reductions"}
    calibrate.save(out / "calibration.json", med, r, {"fit": info, "train_day_hubs": len(train),
                                                       "sample": calibrate.summary(rows),
                                                       "train_capacity": calibrate.train_capacity(fitted),
                                                       "timings": clock})
    guards = {"capacity": cap, "airborne": air, "reductions": asdict(r), "fit": info,
              "pass": cap["pass"] and air["pass"], "timings": clock}
    (out / "guards.json").write_text(json.dumps(guards, indent=1) + "\n")
    (out / "hours.jsonl").write_text("".join(json.dumps({**asdict(h), "capacity": round(h.capacity(r), 2)}) + "\n"
                                             for h in rows))
    (out / "airborne.jsonl").write_text("".join(json.dumps(h) + "\n" for h in air_rows))
    print(json.dumps(guards, indent=1), flush=True)


def worlds(out: Path, workers: int) -> None:
    drawn, _ = days.load(out / "days.json")
    med, r = calibrate.load(out / "calibration.json")
    t = time.time()
    built = build(drawn, med, r, workers)
    build_s = time.time() - t
    rows = []
    for w in built:
        t = time.time()
        naive = world.evaluate(w)
        took = time.time() - t
        a = w.arrivals
        bts = [x for x in a if not x.unseen]
        rows.append({"date": w.date, "hub": w.hub, "bts": len(bts), "cancelled": sum(x.cancelled for x in bts),
                     "unseen": len(a) - len(bts), "arrivals": len(a),
                     "exempt_bts": sum(x.exempt and not x.cancelled for x in bts),
                     "controllable": sum(not x.exempt and not x.cancelled for x in bts),
                     "noncontiguous": sum(x.noncontiguous for x in bts), "airborne_at_t0": sum(x.d0 < 0 for x in bts),
                     "fallback_aircraft": sum(x.source != "T-100 route-month" for x in bts),
                     "scale": round(w.scale, 3), "landings_offered_day": sum(w.caps[:96]),
                     "departures_day": round(sum(w.departures[:24]), 1),
                     "storm_bins": sum(b.storm > 0 for b in w.timeline[:96]),
                     "naive": {k: naive.totals[k] for k in ("system", "ground", "air", "pax", "unseen", "diversions",
                                                            "held_minutes", "cancelled")},
                     "naive_bands": world.bands(w), "evaluate_s": round(took, 4)})
    doc = {"build_s": round(build_s, 1), "evaluate_s_median": statistics.median(r["evaluate_s"] for r in rows),
           "reductions": asdict(r), "days": rows}
    (out / "worlds.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(doc, indent=1), flush=True)



def verify(out: Path, workers: int) -> None:
    _, train = days.load(out / "days.json")
    med, r = calibrate.load(out / "calibration.json")
    t = time.time()
    with ProcessPoolExecutor(workers) as pool:
        got = list(pool.map(verify_job, [(d, hub, med, r) for d, hub in train]))
    rows = [forecast.Row(**x) for g in got for x in g[0]]
    tab = forecast.table(rows, [x for g in got for x in g[1]])
    summary = {}
    for hub in ("JFK", "BOS"):
        mine = [x for x in rows if x.hub == hub]
        summary[hub] = {"pairs": len(mine), "hit_rate": sum(x.prevailing == x.observed for x in mine) / len(mine),
                        "confusion": sorted([*k, v] for k, v in Counter((x.prevailing, x.observed)
                                                                         for x in mine).items()),
                        "with_alternative": sum(x.alternative is not None for x in mine),
                        "pooling": dict(Counter(tab.dist(hub, x.prevailing, x.alternative, x.lead)[1] for x in mine)),
                        "class_capacity": tab.capacity[hub]}
    doc = {"train_day_hubs": len(train), "seconds": round(time.time() - t, 1), "summary": summary,
           "table": tab.to_dict()}
    (out / "verify.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps(summary, indent=1), flush=True)


def verify_job(job: tuple) -> tuple[list[dict], list[tuple]]:
    d, hub, med, r = job
    w = world.build(INPUTS, DERIVED, d, hub, med, r)
    return [asdict(x) for x in forecast.sample(w, taf.load(INPUTS, hub, d))], [(hub, c, v)
                                                                              for c, v in forecast.capacities(w)]


def forecasts(out: Path) -> tuple[forecast.Forecast, forecast.Forecast]:
    """Reactive and TAF-based, from the calibration's train capacity and the verification table."""
    tab = forecast.Table.from_dict(json.loads((out / "verify.json").read_text())["table"])
    train = json.loads((out / "calibration.json").read_text())["train_capacity"]
    return forecast.reactive(train), forecast.taf_based(lambda w: taf.load(INPUTS, w.hub, w.date), tab)


def run_policies(out: Path, workers: int) -> None:
    drawn, _ = days.load(out / "days.json")
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    t = time.time()
    with ProcessPoolExecutor(workers) as pool:
        for res in pool.map(policy_job, [(d, hub, out) for d, hub in drawn]):
            (raw / f"policies_{res['date']}_{res['hub']}.json").write_text(json.dumps(res, indent=1) + "\n")
            print(f"[{time.time() - t:6.0f}s] {res['date']} {res['hub']}",
                  {k: res[k]["system"] for k in POLICIES}, "LB", round(res["lb"]), flush=True)


def policy_job(job: tuple) -> dict:
    d, hub, out = job
    med, r = calibrate.load(out / "calibration.json")
    reactive, taf_based = forecasts(out)
    clock = {}
    t = time.time()
    w = world.build(INPUTS, DERIVED, d, hub, med, r)
    clock["world_s"] = round(time.time() - t, 2)
    out = {"date": d, "hub": hub}
    t = time.time()
    h = hindsight.solve(w)
    clock["hindsight_s"] = round(time.time() - t, 2)
    t = time.time()
    lb = hindsight.bound(w)
    clock["lb_s"] = round(time.time() - t, 2)
    runs = {"naive": ({}, None), "hindsight": (h.releases, None), "faa": (faa.replay(w), None),
            "split": (faa.replay(w, capacity=True), None)}
    for name, fc in (("reactive", reactive), ("taf", taf_based)):
        t = time.time()
        runs[name] = policies.run(w, fc)
        clock[f"{name}_s"] = round(time.time() - t, 2)
    for name, (rel, issues) in runs.items():
        out[name] = priced(w, rel)
        if issues is not None:
            out[name]["issues"] = len(issues)
            out[name]["program_hours"] = sum(1 for i in issues if i.end is not None)
    out["hindsight"]["columns"] = h.columns
    out["lb"] = lb
    out["clock"] = clock
    return out


def priced(w: world.World, releases: dict) -> dict:
    got = world.evaluate(w, releases)
    t = got.totals
    held = [f.release - w.arrivals[i].d0 for i, f in got.flights.items() if f.release > w.arrivals[i].d0]
    return {**{k: t[k] for k in ("system", "ground", "air", "pax", "unseen", "diversions", "held_minutes")},
            "bands": world.bands(w, releases), "ground_held": len(held), "ground_minutes": sum(held),
            "max_ground": max(held, default=0)}


def run_c3(out: Path, workers: int, prove_s: float, watch_s: float) -> None:
    drawn, _ = days.load(out / "days.json")
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    jobs = [(d, hub, kind, out, prove_s, watch_s) for d, hub in drawn for kind in C3_RUNS
            if not (raw / f"c3_{d}_{hub}_{kind}.json").exists()]
    jobs.sort(key=lambda j: C3_RUNS.index(j[2]))
    print(f"{len(jobs)} jobs", flush=True)
    t = time.time()
    with ProcessPoolExecutor(workers) as pool:
        futures = [pool.submit(c3_job, j) for j in jobs]
        for n, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            (raw / f"c3_{res['date']}_{res['hub']}_{res['kind']}.json").write_text(json.dumps(res, indent=1) + "\n")
            print(f"[{time.time() - t:6.0f}s] {n}/{len(jobs)} {res['date']} {res['hub']} {res['kind']} "
                  f"{res['seconds']}s joint {res.get('joint')} {res.get('error', '')[:160]}", flush=True)


def c3_job(job: tuple) -> dict:
    d, hub, kind, out, prove_s, watch_s = job
    t = time.time()
    res = {"date": d, "hub": hub, "kind": kind}
    try:
        med, r = calibrate.load(out / "calibration.json")
        worlds_ = {h: world.build(INPUTS, DERIVED, d, h, med, r) for h in ("JFK", "BOS")}
        task = desk.tasks(INPUTS, DERIVED, d, hub, worlds_)
        lk = desk.link(worlds_[hub], task, med)
        res |= {"task_id": task.task_id, "task_flights": len(task.flights), "into_hub": len(lk.arrival),
                "added_past_midnight": len(lk.world.arrivals) - len(worlds_[hub].arrivals)}
        if kind == "naive":
            res |= desk.naive(lk)
        elif kind == "hindsight":
            res |= joint.solve(lk, prove_s)
        else:
            _, taf_based = forecasts(out)
            played = desk.play(lk, taf_based, report=kind in ("a", "ab"), honest=kind in ("b", "ab"), watch_s=watch_s)
            res |= played.result
            res["watches"] = played.watches
            res["programs"] = [{"at": i.at, "held": len(i.program), "end": i.end, "honest": i.honest}
                               for i in played.issues]
            res["plan"] = {str(k): v for k, v in sorted(played.plan.items())}
    except desk.Timeout as e:
        res |= {"timeout": True, "error": str(e)}
    except Exception as e:
        res |= {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    res["seconds"] = round(time.time() - t, 1)
    return res


def summarize(out: Path) -> None:
    drawn, _ = days.load(out / "days.json")
    raw = out / "raw"
    guards = json.loads((out / "guards.json").read_text())
    rows = []
    for d, hub in drawn:
        pol = json.loads((raw / f"policies_{d}_{hub}.json").read_text())
        costs = {k: pol[k]["system"] for k in POLICIES} | {"lb": pol["lb"]}
        runs = {k: json.loads((raw / f"c3_{d}_{hub}_{k}.json").read_text()) for k in C3_RUNS}
        rows.append({"date": d, "hub": hub, "season": pack_season(d), "costs": costs,
                     "diversions": {k: pol[k]["diversions"] for k in POLICIES},
                     "bands": {k: pol[k]["bands"] for k in POLICIES},
                     "ground": {k: {x: pol[k][x] for x in ("ground_held", "ground_minutes", "max_ground")}
                                for k in POLICIES},
                     "c1": bars.c1(costs), "c2": bars.c2(costs), "c3": bars.c3(runs),
                     "joint": {k: {x: runs[k].get(x) for x in ("joint", "others", "jetblue", "diversions",
                                                                  "cancelled_by_desk", "timeout", "proven", "bound",
                                                                  "gap_pct", "wall_s", "exact", "converged",
                                                                  "rounds", "seconds", "error")}
                               for k in C3_RUNS}})
    verdict = bars.tally(rows, guards["pass"])
    (out / "premise2.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows)
                                        + json.dumps({"verdict": verdict, "guards": guards["pass"]}) + "\n")
    (out / "premise2_draft.md").write_text(draft(rows, verdict, guards))
    print(json.dumps(verdict, indent=1), flush=True)


def pack_season(d: str) -> str:
    return "summer" if d[5:7] in ("06", "07", "08") else "winter"


def draft(rows: list[dict], verdict: dict, guards: dict) -> str:
    def k(v: float | None) -> str:
        return "–" if v is None else f"{v / 1000:,.0f}k"

    def pct(v: float | None) -> str:
        return "–" if v is None else f"{100 * v:.1f}%"

    def pts(v: float | None) -> str:
        return "–" if v is None else f"{v:+.1f}"

    lines = ["# Command Center premise check: part 2 run (draft, numbers only)", "",
             f"Reading: **{verdict['reading']}**. Guards: {'pass' if guards['pass'] else 'FAIL'}.", "",
             "| Bar | Days passing | Needed | Result |", "|---|---|---|---|"]
    for bar in ("c1", "c2", "c3"):
        v = verdict[bar]
        result = "pass" if v["pass"] else "FAIL"
        lines.append(f"| {bar.upper()} | {v['days']} of {v['of']} | {v['needed']} | {result} |")
    lines += ["", "## C1 and C2 (system cost, passenger-minutes)", "",
              "| Day | Hub | Naive | H | LB | FAA real | Split | Reactive | TAF | C1 over H | C1 | Share R / T | "
              "C2 gain | C2 | Diversions N/F/R/T |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c, c1, c2, dv = r["costs"], r["c1"], r["c2"], r["diversions"]
        lines.append(f"| {r['date']} | {r['hub']} | {k(c['naive'])} | {k(c['hindsight'])} | {k(c['lb'])} | "
                     f"{k(c['faa'])} | {k(c['split'])} | {k(c['reactive'])} | {k(c['taf'])} | {pct(c1['over'])} | "
                     f"{'pass' if c1['pass'] else 'fail'}{' (flips on LB)' if c1['flip'] else ''} | "
                     f"{pct(c2['reactive'])} / {pct(c2['taf'])} | {pts(c2['gain'])} | "
                     f"{'pass' if c2['pass'] else 'fail'}{' (flips on LB)' if c2['flip'] else ''} | "
                     f"{dv['naive']}/{dv['faa']}/{dv['reactive']}/{dv['taf']} |")
    lines += ["", "## C3 (joint cost: the system less JetBlue's task flights, plus JetBlue's step-0 cost)", "",
              "| Day | Hub | Joint naive | Joint H | Off | (a) | (b) | Both | Share off / both | Gain both | "
              "Gain (a) | Gain (b) | Time-outs | C3 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        j, c3 = r["joint"], r["c3"]
        sh = c3["shares"]
        lines.append(f"| {r['date']} | {r['hub']} | {k(j['naive']['joint'])} | {k(j['hindsight']['joint'])} | "
                     f"{k(j['off']['joint'])} | {k(j['a']['joint'])} | {k(j['b']['joint'])} | {k(j['ab']['joint'])} | "
                     f"{pct(sh['off'])} / {pct(sh['ab'])} | {pts(c3['gain'])} | {pts(c3['gain_a'])} | "
                     f"{pts(c3['gain_b'])} | {', '.join(c3['timeouts']) or 'none'} | "
                     f"{'pass' if c3['pass'] else 'fail'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
