"""The two desks of the control center, scripted, with and without the channel between them: is the two-desk world worth
building? Its two bars and its checks were registered before the run. Only the units at the start are rebuilt, with
the crew run's solver; the policies solve no MIP and no LLM is called.

    python scripts/occ_check.py --out cache/<run> --run cache/<crew_check run> --desk cache/<its desk replay>
                                --split <o26_split.json> --tasks cache/<world_check run> [--only DATE|TASK_ID,...]
                                [--workers 4] [--resume] [--bar1-only]

Per date with a row in --desk (the eval dates of --split gated, every other date reported), in its own process:
1. the trips and the units at the start as the desk replay rebuilt them (crewday.storm_units on --run's budgets);
2. F1, the same world as the crew run: the morning replay reproduces --run's (objective and flights without a crew), the
   standard crew desk's day reproduces --desk's (notice and flat within 0.5), and --run's stored best plans (notice,
   flat) replay with their crews named (fill off) at its best (within 0.5);
3. the modes of programs/occ.py (baseline, quiet-idle, talk-idle, quiet, quiet-flags, talk, one) and the check-only run
   `at once` (F7: idle ops accepting each CANCEL at its sent minute reproduces the baseline; reported);
4. per mode F2 (notice-priced not below the bound less 0.5, flat not below the flat bound where there is one), F3 (every
   flight flown with a legal crew of each kind, the day replayed from its named plan with fill off within 0.5, the audit
   of that plan empty), F8 (occ.roles) and F4 (the watches where something before t changed; reported).
It writes dates/<date>.json and logs/<date>/<mode>.json (the crew desk's messages in the request log's shape with
ops's replies, ops's plan with each cancellation's cause, each watch's decisions and the day's flights) as each date
finishes (`--resume` keeps those written), then gates.json and report.md.

The bars, as registered (an eval date failing F1, F2, F3 or F8, or with an error, fails both):
- bar 1, room to gain: room = the standard crew desk's flat cost / the flat best plan - 1 (--desk's crew_desk.flat and
  joint.flat.best); a date passes with room >= 10% and its desk replay on the run's units (units_as_run). Reported: room
  priced with notice, room against the bound (the flat bound, else the notice-priced one) and the dates between the
  two readings;
- bar 2, coordination: gain = 100 (min(C(quiet), C(quiet-flags)) - C(talk)) / max(B0 - L, 0.1 L), notice-priced, B0
  the baseline (the standard crew desk with the aircraft doing nothing), L the date's bound; a date passes with
  gain >= 5. Reported: the literal reading, 100 (C(quiet) - C(talk)) / (B0 - best);
- each passes on at least 60% of --split's eval dates (rounded up). With `--bar1-only` bar 1 alone is read from --desk
  and --split, before any check, and nothing runs.
Reported, not gated: each mode's cost and its share of B0 - best and of B0 - L, splitting the job (quiet* - one) and
talking (quiet* - talk) in passenger-minutes and in points of max(B0 - L, 0.1 L), messages sent and accepted,
cancellations by cause and auto-cancels, crew cost, the controls, every O26-kept date and every storm date, run times
and peak memory.
"""

import argparse
import hashlib
import json
import math
import resource
import statistics
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from agentenv_airops.core.sim import FLOWN
from agentenv_airops.programs import crewday, crewplan, crewsim, occ
from agentenv_airops.programs.model import Task

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
ROOM, GAIN, FLOOR, GATE_SHARE = 0.10, 5.0, 0.10, 0.6
TOL = 0.5
GATED = ("quiet", "quiet-flags", "talk")
RSS_UNIT = 1 << 30 if sys.platform == "darwin" else 1 << 20


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run", type=Path, required=True, help="the crew_check run: its best plans and bounds")
    ap.add_argument("--desk", type=Path, required=True, help="its desk replay: the standard crew desk")
    ap.add_argument("--split", type=Path, required=True, help="o26_split.json: the eval dates under key eval")
    ap.add_argument("--tasks", type=Path, help="the world_check run whose tasks.jsonl the crew run read")
    ap.add_argument("--only", default="", help="comma-separated dates or task ids (default: all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--bar1-only", action="store_true")
    a = ap.parse_args()
    split = json.loads(a.split.read_text())
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    a.out.mkdir(parents=True, exist_ok=True)
    rows = {p.stem: json.loads(p.read_text()) for p in sorted((a.desk / "dates").glob("*.json"))}
    rows = {d: r for d, r in rows.items() if not only or d in only or r.get("task_id") in only}
    if a.bar1_only:
        report = {"bar1": bar1(rows, split, None), "bar1_only": True, "inputs": inputs(a), "commit": commit()}
        (a.out / "gates.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
        (a.out / "report.md").write_text(markdown(report, rows, {}, split))
        print(json.dumps(report["bar1"]["gate"], indent=1), flush=True)
        return
    if a.tasks is None:
        sys.exit("ERROR: --tasks is required unless --bar1-only")
    for sub in ("dates", "logs"):
        (a.out / sub).mkdir(parents=True, exist_ok=True)
    lines = {}
    for line in (a.tasks / "tasks.jsonl").open():
        head = json.loads(line[:line.index(',"tz"')] + "}")
        if head["date"] in rows:
            lines[head["date"]] = line
    todo = [d for d in sorted(lines) if not (a.resume and (a.out / "dates" / f"{d}.json").exists())]
    todo.sort(key=lambda d: -len(lines[d]))
    t0 = time.monotonic()
    if todo:
        with ProcessPoolExecutor(min(a.workers, len(todo)), max_tasks_per_child=1) as pool:
            futures = {pool.submit(job, lines[d], a.run, a.desk, a.out): d for d in todo}
            for n, f in enumerate(as_completed(futures), 1):
                print(f"[{time.monotonic() - t0:8.0f}s] {n}/{len(todo)} {futures[f]} {f.result()}", flush=True)
    done = {d: json.loads((a.out / "dates" / f"{d}.json").read_text()) for d in sorted(rows)
            if (a.out / "dates" / f"{d}.json").exists()}
    report = gates(rows, done, split)
    report |= {"inputs": inputs(a), "commit": commit(), "seconds": round(time.monotonic() - t0, 1)}
    (a.out / "gates.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    (a.out / "report.md").write_text(markdown(report, rows, done, split))
    print(json.dumps({k: report[k]["gate"] for k in ("bar1", "bar2")} | {"go": report["go"]}, indent=1), flush=True)


def inputs(a) -> dict:
    return {"run": str(a.run), "desk": str(a.desk), "split": str(a.split), "tasks": str(a.tasks), "only": a.only}


def job(line: str, run: Path, desk: Path, out: Path) -> str:
    t0 = time.monotonic()
    task = Task.from_dict(json.loads(line))
    row = {"date": task.date, "task_id": task.task_id, "split": task.split}
    try:
        row |= check(task, json.loads((run / "dates" / f"{task.date}.json").read_text()),
                     json.loads((desk / "dates" / f"{task.date}.json").read_text()),
                     run / "plans" / f"{task.date}.json", out / "logs" / task.date)
    except Exception:
        row["error"] = traceback.format_exc()
    row["seconds"] = round(time.monotonic() - t0, 1)
    row["peak_rss_gb"] = rss()
    (out / "dates" / f"{task.date}.json").write_text(json.dumps(row, indent=1, default=str))
    return "error" if "error" in row else f"{row['seconds']}s {row['peak_rss_gb']}GB"


def rss() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_UNIT, 2)


def check(task: Task, ran: dict, desk: dict, plans_path: Path, logs: Path) -> dict:
    if "error" in ran or "error" in desk:
        raise RuntimeError("error in the crew run or its desk replay")
    laps = {}
    t0 = time.monotonic()
    w = crewday.world(task, INPUTS, DERIVED)
    units, before = crewday.storm_units(w, "middle", ran["budget"]["morning"], ran["budget"]["polish"])
    laps["setup"] = round(time.monotonic() - t0, 1)
    case = occ.Case.of(w, units)
    j = ran["bands"]["middle"]["joint"]
    bound, flat_bound, best = j["notice"].get("bound"), j["flat"].get("bound"), j["notice"]["best"]
    if bound is None:
        raise RuntimeError("the crew run has no bound on this date")
    logs.mkdir(parents=True, exist_ok=True)
    modes, runs = {}, {}
    for name in occ.MODES:
        r = occ.run(case, name)
        runs[name] = r
        modes[name] = mode_row(case, r, bound, flat_bound, best)
        modes[name]["peak_rss_gb"] = rss()
        write_log(logs / f"{name}.json", case, r)
    t1 = time.monotonic()
    at = occ.at_once(case)
    write_log(logs / "at-once.json", case, at)
    base = runs["baseline"].day
    differ = sorted(i for i, x in at.day.run.flights.items() if x != base.run.flights[i])
    f7 = {"same": occ.same(at.day, base), "flights": differ, "notice": round(at.day.total - base.total, 1),
          "cancels": len(at.plan), "days": at.days, "seconds": round(time.monotonic() - t1, 1)}
    t1 = time.monotonic()
    f1 = same_world(case, ran, desk, before, base, json.loads(plans_path.read_text()))
    laps["f1"] = round(time.monotonic() - t1, 1)
    b0 = crewplan.worth(base, False)
    out = {"flights": len(task.flights), "watches": len(occ.watches(task)), "round_trips": len(case.trips),
           "moved_legs": len(task.moved), "laps": laps, "F1": f1, "F7": f7, "modes": modes,
           "best": best, "bound": bound, "flat_best": j["flat"]["best"], "flat_bound": flat_bound, "B0": round(b0, 1)}
    out["checks_ok"] = f1["ok"] and all(m["F2"]["ok"] and m["F3"]["ok"] and not m["F8"] for m in modes.values())
    out["bar2"] = gain(b0, bound, best, {k: modes[k]["notice"] for k in modes})
    return out


def same_world(case: occ.Case, ran: dict, desk: dict, before: dict, base: crewsim.CrewDay, plans: dict) -> dict:
    """F1: the units, the standard crew desk and the stored best plans reproduce the crew run's."""
    task = case.task
    morning = ran["bands"]["middle"]["record"]["before_start"]
    units = {k: [before.get(k), morning.get(k)] for k in ("objective", "uncrewed")}
    played = desk["bands"]["middle"]["joint"]["played"]["crew_desk"]
    b0 = {"notice": [round(crewplan.worth(base, False), 1), played["notice"]],
          "flat": [round(crewplan.worth(base, True), 1), played["flat"]]}
    j = ran["bands"]["middle"]["joint"]
    stored = {}
    for name in ("notice", "flat"):
        plan = {int(k) if k != "reports" else k: v for k, v in plans[name].items()}
        day = crewsim.execute(task, plan, case.units, case.world, case.local, fill=False, airports=case.airports)
        stored[name] = [round(crewplan.worth(day, name == "flat"), 1), j[name]["best"]]
    as_run = desk.get("desk_replay", {}).get("units_as_run") is True
    failed = [k for k, bad in (("units", any(a != b for a, b in units.values())), ("units_as_run", not as_run),
                               ("B0", any(abs(a - b) > TOL for a, b in b0.values())),
                               ("stored", any(abs(a - b) > TOL for a, b in stored.values()))) if bad]
    return {"ok": not failed, "failed": failed, "units": units, "units_as_run": as_run, "B0": b0, "stored": stored}


def mode_row(case: occ.Case, r: occ.Run, bound: float, flat_bound: float | None, best: float) -> dict:
    task, day = case.task, r.day
    notice, flat = crewplan.worth(day, False), crewplan.worth(day, True)
    named = crewday.named(day)
    replay = crewsim.execute(task, named, case.units, case.world, case.local, fill=False, airports=case.airports)
    deps = {i: x.dep for i, x in day.run.flights.items() if x.status == FLOWN}
    audit = crewsim.audit(task, case.units, named, deps, {i: day.run.flights[i].arr - d for i, d in deps.items()},
                          case.local, case.airports)
    f2 = {"ok": notice >= bound - TOL and (flat_bound is None or flat >= flat_bound - TOL),
          "below_best": notice < best - TOL}
    f3 = {"ok": not day.uncrewed and abs(crewplan.worth(replay, False) - notice) <= TOL and not audit,
          "uncrewed": list(day.uncrewed), "replayed": round(crewplan.worth(replay, False), 1), "audit": audit[:20]}
    return {"notice": round(notice, 1), "flat": round(flat, 1), "aircraft": day.run.cost.to_dict(),
            "crew_cost": {k: round(v, 1) for k, v in day.cost.items()}, **occ.counts(r), "F2": f2, "F3": f3,
            "F8": occ.roles(r, occ.MODES[r.mode], task), "F4": r.acausal,
            "days": r.days, "seconds": r.seconds, "digest": digest(r)}


def digest(r: occ.Run) -> str:
    day = r.day
    body = json.dumps([day.run.rows(), sorted(day.crews.items()), sorted(day.riders.items()), r.messages(),
                       sorted(r.plan.items()), sorted(r.causes.items())], default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def write_log(path: Path, case: occ.Case, r: occ.Run) -> None:
    flights = [[x.id, x.status, x.dep, x.arr, x.decided, x.tail] for x in r.day.run.flights.values()]
    path.write_text(json.dumps({"mode": r.mode, "plan": {i: {**e, "cause": r.causes.get(i)} for i, e in
                                                         sorted(r.plan.items())},
                                "messages": r.messages(), "watches": r.watches, "acausal": r.acausal,
                                "flights": flights, "crews": r.day.crews, "digest": digest(r)}, default=str))


def gain(b0: float, bound: float, best: float, cost: dict[str, float]) -> dict:
    """Bar 2's reading of one date, and the reported ones."""
    quiet = min(cost["quiet"], cost["quiet-flags"])
    scale = max(b0 - bound, FLOOR * bound)
    g = 100 * (quiet - cost["talk"]) / scale
    span = b0 - best
    return {"gain": round(g, 4), "pass": g >= GAIN, "quiet_star": quiet, "scale": round(scale, 1),
            "literal": None if span <= 0 else round(100 * (cost["quiet"] - cost["talk"]) / span, 4),
            "split_costs": round(quiet - cost["one"], 1),
            "split_costs_pts": round(100 * (quiet - cost["one"]) / scale, 4),
            "talking_recovers": round(quiet - cost["talk"], 1),
            "share_best": {k: None if span <= 0 else round((b0 - v) / span, 4) for k, v in cost.items()},
            "share_bound": {k: round((b0 - v) / (b0 - bound), 4) if b0 > bound else None for k, v in cost.items()}}


def room(r: dict) -> dict:
    """Bar 1's reading of one --desk row."""
    if "error" in r:
        return {"error": True, "pass": False}
    j = r["bands"]["middle"]["joint"]
    desk, flat, notice = j["played"]["crew_desk"], j["flat"], j["notice"]
    value = desk["flat"] / flat["best"] - 1
    under = flat.get("bound"), notice.get("bound")
    upper = desk["flat"] / under[0] - 1 if under[0] is not None else \
        desk["notice"] / under[1] - 1 if under[1] is not None else None
    as_run = r.get("desk_replay", {}).get("units_as_run") is True
    return {"room": round(value, 4), "upper": None if upper is None else round(upper, 4),
            "notice": round(desk["notice"] / notice["best"] - 1, 4),
            "undetermined": upper is not None and value < ROOM <= upper, "units_as_run": as_run,
            "flat_from": flat.get("from"), "pass": value >= ROOM and as_run}


def roles_of(rows: dict[str, dict], split: dict) -> dict[str, str]:
    """Each date's part: eval, train or neither (kept by O26), dropped by O26, or control."""
    kept = {**{d: "eval" for d in split["eval"]}, **{d: "train" for d in split["train"]},
            **{d: "neither" for d in split["neither"]}}
    return {d: "control" if r.get("split") == "control" else kept.get(d, "dropped") for d, r in rows.items()}


def need(n: int) -> int:
    return math.ceil(GATE_SHARE * n - 1e-9)


def bar1(rows: dict[str, dict], split: dict, done: dict[str, dict] | None) -> dict:
    per = {d: room(r) for d, r in rows.items()}
    if done is not None:
        for d, v in per.items():
            v["checks_ok"] = d in done and "error" not in done[d] and done[d].get("checks_ok", False)
            v["pass"] = v["pass"] and v["checks_ok"]
    evals = sorted(split["eval"])
    for d in evals:
        per[d] = per.get(d, {"missing": True, "pass": False})
        per[d]["why"] = [] if per[d]["pass"] else why(per[d])
    passed = [d for d in evals if per[d]["pass"]]
    part = roles_of(rows, split)
    return {"gate": {"n": len(evals), "need": need(len(evals)), "passed": len(passed),
                     "pass": len(passed) >= need(len(evals)) and bool(evals),
                     "rule": f"room >= {ROOM:.0%} on >= {GATE_SHARE:.0%} of eval dates",
                     "checks_applied": done is not None},
            "eval": {d: per[d] for d in evals},
            "failed": {d: per[d]["why"] for d in evals if not per[d]["pass"]},
            "undetermined": sorted(d for d in evals if per.get(d, {}).get("undetermined")),
            "controls": {d: per[d] for d in per if part.get(d) == "control"},
            "kept": summary([per[d] for d in per if part.get(d) in ("eval", "train", "neither")]),
            "storm": summary([per[d] for d in per if d in part and part[d] != "control"])}


def why(v: dict) -> list[str]:
    """Why a date fails bar 1."""
    if v.get("missing") or v.get("error"):
        return ["missing" if v.get("missing") else "error in the desk replay"]
    return [w for w, bad in (("units_as_run false", not v["units_as_run"]), ("room under 10%", v["room"] < ROOM),
                             ("checks", v.get("checks_ok") is False)) if bad]


def summary(vs: list[dict]) -> dict:
    ok = [v for v in vs if "room" in v]
    return {"n": len(vs), "errors": len(vs) - len(ok), "room": dist([v["room"] for v in ok]),
            "notice": dist([v["notice"] for v in ok]),
            "upper": dist([v["upper"] for v in ok if v["upper"] is not None]),
            "ge_10pct": sum(v["room"] >= ROOM for v in ok), "undetermined": sum(v["undetermined"] for v in ok)}


def gates(rows: dict[str, dict], done: dict[str, dict], split: dict) -> dict:
    evals = sorted(split["eval"])
    part = roles_of(rows, split)
    per = {}
    for d in evals:
        r = done.get(d)
        if r is None or "error" in r:
            per[d] = {"pass": False, "why": ["missing" if r is None else "error"]}
        else:
            fails = [k if k != "F1" else f"F1: {', '.join(r['F1']['failed'])}" for k in ("F1", "F2", "F3", "F8")
                     if not fine(r, k)]
            per[d] = {**r["bar2"], "checks_ok": r["checks_ok"], "pass": r["bar2"]["pass"] and r["checks_ok"],
                      "why": fails + ([] if r["bar2"]["pass"] else ["gain under 5"])}
    passed = [d for d in evals if per[d]["pass"]]
    b2 = {"gate": {"n": len(evals), "need": need(len(evals)), "passed": len(passed),
                   "pass": len(passed) >= need(len(evals)) and bool(evals),
                   "rule": f"gain >= {GAIN} points of max(B0 - L, {FLOOR} L) on >= {GATE_SHARE:.0%} of eval dates"},
          "eval": per, "failed": {d: per[d]["why"] for d in evals if not per[d]["pass"]},
          "literal": {"ge_5": sum((per[d].get("literal") or -1e9) >= GAIN for d in evals), "n": len(evals)}}
    b1 = bar1(rows, split, done)
    ok = [r for r in done.values() if "error" not in r]
    out = {"bar1": b1, "bar2": b2, "go": b1["gate"]["pass"] and b2["gate"]["pass"],
           "checks": checks(done, evals), "reported": reported(ok, part), "timing": timing(list(done.values()))}
    return out


def checks(done: dict[str, dict], evals: list[str]) -> dict:
    ok = {d: r for d, r in done.items() if "error" not in r}
    failing = {d: [k for k in ("F1", "F2", "F3", "F8") if not fine(r, k)] for d, r in ok.items()}
    return {"dates": len(done), "errors": sorted(d for d, r in done.items() if "error" in r),
            "failing": {d: v for d, v in sorted(failing.items()) if v},
            "every_date_fails": [k for k in ("F1", "F2", "F3", "F8") if ok and all(k in v for v in failing.values())],
            "F4_dates": sorted(d for d, r in ok.items() if any(m["F4"] for m in r["modes"].values())),
            "F7_same": sum(r["F7"]["same"] for r in ok.values()),
            "F7_differ": {d: {k: r["F7"][k] for k in ("flights", "notice")} for d, r in sorted(ok.items())
                          if not r["F7"]["same"]},
            "eval_errors": sorted(d for d in evals if d in done and "error" in done[d])}


def fine(r: dict, k: str) -> bool:
    if k == "F1":
        return r["F1"]["ok"]
    if k == "F8":
        return not any(m["F8"] for m in r["modes"].values())
    return all(m[k]["ok"] for m in r["modes"].values())


def reported(ok: list[dict], part: dict[str, str]) -> dict:
    out = {}
    for group, sel in (("eval", ("eval",)), ("control", ("control",)), ("kept", ("eval", "train", "neither")),
                       ("storm", ("eval", "train", "neither", "dropped"))):
        rs = [r for r in ok if part.get(r["date"]) in sel]
        out[group] = {"n": len(rs), "gain": dist([r["bar2"]["gain"] for r in rs]),
                      "literal": dist([r["bar2"]["literal"] for r in rs if r["bar2"]["literal"] is not None]),
                      "split_costs_pts": dist([r["bar2"]["split_costs_pts"] for r in rs]),
                      "modes": {m: {"over_B0_pct": dist([100 * (r["modes"][m]["notice"] / r["B0"] - 1) for r in rs]),
                                    "share_best": dist([r["bar2"]["share_best"][m] for r in rs
                                                        if r["bar2"]["share_best"][m] is not None]),
                                    "share_bound": dist([r["bar2"]["share_bound"][m] for r in rs
                                                         if r["bar2"]["share_bound"][m] is not None]),
                                    "cancelled": {c: sum(r["modes"][m]["cancelled"][c] for r in rs)
                                                  for c in (occ.RULE, occ.CUT, occ.REQUEST, occ.FLAG)},
                                    "desk": sum(r["modes"][m]["desk"] for r in rs),
                                    "auto": sum(r["modes"][m]["auto"] for r in rs),
                                    "sent": {k: sum(r["modes"][m]["sent"][k] for r in rs) for k in ("CANCEL", "HOLD")},
                                    "accepted": {k: sum(r["modes"][m]["accepted"][k] for r in rs)
                                                 for k in ("CANCEL", "HOLD")},
                                    "crew_cost": dist([sum(r["modes"][m]["crew_cost"].values()) for r in rs])}
                                for m in occ.MODES}}
    return out


def timing(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    return {"per_date_s": dist([r["seconds"] for r in rows if "seconds" in r]),
            "sum_s": round(sum(r.get("seconds", 0) for r in rows), 1),
            "peak_rss_gb": dist([r["peak_rss_gb"] for r in rows if "peak_rss_gb" in r]),
            "setup_s": dist([r["laps"]["setup"] for r in ok]),
            "modes_s": {m: dist([r["modes"][m]["seconds"] for r in ok]) for m in occ.MODES},
            "at_once_s": dist([r["F7"]["seconds"] for r in ok])}


def dist(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {"n": len(v), "min": round(v[0], 4), "p10": round(pct(v, 10), 4), "median": round(statistics.median(v), 4),
            "p90": round(pct(v, 90), 4), "max": round(v[-1], 4), "mean": round(statistics.mean(v), 4)}


def brief(d: dict) -> str:
    return "n 0" if not d.get("n") else f"median {d['median']} (min {d['min']}, max {d['max']}, n {d['n']})"


def pct(v: list[float], p: float) -> float:
    return v[min(len(v) - 1, math.ceil(p / 100 * len(v)) - 1)]


def commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def markdown(report: dict, rows: dict[str, dict], done: dict[str, dict], split: dict) -> str:
    b1 = report["bar1"]
    g1 = b1["gate"]
    out = ["# The two desks, scripted: the premise bars", "", f"Commit {report['commit']}.", "",
           "| Bar | Passed | Needed (of N eval dates) | Pass |", "|---|---|---|---|",
           f"| 1: room >= 10% | {g1['passed']} | {g1['need']} of {g1['n']} | {g1['pass']} |"]
    if "bar2" in report:
        g2 = report["bar2"]["gate"]
        out.append(f"| 2: gain >= 5 points | {g2['passed']} | {g2['need']} of {g2['n']} | {g2['pass']} |")
        out += ["", f"Both bars pass: {report['go']}"]
    else:
        out += ["", "Bar 1 alone, read from the desk replay before any check (F1-F3, F8 not applied)."]
    controls = b1["controls"]
    out += ["", "Bar 1 on the controls (reported): " + "; ".join(f"{d} room {v.get('room')}" for d, v in
                                                                  sorted(controls.items())),
            "Under the crew desk baseline bar 1 does not separate calm days from storms: a control at or over 10% "
            "would pass it too (stated before the run).", "",
            "## Eval dates", "",
            "| Date | Bar 1 room | Room upper | Bar 1 | Gain | Literal | Checks | Bar 2 | B0 | quiet | quiet-flags "
            "| talk | one |", "|" + "---|" * 13]
    b2 = report.get("bar2", {}).get("eval", {})
    for d in sorted(split["eval"]):
        v = b1["eval"][d]
        r = done.get(d, {})
        m = r.get("modes", {})
        cost = [m.get(k, {}).get("notice") for k in ("quiet", "quiet-flags", "talk", "one")]
        g = b2.get(d, {})
        out.append(f"| {d} | {v.get('room')} | {v.get('upper')} | {v['pass']} | {g.get('gain')} | {g.get('literal')} "
                   f"| {r.get('checks_ok')} | {g.get('pass')} | {r.get('B0')} | " + " | ".join(map(str, cost)) + " |")
    for name in ("bar1", "bar2"):
        if report.get(name, {}).get("failed"):
            out += ["", f"{name[:3].title()} {name[3:]} fails on: " + "; ".join(
                f"{d} ({', '.join(w)})" for d, w in report[name]["failed"].items())]
    if done:
        part = roles_of(rows, split)
        out += ["", "## Every date", "",
                "| Date | Part | Bar 1 room | Gain | Literal | Checks | F4 | F7 | Seconds | GB |", "|" + "---|" * 10]
        for d, r in sorted(done.items()):
            if "error" in r:
                out.append(f"| {d} | {part.get(d)} | error |" + " |" * 7)
                continue
            out.append(f"| {d} | {part.get(d)} | {b1['controls'].get(d, room(rows[d])).get('room')} | "
                       f"{r['bar2']['gain']} | {r['bar2']['literal']} | {r['checks_ok']} | "
                       f"{sum(len(m['F4']) for m in r['modes'].values())} | {r['F7']['same']} | {r['seconds']} | "
                       f"{r['peak_rss_gb']} |")
    if "checks" in report:
        out += ["", "## Checks", "", f"```\n{json.dumps(report['checks'], indent=1, default=str)}\n```"]
    for group, rep in report.get("reported", {}).items():
        out += ["", f"## Reported: {group} ({rep['n']} dates)", "",
                f"Gain {brief(rep['gain'])}; literal {brief(rep['literal'])}; splitting the job costs (points) "
                f"{brief(rep['split_costs_pts'])}.", "",
                "| Mode | Over B0 % | Share of B0 - best | Share of B0 - L | Rule | Cut | Request | Flag | Desk | Auto "
                "| CANCEL sent/accepted | HOLD sent/accepted |", "|" + "---|" * 12]
        for m, v in rep["modes"].items():
            c = v["cancelled"]
            sent, accepted = v["sent"], v["accepted"]
            out.append(f"| {m} | {brief(v['over_B0_pct'])} | {brief(v['share_best'])} | {brief(v['share_bound'])} | "
                       f"{c['rule']} | {c['cut']} | "
                       f"{c['request']} | {c['flag']} | {v['desk']} | {v['auto']} | "
                       f"{sent['CANCEL']}/{accepted['CANCEL']} | {sent['HOLD']}/{accepted['HOLD']} |")
    if "timing" in report:
        out += ["", "## Timing", "", f"```\n{json.dumps(report['timing'], indent=1, default=str)}\n```"]
    out += ["", "## Bar 1 over the other dates (reported)", "",
            f"```\n{json.dumps({k: b1[k] for k in ('kept', 'storm', 'undetermined')}, indent=1, default=str)}\n```", ""]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
