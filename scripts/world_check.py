"""Does the storm-day world reproduce the real record well enough to grade against? Its gates F1-F6 were set before
the run. No LLM is called; only the reference solvers run (HiGHS).

    python scripts/world_check.py --out cache/<run> [--workers 4] [--only ID,...] [--resume] [--seed 0]
                                  [--prove-s 3600] [--watch-s 2400] [--scale JFK=0.5,BOS=0.5]

1. every candidate over the 19 cached months (programs.candidates) built at scale 1 and the 03:00 cutoff, plus the
   four rule-chosen controls; a day-hub that can't be a candidate (a neighbour day not loaded) is counted with its
   reason, and so are the dates an advisory-archive outage drops;
2. the seeded whole-event split over the built day-hubs (candidates.split: per hub 12 eval, 8 summer and 4 winter, one
   per storm event; train every built day-hub of an event without one). Being built is the only clause;
3. the unseen-traffic scale per airport fitted on the train day-hubs alone (unseen.fit_scale); `--scale` sets it
   instead, and F2 then fails as not out of sample. A hub without train day-hubs stops the run unless `--scale` is
   given;
4. every reference on every task at that scale, one JSON per task under refs/ as it finishes (`--resume` keeps each
   whose signature is unchanged): naive, the airline replay, the recorded day, the anchor (notice-priced, proven and
   canonical), the flat anchor, the follower, the program-blind follower, the rule and the calm day;
5. a task is kept when every one of its MIPs (anchor, flat anchor, calm day, every watch of both followers) proved to
   GAP and no reference failed; any other is left out of every gate's pool and counted, and a drawn eval day-hub so
   left out is dropped, not replaced;
6. gates.json (F1-F6 with values, thresholds and pass/fail, the parameters and the diagnostics), candidates.jsonl
   (every candidate's build, split and proof) and report.md (numbers only).

The gates, as registered:
- F1 airline replay: on >= 90% of the kept built day-hubs, its auto-cancels are at most 1% of the flights JetBlue flew
  by their deadline and at most 2;
- F2 fidelity: the other carriers' modelled program delay over their BTS NAS delay, pooled per hub over the kept
  day-hubs whose own ratio is within 0.5-2 (the others dropped and counted), within 0.8-1.25 on eval and on train
  separately, at a scale fitted on train;
- F3 calm day: at most 1 forced cancellation and 2 ferries on >= 90% of the kept built day-hubs;
- F4 controls: on all 4, JetBlue's recorded day costs at least the anchor, and naive is within 2% of the flat anchor;
- F5 references: every kept task's anchor proven and replayed exactly, no reference below a proven anchor, pax <= seats
  on every flight a reference flies, the tasks left out counted;
- F6, reported only: the built day-hubs by hub and season, and the split.

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
from agentenv_airops.core.sim import AUTO_CANCELLED, FLOWN
from agentenv_airops.programs import candidates, rbs, references, sim, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.model import Task, write_tasks
from agentenv_airops.programs.repairs import CUTOFF, DMAX

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
AUTO_SHARE, AUTO_MAX = 0.01, 2
POOLED, PER_DAY = (0.8, 1.25), (0.5, 2.0)
CONTROL_MARGIN = 0.02
CALM_CANCELS, CALM_FERRIES = 1, 2
GATE_SHARE = 0.9
LEVERAGE = 0.10
CUTOFF_SHARE = 0.9
PROVE_S, WATCH_S = 3600.0, 2400.0
REFERENCES = ("naive", "airline", "recorded", "anchor", "flat", "follower", "blind", "rule", "calm")
SOLVED = ("anchor", "flat", "calm")
PLAYED = ("naive", "airline", "anchor", "follower", "blind", "rule")
COLUMNS = ("task_id", "split", "kept", "flights", "moves", "connections", "anchor", "naive", "follower", "blind",
           "rule", "airline", "recorded", "flat_anchor", "naive_flat", "calm", "replay_auto", "fidelity")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="", help="comma-separated task ids, dates or date/hub (default: all)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prove-s", type=float, default=PROVE_S, help="time limit of each anchor, flat or calm solve")
    ap.add_argument("--watch-s", type=float, default=WATCH_S, help="time limit of each follower watch solve")
    ap.add_argument("--scale", default="", help="HUB=scale,... in place of the train fit (F2 then fails)")
    args = ap.parse_args()
    limits = (args.prove_s, args.watch_s)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    only = wanted(args.only)
    ones = dict.fromkeys(HUBS, 1.0)

    fits = candidates.e_table(INPUTS, DERIVED, workers=args.workers)
    e_fit, e_median = unseen.table(fits)
    every_key, counts = candidates.candidates(INPUTS)
    keys = [k for k in every_key if only(k)]
    built = candidates.build_all(keys, INPUTS, DERIVED, e_fit, e_median, ones, workers=args.workers)
    controls = [candidates.build_candidate(d, h, INPUTS, DERIVED, e_fit, e_median, ones, window=w, split="control")
                for d, h, w in candidates.controls(INPUTS, DERIVED) if only((d, h))]
    tasks = {b.task.task_id: b.task for b in built if b.task}
    held = {b.task.task_id: b.task for b in controls if b.task}
    clock.lap("build", f"{len(tasks)} of {len(keys)} candidates built, {len(held)} controls")

    event = candidates.events(d for d, _ in every_key)
    drawn = candidates.split(sorted(key(t) for t in tasks.values()), event, args.seed)
    train = [t for t in tasks.values() if drawn.get(key(t)) == "train"]
    fitted = unseen.fit_scale(train, fits)
    scale = parsed(args.scale) if args.scale else fitted
    if set(scale) != set(HUBS):
        sys.exit(f"ERROR: no train day-hubs to fit the scale at {sorted(set(HUBS) - set(scale))} "
                 f"({len(train)} train day-hubs); give --scale to run anyway, and F2 fails")
    shipped_e = unseen.table(fits, scale)
    label = {i: drawn.get(key(t), "") for i, t in tasks.items()}
    shipped = {i: rbs.rescale(replace(t, split=label.get(i, "control")), scale, *shipped_e)
               for i, t in {**tasks, **held}.items()}
    write_tasks(list(shipped.values()), out / "tasks.jsonl")
    clock.lap("split", f"eval {sum(v == 'eval' for v in drawn.values())}, train {len(train)}, scale {scale}")

    refs = run_pass(out / "refs", shipped, args.workers, args.resume, limits, clock)
    clock.lap("references")

    kept = {i for i, r in refs.items() if not unproven(r)}
    final = candidates.settle(drawn, [key(t) for i, t in tasks.items() if i in kept], event)
    split = {i: final[key(t)] for i, t in tasks.items() if key(t) in final}
    fid = {i: unseen.program_delay(t) for i, t in shipped.items() if i in tasks}
    params = {"seed": args.seed, "scale": scale, "scale_fitted_on_train": fitted, "out_of_sample": not args.scale,
              "train_day_hubs_fitted": len(train), "cutoff": CUTOFF, "dmax": DMAX, "time_limits_s": limits,
              "e_median": {"scale_1": e_median, "shipped": shipped_e[1]}, "advisories_fitted": len(fits),
              "months": list(candidates.MONTHS), "late_departures": late_departures([key(t) for t in train])}
    report = summary(tasks, held, shipped, refs, kept, drawn, split, fid, built, counts, params, clock, keys)
    rows = candidate_rows(built, controls, refs, kept, drawn, split, fid)
    (out / "candidates.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    (out / "gates.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    (out / "report.md").write_text(markdown(report))
    print(json.dumps(report["gates"], indent=1, default=str), flush=True)


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


def parsed(text: str) -> dict[str, float]:
    return {h.strip().upper(): float(v) for h, _, v in (x.partition("=") for x in text.split(",") if x.strip())}


def late_departures(keys: Iterable[tuple[str, str]]) -> dict:
    """JetBlue's recorded hub departures after local midnight on the train day-hubs, and the first whole hour by
    which CUTOFF_SHARE of them had left: reported only, the cutoff stays at its default."""
    deps = []
    for d, hub in sorted(keys):
        x = pack.local(candidates.frame(INPUTS, DERIVED, d), candidates.midnight(d))
        ours = x[(x.Reporting_Airline == candidates.CARRIER) & (x.FlightDate == d) & (x.Origin == hub)
                 & (x.Cancelled == 0) & (x.ad >= 1440)]
        deps += [int(a) for a in ours.ad]
    hour = next((60 * h for h in range(25, 49) if sum(d < 60 * h for d in deps) >= CUTOFF_SHARE * len(deps)),
                None) if deps else None
    return {"n": len(deps), "hour_holding_share": hour}


def run_pass(folder: Path, tasks: Mapping[str, Task], workers: int, resume: bool, limits: tuple[float, float],
             clock: Clock) -> dict[str, dict]:
    """`job` on every task in a process pool, heaviest first, each result written to folder/<task id>.json as it
    finishes; with `resume`, a result whose signature matches is kept."""
    folder.mkdir(parents=True, exist_ok=True)
    results, jobs = {}, []
    for i, t in tasks.items():
        sig = signature(t, limits)
        path = folder / f"{i}.json"
        if resume and path.exists():
            kept = json.loads(path.read_text())
            if kept.get("signature") == sig:
                results[i] = kept
                continue
        jobs.append((i, t.to_json(), sig, limits))
    jobs.sort(key=lambda j: weight(tasks[j[0]]), reverse=True)
    if jobs:
        with ProcessPoolExecutor(min(workers, len(jobs))) as pool:
            futures = {pool.submit(job, j): j[0] for j in jobs}
            for fut in as_completed(futures):
                r = fut.result()
                (folder / f"{r['task_id']}.json").write_text(json.dumps(r, default=str))
                results[r["task_id"]] = r
                print(f"  [{time.monotonic() - clock.t0:8.1f}s] {len(results)}/{len(tasks)} {r['task_id']} "
                      f"{brief(r)}", flush=True)
    return results


def signature(task: Task, limits: tuple[float, float]) -> str:
    return hashlib.sha256(f"{task.to_json()}|{limits}".encode()).hexdigest()[:16]


def weight(task: Task) -> int:
    return len(task.flights) * (1 + sum(len(t.repositions) for t in task.tails))


def brief(r: dict) -> str:
    parts = [f"{k}={v}s" for k, v in r.get("seconds", {}).items()]
    if unproven(r):
        parts.append(f"LEFT OUT {unproven(r)}")
    if r.get("below_anchor"):
        parts.append(f"BELOW ANCHOR {r['below_anchor']}")
    return " ".join(parts)


def job(j: tuple) -> dict:
    """Every reference of one task: its costs, counts, proofs and solve times."""
    tid, line, sig, (prove, watch) = j
    task = Task.from_dict(json.loads(line))
    out = {"task_id": tid, "signature": sig, "split": task.split, "hub": task.hub, "date": task.date,
           "slide": task.slide, "flights": len(task.flights), "tails": len(task.tails),
           "moves": sum(len(t.repositions) for t in task.tails), "watches": len(references.watch_times(task)),
           "connections": {"flown": sum(len(t.connections) for t in task.tails), "faster": len(task.quick)},
           "seconds": {}}
    anchor = {}
    attempt(out, "naive", lambda: played(task, references.naive(task)))
    attempt(out, "airline", lambda: replayed(task))
    attempt(out, "recorded", lambda: recorded(task))
    attempt(out, "anchor", lambda: anchored(task, prove, anchor))
    attempt(out, "flat", lambda: solved(references.flat_anchor(task, prove, start=anchor.get("run"))))
    attempt(out, "follower", lambda: followed(task, references.follow(task, True, watch)))
    attempt(out, "blind", lambda: followed(task, references.follow(task, False, watch)))
    attempt(out, "rule", lambda: followed(task, references.rule(task)))
    attempt(out, "calm", lambda: calmed(task, references.calm(task, prove)))
    a = out["anchor"]
    if a.get("proven"):
        costs = {k: out[k]["total"] for k in PLAYED if k != "anchor" and "total" in out[k]}
        out["below_anchor"] = references.below_anchor(costs, a["objective"])
    return out


def attempt(out: dict, name: str, play: Callable[[], dict]) -> None:
    t0 = time.monotonic()
    try:
        out[name] = play()
    except Exception as e:
        out[name] = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    out["seconds"][name] = round(time.monotonic() - t0, 1)


def played(task: Task, run: sim.Execution) -> dict:
    return {**run.cost.to_dict(), **run.counts, "seats_ok": references.seats_ok(task, run)}


def replayed(task: Task) -> dict:
    air = references.airline(task)
    return {**played(task, air.run), "auto": air.auto, "flew": air.flew, "causes": causes(task, air.run)}


def causes(task: Task, run: sim.Execution) -> list[dict]:
    """Where the record says why, each flight JetBlue flew by its deadline that the replay auto-cancels: JetBlue's
    aircraft started the day elsewhere than the schedule puts it, reached the origin on no leg BTS has, a Move carried
    the aircraft of the leg before it away, the leg before it auto-cancelled too, the tail flew its legs out of
    scheduled order; else other."""
    cancelled = {f.id for f in task.flights if f.actual.dep is None}
    carried, _ = sim.routes(task, cancelled)
    before, last = {}, {}
    for f in sorted((f for f in task.flights if f.actual.dep is not None), key=lambda f: (f.actual.dep, f.id)):
        before[f.id], last[f.actual.tail] = last.get(f.actual.tail), f
    tails = {t.tail: t for t in task.tails}
    out = []
    for i in references.counted(task):
        if run.flights[i].status != AUTO_CANCELLED:
            continue
        f, p, t = task.flight(i), before[i], tails.get(task.flight(i).actual.tail)
        order = [g for g in (t.legs if t else ()) if task.flight(g).actual.dep is not None]
        if p is None:
            cause = "start position" if t is None or t.pos != f.orig else "other"
        elif p.actual.end != f.orig:
            cause = "repositioning BTS lacks"
        elif p.id in carried:
            cause = "carried by a Move"
        elif run.flights[p.id].status == AUTO_CANCELLED:
            cause = "leg before auto-cancelled"
        elif order != sorted(order, key=lambda g: (task.flight(g).actual.dep, g)):
            cause = "flown out of scheduled order"
        else:
            cause = "other"
        out.append({"id": i, "flight": f.flight, "orig": f.orig, "tail": f.actual.tail, "cause": cause})
    return out


def recorded(task: Task) -> dict:
    cost, ferries = references.recorded(task)
    return {**cost.to_dict(), "ferries": ferries}


def anchored(task: Task, prove: float, keep: dict) -> dict:
    sol, run = references.anchor(task, prove)
    keep["run"] = run
    exact = run.cost.total == sol.objective and all(
        (run.flights[i].dep if run.flights[i].status == FLOWN else None) == d for i, d in sol.deps.items())
    return {**sol.to_dict(), **run.cost.to_dict(), **run.counts, "proven": sol.proven, "exact": exact,
            "seats_ok": references.seats_ok(task, run), "past_midnight": references.past_midnight(task, run)}


def solved(sol) -> dict:
    return {**{k: v for k, v in sol.to_dict().items() if k in ("status", "objective", "gap_pct", "wall_s")},
            "proven": sol.proven}


def followed(task: Task, f: references.Followed) -> dict:
    cancels = [v for v in f.plan.values() if v.get("cancel")]
    walls = [w.get("wall_s", 0) for w in f.watches]
    return {**played(task, f.run), "watch_solves": sum("wall_s" in w for w in f.watches),
            "max_watch_s": max(walls, default=0), "sum_watch_s": round(sum(walls), 1),
            "decided": dict(Counter(v["decided"] for v in cancels))}


def calmed(task: Task, c: references.Calm) -> dict:
    """The calm day's forced cancellations, those of a leg a Move binds or that leaves where one arrives (`moved`),
    and its ferries."""
    ids = sorted(i for i, d in c.solution.deps.items() if d is None)
    near = {m.leg for t in task.tails for m in t.repositions} | {
        j for t in task.tails for k, j in enumerate(t.legs) if k and t.legs[k - 1] in task.moved}
    return {"cancelled": c.cancelled, "ferries": c.ferries, "ids": ids, "moved": sum(i in near for i in ids),
            **solved(c.solution)}


def unproven(r: dict) -> list[str]:
    """The references of a task's result that failed or whose MIP did not prove: the reasons it is left out."""
    if r.get("error"):
        return ["task"]
    bad = [k for k in REFERENCES if "error" in r.get(k, {"error": "missing"})]
    return bad + [k for k in SOLVED if k not in bad and not r[k]["proven"]]


def summary(tasks: Mapping[str, Task], held: Mapping[str, Task], shipped: Mapping[str, Task], refs: Mapping[str, dict],
            kept: set[str], drawn: Mapping[tuple, str], split: Mapping[str, str], fid: Mapping[str, tuple],
            built: list, counts: dict, params: dict, clock: Clock, keys: list) -> dict:
    pool = {i: r for i, r in refs.items() if i in tasks and i in kept}
    autos = [(r["airline"]["auto"], r["airline"]["flew"]) for r in pool.values()]
    replay = [replays(a, n) for a, n in autos]
    calm = [r["calm"]["cancelled"] <= CALM_CANCELS and r["calm"]["ferries"] <= CALM_FERRIES for r in pool.values()]
    ctl = {i: control_checks(refs.get(i, {}), i in kept) for i in held}
    left = {i: unproven(r) for i, r in refs.items() if i not in kept}
    gates = {
        "F1_airline_replay": gate(share(replay), len(replay),
                                  f"auto-cancels <= {AUTO_SHARE:.0%} of the flights JetBlue flew by their deadline and "
                                  f"<= {AUTO_MAX}, per kept built day-hub",
                                  {"rounded_up_share": share([a <= min(AUTO_MAX, math.ceil(AUTO_SHARE * n))
                                                              for a, n in autos]),
                                   "auto_cancels": sum(a for a, _ in autos), "flew": sum(n for _, n in autos)}),
        "F2_fidelity": fidelity_gate({i: fid[i] for i in pool}, split, {i: tasks[i].hub for i in pool},
                                     params["out_of_sample"]),
        "F3_calm_day": gate(share(calm), len(calm), f"<= {CALM_CANCELS} forced cancellation and <= {CALM_FERRIES} "
                            "ferries, per kept built day-hub"),
        "F4_controls": {"controls": ctl, "n": len(ctl), "threshold": "all 4: recorded >= anchor and naive within "
                        f"{CONTROL_MARGIN:.0%} of the flat anchor", "pass": len(ctl) == 4
                        and all(c["ok"] for c in ctl.values())},
        "F5_references": references_gate({i: r for i, r in refs.items() if i in kept}, left),
        "F6_days": days_report(tasks, drawn, split, kept, counts, built, keys),
    }
    gates["go"] = all(g["pass"] for k, g in gates.items() if k != "F6_days")
    return {"gates": gates, "parameters": params,
            "diagnostics": diagnostics(tasks, refs, pool, split, shipped),
            "tasks": [row_of(i, r, i in kept, fid.get(i)) for i, r in sorted(refs.items())],
            "seconds": clock.laps, "timing": timing(refs)}


def replays(auto: int, flew: int) -> bool:
    """F1's literal reading of one day: its auto-cancels at most AUTO_SHARE of the flights flown and at most
    AUTO_MAX."""
    return auto <= AUTO_SHARE * flew and auto <= AUTO_MAX


def gate(value: float | None, n: int, rule: str, alt: dict | None = None) -> dict:
    return {"value": value, "threshold": GATE_SHARE, "n": n, "rule": rule,
            "pass": value is not None and value >= GATE_SHARE, **(alt or {})}


def share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def control_checks(r: dict, kept: bool) -> dict:
    if not kept:
        return {"ok": False, "left_out": unproven(r) if r else ["missing"]}
    naive_ok = r["naive"]["flat"] <= (1 + CONTROL_MARGIN) * r["flat"]["objective"]
    recorded_ok = r["recorded"]["total"] >= r["anchor"]["objective"]
    return {"ok": naive_ok and recorded_ok, "recorded": r["recorded"]["total"], "anchor": r["anchor"]["objective"],
            "recorded_ge_anchor": recorded_ok, "naive_flat": r["naive"]["flat"], "flat_anchor": r["flat"]["objective"],
            "naive_flat_over_anchor": round(r["naive"]["flat"] / r["flat"]["objective"] - 1, 4),
            "naive_within_margin": naive_ok,
            "pax_delay": {"recorded": r["recorded"]["delay"], "anchor": r["anchor"]["delay"]}}


def fidelity_gate(fid: Mapping[str, tuple], split: Mapping[str, str], hub: Mapping[str, str],
                  out_of_sample: bool) -> dict:
    """F2: per split and hub, the modelled over the observed other-carrier program delay, pooled over the day-hubs
    whose own ratio is within PER_DAY; passes only when every pool is within POOLED and the scale was fitted on
    train."""
    out = {"threshold": {"pooled": POOLED, "per_day_filter": PER_DAY}, "out_of_sample": out_of_sample}
    ok = out_of_sample
    for s in ("eval", "train"):
        for h in HUBS:
            days = [fid[i] for i in fid if split.get(i) == s and hub[i] == h]
            p = pooled(days)
            out[f"{s}_{h}"] = p
            ok = ok and p["ratio"] is not None and POOLED[0] <= p["ratio"] <= POOLED[1]
    return {**out, "pass": ok}


def pooled(days: list[tuple[float, float, int]]) -> dict:
    """Modelled over observed program delay over the days whose own ratio is within PER_DAY; the others, and those
    without observed delay, counted."""
    kept = [(m, o) for m, o, _ in days if o > 0 and PER_DAY[0] <= m / o <= PER_DAY[1]]
    observed = sum(o for _, o in kept)
    measured = sum(1 for _, o, _ in days if o > 0)
    return {"ratio": round(sum(m for m, _ in kept) / observed, 4) if observed else None, "days": len(kept),
            "dropped": measured - len(kept), "no_delay": len(days) - measured}


def references_gate(kept: Mapping[str, dict], left: Mapping[str, list[str]]) -> dict:
    """F5 over the kept tasks: anchors proven and exact, no reference below a proven anchor, pax <= seats."""
    not_exact = sorted(i for i, r in kept.items() if not r["anchor"]["exact"])
    below = {i: r["below_anchor"] for i, r in kept.items() if r.get("below_anchor")}
    seats = sorted(i for i, r in kept.items() if not all(r[k]["seats_ok"] for k in PLAYED))
    gaps = [r["anchor"]["gap_pct"] for r in kept.values()]
    return {"kept": len(kept), "left_out": len(left), "left_out_by": dict(Counter(k for v in left.values() for k in v)),
            "left_out_tasks": dict(sorted(left.items())), "anchor_not_exact": not_exact, "below_anchor": below,
            "pax_over_seats": seats, "max_anchor_gap_pct": max(gaps, default=None),
            "pass": bool(kept) and not not_exact and not below and not seats}


def days_report(tasks: Mapping[str, Task], drawn: Mapping[tuple, str], split: Mapping[str, str], kept: set[str],
                counts: dict, built: list, keys: list) -> dict:
    """F6: the built day-hubs by hub and season, the seeded split as drawn and as kept, and why candidates failed."""
    def by(ids: Iterable[str]) -> dict:
        return dict(sorted(Counter(f"{tasks[i].hub} {candidates.season(tasks[i].date)}" for i in ids).items()))

    eval_drawn = [i for i, t in tasks.items() if drawn.get(key(t)) == "eval"]
    return {"candidates": counts["candidates"], "keys_run": len(keys), "built": len(tasks), "built_by": by(tasks),
            "kept": len(kept & set(tasks)), "drawn": {"eval": by(eval_drawn),
                                                     "train": sum(v == "train" for v in drawn.values()),
                                                     "neither": len(tasks) - len(drawn)},
            "split": {"eval": by(i for i, v in split.items() if v == "eval"),
                      "train": sum(v == "train" for v in split.values())},
            "eval_dropped": sorted(i for i in eval_drawn if i not in kept),
            "not_built": dict(Counter(b.row.get("reason") for b in built if b.task is None)),
            "rejected": counts["rejected"], "archive_gap_dates": counts["archive_gap_dates"],
            "counts": {k: v for k, v in counts.items() if isinstance(v, int)}, "pass": True}


def diagnostics(tasks: Mapping[str, Task], refs: Mapping[str, dict], pool: Mapping[str, dict],
                split: Mapping[str, str], shipped: Mapping[str, Task]) -> dict:
    """What the report reads beside the gates: leverage, the replay's auto-cancels by cause, the connections JetBlue
    flew faster than the rule, the references over the anchor, the solve times."""
    flat = [r["naive"]["flat"] / r["flat"]["objective"] - 1 for r in pool.values()]
    notice = [r["naive"]["total"] / r["anchor"]["objective"] - 1 for r in pool.values()]
    every = [r for r in refs.values() if "auto" in r.get("airline", {})]
    over = {k: dist1([100 * (r[k]["total"] / r["anchor"]["objective"] - 1) for r in pool.values()
                      if "total" in r.get(k, {})]) for k in ("naive", "follower", "blind", "rule", "airline")}
    return {
        "leverage": {"flat": {**dist1(flat), "share_ge_10pct": share([v >= LEVERAGE for v in flat])},
                     "notice": {**dist1(notice), "share_ge_10pct": share([v >= LEVERAGE for v in notice])}},
        "auto_cancel_causes": {"all_tasks": dict(Counter(c["cause"] for r in every for c in r["airline"]["causes"])),
                               "kept_built": dict(Counter(c["cause"] for r in pool.values()
                                                          for c in r["airline"]["causes"])),
                               "by_task": {r["task_id"]: r["airline"]["causes"] for r in every
                                           if r["airline"]["causes"]}},
        "connections": {"flown": sum(len(x.connections) for t in shipped.values() for x in t.tails),
                        "faster_than_rule": sum(len(t.quick) for t in shipped.values()),
                        "by_task": {i: [sum(len(x.connections) for x in t.tails), len(t.quick)]
                                    for i, t in sorted(shipped.items())}},
        "over_anchor_pct": over,
        "recorded_over_anchor": dist1([r["recorded"]["total"] / r["anchor"]["objective"] for r in pool.values()]),
        "replay_over_recorded": dist1([r["airline"]["total"] / r["recorded"]["total"] for r in pool.values()
                                       if r["recorded"]["total"]]),
        "calm_cancels": {"total": sum(r["calm"]["cancelled"] for r in pool.values()),
                         "next_to_a_move": sum(r["calm"].get("moved", 0) for r in pool.values())},
        "anchor_cancels_per_day": dist1([r["anchor"]["cancelled"] + r["anchor"]["auto_cancelled"]
                                         for r in pool.values()]),
        "jetblue_cancels_per_day": dist1([r["airline"]["cancelled"] for r in pool.values()]),
        "mip": {k: dist1([r["anchor"][k] for r in pool.values()]) for k in ("cols", "rows", "copies")},
        "unseen_share": unseen.unseen_share([tasks[i] for i, v in split.items() if v == "train"]),
        "movements": movements([tasks[i] for i, v in split.items() if v == "train"]),
    }


def movements(train: list[Task]) -> dict:
    """The unseen stream's share of program-hour demand on the train days beside the share of each airport's own
    counted arrivals that BTS misses (BTS: arrivals that operated, over the cached months)."""
    out = unseen.unseen_share(train)
    days = {m: len(pack.month_days(m)) for m in candidates.MONTHS}
    for hub in HUBS:
        n = 0
        for m in candidates.MONTHS:
            x = candidates.month_frame(INPUTS, DERIVED, m)
            n += int(((x.Dest == hub) & (x.Cancelled == 0) & x.FlightDate.str.startswith(m)).sum())
        out[hub] = {**out.get(hub, {}), **unseen.missed_share(hub, n / sum(days.values()), candidates.MONTHS, days)}
    return out


def row_of(i: str, r: dict, kept: bool, fid: tuple | None) -> dict:
    costs = {k: r[k]["total"] for k in ("anchor", "naive", "follower", "blind", "rule", "airline", "recorded")
             if "total" in r.get(k, {})}
    calm = r.get("calm", {})
    return {"task_id": i, "split": r.get("split"), "kept": kept, "left_out": unproven(r), "flights": r.get("flights"),
            "moves": r.get("moves"), "connections": list((r.get("connections") or {}).values()), **costs,
            "flat_anchor": (r.get("flat") or {}).get("objective"), "naive_flat": (r.get("naive") or {}).get("flat"),
            "calm": [calm.get("cancelled"), calm.get("ferries")],
            "replay_auto": [(r.get("airline") or {}).get("auto"), (r.get("airline") or {}).get("flew")],
            "fidelity": round(fid[0] / fid[1], 3) if fid and fid[1] > 0 else None,
            "anchor_gap_pct": (r.get("anchor") or {}).get("gap_pct"), "seconds": r.get("seconds", {})}


def candidate_rows(built: list, controls: list, refs: Mapping[str, dict], kept: set[str], drawn: Mapping[tuple, str],
                   split: Mapping[str, str], fid: Mapping[str, tuple]) -> list[dict]:
    rows = []
    for b in [*built, *controls]:
        row = dict(b.row)
        i = row.get("task_id")
        if b.task is None:
            rows.append({**row, "built": False})
            continue
        r = refs.get(i, {})
        rows.append({**row, "built": True, "slide": b.task.slide, "build_clauses": b.task.clauses,
                     "drawn": "control" if b.task.split == "control" else drawn.get(key(b.task)),
                     "split": "control" if b.task.split == "control" else split.get(i), "kept": i in kept,
                     "left_out": unproven(r) if r else ["missing"], "fidelity": list(fid[i]) if i in fid else None,
                     "replay_auto": [(r.get("airline") or {}).get("auto"), (r.get("airline") or {}).get("flew")]})
    return rows


def timing(refs: Mapping[str, dict]) -> dict:
    per = defaultdict(list)
    for r in refs.values():
        for k, v in r.get("seconds", {}).items():
            per[k].append(v)
    total = [sum(r.get("seconds", {}).values()) for r in refs.values()]
    solves = {"anchor_wall_s": [r["anchor"]["wall_s"] for r in refs.values() if "wall_s" in r.get("anchor", {})],
              "max_watch_s": [r[k]["max_watch_s"] for r in refs.values() for k in ("follower", "blind")
                              if "max_watch_s" in r.get(k, {})]}
    return {"per_task": {k: dist1(v) for k, v in per.items()}, "task_total": dist1(total),
            "solves": {k: dist1(v) for k, v in solves.items()}, "sum_s": round(sum(total), 1)}


def dist1(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {"n": len(v), "min": round(v[0], 3), "p10": round(percentile(v, 10), 3),
            "median": round(statistics.median(v), 3), "p90": round(percentile(v, 90), 3), "max": round(v[-1], 3),
            "mean": round(statistics.mean(v), 3)}


def percentile(values: list[float], pct: float) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, math.ceil(pct / 100 * len(v)) - 1)]


def markdown(report: dict) -> str:
    g = report["gates"]
    lines = [f"# Storm-day world check: numbers ({datetime.now(UTC).date().isoformat()})", "", "## Gates", "",
             "| Gate | Value | Threshold | Pass |", "|---|---|---|---|"]
    for name, x in g.items():
        if name == "go":
            continue
        value = {k: v for k, v in x.items() if k not in ("threshold", "pass", "rule", "controls", "left_out_tasks")}
        lines.append(f"| {name} | {json.dumps(value, default=str)} | {x.get('threshold')} | {x['pass']} |")
    lines += ["", f"All gated pass: {g['go']}", "", "## Controls", "",
              f"```\n{json.dumps(g['F4_controls']['controls'], indent=1, default=str)}\n```", "", "## Parameters", "",
              f"```\n{json.dumps(report['parameters'], indent=1, default=str)}\n```", "", "## Diagnostics", "",
              f"```\n{json.dumps(report['diagnostics'], indent=1, default=str)}\n```", "", "## Timing", "",
              f"```\n{json.dumps({'stages_s': report['seconds'], **report['timing']}, indent=1, default=str)}\n```",
              "", "## Per task", "", "| " + " | ".join(COLUMNS) + " | seconds |", "|" + "---|" * (len(COLUMNS) + 1)]
    for row in report["tasks"]:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in COLUMNS) + f" | {json.dumps(row['seconds'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
