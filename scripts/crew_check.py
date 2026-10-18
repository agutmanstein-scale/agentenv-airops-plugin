"""Can the crews fly JetBlue's real day, and what do they add to the calm day? Only the crew and aircraft solvers run
(HiGHS); no LLM is called.

    python scripts/crew_check.py --out cache/<run> [--tasks cache/stage1/full] [--only DATE,...]
                                 [--bands low,middle,high] [--workers 4] [--resume]

Per date (the tasks of a world_check run, at its fitted scale):
1. the trips over the published schedule (pairings.py), built once, and the calm optimum without crews
   (references.calm's), solved once;
2. per reserve band (the share of crews on reserve: pilots 13/16/20%, flight attendants 16/19/22%):
   - the crew replay of JetBlue's real day: the units at the start from a crew replay of the record before the start,
     then its flown flights at their recorded departures and blocks crewed by them, the fewest flights without a legal
     crew first (crewday.replay_record), with the LP bound and each such flight's cause; it passes with at most 1% of
     the date's flown flights and at most 2. The plan is audited move by move against the crew sim's rules;
   - the crew-aware calm day (crewday.calm_day) against the calm optimum without crews: it passes when it rises by at
     most 2% (from a calm optimum of 0, when it stays 0). Its plan must replay exactly in the crew sim.
Writes dates/<date>.json as each date finishes (`--resume` keeps those already written) and report.md.
"""

import argparse
import json
import math
import resource
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from agentenv_airops.programs import crewday, crews, crewsim
from agentenv_airops.programs.model import Task

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
DATES = ("2024-08-21", "2025-02-04", "2023-07-16", "2025-02-09", "2026-06-22", "2023-12-18", "2026-07-21")
SHARE, MOST = 0.01, 2
RISE = 2.0
GATE_SHARE = 0.9


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, default=ROOT / "cache" / "stage1" / "full")
    ap.add_argument("--only", default=",".join(DATES))
    ap.add_argument("--bands", default=",".join(crews.BANDS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    dates = a.only.split(",")
    bands = tuple(a.bands.split(","))
    (a.out / "dates").mkdir(parents=True, exist_ok=True)
    tasks = {}
    for line in (a.tasks / "tasks.jsonl").read_text().splitlines():
        head = json.loads(line)
        if head["date"] in dates:
            tasks[head["date"]] = line
    refs = {d: json.loads(p.read_text()) for p in (a.tasks / "refs").glob("*.json")
            for d in [json.loads(p.read_text())["date"]] if d in dates}
    todo = [d for d in dates if not (a.resume and (a.out / "dates" / f"{d}.json").exists())]
    with ProcessPoolExecutor(min(a.workers, max(1, len(todo)))) as pool:
        futures = {pool.submit(job, tasks[d], bands, a.out / "dates" / f"{d}.json",
                               refs.get(d, {}).get("calm", {}).get("objective")): d for d in todo}
        for f in as_completed(futures):
            print(f"{futures[f]} {f.result()}", flush=True)
    rows = [json.loads((a.out / "dates" / f"{d}.json").read_text()) for d in dates
            if (a.out / "dates" / f"{d}.json").exists()]
    (a.out / "report.md").write_text(report(rows, bands))
    print(a.out / "report.md")


def job(line: str, bands: tuple[str, ...], out: Path, calm_ref) -> str:
    t0 = time.monotonic()
    try:
        row = check(Task.from_dict(json.loads(line)), bands, calm_ref)
    except Exception:
        row = {"date": json.loads(line)["date"], "error": traceback.format_exc()}
    row["seconds"] = round(time.monotonic() - t0, 1)
    row["peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 30, 2)
    out.write_text(json.dumps(row, indent=1, default=str))
    return "error" if "error" in row else "ok"


def check(task: Task, bands: tuple[str, ...], calm_ref) -> dict:
    laps = {}
    t = time.monotonic()
    w = crewday.world(task, INPUTS, DERIVED)
    laps["trips"] = round(time.monotonic() - t, 1)
    row = {"date": task.date, "task_id": task.task_id, "split": task.split, "start": task.start,
           "flights": len(task.flights), "trips": trips(w), "laps": laps, "bands": {}}
    t = time.monotonic()
    optimum = crewday.calm_optimum(w)
    laps["calm_optimum"] = round(time.monotonic() - t, 1)
    row["calm_optimum"] = {"objective": optimum.objective, "status": optimum.status, "gap_pct": optimum.gap_pct,
                           "reference": calm_ref}
    for band in bands:
        row["bands"][band] = {"record": record(w, band), "calm": calm(w, band, optimum)}
    return row


def trips(w: crewday.World) -> dict:
    return {f"{k}/{g}": {"duties": len(b.duties), "uncovered": len(b.uncovered), "gap_pct": round(b.gap_pct, 3),
                         "columns": b.columns, "rounds": b.rounds, "seconds": b.seconds,
                         "legs_per_duty": round(sum(len(d.legs) for d in b.duties) / max(1, len(b.duties)), 2),
                         "outstation_starts": sum(d.start not in crews.bases(k, w.task.date) for d in b.duties)}
            for (k, g), b in w.built.items()}


def record(w: crewday.World, band: str) -> dict:
    task = w.task
    t = time.monotonic()
    units, before = crewday.storm_units(w, band)
    pre = round(time.monotonic() - t, 1)
    p, sol = crewday.replay_record(w, units)
    deps, blocks, ends = crewday.recorded(task)
    plan = crewday.plan_of(p, sol)
    audit = crewsim.audit(task, units, plan, deps, blocks, w.local, w.airports, ends)
    flown = len(deps)
    n = len(sol.uncrewed)
    return {"flown": flown, "uncrewed": n, "pass": n <= min(MOST, math.floor(SHARE * flown)),
            "solve": sol.summary(), "causes": crewday.uncrewed_causes(p, sol),
            "crew_cost": round(sol.crew_cost, 1), "audit": audit, "units": units_at(units),
            "before_start": before, "seconds_before_start": pre}


def calm(w: crewday.World, band: str, optimum) -> dict:
    units = crewday.calm_units(w, band)
    c = crewday.calm_day(w, units, optimum)
    deps = {i: r.dep for i, r in c.day.run.flights.items() if r.dep is not None}
    blocks = {f.id: f.sched_block for f in c.task.flights}
    audit = crewsim.audit(c.task, units, c.plan, deps, blocks, w.local, w.airports)
    rise = c.rise_pct
    costly = sorted(((sum(r["cost"].values()), uid, r["cost"]) for uid, r in c.day.units.items()), reverse=True)
    return {"optimum": optimum.objective, "crew_aware": round(c.day.total, 1), "rise_pct": rise,
            "pass": rise is not None and rise <= RISE, "crew_cost": {k: round(v, 1) for k, v in c.day.cost.items()},
            "aircraft": c.day.run.cost.to_dict(), "cancelled": sum(r.dep is None for r in c.day.run.flights.values()),
            "uncrewed": len(c.day.uncrewed), "attempts": c.attempts, "audit": audit, "units": units_at(units),
            "pending_crews": sum(u.id.endswith("p") for u in units),
            "costliest_units": [{"unit": uid, **{k: round(v, 1) for k, v in cost.items()}}
                                for total, uid, cost in costly[:10] if total > 0]}


def units_at(units) -> dict:
    return dict(sorted(Counter(f"{u.kind}/{u.reserve or 'trip'}" for u in units).items()))


def report(rows: list[dict], bands: tuple[str, ...]) -> str:
    out = ["# Crews: the record replayed, and the calm day", ""]
    bad = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    for band in bands:
        out += [f"## Reserve band: {band}", "",
                "| Date | Flown | Without a legal crew (LP bound) | Passes | Calm optimum | Crew-aware calm | Rise % | "
                "Passes | Seconds |", "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            b = r["bands"].get(band)
            if b is None:
                continue
            rec, cal = b["record"], b["calm"]
            rise = "-" if cal["rise_pct"] is None else f"{cal['rise_pct']:.2f}"
            out.append(f"| {r['date']} | {rec['flown']} | {rec['uncrewed']} ({rec['solve']['uncrewed_bound']}) | "
                       f"{rec['pass']} | {cal['optimum']} | {cal['crew_aware']} | {rise} | {cal['pass']} | "
                       f"{r['seconds']} |")
        n = sum(band in r["bands"] for r in rows)
        rec_ok = sum(r["bands"][band]["record"]["pass"] for r in rows if band in r["bands"])
        cal_ok = sum(r["bands"][band]["calm"]["pass"] for r in rows if band in r["bands"])
        out += ["", f"Record replay passes on {rec_ok} of {n} dates; the calm day on {cal_ok} of {n} "
                    f"(each needs {GATE_SHARE:.0%} of storm dates over the full run).", ""]
        for r in rows:
            for c in r["bands"].get(band, {}).get("record", {}).get("causes", []):
                out.append(f"- {r['date']} {c['flight']} {c['orig']}-{c['dest']} at {c['dep']}: "
                           + "; ".join(f"{k}: {v}" for k, v in c["missing"].items()))
        out.append("")
    if bad:
        out += ["## Errors", ""] + [f"- {r['date']}" for r in bad]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
