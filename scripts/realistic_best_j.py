"""The best plan found under JetBlue's cost J in realistic mode, per day (spike, throwaway).

The pack's search (crewplan.search: the aircraft MIP's optimum, crewed, then its local search over departures and
cancellations, every plan replayed in the crew sim with passengers) with J in place of cost P:
- the aircraft MIP built without passengers and repriced: a flight's copy costs its aircraft's minutes alone
  (cost.flown_price with no passengers), a cancellation the lost revenue of its booked passengers (each itinerary's
  fare by realistic.od_fare, times LOST, JetBlue's Q1 2014 rate, for one start, times 1 for a second: the linear MIP
  can't see refunds rise as a day's seats run out) less SAVE (the report's fit, --save) of what it saves;
- a replay's worth is J as the realism report prices it: aircraft with SAVE of its cancellations' saving, crew,
  tomorrow at the aircraft's cost, the refunded at their fares and the stranded at TAKE (the report's fit, --take) of
  theirs.
Seeds (crewed by the crew sim's default rule): the desks' day, JetBlue's replay, the pack's best plan under cost P and
the cut@Nh probes. The plan found is replayed in the env's truth (occ.run.truth) and priced there.

Usage: .venv/bin/python scripts/realistic_best_j.py REALREP_DIR OUT_DIR --take T --save S [--budget 1200] [--workers 4]
"""

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from agentenv_airops import realistic
from agentenv_airops.occ import run as occrun
from agentenv_airops.occ.pack import Pack3
from agentenv_airops.programs import crewplan, sim
from agentenv_airops.programs.cost import RATES, flown_price

PACK = Path.home() / "Code" / "agentenv-airops-plugin" / "data" / "b6-v3"
LOST = 50e6 / (4100 * 7_333_000 / 68_152 * 167.69)
CUTS = (2, 4, 6)


def j_of(a: dict, take: float, save: float) -> float:
    return (a["aircraft"] + (1 - save) * a["saved"] + a["crew"] + a["tomorrow"] + a["refunded_rev"]
            + take * a["stranded_rev"])


def repriced(air: crewplan.Aircraft, task, world, booked: dict, share: float, save: float):
    """The MIP's cost vector under J, a cancelled passenger losing `share` of its fare."""
    net = air.net
    vec = air.notice.copy()
    for (i, d), c in net.xs.items():
        vec[c] = flown_price(task.flight(i), d, d + world.block[i], pax=0)
    for i, c in (*net.cancel.items(), *net.lapsed.items(), *net.on_reserve.items()):
        f = task.flight(i)
        vec[c] = round(share * booked.get(i, 0.0) - save * RATES[f.family].saved * f.sched_block)
    return vec


def one(task_id: str, rep_dir: str, out_dir: str, budget: float, gate_caps: dict, rate: float, take: float,
        save: float) -> str:
    t0 = time.monotonic()
    w = Pack3(PACK).world(task_id)
    task_r = realistic.layered(w.task)
    w_r = replace(w, task=task_r)
    world_r = realistic.world(task_r, gate_caps, rate)
    firsts = realistic.first_departures(w.task)
    row = json.load(open(Path(rep_dir) / f"{task_r.date}.json"))
    crewplan.worth = lambda day, flat: j_of(realistic.airline(task_r, day, firsts, w.passengers), take, save)
    booked: dict = {}
    for g in w.passengers.groups:
        v = g.pax * realistic.od_fare(task_r, g.legs)
        for i in g.legs:
            booked[i] = booked.get(i, 0.0) + v
    air = crewplan.Aircraft(task_r, world_r)
    desks0 = sim.execute(task_r, {}, world_r)
    starts = []
    for label, share in (("J optimum, full fare", 1.0), ("J optimum", LOST)):
        air.notice = repriced(air, task_r, world_r, booked, share, save)
        air.flat = air.notice.copy()
        starts.append((label, air.optimum(air.cost(), air.ub, desks0, 300.0)[0]))
    gone = [f for f in w.task.flights if f.actual.cancelled or f.actual.diverted]
    jb = {f.id: {"cancel": True, "decided": max(f.sched_dep - 360, task_r.start)} for f in gone}
    desks = row["real"]["desks"]["flights"]
    seeds = [("desks", {}), ("jetblue", jb), ("best P", w.best_plan)]
    for h in CUTS:
        seeds.append((f"cut@{h}h", {f.id: {"cancel": True, "decided": max(f.sched_dep - 120, task_r.start)}
                                    for f in task_r.flights if desks[str(f.id)]["status"] == "flown"
                                    and desks[str(f.id)]["arr"] - f.sched_arr >= 60 * h}))
    found = crewplan.search(task_r, world_r, w.units, w.local, w.airports, air, starts,
                            time.monotonic() + budget, crew_limit=300.0, air_limit=300.0, days=seeds,
                            pax=w.passengers)
    day = occrun.truth(w_r, found.plan, world_r)
    out = {"date": task_r.date, "optima": {k: v.objective for k, v in starts}, "search_value": found.value,
           "attempts": found.attempts, "plan": {str(k): v for k, v in found.plan.items()},
           "airline": realistic.airline(task_r, day, firsts, w.passengers),
           "flights": {str(i): {"status": r.status, "dep": r.dep, "arr": r.arr} for i, r in day.run.flights.items()},
           "seconds": round(time.monotonic() - t0, 1)}
    Path(out_dir, f"{task_r.date}.json").write_text(json.dumps(out, default=str))
    canc = sum(r.status != "flown" for r in day.run.flights.values())
    return f"{task_r.date} {out['seconds']}s J {j_of(out['airline'], take, save) * 1.07 / 1e6:.2f}M cancelled {canc}"


def main():
    rep_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    budget = float(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else 1200.0
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
    take = float(sys.argv[sys.argv.index("--take") + 1])
    save = float(sys.argv[sys.argv.index("--save") + 1])
    out_dir.mkdir(parents=True, exist_ok=True)
    pack = Pack3(PACK)
    ids = {v["date"]: t for t, v in pack.tasks.items()}
    dates = sorted(p.stem for p in rep_dir.glob("*.json"))
    if "--dates" in sys.argv:
        dates = sys.argv[sys.argv.index("--dates") + 1].split(",")
    gate_caps = realistic.gates(PACK / "dates")
    rate = json.load(open(rep_dir / f"{dates[0]}.json"))["realistic"]["rate"]
    todo = [d for d in dates if not (out_dir / f"{d}.json").exists()]
    t0 = time.monotonic()
    with ProcessPoolExecutor(workers, max_tasks_per_child=1) as pool:
        futures = {pool.submit(one, ids[d], str(rep_dir), str(out_dir), budget, gate_caps, rate, take, save): d
                   for d in todo}
        for n, f in enumerate(as_completed(futures), 1):
            try:
                msg = f.result()
            except Exception as e:
                msg = f"{futures[f]} FAILED {type(e).__name__}: {e}"
            print(f"[{time.monotonic() - t0:6.0f}s] {n}/{len(todo)} {msg}", flush=True)


if __name__ == "__main__":
    main()
