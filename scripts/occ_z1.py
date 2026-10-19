"""The ops control center's zero-spend checks in process (Z1), through OccDay's calls, on a pack's dates. No LLM, no
network: the desks are scripts.

    python scripts/occ_z1.py --out cache/<run> [--pack data/b6-v2] [--only DATE|TASK_ID,...] [--workers 4]

Per date, in its own process:
- Z1a idle: both desks only end their turns; the cost equals the pack's idle reference.
- Z1b oracle ops: the stage-2b ops script (occ.watch, quiet: the rule and round-trip cuts) decides at each ops turn
  with its internals and confirms through ops_confirm, the crew desk idle; the cost equals occ.run(case, "quiet").
- Z1c channel: the crew script forwards the crew system's words (CANCEL as requests, HOLD as texts) and the ops script
  accepts each CANCEL at its turn (agents/airops-desk's Script); the cost equals occ.run(case, "talk-idle").
- Z1d anchor: the pack's best plan confirmed at the first watch (every unit it leaves idle kept off the rides its trip
  plans), both desks idle after; its cost equals the best plan's (within 0.5), reward 1.0.
- Z1e: every commit kept the past (A3), the audit passes (A1-A7), and a leak test: delays and blocks past the watch's
  LOOK changed, every call and result up to that watch is the same.
- Z1f: the largest page per desk (joint: ops and crew within 8k characters, one-agent within 12k).
- Z1g: the channel run again gives the same digest.
- One-agent: the one-desk script (cancel what the crew system can't crew, hold to its crew times).
It writes dates/<date>.json as each finishes, then summary.json: every number, the pass/fail per check, the timings.
"""

import argparse
import asyncio
import importlib.util
import json
import resource
import statistics
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from agentenv_airops.core.known import LOOK
from agentenv_airops.core.model import hhmm
from agentenv_airops.occ.day import OccDay, fid
from agentenv_airops.occ.pack import Pack, World
from agentenv_airops.programs import crewplan, occ, sim

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("airops_desk", ROOT / "agents" / "airops-desk" / "agent.py")
desk_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desk_agent)
TOL = 0.5
LIMITS = {"ops": 8000, "crew": 8000, "one": 12000}
ROLE = {"ops": "ops", "crew": "crew", "one": "both"}
LEAK_AT = 720
RSS_UNIT = 1 << 30 if sys.platform == "darwin" else 1 << 20


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=ROOT / "data" / "b6-v2")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    pack = Pack(a.pack)
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    ids = [t for t, v in sorted(pack.tasks.items(), key=lambda kv: kv[1]["date"])
           if (not only or t in only or v["date"] in only) and v["split"] in ("eval", "control")]
    (a.out / "dates").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with ProcessPoolExecutor(min(a.workers, len(ids)), max_tasks_per_child=1) as ex:
        futures = {ex.submit(job, str(a.pack), t, a.out): t for t in ids}
        for n, f in enumerate(as_completed(futures), 1):
            print(f"[{time.monotonic() - t0:7.0f}s] {n}/{len(ids)} {futures[f]} {f.result()}", flush=True)
    rows = [json.loads(p.read_text()) for p in sorted((a.out / "dates").glob("*.json"))]
    report = summary(rows, time.monotonic() - t0)
    (a.out / "summary.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("dates", "passed", "failed", "checks")}, indent=1), flush=True)


def job(pack: str, task_id: str, out: Path) -> str:
    t0 = time.monotonic()
    w = Pack(pack).world(task_id)
    row = {"task_id": task_id, "date": w.task.date, "split": w.task.split}
    try:
        row |= checks(w)
    except Exception:
        row["error"] = traceback.format_exc()
    row["seconds"] = round(time.monotonic() - t0, 1)
    row["peak_rss_gb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / RSS_UNIT, 2)
    (out / "dates" / f"{w.task.date}.json").write_text(json.dumps(row, indent=1, default=str))
    return "error" if "error" in row else f"{'ok' if row['ok'] else 'FAIL ' + ','.join(row['failed'])} " \
                                           f"{row['seconds']}s"


def idle(page: str, call) -> None:
    return None


async def play(w: World, mode: str, policies, upto: int | None = None) -> OccDay:
    """The day played turn by turn by `policies` (desk: async (page, call), or a function of the day giving them),
    each watch's advisories delivered as the trigger delivers them, every desk resigning once the day is over; with
    `upto`, played to that minute."""
    day = OccDay(w, mode)
    policies = policies(day) if callable(policies) else policies
    laps = []
    while not day.done and (upto is None or day.t <= upto):
        desk = day.turn["desk"]
        for n in list(day.pending):
            day.notice(n, "trigger")
        t0 = time.monotonic()
        page = day.call(ROLE[desk], "wait_turn", {})
        t1 = time.monotonic()

        async def call(name: str, args: dict, desk=desk) -> dict:
            return json.loads(json.dumps(day.call(ROLE[desk], name, args), default=str))
        out = policies.get(desk, idle)(page["page"], call)
        if asyncio.iscoroutine(out):
            await out
        t2 = time.monotonic()
        day.call(ROLE[desk], "end_turn", {"note": None, "stand_down": False})
        laps.append({"watch": page["watch"], "desk": desk, "page_s": round(t1 - t0, 2), "act_s": round(t2 - t1, 2),
                     "end_s": round(time.monotonic() - t2, 2)})
    if day.done:
        for d in day.desks:
            day.call(ROLE[d], "resign", {"reason": "done"})
    day.laps = laps
    return day


def scripts(*desks: str) -> dict:
    return {d: desk_agent.Script(d).turn for d in desks}


def oracle(case: occ.Case):
    """Ops by the stage-2b quiet script with its internals, beside the env's day: its run, its log, its policies."""
    r = occ.Run("quiet", case.day({}, False))
    committed, eta, log = set(), {}, []

    def policies(day: OccDay) -> dict:
        async def turn(page: str, call) -> None:
            had = set(r.plan)
            occ.watch(case, occ.MODES["quiet"], r, day.t, committed, eta)
            if new := sorted(i for i in r.plan if i not in had):
                out = await call("ops_confirm", {"changes": [{"flight": fid(i), "action": "cancel", "time": None,
                                                              "other": None, "ref": None} for i in new]})
                if out.get("refused") or out.get("error"):
                    log.append({"t": day.t, "refused": out.get("refused"), "error": out.get("error")})
            if day.truth.run.rows() != r.day.run.rows():
                log.append({"t": day.t, "differ": True})
        return {"ops": turn}
    return r, log, policies


def case_of(w: World) -> occ.Case:
    return occ.Case(w.task, w.units, w.local, w.airports, w.trips, sim.truth(w.task))


def leaky(w: World, t: int) -> World:
    """The world with every delay a desk can't know by t (not negative, its flight due after t + LOOK) and every such
    block changed."""
    flights = tuple(replace(f, lb=f.lb + 37, block=f.block + 11) if f.sched_dep > t + LOOK and f.lb >= 0 else f
                    for f in w.task.flights)
    return replace(w, task=replace(w.task, flights=flights))


def prefix(day: OccDay, t: int) -> list:
    return [[c["desk"], c["tool"], c["args"], c["result"]] for c in day.calls if c["t"] <= t]


def best_moves(w: World) -> tuple[list[dict], list[dict]]:
    """The best plan as the desks would confirm it at the first watch: ops's holds and cancellations, crew's names,
    riders and reports."""
    plan, start = w.best_plan, w.task.start
    ops, crew = [], []
    for k, e in plan.items():
        if k == "reports":
            continue
        if e.get("cancel"):
            ops.append({"flight": fid(k), "action": "cancel", "time": None, "other": None, "ref": None})
            continue
        if e.get("not_before") is not None and e["not_before"] > start:
            ops.append({"flight": fid(k), "action": "hold", "time": hhmm(e["not_before"]), "other": None,
                        "ref": None})
        for kind, uid in e.get("crew", {}).items():
            crew.append({"action": "assign", "unit": uid, "flight": fid(k), "other": None, "kind": kind, "time": None,
                         "ref": None})
        for uid in e.get("deadhead", ()):
            crew.append({"action": "deadhead", "unit": uid, "flight": fid(k), "other": None, "kind": None,
                         "time": None, "ref": None})
    for key, r in plan.get("reports", {}).items():
        uid, _, second = key.partition("#")
        crew.append({"action": "rest" if second else "call_reserve", "unit": uid, "flight": None, "other": None,
                     "kind": None, "time": hhmm(r), "ref": None})
    named = {k.split("#")[0] for k in plan.get("reports", {})} | {
        uid for k, e in plan.items() if k != "reports" for uid in [*e.get("crew", {}).values(), *e.get("deadhead", ())]}
    for u in w.units:
        if u.deadheads and u.id not in named:
            crew.append({"action": "release", "unit": u.id, "flight": fid(u.deadheads[0]), "other": None, "kind": None,
                         "time": None, "ref": None})
    return ops, crew


def anchor(w: World):
    ops, crew = best_moves(w)
    done = {}

    def once(tool: str, rows: list[dict]):
        async def turn(page: str, call) -> None:
            if not done.get(tool):
                done[tool] = await call(tool, {"changes" if tool == "ops_confirm" else "moves": rows})
        return turn
    return {"crew": once("crew_confirm", crew), "ops": once("ops_confirm", ops)}, done


def run_row(day: OccDay) -> dict:
    g = day.grade
    pages = {}
    for p in day.shown:
        pages[p["desk"]] = max(pages.get(p["desk"], 0), p["chars"])
    laps = day.laps
    return {"cost": g["cost"], "reward": g["reward"], "reward_L": g["reward_L"], "audit": day.audit(),
            "acausal": day.acausal, "digest": day.digest(), "counts": g["counts"], "pages": pages,
            "watches_played": len({x["watch"] for x in laps}), "turns": len(laps),
            "page_s": dist([x["page_s"] for x in laps]), "act_s": dist([x["act_s"] for x in laps]),
            "end_s": dist([x["end_s"] for x in laps]), "costs": g["costs"]}


def checks(w: World) -> dict:
    case, refs = case_of(w), w.refs
    out, failed, t0 = {"refs": refs}, [], time.monotonic()
    runs = {}
    a = runs["a"] = asyncio.run(play(w, "joint", {}))
    out["Z1a"] = run_row(a) | {"want": refs["idle"]}
    if abs(a.grade["cost"] - refs["idle"]) > TOL:
        failed.append("Z1a")
    quiet = occ.run(case, "quiet")
    _, log, policies = oracle(case)
    b = runs["b"] = asyncio.run(play(w, "joint", policies))
    want = crewplan.worth(quiet.day, False)
    out["Z1b"] = run_row(b) | {"want": round(want, 1), "log": log[:20], "same_day": b.truth.run.rows() ==
                               quiet.day.run.rows()}
    if abs(b.grade["cost"] - want) > TOL or log:
        failed.append("Z1b")
    talk = occ.run(case, "talk-idle")
    c = runs["c"] = asyncio.run(play(w, "joint", scripts("crew", "ops")))
    want = crewplan.worth(talk.day, False)
    out["Z1c"] = run_row(c) | {"want": round(want, 1), "same_day": c.truth.run.rows() == talk.day.run.rows()}
    if abs(c.grade["cost"] - want) > TOL:
        failed.append("Z1c")
    moves, done = anchor(w)
    d = runs["d"] = asyncio.run(play(w, "joint", moves))
    out["Z1d"] = run_row(d) | {"want": refs["best"], "confirmed": {k: {x: v.get(x) for x in ("refused", "error")}
                                                                   for k, v in done.items()}}
    if abs(d.grade["cost"] - refs["best"]) > TOL or d.grade["reward"] < 1.0:
        failed.append("Z1d")
    leak_t = next(t for t in c.times if t >= LEAK_AT)
    e = asyncio.run(play(leaky(w, leak_t), "joint", scripts("crew", "ops"), upto=leak_t))
    out["Z1e"] = {"A3": {k: len(x.acausal) for k, x in runs.items()},
                  "audit": {k: x.audit()["ok"] for k, x in runs.items()}, "leak_at": leak_t,
                  "leak_same": prefix(e, leak_t) == prefix(c, leak_t), "leak_calls": len(prefix(c, leak_t))}
    if any(out["Z1e"]["A3"].values()) or not all(out["Z1e"]["audit"].values()) or not out["Z1e"]["leak_same"]:
        failed.append("Z1e")
    one = asyncio.run(play(w, "one", scripts("one")))
    out["one"] = run_row(one)
    pages = {k: max(x.get(k, 0) for x in [run_row(r)["pages"] for r in [*runs.values(), one]]) for k in LIMITS}
    out["Z1f"] = {"max_chars": pages, "limits": LIMITS}
    if any(pages[k] > LIMITS[k] for k in pages):
        failed.append("Z1f")
    g = asyncio.run(play(w, "joint", scripts("crew", "ops")))
    out["Z1g"] = {"digest": g.digest(), "same": g.digest() == c.digest()}
    if not out["Z1g"]["same"]:
        failed.append("Z1g")
    if not one.audit()["ok"]:
        failed.append("one-audit")
    out["scripted"] = {"joint": c.grade["cost"], "one": one.grade["cost"]}
    out["failed"], out["ok"] = failed, not failed
    out["check_seconds"] = round(time.monotonic() - t0, 1)
    return out


def dist(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    return {"n": len(v), "median": round(statistics.median(v), 2), "p90": round(v[int(0.9 * (len(v) - 1))], 2),
            "max": round(v[-1], 2), "sum": round(sum(v), 1)}


def summary(rows: list[dict], seconds: float) -> dict:
    ok = [r for r in rows if "error" not in r]
    names = ("Z1a", "Z1b", "Z1c", "Z1d", "Z1e", "Z1f", "Z1g", "one-audit")
    return {"dates": len(rows), "passed": sum(r["ok"] for r in ok), "failed": {r["date"]: r["failed"] for r in ok
                                                                                if not r["ok"]},
            "errors": sorted(r["date"] for r in rows if "error" in r),
            "checks": {n: sum(n not in r["failed"] for r in ok) for n in names}, "seconds": round(seconds, 1)}


if __name__ == "__main__":
    sys.exit(main())
