"""Replay sample b6-v3 days in the current mode and in realistic mode (agentenv_airops.realistic), per flight, for the
realism report (spike, throwaway).

Scenarios, each played by occ.run.truth (the standard crew desk crews what the plan leaves alone, passengers on board):
  desks        the empty plan (the pack's B0)
  jbsim@N      JetBlue's cancellations and diversions only, each decided N min before its scheduled departure
               (120, 360, or "start": the day's start); everything else as the desks fly it
  jbfull@360   jbtails@360 with JetBlue's slot use in the programs too: each controlled or ground-stopped flight
               claims the slot at its recorded departure plus its scheduled block (its CDM substitutions)
  jbtails@360  jbsim@360 with JetBlue's own aircraft assignments too: every flight takes the aircraft JetBlue flew
               it with when that aircraft is at its origin (no other swap anywhere)
  model        the plan GPT-6 Astra made on the 2026-10-23 stream (its day record), on the 13 stream days
  best         the pack's best plan found under cost P
  cut@Nh       a probe, in realistic mode only: the desks' day with every flight the desks fly N h or more late
               cancelled 2 h before its scheduled departure (hindsight, one pass)
In realistic mode each scenario is also priced without the passenger model ("tdi": v2's cost, a cancelled passenger
457 min of delay plus the 66 refund exposure, the Total Delay Impact study's disrupted-passenger average).

Usage: .venv/bin/python scripts/realistic_compare.py OUT_DIR --dates D1,D2,... [--run RUN_DIR] [--workers 6]
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

PACK = Path.home() / "Code" / "agentenv-airops-plugin" / "data" / "b6-v3"
LEADS = {"jbsim@120": 120, "jbsim@360": 360, "jbsim@start": None}
CUTS = (2, 4, 6)


def plans(w, model_plan: dict | None) -> dict[str, dict]:
    task = w.task
    out = {"desks": {}}
    gone = [f for f in task.flights if f.actual.cancelled or f.actual.diverted]
    for name, lead in LEADS.items():
        out[name] = {f.id: {"cancel": True, "decided": task.start if lead is None else max(f.sched_dep - lead,
                                                                                            task.start)}
                     for f in gone}
    if model_plan is not None:
        out["model"] = model_plan
    out["best"] = w.best_plan
    return out


def played(task, day) -> dict:
    return {i: {"status": r.status, "dep": r.dep, "arr": r.arr} for i, r in day.run.flights.items()}


def one(task_id: str, out_dir: str, model_path: str | None, gate_caps: dict, rate: float) -> str:
    t0 = time.monotonic()
    w = Pack3(PACK).world(task_id)
    model_plan = None
    if model_path:
        raw = json.load(open(model_path))["plan"]
        model_plan = {(int(k) if k.isdigit() else k): v for k, v in raw.items()}
    task_r = realistic.layered(w.task)
    w_r = replace(w, task=task_r)
    world_now, world_r = sim.truth(w.task), realistic.world(task_r, gate_caps, rate)
    firsts = realistic.first_departures(w.task)
    lay = task_r.layers
    row = {"task_id": task_id, "date": w.task.date, "split": w.task.split, "refs": {"best": w.refs["best"],
           "B0": w.refs["B0"]["notice"]},
           "realistic": {"dep_caps": sum(len(v) for v in lay.dep_caps.values()),
                         "arr_caps": sum(len(v) for v in lay.arr_caps.values()),
                         "closures": sum(len(v) for v in lay.closures.values()),
                         "broken": dict(world_r.broken), "rate": rate},
           "now": {}, "real": {}}
    for name, plan in plans(w, model_plan).items():
        day = occrun.truth(w, plan, world_now)
        row["now"][name] = {"cost": realistic.rated(w.task, day.run, crewplan.worth(day, False), firsts),
                            "flights": played(w.task, day)}
        day = occrun.truth(w_r, plan, world_r)
        row["real"][name] = {"cost": realistic.rated(task_r, day.run, crewplan.worth(day, False), firsts),
                             "flights": played(task_r, day), "airline": realistic.airline(task_r, day, firsts, w.passengers)}
    jb = plans(w, None)["jbsim@360"]
    full = jb | {f.id: {"claim": f.actual.dep + f.sched_block} for f in w.task.flights
                 if f.id not in jb and f.actual.dep is not None and (f.controlled or f.gs_held)}
    row["claims"] = len(full) - len(jb)
    for mode, ww, base in (("now", w, world_now), ("real", w_r, world_r)):
        for name, plan in (("jbtails@360", jb), ("jbfull@360", full)):
            day = occrun.truth(ww, plan, replace(base, swaps=frozenset()))
            row[mode][name] = {"cost": realistic.rated(ww.task, day.run, crewplan.worth(day, False), firsts),
                               "flights": played(ww.task, day)}
            if mode == "real":
                row[mode][name]["airline"] = realistic.airline(ww.task, day, firsts, w.passengers)
    desks = row["real"]["desks"]["flights"]
    for hours in CUTS:
        cut = {f.id: {"cancel": True, "decided": max(f.sched_dep - 120, task_r.start)} for f in task_r.flights
               if desks[f.id]["status"] == "flown" and desks[f.id]["arr"] - f.sched_arr >= 60 * hours}
        day = occrun.truth(w_r, cut, world_r)
        row["real"][f"cut@{hours}h"] = {"cost": realistic.rated(task_r, day.run, crewplan.worth(day, False), firsts),
                                        "flights": played(task_r, day),
                                        "airline": realistic.airline(task_r, day, firsts, w.passengers)}
    w_tdi = replace(w_r, passengers=None)
    for name, plan in (*plans(w, model_plan).items(), ("jbfull@360", full)):
        base = replace(world_r, swaps=frozenset()) if name == "jbfull@360" else world_r
        day = occrun.truth(w_tdi, plan, base)
        row["real"][name]["cost"]["tdi"] = realistic.rated(task_r, day.run, crewplan.worth(day, False),
                                                           firsts)["realistic"]
    row["seconds"] = round(time.monotonic() - t0, 1)
    Path(out_dir, f"{w.task.date}.json").write_text(json.dumps(row))
    return f"{w.task.date} {row['seconds']}s layers {row['realistic']} "


def main():
    out_dir = Path(sys.argv[1])
    dates = sys.argv[sys.argv.index("--dates") + 1].split(",")
    run = Path(sys.argv[sys.argv.index("--run") + 1]) if "--run" in sys.argv else None
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
    out_dir.mkdir(parents=True, exist_ok=True)
    pack = Pack3(PACK)
    ids = {v["date"]: t for t, v in pack.tasks.items()}
    models = {}
    if run:
        for line in (run / "results.jsonl").open():
            r = json.loads(line)
            if r.get("reward") is not None and r.get("day"):
                models[r["date"]] = str(run / r["day"])
    gate_caps = realistic.gates(PACK / "dates")
    months = sorted({m for d in dates for m in realistic.candidates.months_of(d)})
    rate = realistic.breakdown_rate(tuple(months))
    print(f"gates at {len(gate_caps)} stations; breakdown rate {rate:.5f} per leg over {months}", flush=True)
    todo = [d for d in dates if not (out_dir / f"{d}.json").exists()]
    t0 = time.monotonic()
    with ProcessPoolExecutor(workers, max_tasks_per_child=1) as pool:
        futures = {pool.submit(one, ids[d], str(out_dir), models.get(d), gate_caps, rate): d for d in todo}
        for n, f in enumerate(as_completed(futures), 1):
            try:
                msg = f.result()
            except Exception as e:
                msg = f"{futures[f]} FAILED {type(e).__name__}: {e}"
            print(f"[{time.monotonic() - t0:6.0f}s] {n}/{len(todo)} {msg}", flush=True)


if __name__ == "__main__":
    main()
