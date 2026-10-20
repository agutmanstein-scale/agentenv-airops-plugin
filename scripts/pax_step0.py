"""Step 0 of AirOpsEnv v3, the passengers' premise, as registered: with passengers priced by cost P (programs.pax),
does what the aircraft plan and the rebooking decide change enough to matter? Aircraft only, the v2 step-0 world (the
whole day from 04:00, the truth); no LLM is called.

    python scripts/pax_step0.py --out cache/v3/step0/<run> [--workers 72] [--split eval] [--only DATE,...]
        [--levels 1,0.5,2] [--layers off] [--time-limit 1800] [--inputs cache/inputs] [--derived cache/derived]
        [--board results/runs/occ-board-sol] [--resume] [--summarize]

One job per date and share level k (the date's survey flows times k: 1 the measured shares, which gate; 0.5 and 2 the
sensitivity):
- P(naive): the empty plan, its passengers rebooked first come first served (FCFS);
- A_v2: v2's references.anchor plan (the hindsight MIP under v2's cost), scored under P, FCFS;
- A_pax: the passenger-aware MIP (mip.Network with pax.Proxy) started from whichever the proxy prices lowest of A_v2's
  day, the empty plan's and its LP relaxation rounded (Network.rounded), --time-limit seconds in all,
  scored under P, FCFS, and with its passengers rebooked optimally (pax.optimal, an LP over the day's seats);
- avoidable = P(naive) - P(A_pax, optimal); lever (a), the plan, P(A_v2) - P(A_pax); lever (b), the rebooking,
  P(A_pax) - P(A_pax, optimal); combined P(A_v2) - P(A_pax, optimal); each also as a share of avoidable;
- G1 (k = 1): the combined share is at least 5% on at least 12 of the 24 eval dates; G2 (k = 1): A_pax is proven within
  1% inside 30 minutes on at least 20 of 24;
- reported, not gating: the changed cancellations (auto-cancels included) and holds (references.deliberate's) as
  |A_v2 xor A_pax| / |A_v2 or A_pax|, departures moved by more than 5 and 15 minutes, misconnected and stranded
  passengers; at k = 1 the crew world: the pack's joint best plan and B0, and the Sol board's days (--board, when
  present), under P against their v2 cost.
Layers (--layers on, Alec's world layers) are not in this tree; the switch is kept for the 2x2.
Every job writes dates/<date>-k<k>.json as it finishes (--resume keeps those written); guards.json and report.md are
recomputed from those files alone (--summarize does only that).
"""

import argparse
import glob
import json
import resource
import statistics
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from agentenv_airops.build import nextday
from agentenv_airops.core.sim import FLOWN
from agentenv_airops.occ.pack import Pack
from agentenv_airops.programs import crewsim, mip, pax, references, sim
from agentenv_airops.programs.cost import REFUND

G1_SHARE, G1_DATES, G2_GAP, G2_WALL, G2_DATES = 0.05, 12, 1.0, 1800.0, 20
MARGIN = 30.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--split", default="eval", help="eval, train, control or all")
    ap.add_argument("--only", default="", help="comma-separated dates")
    ap.add_argument("--levels", default="1,0.5,2")
    ap.add_argument("--layers", choices=("off", "on"), default="off")
    ap.add_argument("--time-limit", type=float, default=G2_WALL)
    ap.add_argument("--inputs", type=Path, default=Path("cache/inputs"))
    ap.add_argument("--derived", type=Path, default=Path("cache/derived"))
    ap.add_argument("--board", type=Path, default=Path("results/runs/occ-board-sol"))
    ap.add_argument("--pack", type=Path, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    a = ap.parse_args()
    if a.layers == "on":
        sys.exit("--layers on: the world layers are not in this tree")
    days = a.out / "dates"
    days.mkdir(parents=True, exist_ok=True)
    if not a.summarize:
        tasks = Pack(a.pack).tasks
        only = set(filter(None, a.only.split(",")))
        dates = sorted(v["date"] for v in tasks.values() if (a.split == "all" or v["split"] == a.split)
                       and (not only or v["date"] in only))
        levels = [float(k) for k in a.levels.split(",")]
        todo = [(d, k) for d in dates for k in levels if not (a.resume and path(days, d, k).exists())]
        print(f"{len(todo)} jobs on {min(a.workers, max(1, len(todo)))} workers", file=sys.stderr)
        with ProcessPoolExecutor(min(a.workers, max(1, len(todo))), max_tasks_per_child=1) as ex:
            futures = {ex.submit(job, d, k, a): (d, k) for d, k in todo}
            for fut in as_completed(futures):
                d, k = futures[fut]
                try:
                    row = fut.result()
                except Exception:
                    row = {"date": d, "k": k, "error": traceback.format_exc()}
                path(days, d, k).write_text(json.dumps(row, indent=1))
                print(f"{d} k={k:g}: {brief(row)}", file=sys.stderr)
    summary = guards(a.out)
    (a.out / "guards.json").write_text(json.dumps(summary, indent=1))
    (a.out / "report.md").write_text(report(summary))
    print(json.dumps({g: summary[g]["pass"] for g in ("G1", "G2")}))


def path(days: Path, date: str, k: float) -> Path:
    return days / f"{date}-k{k:g}.json"


def job(date: str, k: float, a: argparse.Namespace) -> dict:
    t0 = time.monotonic()
    pack = Pack(a.pack)
    tid = next(t for t, v in pack.tasks.items() if v["date"] == date)
    w = pack.world(tid)
    task = w.task
    flows = pax.Flows.load(date)
    flows = flows if k == 1 else flows.scaled(k)
    passengers = pax.book(task, flows, pax.Dwell.load(), nextday.seats(task, a.inputs, a.derived))
    proxy = pax.Proxy(task, passengers)
    t1 = time.monotonic()
    anchor, _ = references.anchor(task)
    plan_v2 = anchor.plan(task.start)
    t2 = time.monotonic()
    net = mip.Network(task, pax=proxy)
    starts = [sim.execute(task, plan_v2), sim.execute(task, {})]
    lp_plan = net.rounded(a.time_limit)
    if lp_plan is not None:
        starts.append(sim.execute(task, lp_plan))
    start = min(starts, key=proxy.cost)
    sol = net.solve(max(60.0, a.time_limit - (time.monotonic() - t2) - MARGIN), canonical=False,
                    start=net.vector(start))
    t3 = time.monotonic()
    naive = sim.execute(task, {}, pax=passengers)
    v2 = sim.execute(task, plan_v2, pax=passengers)
    best = sim.execute(task, sol.plan(task.start), pax=passengers)
    optimal = pax.optimal(task, passengers, best.flights)
    p = {"naive": naive.cost.total, "v2": v2.cost.total, "pax": best.cost.total,
         "pax_optimal": best.cost.total - best.pax.delay - best.pax.lodging + optimal.delay + optimal.lodging}
    room = p["naive"] - p["pax_optimal"]
    levers = {"plan": p["v2"] - p["pax"], "rebooking": p["pax"] - p["pax_optimal"],
              "combined": p["v2"] - p["pax_optimal"]}
    row = {"date": date, "task_id": tid, "split": pack.tasks[tid]["split"], "k": k, "flows": flows.label,
           "passengers": counts(passengers), "P": p, "avoidable": room, "levers": levers,
           "shares": {n: v / room if room > 0 else None for n, v in levers.items()},
           "anchor": anchor.to_dict(), "mip": {**sol.to_dict(), "proxy_replay": round(proxy.cost(best), 1),
                                               "start": round(proxy.cost(start), 1)},
           "G2": {"proven_1pct": sol.gap_pct <= G2_GAP and t3 - t2 <= G2_WALL, "gap_pct": sol.gap_pct,
                  "wall_s": round(t3 - t2, 1)},
           "changed": changed(task, v2, best), "costs": {n: r.cost.to_dict() for n, r in
                                                            (("naive", naive), ("v2", v2), ("pax", best))},
           "outcomes": {n: {**r.pax.outcomes(), "misconnected": r.pax.misconnected()} for n, r in
                        (("naive", naive), ("v2", v2), ("pax", best))},
           "optimal_outcomes": optimal.outcomes(),
           "wall_s": {"build": round(t1 - t0, 1), "anchor": round(t2 - t1, 1), "pax_mip": round(t3 - t2, 1),
                      "total": round(time.monotonic() - t0, 1)},
           "rss_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2 ** 30 if sys.platform == "darwin"
                                                                                  else 2 ** 20), 2)}
    if k == 1:
        row["crew"] = crew_world(w, passengers, a.board)
    return row


def counts(p: pax.Passengers) -> dict:
    conn = sum(g.pax for g in p.groups if len(g.legs) == 2)
    trips = sum(g.pax for g in p.groups)
    return {"groups": len(p.groups), "trips": trips, "connecting": conn, "connecting_share": conn / trips,
            "next_day_flights": len(p.tomorrow)}


def changed(task, v2: sim.Execution, best: sim.Execution) -> dict:
    out = {}
    for name, run in (("v2", v2), ("pax", best)):
        cut = {i for i, r in run.flights.items() if r.status != FLOWN}
        plan = {i: {"cancel": True, "decided": task.start} for i in cut}
        held = references.deliberate(task, plan, {i: r.dep for i, r in run.flights.items() if i not in cut},
                                     sim.truth(task), task.start)
        out[name] = (cut, set(held))
    both = [i for i in v2.flights if i not in out["v2"][0] | out["pax"][0]]
    moved = [abs(best.flights[i].dep - v2.flights[i].dep) for i in both]

    def share(a: set, b: set) -> float | None:
        return len(a ^ b) / len(a | b) if a | b else None

    (c1, h1), (c2, h2) = out["v2"], out["pax"]
    return {"cancelled": {"v2": len(c1), "pax": len(c2), "changed_share": share(c1, c2)},
            "held": {"v2": len(h1), "pax": len(h2), "changed_share": share(h1, h2)},
            "either_changed_share": share(c1 | h1, c2 | h2),
            "moved_over_5": sum(m > 5 for m in moved), "moved_over_15": sum(m > 15 for m in moved)}


def crew_world(w, passengers: pax.Passengers, board: Path) -> dict:
    """The pack's joint best plan and B0, and the Sol board's days of the date, under P and under v2's cost."""
    task = w.task
    out = {}
    for name, plan, kw, ref in (("best", w.best_plan, {"fill": False}, w.refs["best"]),
                                ("B0", {}, {"desk": True}, w.refs["B0"]["notice"])):
        day = crewsim.execute(task, plan, w.units, local=w.local, airports=w.airports, pax=passengers, **kw)
        out[name] = {"P": day.total, "v2": ref, "outcomes": day.run.pax.outcomes()}
    size = {u.id: u.size for u in w.units}
    for f in sorted(glob.glob(str(board / "days" / f"{task.task_id}-*.json"))):
        d = json.loads(Path(f).read_text())
        runs = {r["id"]: sim.FlightRun(**r) for r in d["grade"]["executed"]}
        riders: dict[int, int] = {}
        for uid, c in d["crews"].items():
            for duty in c["duties"]:
                for dh in duty["deadheads"]:
                    riders[dh[0]] = riders.get(dh[0], 0) + size[uid]
        day = pax.fly(task, passengers, runs, riders)
        lost = sum(task.flight(i).pax for i, r in runs.items() if r.status != FLOWN)
        v2 = d["grade"]["costs"]
        out[Path(f).stem] = {"P": v2["total"] - v2["delay"] - v2["cancel"] + day.delay + REFUND * lost + day.lodging,
                             "v2": v2["total"], "outcomes": day.outcomes()}
    return out


def brief(row: dict) -> str:
    if "error" in row:
        return "ERROR " + row["error"].strip().splitlines()[-1]
    s = row["shares"]
    return (f"combined {pct(s['combined'])} plan {pct(s['plan'])} rebooking {pct(s['rebooking'])}; mip "
            f"{row['mip']['status']} gap {row['mip']['gap_pct']}% {row['mip']['wall_s']} s")


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def guards(out: Path) -> dict:
    rows = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(out / "dates" / "*.json")))]
    ok = [r for r in rows if "error" not in r]
    base = [r for r in ok if r["k"] == 1 and r["split"] == "eval"]
    g1 = [r["date"] for r in base if (r["shares"]["combined"] or 0) >= G1_SHARE]
    g2 = [r["date"] for r in base if r["G2"]["proven_1pct"]]
    by_k = {}
    for k in sorted({r["k"] for r in ok}):
        rs = [r for r in ok if r["k"] == k]
        by_k[f"{k:g}"] = {n: med([r["shares"][n] for r in rs]) for n in ("plan", "rebooking", "combined")} | {
            "dates": len(rs), "changed_either": med([r["changed"]["either_changed_share"] for r in rs]),
            "mip_gap_pct": med([r["mip"]["gap_pct"] for r in rs]), "mip_wall_s": med([r["mip"]["wall_s"] for r in rs])}
    return {"G1": {"rule": f"combined share >= {G1_SHARE:.0%} on >= {G1_DATES} of 24 eval dates at k = 1",
                   "dates": len(base), "passing": len(g1), "pass": len(base) == 24 and len(g1) >= G1_DATES,
                   "passing_dates": g1},
            "G2": {"rule": f"passenger MIP proven within {G2_GAP:g}% inside {G2_WALL:g} s on >= {G2_DATES} of 24",
                   "dates": len(base), "passing": len(g2), "pass": len(base) == 24 and len(g2) >= G2_DATES,
                   "passing_dates": g2},
            "medians": by_k, "errors": [(r["date"], r["k"]) for r in rows if "error" in r]}


def med(vs: list) -> float | None:
    vs = [v for v in vs if v is not None]
    return statistics.median(vs) if vs else None


def report(s: dict) -> str:
    lines = ["# v3 step 0: passengers (numbers only)", ""]
    for g in ("G1", "G2"):
        verdict = "PASS" if s[g]["pass"] else "FAIL"
        lines.append(f"- {g}: {verdict}, {s[g]['passing']} of {s[g]['dates']} ({s[g]['rule']})")
    lines += ["", "| k | dates | plan share | rebooking share | combined share | changed cancels+holds | MIP gap % "
              "| MIP s |", "|---|---|---|---|---|---|---|---|"]
    for k, m in s["medians"].items():
        lines.append(f"| {k} | {m['dates']} | {pct(m['plan'])} | {pct(m['rebooking'])} | {pct(m['combined'])} | "
                     f"{pct(m['changed_either'])} | {m['mip_gap_pct']} | {m['mip_wall_s']} |")
    if s["errors"]:
        lines += ["", f"errors: {s['errors']}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
