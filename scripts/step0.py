"""Step 0 of AirOpsEnv v2, the go/no-go: can the programs model reproduce the real storm days well enough to grade
against? No LLM is called; only the reference solvers run (HiGHS).

    python scripts/step0.py --out cache/step0/<run> [--workers 12] [--only ID,...] [--resume] [--seed 0]
                            [--prove-s 3600] [--watch-s 1800]

The order keeps the unseen-traffic scale out of sample (spec, "Order in build_pack"):
1. every candidate (programs.candidates) built in parallel at scale 1 and the provisional 03:00 cutoff, written to
   tasks.jsonl, plus the four rule-chosen controls;
2. the provisional scale per airport, fitted on every candidate's other carriers;
3. pass 1 of the clauses on every task: data and program in window (the build's), airline replay (auto-cancels, and
   its cost within the provisional tolerance of the recorded day's), recorded day >= anchor, leverage on flat cost and
   proof. Fidelity waits for step 7: it is out of sample by definition, and a filter on the provisional scale (fitted
   on every candidate) would hand G2 days already chosen for agreeing with the model;
4. storm events over the candidates' dates and the seeded draw;
5. on the draw's train days: the shipped scale, the cutoff hour (by which CUTOFF_SHARE of JetBlue's after-midnight hub
   departures had left, read from BTS) and the replay tolerance (the TOLERANCE_PCT percentile of |ln(replay /
   recorded)| over the day-hubs of train events that pass every other clause, so the tolerance is not fitted to days it
   already censored);
6. if the scale or the cutoff moved, pass 2 of the solver clauses with them; the clauses again with the fixed
   tolerance, and the draw settled: a drawn eval day that now fails is dropped, not replaced, so no day the scale,
   cutoff or tolerance was fitted on can become eval;
7. fidelity at the shipped scale on every eval and train day (G2 reads them all), then a failing day is dropped, not
   replaced;
8. every reference on every task with the fixed parameters, one JSON per task under refs/ as it finishes; --resume
   keeps every pass result whose task, cutoff and signature are unchanged;
9. gates.json (G1-G5 with values, thresholds and pass/fail, the fixed parameters, the diagnostics), candidates.jsonl
   (every candidate with its clauses and reasons) and a draft step0-report.md (numbers only).

--only takes task ids (b6-jfk-20240819 or b6-jfk-20240819-1300), dates (2024-08-19) or date/hub (2024-08-19/JFK),
for candidates and controls alike.
"""

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agentenv_airops.build import pack
from agentenv_airops.core.sim import FLOWN
from agentenv_airops.programs import candidates, rbs, references, sim, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.model import Task, write_tasks
from agentenv_airops.programs.repairs import CUTOFF, DMAX

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
CUTOFF_SHARE = 0.9
TOLERANCE_PCT = 90
AUTO_SHARE, AUTO_MAX = 0.01, 2
LEVERAGE = 0.10
CONTROL_MARGIN = 0.02
CALM_CANCELS, CALM_FERRIES = 1, 2
GATE_SHARE = 0.9
EVAL_MIN, TRAIN_MIN = 12, 60
BLOCK_SLACK = 120
PROVE_S, WATCH_S = 3600.0, 1800.0
TEMPERATURE, FLOOR_SHARE = 0.5, 0.1
SOLVER_CLAUSES = ("airline_auto", "airline_cost", "recorded_day", "leverage", "proof")
COLUMNS = ("task_id", "split", "flights", "moves", "watches", "anchor", "naive", "follower", "blind", "rule", "airline",
           "recorded", "flat_anchor", "naive_flat", "calm", "replay_auto")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--only", default="", help="comma-separated task ids, dates or date/hub (default: all)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prove-s", type=float, default=PROVE_S, help="time limit of each anchor, flat or calm solve")
    ap.add_argument("--watch-s", type=float, default=WATCH_S, help="time limit of each follower watch solve")
    args = ap.parse_args()
    limits = (args.prove_s, args.watch_s)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    only = wanted(args.only)
    ones = dict.fromkeys(HUBS, 1.0)

    fits = candidates.e_table(INPUTS, DERIVED, workers=args.workers)
    e_fit, e_median = unseen.table(fits)
    keys, counts = candidates.candidates(INPUTS)
    keys = [k for k in keys if only(k)]
    built = candidates.build_all(keys, INPUTS, DERIVED, e_fit, e_median, ones, workers=args.workers)
    controls = [candidates.build_candidate(d, h, INPUTS, DERIVED, e_fit, e_median, ones, window=w, split="control")
                for d, h, w in candidates.controls(INPUTS, DERIVED) if only((d, h))]
    tasks = {b.task.task_id: b.task for b in built if b.task}
    held = {b.task.task_id: b.task for b in controls if b.task}
    write_tasks([*tasks.values(), *held.values()], out / "tasks.jsonl")
    clock.lap("build", f"{len(tasks)} of {len(keys)} candidates built, {len(held)} controls")

    provisional = filled(unseen.fit_scale(tasks.values()), ones)
    every = {**tasks, **held}
    pass1 = run_pass(out / "pass1", at(every, provisional), CUTOFF, clause_job, args.workers, args.resume, clock,
                     limits)
    clock.lap("pass1")
    tol_prov = tolerance([pass1[i] for i in tasks])
    q1 = qualifying(tasks, pass1, tol_prov)
    event = candidates.events(d for d, _ in keys)
    drawn = candidates.split([key(tasks[i]) for i in q1], event, args.seed)
    train1 = [i for i in q1 if drawn.get(key(tasks[i])) == "train"]

    scale = filled(unseen.fit_scale(tasks[i] for i in train1), provisional)
    late = late_departures([key(tasks[i]) for i in train1])
    cutoff = cutoff_hour(late) or CUTOFF
    taken = {event[d] for (d, _), v in drawn.items() if v == "eval"}
    basis = [pass1[i] for i in tasks if event[tasks[i].date] not in taken
             and all(clauses_of(tasks[i], pass1[i], math.inf).values())]
    tol = tolerance(basis) if basis else tol_prov
    rerun = scale != provisional or cutoff != CUTOFF
    pass2 = run_pass(out / "pass2", at(every, scale), cutoff, clause_job, args.workers, args.resume, clock,
                     limits) if rerun else pass1
    clock.lap("pass2" if rerun else "pass2 (skipped: scale and cutoff unchanged)")
    q2 = qualifying(tasks, pass2, tol)
    settled = candidates.settle(drawn, [key(tasks[i]) for i in q2], event)
    split = {i: settled[key(tasks[i])] for i in q2 if key(tasks[i]) in settled}
    lost = sorted(i for i in q1 if drawn.get(key(tasks[i])) == "eval" and i not in split)
    fid_ship = {i: unseen.fidelity(t, scale[t.hub]) for i, t in tasks.items()}
    dropped = {i: v for i, v in split.items() if not in_band(fid_ship[i])}
    final = {i: v for i, v in split.items() if i not in dropped}

    shipped = {i: t if t.split == "control" else replace(t, split=final.get(i, "dropped" if i in dropped else ""))
               for i, t in at(every, scale).items()}
    shipped = {i: sim.with_cutoff(t, cutoff) for i, t in shipped.items()}
    write_tasks(list(shipped.values()), out / "tasks.jsonl")
    refs = run_pass(out / "refs", shipped, cutoff, refs_job, args.workers, args.resume, clock, limits, pass2)
    clock.lap("references")

    params = {"seed": args.seed,
              "provisional": {"scale": provisional, "cutoff": CUTOFF, "tolerance": round(tol_prov, 4)},
              "fixed": {"scale": scale, "cutoff": cutoff, "tolerance": round(tol, 4)},
              "late_departures": {"n": len(late), "train_day_hubs": len(train1)}, "tolerance_basis": len(basis),
              "eval_lost_in_pass2": lost, "pass2": rerun, "dmax": DMAX, "time_limits_s": limits,
              "e_median": e_median, "advisories_fitted": len(fits)}
    rows = candidate_rows(built, controls, tasks, pass1, pass2, tol_prov, tol, fid_ship, drawn, split, dropped)
    (out / "candidates.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    report = summary(tasks, held, shipped, refs, split, final, dropped, fid_ship, built, counts, params, clock, keys)
    (out / "gates.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    (out / "step0-report.md").write_text(markdown(report))
    print(json.dumps(report["gates"], indent=1), flush=True)


class Clock:
    def __init__(self):
        self.t0 = self.last = time.monotonic()
        self.laps: dict[str, float] = {}

    def lap(self, name: str, note: str = "") -> None:
        now = time.monotonic()
        self.laps[name] = round(now - self.last, 1)
        self.last = now
        print(f"[{now - self.t0:8.1f}s] {name} {note}".rstrip(), flush=True)


def wanted(text: str) -> Callable[[tuple[str, str]], bool]:
    """Whether a (date, hub) is among --only's task ids, dates or date/hub items (everything when empty)."""
    items = [x.strip() for x in text.split(",") if x.strip()]
    keys = set()
    for x in items:
        if x.lower().startswith("b6-"):
            _, hub, ymd, *_ = x.lower().split("-")
            keys.add((f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}", hub.upper()))
        else:
            d, _, hub = x.partition("/")
            keys |= {(d, h) for h in ([hub.upper()] if hub else HUBS)}
    return lambda k: not items or tuple(k) in keys


def key(task: Task) -> tuple[str, str]:
    return task.date, task.hub


def filled(scale: Mapping[str, float], fallback: Mapping[str, float]) -> dict[str, float]:
    return {h: scale.get(h, fallback[h]) for h in HUBS}


def at(tasks: Mapping[str, Task], scale: Mapping[str, float]) -> dict[str, Task]:
    return {i: rbs.rescale(t, scale) for i, t in tasks.items()}


def signature(task: Task, cutoff: int) -> str:
    return hashlib.sha256(f"{replace(task, split='').to_json()}|{cutoff}".encode()).hexdigest()[:16]


def run_pass(folder: Path, tasks: Mapping[str, Task], cutoff: int, job: Callable, workers: int, resume: bool,
             clock: Clock, limits: tuple[float, float], base: Mapping[str, dict] | None = None) -> dict[str, dict]:
    """`job` on every task in a process pool, heaviest first, each result written to folder/<task id>.json as it
    finishes; with `resume`, a result whose signature matches is kept."""
    folder.mkdir(parents=True, exist_ok=True)
    results, jobs = {}, []
    for i, t in tasks.items():
        sig = signature(t, cutoff)
        path = folder / f"{i}.json"
        if resume and path.exists():
            kept = json.loads(path.read_text())
            if kept.get("signature") == sig:
                results[i] = kept
                continue
        jobs.append((i, t.to_json(), cutoff, sig, (base or {}).get(i), limits))
    jobs.sort(key=lambda j: weight(tasks[j[0]]), reverse=True)
    if jobs:
        with ProcessPoolExecutor(min(workers, len(jobs))) as pool:
            futures = {pool.submit(job, j): j[0] for j in jobs}
            for fut in as_completed(futures):
                r = fut.result()
                (folder / f"{r['task_id']}.json").write_text(json.dumps(r, default=str))
                results[r["task_id"]] = r
                print(f"  {folder.name} {len(results)}/{len(tasks)} {r['task_id']} {brief(r)}", flush=True)
    return results


def weight(task: Task) -> int:
    return len(task.flights) * (1 + sum(len(t.repositions) + len(t.busy) for t in task.tails))


def brief(r: dict) -> str:
    if r.get("error"):
        return f"ERROR {r['error']}"
    s = r.get("seconds", {})
    parts = [f"{k}={v}s" for k, v in s.items()]
    if r.get("below_anchor"):
        parts.append(f"BELOW ANCHOR {r['below_anchor']}")
    return " ".join(parts)


def clause_job(job: tuple) -> dict:
    """The solver clauses of one task: naive, the airline replay, the recorded day, the anchor and the flat anchor."""
    tid, line, cutoff, sig, _, (prove, _) = job
    task = sim.with_cutoff(Task.from_dict(json.loads(line)), cutoff)
    out = {"task_id": tid, "signature": sig, "cutoff": cutoff, "seconds": {}}
    try:
        t0 = time.monotonic()
        naive = references.naive(task)
        air = references.airline(task)
        rec, rec_ferries = references.recorded(task)
        out |= {"naive": {**naive.cost.to_dict(), **naive.counts},
                "airline": {**air.run.cost.to_dict(), **air.run.counts, "auto": air.auto, "flew": air.flew},
                "recorded": {**rec.to_dict(), "ferries": rec_ferries},
                "jetblue": {"flown": sum(f.actual.dep is not None for f in task.flights),
                            "past_midnight": references.past_midnight(task)}}
        t1 = time.monotonic()
        sol, run = references.anchor(task, prove)
        t2 = time.monotonic()
        flat = references.flat_anchor(task, prove, start=run)
        t3 = time.monotonic()
        exact = run.cost.total == sol.objective and all(
            (run.flights[i].dep if run.flights[i].status == FLOWN else None) == d for i, d in sol.deps.items())
        out |= {
            "anchor": {**sol.to_dict(), **run.cost.to_dict(), **run.counts, "exact": exact,
                       "past_midnight": references.past_midnight(task, run),
                       "seats_ok": references.seats_ok(task, run)},
            "flat": sol_flat(flat),
            "proof": {"proven": sol.proven, "exact": exact,
                      "block": all(f.block <= f.sched_block + BLOCK_SLACK for f in task.flights),
                      "pax": all(f.pax <= min(t.seats for t in task.tails if t.family == f.family)
                                 for f in task.flights)},
            "below_anchor": references.below_anchor({"naive": naive.cost.total, "airline": air.run.cost.total},
                                                     sol.objective),
            "seconds": {"sims": round(t1 - t0, 1), "anchor": round(t2 - t1, 1), "flat": round(t3 - t2, 1)}}
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
    return out


def sol_flat(sol) -> dict:
    return {k: v for k, v in sol.to_dict().items() if k in ("status", "objective", "gap_pct", "wall_s")}


def refs_job(job: tuple) -> dict:
    """Every reference of one task: its pass result, then the follower, the program-blind follower, the rule and
    the calm day."""
    tid, line, cutoff, sig, base, (prove, watch) = job
    task = sim.with_cutoff(Task.from_dict(json.loads(line)), cutoff)
    out = {**(base or {}), "task_id": tid, "signature": sig, "split": task.split, "hub": task.hub, "date": task.date,
           "slide": task.slide, "watches": len(references.watch_times(task)), "flights": len(task.flights),
           "tails": len(task.tails), "moves": sum(len(t.repositions) + len(t.busy) for t in task.tails)}
    out["seconds"] = dict(out.get("seconds", {}))
    runs = (("follower", lambda: references.follow(task, True, watch)),
            ("blind", lambda: references.follow(task, False, watch)), ("rule", lambda: references.rule(task)))
    for name, play in runs:
        t0 = time.monotonic()
        try:
            f = play()
            cancels = [v for v in f.plan.values() if v.get("cancel")]
            out[name] = {**f.run.cost.to_dict(), **f.run.counts, "seats_ok": references.seats_ok(task, f.run),
                         "watch_solves": len(f.watches), "max_watch_s": max((w.get("wall_s", 0) for w in f.watches),
                                                                            default=0),
                         "decided": dict(Counter(v["decided"] for v in cancels))}
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"}
        out["seconds"][name] = round(time.monotonic() - t0, 1)
    t0 = time.monotonic()
    try:
        c = references.calm(task, prove)
        out["calm"] = {"cancelled": c.cancelled, "ferries": c.ferries, "objective": c.solution.objective,
                       "status": c.solution.status}
    except Exception as e:
        out["calm"] = {"error": f"{type(e).__name__}: {e}"}
    out["seconds"]["calm"] = round(time.monotonic() - t0, 1)
    anchor = out.get("anchor", {}).get("objective")
    if anchor is not None:
        costs = {k: out[k]["total"] for k in ("naive", "airline", "follower", "blind", "rule")
                 if "total" in out.get(k, {})}
        out["below_anchor"] = references.below_anchor(costs, anchor)
    return out


def tolerance(results: Iterable[dict]) -> float:
    """exp of the TOLERANCE_PCT percentile of |ln(replay / recorded)|."""
    logs = sorted(abs(math.log(r["airline"]["total"] / r["recorded"]["total"])) for r in results
                  if not r.get("error") and r["recorded"]["total"] > 0)
    return math.exp(percentile(logs, TOLERANCE_PCT)) if logs else math.inf


def percentile(values: list[float], pct: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, math.ceil(pct / 100 * len(v)) - 1)]


def clauses_of(task: Task, r: dict, tol: float) -> dict[str, bool]:
    """The clauses of a built task but fidelity, which waits for the train-fitted scale (the data clauses held at its
    window, else it would not have been built)."""
    base = {"program_in_window": bool(task.clauses["program_in_window"])}
    if r.get("error"):
        return {**base, **dict.fromkeys(SOLVER_CLAUSES, False)}
    a, rec = r["airline"], r["recorded"]
    ratio = a["total"] / rec["total"] if rec["total"] else math.inf
    return {**base, "airline_auto": a["auto"] <= AUTO_SHARE * a["flew"] and a["auto"] <= AUTO_MAX,
            "airline_cost": abs(math.log(ratio)) <= math.log(tol) if ratio > 0 else False,
            "recorded_day": rec["total"] >= r["anchor"]["objective"],
            "leverage": r["naive"]["flat"] >= (1 + LEVERAGE) * r["flat"]["objective"],
            "proof": all(r["proof"].values())}


def in_band(fid: float | None) -> bool:
    return fid is None or unseen.FIDELITY[0] <= fid <= unseen.FIDELITY[1]


def qualifying(tasks: Mapping[str, Task], results: Mapping[str, dict], tol: float) -> list[str]:
    return [i for i, t in tasks.items() if all(clauses_of(t, results[i], tol).values())]


def late_departures(keys: Iterable[tuple[str, str]]) -> list[int]:
    """JetBlue's recorded departures from the hub after local midnight on each (date, hub), in minutes from the
    date's midnight (BTS)."""
    out = []
    for d, hub in sorted(keys):
        x = pack.local(candidates.frame(INPUTS, DERIVED, d), candidates.midnight(d))
        ours = x[(x.Reporting_Airline == candidates.CARRIER) & (x.FlightDate == d) & (x.Origin == hub)
                 & (x.Cancelled == 0) & (x.ad >= 1440)]
        out += [int(a) for a in ours.ad]
    return out


def cutoff_hour(departures: list[int]) -> int | None:
    """The first whole hour after midnight by which CUTOFF_SHARE of `departures` had left."""
    if not departures:
        return None
    return next(60 * h for h in range(25, 49) if sum(d < 60 * h for d in departures) >= CUTOFF_SHARE * len(departures))


def candidate_rows(built: list, controls: list, tasks: Mapping[str, Task], pass1: Mapping[str, dict],
                   pass2: Mapping[str, dict], tol_prov: float, tol: float, fid: Mapping[str, float | None],
                   drawn: Mapping[tuple, str], split: Mapping[str, str], dropped: Mapping[str, str]) -> list[dict]:
    rows = []
    for b in [*built, *controls]:
        row = {k: v for k, v in b.row.items() if k != "tried"}
        row["tried"] = b.row.get("tried", [])
        i = row.get("task_id")
        if b.task is None:
            rows.append({**row, "built": False, "split": None, "qualifies": False})
            continue
        t = b.task
        row |= {"built": True, "slide": t.slide, "build_clauses": t.clauses}
        if t.split == "control":
            rows.append({**row, "split": "control"})
            continue
        c1 = clauses_of(t, pass1[i], tol_prov)
        c2 = {**clauses_of(t, pass2[i], tol), "fidelity": in_band(fid[i])}
        r = pass2[i]
        rows.append({**row, "clauses_pass1": c1, "clauses": c2,
                     "failed": [k for k, v in c2.items() if not v], "qualifies": all(c2.values()),
                     "fidelity": fid[i], "drawn": drawn.get(key(t)), "split": split.get(i), "dropped": i in dropped,
                     "replay_ratio": ratio(r), "airline_auto": (r.get("airline") or {}).get("auto"),
                     "airline_flew": (r.get("airline") or {}).get("flew"), "leverage_flat": leverage(r),
                     "error": r.get("error")})
    return rows


def leverage(r: dict) -> float | None:
    if r.get("error"):
        return None
    return round(r["naive"]["flat"] / r["flat"]["objective"] - 1, 4)


def ratio(r: dict) -> float | None:
    if r.get("error") or not r["recorded"]["total"]:
        return None
    return round(r["airline"]["total"] / r["recorded"]["total"], 4)


def control_checks(r: dict) -> dict:
    if r.get("error") or "flat" not in r:
        return {"ok": False, "error": r.get("error", "missing")}
    naive_ok = r["naive"]["flat"] <= (1 + CONTROL_MARGIN) * r["flat"]["objective"]
    recorded_ok = r["recorded"]["total"] >= r["anchor"]["objective"]
    return {"ok": naive_ok and recorded_ok, "naive_flat_over_anchor": round(r["naive"]["flat"] / r["flat"]["objective"]
                                                                            - 1, 4),
            "naive_within_2pct": naive_ok, "recorded": r["recorded"]["total"], "anchor": r["anchor"]["objective"],
            "recorded_ge_anchor": recorded_ok}


def reward(cost: float, anchor: float, naive: float) -> float:
    g = max(0.0, cost - anchor) / max(naive - anchor, FLOOR_SHARE * anchor)
    return 0.2 + 0.8 * math.exp(-g / TEMPERATURE)


def summary(tasks: Mapping[str, Task], held: Mapping[str, Task], shipped: Mapping[str, Task],
            refs: Mapping[str, dict], split: Mapping[str, str], final: Mapping[str, str], dropped: Mapping[str, str],
            fid: Mapping[str, float | None], built: list, counts: dict, params: dict, clock: Clock,
            keys: list) -> dict:
    mine = {i: r for i, r in refs.items() if i in tasks}
    autos = [(r["airline"]["auto"], r["airline"]["flew"]) for r in mine.values() if "airline" in r]
    replay = [a <= AUTO_SHARE * n and a <= AUTO_MAX for a, n in autos]
    rounded = [a <= min(AUTO_MAX, math.ceil(AUTO_SHARE * n)) for a, n in autos]
    loose = [a <= max(AUTO_SHARE * n, AUTO_MAX) for a, n in autos]
    fid_eval = [in_band(fid[i]) for i, v in split.items() if v == "eval" and fid[i] is not None]
    fid_train = [in_band(fid[i]) for i, v in split.items() if v == "train" and fid[i] is not None]
    calm = [r.get("calm", {}).get("cancelled", 99) <= CALM_CANCELS and r.get("calm", {}).get("ferries", 99)
            <= CALM_FERRIES for r in mine.values()]
    per_hub = Counter(tasks[i].hub for i, v in final.items() if v == "eval")
    n_train = sum(v == "train" for v in final.values())
    ctl = {i: control_checks(refs.get(i, {})) for i in held}
    gates = {
        "G1_airline_replay": gate(share(replay), GATE_SHARE, len(replay),
                                  f"auto-cancels <= {AUTO_SHARE:.0%} and <= {AUTO_MAX} per day of the flights JetBlue "
                                  "flew by their deadline",
                                  alt={"rounded_up_share": share(rounded), "either_reading_share": share(loose),
                                       "auto_cancels": sum(a for a, _ in autos), "flew": sum(n for _, n in autos),
                                       "missing": len(mine) - len(autos)}),
        "G2_fidelity": {"eval": share(fid_eval), "train": share(fid_train), "threshold": GATE_SHARE,
                        "n_eval": len(fid_eval), "n_train": len(fid_train),
                        "pass": bool(fid_eval) and bool(fid_train) and share(fid_eval) >= GATE_SHARE
                        and share(fid_train) >= GATE_SHARE},
        "G3_calm_day": gate(share(calm), GATE_SHARE, len(calm),
                            f"<= {CALM_CANCELS} forced cancellation and <= {CALM_FERRIES} ferries"),
        "G4_enough_days": {"eval_per_hub": {h: per_hub[h] for h in HUBS}, "train": n_train,
                           "threshold": {"eval_per_hub": EVAL_MIN, "train": TRAIN_MIN},
                           "pass": all(per_hub[h] >= EVAL_MIN for h in HUBS) and n_train >= TRAIN_MIN},
        "G5_controls": {"controls": ctl, "n": len(ctl), "threshold": "all 4",
                        "pass": len(ctl) == 4 and all(c["ok"] for c in ctl.values())},
    }
    gates["go"] = all(g["pass"] for g in gates.values())
    return {"gates": gates, "parameters": params, "counts": {**counts, "built": len(tasks), "keys": len(keys),
                                                              "rejected": Counter(b.row.get("reason") for b in built
                                                                                  if b.task is None)},
            "split": {"eval": dict(Counter(f"{tasks[i].hub} {candidates.season(tasks[i].date)}"
                                           for i, v in final.items() if v == "eval")), "train": n_train,
                      "dropped": sorted(dropped),
                      "qualifying": len(split)},
            "diagnostics": diagnostics(tasks, held, refs, final),
            "movements": movements([t for i, t in shipped.items() if final.get(i) == "train"]),
            "tasks": [row_of(i, r) for i, r in sorted(refs.items())], "seconds": clock.laps, "timing": timing(refs)}


def movements(train: list[Task]) -> dict:
    """W3's check: the unseen stream's share of program-hour demand on the train days, beside the share of each
    airport's own counted arrivals that BTS misses (BTS: arrivals that operated, over the cached months)."""
    share = unseen.unseen_share(train)
    days = {m: len(pack.month_days(m)) for m in pack.MONTHS}
    out = {}
    for hub in HUBS:
        n = 0
        for m in pack.MONTHS:
            x = candidates.month_frame(INPUTS, DERIVED, m)
            n += int(((x.Dest == hub) & (x.Cancelled == 0) & x.FlightDate.str.startswith(m)).sum())
        out[hub] = {**share.get(hub, {}), **unseen.missed_share(hub, n / sum(days.values()), pack.MONTHS, days)}
    return out


def row_of(i: str, r: dict) -> dict:
    """One task's references and timings, compact."""
    costs = {k: r[k]["total"] for k in ("anchor", "naive", "follower", "blind", "rule", "airline", "recorded")
             if "total" in r.get(k, {})}
    calm = r.get("calm", {})
    return {"task_id": i, "split": r.get("split"), "flights": r.get("flights"), "moves": r.get("moves"),
            "watches": r.get("watches"), **costs, "flat_anchor": (r.get("flat") or {}).get("objective"),
            "naive_flat": (r.get("naive") or {}).get("flat"), "calm": [calm.get("cancelled"), calm.get("ferries")],
            "replay_auto": [(r.get("airline") or {}).get("auto"), (r.get("airline") or {}).get("flew")],
            "seconds": r.get("seconds", {}), "error": r.get("error")}


def gate(value: float | None, threshold: float, n: int, rule: str, alt: dict | None = None) -> dict:
    return {"value": value, "threshold": threshold, "n": n, "rule": rule, "pass": value is not None
            and value >= threshold, **(alt or {})}


def share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def diagnostics(tasks: Mapping[str, Task], held: Mapping[str, Task], refs: Mapping[str, dict],
                final: Mapping[str, str]) -> dict:
    ok = {i: r for i, r in refs.items() if not r.get("error") and "anchor" in r}
    rows = ("naive", "follower", "blind", "rule", "airline")
    over, rewards = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for i, r in ok.items():
        a, n = r["anchor"]["objective"], r["naive"]["total"]
        for k in rows:
            if "total" in r.get(k, {}):
                over[k].append(r[k]["total"] / a - 1)
                rewards[final.get(i, "other")][k].append(reward(r[k]["total"], a, n))
    seats = [all(r[k].get("seats_ok", True) for k in ("anchor", "follower", "blind", "rule") if k in r)
             for r in ok.values()]
    proven = {i for i, r in ok.items() if r["proof"]["proven"]}
    below = {i: r["below_anchor"] for i, r in ok.items() if r.get("below_anchor") and i in proven}
    unproven = {i: r.get("below_anchor") or {} for i, r in ok.items() if i not in proven}
    errors = {i: {k: v["error"] for k, v in r.items() if isinstance(v, dict) and v.get("error")} | (
        {"pass": r["error"]} if r.get("error") else {}) for i, r in refs.items()}
    lev = [r["naive"]["flat"] / r["flat"]["objective"] - 1 for i, r in ok.items() if i in tasks]
    notice = [r["naive"]["notice"] / (r["naive"]["total"] - r["anchor"]["objective"]) for i, r in ok.items()
              if i in tasks and r["naive"]["total"] > r["anchor"]["objective"]]
    rule_above = [(r["rule"]["total"] - r["follower"]["total"]) / (r["rule"]["total"] - r["anchor"]["objective"])
                  for r in ok.values() if "total" in r.get("rule", {}) and "total" in r.get("follower", {})
                  and r["rule"]["total"] > r["anchor"]["objective"]]
    return {
        "anchor_assertion": {"violations": below, "n_tasks": len(proven), "pass": not below,
                             "unproven_anchor": unproven},
        "errors": {i: e for i, e in errors.items() if e},
        "pax_le_seats": {"tasks_ok": sum(seats), "n": len(seats)},
        "proof": {"proven": sum(r["anchor"]["status"] == "Optimal" for r in ok.values()),
                  "exact_replay": sum(bool(r["anchor"].get("exact")) for r in ok.values()), "n": len(ok)},
        "mip": dist({**{k: [r["anchor"][k] for r in ok.values()] for k in ("cols", "rows", "copies", "wall_s")},
                     "max_watch_s": [r["follower"]["max_watch_s"] for r in ok.values()
                                     if "max_watch_s" in r.get("follower", {})]}),
        "hindsight_vs_jetblue": {
            "flown": [sum(r["anchor"]["flown"] for r in ok.values()), sum(r["jetblue"]["flown"] for r in ok.values())],
            "past_midnight_hub_movements": [sum(r["anchor"]["past_midnight"] for r in ok.values()),
                                            sum(r["jetblue"]["past_midnight"] for r in ok.values())]},
        "over_anchor_pct": {k: dist1([100 * v for v in vs]) for k, vs in over.items()},
        "reward_mean": {s: {k: round(statistics.mean(v), 3) for k, v in d.items()} for s, d in rewards.items()},
        "follower_above_rule": dist1(rule_above),
        "notice_share_of_naive_excess": dist1(notice),
        "windows_slid": {"n": sum(t.slide > 0 for t in tasks.values()), "of": len(tasks),
                         "by": dict(Counter(t.slide for t in tasks.values()))},
        "leverage_flat": dist1(lev),
        "unseen_share": unseen.unseen_share([t for i, t in tasks.items() if final.get(i) == "train"]),
        "replay_ratio": dist1([r["airline"]["total"] / r["recorded"]["total"] for i, r in ok.items() if i in tasks]),
        "controls": sorted(held),
    }


def dist(values: Mapping[str, list[float]]) -> dict:
    return {k: dist1(v) for k, v in values.items()}


def dist1(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {"n": len(v), "min": round(v[0], 3), "p10": round(percentile(v, 10), 3),
            "median": round(statistics.median(v), 3), "p90": round(percentile(v, 90), 3), "max": round(v[-1], 3),
            "mean": round(statistics.mean(v), 3)}


def timing(refs: Mapping[str, dict]) -> dict:
    per = defaultdict(list)
    for r in refs.values():
        for k, v in r.get("seconds", {}).items():
            per[k].append(v)
    total = [sum(r.get("seconds", {}).values()) for r in refs.values()]
    return {"per_task": {k: dist1(v) for k, v in per.items()}, "task_total": dist1(total),
            "sum_s": round(sum(total), 1)}


def markdown(report: dict) -> str:
    g, p, d = report["gates"], report["parameters"], report["diagnostics"]
    lines = [f"# AirOpsEnv v2 step 0: draft numbers ({datetime.now(UTC).date().isoformat()})", "",
             "Numbers only; the final report is written from them.", "", "## Gates", "",
             "| Gate | Value | Threshold | Pass |", "|---|---|---|---|"]
    for name, x in g.items():
        if name == "go":
            continue
        value = {k: v for k, v in x.items() if k not in ("threshold", "pass", "rule", "controls")}
        lines.append(f"| {name} | {json.dumps(value, default=str)} | {x.get('threshold')} | {x['pass']} |")
    lines += ["", f"Go: {g['go']}", "", "## Parameters", "", f"```\n{json.dumps(p, indent=1, default=str)}\n```", "",
              "## Candidates and split", "", f"```\n{json.dumps(report['counts'], indent=1, default=str)}\n"
              f"{json.dumps(report['split'], indent=1, default=str)}\n```", "", "## Diagnostics", "",
              f"```\n{json.dumps(d, indent=1, default=str)}\n```", "", "## Timing", "",
              f"```\n{json.dumps({'stages_s': report['seconds'], **report['timing']}, indent=1, default=str)}\n```", "",
              "## Movement-count check", "", f"```\n{json.dumps(report['movements'], indent=1, default=str)}\n```", "",
              "## Per task", "", "| " + " | ".join(COLUMNS) + " | seconds |", "|" + "---|" * (len(COLUMNS) + 1)]
    for row in report["tasks"]:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in COLUMNS) + f" | {json.dumps(row['seconds'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
