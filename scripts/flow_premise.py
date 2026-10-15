"""The two-seat premise check (the Command Center model), part 1: the draw, the world and its guards. No LLM is called.

    python scripts/flow_premise.py draw --out DIR
    python scripts/flow_premise.py pin --out DIR [--workers 8]
    python scripts/flow_premise.py calibrate --out DIR [--workers 12]
    python scripts/flow_premise.py worlds --out DIR [--workers 12]

draw writes DIR/days.json: the 20 day-hubs (seed 2026) and the train day-hubs, from step 0's built candidates. It runs
before anything else is computed, and never again. pin fetches and pins what the world adds to step 0's inputs: the
ATR 2024 and FAA Order 8400.9 PDFs, the SPECIs of the 13 months and the radar of every drawn and train date.
calibrate reads train day-hubs only: the medians, the reductions and both guards, into DIR/calibration.json,
DIR/guards.json, DIR/hours.jsonl and DIR/airborne.jsonl. worlds builds the 20 drawn day-hubs' worlds with that
calibration and writes their sizes, capacity and naive day to DIR/worlds.json (no policy is run).
"""

import argparse
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from agentenv_airops.build import pack
from agentenv_airops.flow import calibrate, days, demand, sources, world
from agentenv_airops.flow.capacity import Reductions

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
STEP0 = ROOT / "cache" / "step0" / "full"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="stage", required=True)
    d = sub.add_parser("draw")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--seed", type=int, default=days.SEED)
    p = sub.add_parser("pin")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=8)
    for name in ("calibrate", "worlds"):
        c = sub.add_parser(name)
        c.add_argument("--out", type=Path, required=True)
        c.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    if args.stage == "draw":
        draw(args.out, args.seed)
    elif args.stage == "pin":
        pin(args.out, args.workers)
    elif args.stage == "calibrate":
        fit(args.out, args.workers)
    else:
        worlds(args.out, args.workers)


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
                                                       "sample": calibrate.summary(rows), "timings": clock})
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


if __name__ == "__main__":
    main()
