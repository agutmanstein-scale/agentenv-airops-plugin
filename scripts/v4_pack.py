"""Build v4's pack: every whole-network storm date with crew and every v4 layer, its constants fitted on the train
dates alone, and the control dates beside them (docs/v4/DESIGN.md §2, §6.1); with `--refs`, the references
scripts/v4_check.py reads, and each task's anchor and follower costs for the playable day's load.

    python scripts/v4_pack.py --out DIR [--only DATE,...] [--workers 14] [--seed 0] [--resume]
                              [--refs] [--prove-s 1800] [--watch-s 900] [--bases none|JFK,BOS,...]
                              [--crew edgar|v4] [--buffer MIN] [--max-segments N]

1. every storm date over the 19 cached months (network.storms) built as a whole-network task, without crews by default
   (v3: docs/v4/DESIGN.md §0 status; `--bases JFK,BOS,FLL,MCO` turns the crew model on at those bases, its pairings
   cut by `--crew`: Edgar's proxy, the default, or v4's legal on schedule, layers.crew, at `--buffer` and
   `--max-segments`), at scale 1 and the 03:00 cutoff, as world_check builds them, and so are the control
   dates (network.control_dates, each with its hubs' control windows, split 'control'); each date's build is written
   to DIR/built/ (v4's to DIR/built-v4-BUFFER-SEGMENTS/) as it finishes (a control's as control-DATE.json), and
   `--resume` keeps those already built there (a date left unbuilt, or a file cut short, is built again). A date an
   input is missing for (say the FAA registry, for a tail B-43 doesn't type) is unbuilt, its error the reason; any
   other error stops the pack;
2. the seeded whole-event split over the built storm dates, as world_check draws it (network.split, `--seed`, default
   0), and the unseen-traffic scale fitted on the train dates (unseen.fit_scale), every task (the controls too)
   rescaled to it;
3. the layer constants fitted on the train dates only (layers.calibrate.fit): the ramp closures' rate per hub, then,
   with `--crew v4` and `--buffer` or `--max-segments` left out, v4's crew cut over the grid of what is left out
   (layers.calibrate.crew_fit, on its seeded sample of the train dates, each built at every cut of the grid and once
   without crews, to DIR/built-nocrew/, its scores kept under DIR/crew-fit/; no cut holding the guards stops the pack,
   the fit written to the manifest), every date then built again at the cut, then
   the reserve pool per base, the cap clauses kept at 0.9 and 5;
4. every date of the split (eval and train) and every control date (`--only` a few of them, storm or control) given
   every layer at those constants (network.layered, as network.build(layers=) does), written to DIR/tasks.jsonl, the
   storm dates first, and DIR/manifest.json: the inputs the pack reads beyond each task's own pins (`sources`) with
   their sha256 (BTS on-time per month, the hubs' IEM METARs, SPECIs and TAFs per month), the seed, the split, the
   controls and the scale, the crew mode with its buffer, max segments and fit, the constants with the dates they were
   fitted on, the counts per task and per layer, and the wall clock per phase;
5. with `--refs`, the references on every task written (`refs`, below): one JSON per task under DIR/refs/ as it
   finishes, in a pool of `--workers` processes, heaviest first, a result kept when its task's signature (the task, the
   references and the time limits) matches; then DIR/refs.json, each task's anchor and follower costs on the truth
   with the task's digest (references.digest), which the playable day's load reads (v4day.live) so it never solves a
   MIP.

The references per task (world_check's, so its gates read them as they are): naive; the airline replay (cost,
auto-cancels over the flights JetBlue flew by their deadline, reserves called and granted) and its fidelity (of the
flights JetBlue flew, how many the replay flies and how many of those leave within FIDELITY_MIN of JetBlue's minute);
the recorded day; the anchor (references.anchor at `--prove-s`: objective, status, gap, whether its plan replays
exactly); the follower (references.follow at `--watch-s`: cost and watches; a watch solve that does not prove stops
it, captured as its error and flagged `unproven_watch`); the calm day (forced cancellations and ferries); the flat
anchor on the control dates (world_check's controls gate); and the probe's verdict counts (programs.probe, a restored
pair once). A reference that fails is captured as its error, never raised.

`--only` still builds and splits every storm date: the split and the constants are the whole pack's, so its tasks are
the ones the full pack writes for those dates.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import pandas as pd
import world_check

from agentenv_airops.build import bts, pack, weather
from agentenv_airops.core.sim import FLOWN
from agentenv_airops.flow import sources
from agentenv_airops.programs import candidates, network, probe, rbs, references, repairs, sim, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.candidates import Built
from agentenv_airops.programs.layers import calibrate, config, crew
from agentenv_airops.programs.model import Task, write_tasks

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
CONTROL = "control-"
REFS = ("naive", "airline", "recorded", "anchor", "follower", "calm")
CONTROL_REFS = ("flat",)
PROVE_S, WATCH_S = 1800.0, 900.0
FIDELITY_MIN = 15
REFS_VERSION = 1
"""Bumped when a reference's definition here changes, so a resumed --refs runs it again."""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="", help="comma-separated storm or control dates to write (default: every one)")
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="keep the builds already under DIR/built/")
    ap.add_argument("--refs", action="store_true", help="run the references on every task written")
    ap.add_argument("--bases", default="none",
                    help="crew bases for Edgar's crew model, comma-separated (JetBlue's: JFK,BOS,FLL,MCO); 'none', the "
                         "v3 default, builds without crews (docs/v4/DESIGN.md §0 status)")
    ap.add_argument("--crew", choices=crew.MODES, default="edgar",
                    help="how the crew model cuts pairings with --bases: Edgar's proxy or v4's legal on schedule")
    ap.add_argument("--buffer", type=int, help="v4's duty buffer in minutes (default: fitted on the train dates)")
    ap.add_argument("--max-segments", type=int, help="v4's legs per duty at most (default: fitted on the train dates)")
    ap.add_argument("--prove-s", type=float, default=PROVE_S, help="time limit of each anchor, flat or calm solve")
    ap.add_argument("--watch-s", type=float, default=WATCH_S, help="time limit of each follower watch solve")
    args = ap.parse_args(argv)
    out = args.out
    bases = () if args.bases.lower() == "none" else tuple(b for b in args.bases.split(",") if b)
    if args.crew == "v4" and not bases:
        sys.exit("ERROR: --crew v4 cuts the crew model's pairings: give it --bases")
    fitting = args.crew == "v4" and (args.buffer is None or args.max_segments is None)
    rule = crew.Rule(args.crew, crew.BUFFER if args.buffer is None else args.buffer,
                     crew.MAX_SEGMENTS if args.max_segments is None else args.max_segments)
    out.mkdir(parents=True, exist_ok=True)
    clock = Clock()

    fits = candidates.e_table(INPUTS, DERIVED, workers=args.workers)
    e_fit, e_median = unseen.table(fits)
    storms, counts = network.storms(INPUTS)
    controls = network.control_dates(INPUTS, DERIVED)
    clock.lap("storms", f"{len(storms)} storm dates, {len(controls)} control dates, {len(fits)} advisories fitted")
    jobs = {d: (d, hubs, None, "") for d, hubs in storms.items()}
    jobs |= {CONTROL + d: (d, tuple(h for h in HUBS if h in w), w, "control") for d, w in controls.items()}
    built = build(folder_of(out, rule), jobs, e_fit, e_median, args.workers, args.resume, bases, rule)
    tasks, held = storm_and_control(built)
    clock.lap("build", f"{len(tasks)} of {len(storms)} storm dates built, {len(held)} of {len(controls)} controls")

    event = candidates.events(storms)
    drawn = network.split({d: t.storms for d, t in tasks.items()}, event, args.seed)
    train = sorted(d for d, v in drawn.items() if v == "train")
    scale = unseen.fit_scale([tasks[d] for d in train], fits)
    if set(scale) != set(HUBS):
        sys.exit(f"ERROR: no train dates to fit the scale at {sorted(set(HUBS) - set(scale))}")
    shipped_e = unseen.table(fits, scale)
    clock.lap("split", f"eval {len(drawn) - len(train)}, train {len(train)}, scale {scale}")

    spans = config.spans(INPUTS, candidates.MONTHS)
    pushed = pd.concat([candidates.month_frame(INPUTS, DERIVED, m)[["Origin", "act_dep"]] for m in candidates.MONTHS],
                       ignore_index=True)
    rated = calibrate.rates(train, pushed, spans)
    crewing = {**rule.to_dict(), "fitted": fitting}
    if fitting:
        sample = calibrate.sample(train)
        rules = calibrate.grid((args.buffer,) if args.buffer is not None else calibrate.BUFFERS,
                               (args.max_segments,) if args.max_segments is not None else calibrate.SEGMENTS)
        grid = {}
        for r in [*rules, None]:
            at = build(out / "built-nocrew" if r is None else folder_of(out, r), {d: jobs[d] for d in sample}, e_fit,
                       e_median, args.workers, args.resume, () if r is None else bases, r or crew.EDGAR)
            grid[r] = [network.layered(rbs.rescale(replace(t, split="train"), scale, *shipped_e), rated, INPUTS,
                                       DERIVED) for t in storm_and_control(at)[0].values()]
        plain = grid.pop(None)
        chosen, rows = calibrate.crew_fit(grid, plain, args.workers, cache=out / "crew-fit")
        crewing["fit"] = {"sample": list(sample), "seed": calibrate.SAMPLE_SEED, "time_limit_s": calibrate.CREW_S,
                          "rules": rows, "chosen": chosen.to_dict() if chosen else None}
        clock.lap("crew fit", json.dumps(chosen.to_dict() if chosen else None))
        if chosen is None:
            (out / "manifest.json").write_text(json.dumps({"code": revision(), "crew": crewing}, indent=1) + "\n")
            sys.exit("ERROR: no crew cut of the grid holds the guards on the train sample (manifest.json: crew.fit)")
        if chosen != rule:
            rule = chosen
            built = build(folder_of(out, rule), jobs, e_fit, e_median, args.workers, args.resume, bases, rule)
            again = storm_and_control(built)
            if set(again[0]) != set(tasks) or set(again[1]) != set(held):
                sys.exit("ERROR: the dates built at the fitted crew cut are not those the split drew from")
            tasks, held = again
            clock.lap("build", f"again at {rule}")
        crewing |= rule.to_dict()
    shipped = {d: rbs.rescale(replace(tasks[d], split=v), scale, *shipped_e) for d, v in drawn.items()}
    calm = {d: rbs.rescale(replace(t, split="control"), scale, *shipped_e) for d, t in held.items()}
    constants = calibrate.pooled_fit(rated, [shipped[d] for d in train], frame_of, spans, args.workers)
    clock.lap("calibrate", json.dumps({k: v for k, v in constants.to_dict().items() if k != "dates"}))

    only = dates_of(args.only)
    if missing := [d for d in only if d not in shipped and d not in calm]:
        sys.exit(f"ERROR: not in the split or the controls: "
                 f"{', '.join(f'{d} ({why(d, storms, controls, built)})' for d in missing)}")
    chosen = [shipped[d] for d in sorted(shipped) if not only or d in only]
    chosen += [calm[d] for d in sorted(calm) if not only or d in only]
    layered = [network.layered(t, constants, INPUTS, DERIVED) for t in chosen]
    write_tasks(layered, out / "tasks.jsonl")
    clock.lap("layers", f"{len(layered)} tasks written")

    manifest = {"code": revision(), "seed": args.seed, "months": list(candidates.MONTHS), "bases": list(bases),
                "crew": crewing if bases else None, "only": only, "storm_dates": len(storms), "built": len(tasks),
                "candidates": counts,
                "unbuilt": {d: b.row["reason"] for d, b in built.items() if not b.task},
                "split": dict(sorted(drawn.items())), "unsplit": sorted(set(tasks) - set(drawn)),
                "controls": {d: {"hubs": list(jobs[CONTROL + d][1]), "windows": {h: list(x) for h, x in w.items()},
                                 "task_id": calm[d].task_id if d in calm else None} for d, w in controls.items()},
                "scale": scale, "constants": constants.to_dict(), "inputs": inputs_of(INPUTS, candidates.MONTHS),
                "counts": {"total": total(layered), "tasks": {t.task_id: tally(t) for t in layered}},
                "wall_s": clock.laps}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    if args.refs:
        results = refs(out, layered, args.workers, (args.prove_s, args.watch_s), clock)
        manifest["refs"] = {"tasks": len(results), "time_limits_s": [args.prove_s, args.watch_s],
                            "failed": failed(results)}
        manifest["wall_s"] = clock.laps
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"constants": manifest["constants"] | {"dates": len(constants.dates)},
                      "counts": manifest["counts"]["total"], "wall_s": clock.laps}, indent=1), flush=True)


class Clock:
    def __init__(self):
        self.t0 = self.t = time.monotonic()
        self.laps: dict[str, float] = {}

    def lap(self, name: str, note: str = "") -> None:
        now = time.monotonic()
        self.laps[name] = round(now - self.t, 1)
        self.laps["total"] = round(now - self.t0, 1)
        self.t = now
        print(f"[{self.laps['total']:7.1f}s] {name}: {note}", flush=True)


def build(folder: Path, jobs: Mapping[str, tuple[str, tuple[str, ...], network.Windows | None, str]],
          e_fit: Mapping[str, float], e_median: Mapping[str, float], workers: int, resume: bool,
          bases: Sequence[str] = repairs.BASES, rule: crew.Rule = crew.EDGAR) -> dict[str, Built]:
    """Every job's whole-network task with crew at `bases` cut by `rule`, at scale 1, one JSON per job under `folder` as
    it finishes (a job keyed by its date, a control's by CONTROL + its date; each a (date, storm hubs, windows or None,
    split)); with `resume` the jobs built there already are read back (kept), the others built again."""
    folder.mkdir(parents=True, exist_ok=True)
    out = kept(folder, jobs) if resume else {}
    ones = dict.fromkeys(HUBS, 1.0)
    args = {k: (d, hubs, INPUTS, DERIVED, dict(e_fit), dict(e_median), ones, repairs.CUTOFF, w, split, tuple(bases),
                None, rule) for k, (d, hubs, w, split) in jobs.items() if k not in out}
    with ProcessPoolExecutor(max(1, workers)) as pool:
        futures = {pool.submit(build_one, a): k for k, a in args.items()}
        for job in as_completed(futures):
            b = job.result()
            save(folder / f"{futures[job]}.json", b)
            out[futures[job]] = b
    return dict(sorted(out.items()))


def folder_of(out: Path, rule: crew.Rule) -> Path:
    """Where a pack's builds at `rule` go: DIR/built/ for Edgar's cut (or none), DIR/built-v4-BUFFER-SEGMENTS/ for
    v4's."""
    return out / ("built" if rule.mode == "edgar" else f"built-v4-{rule.buffer}-{rule.max_segments}")


def storm_and_control(built: Mapping[str, Built]) -> tuple[dict[str, Task], dict[str, Task]]:
    """The built storm tasks by date, and the built controls by date."""
    return ({d: b.task for d, b in built.items() if b.task and not d.startswith(CONTROL)},
            {d.removeprefix(CONTROL): b.task for d, b in built.items() if b.task and d.startswith(CONTROL)})


def kept(folder: Path, keys: Iterable[str]) -> dict[str, Built]:
    """The jobs whose build an earlier run wrote whole under `folder`, with a task or left out by the build's rules
    (network.build's reason, say too few flights controlled): one it left unbuilt on an error (an input missing then
    may be there now, `build_one`) or whose file it was killed writing is built again."""
    out = {}
    for d in keys:
        try:
            x = json.loads((folder / f"{d}.json").read_text())
        except (OSError, ValueError):
            continue
        if x.get("task"):
            out[d] = Built(Task.from_dict(x["task"]), x["row"])
        elif not str(x["row"].get("reason", "")).startswith("error:"):
            out[d] = Built(None, x["row"])
    return out


def save(path: Path, b: Built) -> None:
    """Write a date's build whole or not at all (a run killed mid-write leaves the old file, or none)."""
    part = path.with_suffix(".part")
    part.write_text(json.dumps({"row": b.row, "task": b.task.to_dict() if b.task else None}, default=str))
    os.replace(part, path)


def build_one(job: tuple) -> Built:
    """network.build_one, a date an input is missing for (say the FAA registry, for a tail B-43 doesn't type) unbuilt
    with the error as its reason. Any other error (a bug, or a fault such as too many open files that would drop a
    date and so reshuffle the split) stops the pack."""
    try:
        return network.build_one(job)
    except FileNotFoundError as e:
        return Built(None, {"date": job[0], "hubs": list(job[1]), "task_id": None,
                            "reason": f"error: {type(e).__name__}: {e}"})


def dates_of(only: str) -> list[str]:
    """`--only`'s dates, each once, in order."""
    return sorted({x.strip() for x in only.split(",") if x.strip()})


def frame_of(d: str) -> pd.DataFrame:
    return candidates.frame(INPUTS, DERIVED, d)


def why(d: str, storms: Mapping[str, tuple[str, ...]], controls: Mapping[str, network.Windows],
        built: Mapping[str, Built]) -> str:
    key = CONTROL + d if d in controls else d
    if d not in storms and d not in controls:
        return "neither a storm nor a control date"
    if not built[key].task:
        return built[key].row["reason"]
    return "an event with an eval date elsewhere"


def inputs_of(inputs: Path, months: Iterable[str]) -> dict[str, str]:
    """The sha256 of each input the pack reads beyond the tasks' own pins: every month's BTS on-time (the closure
    rates' pushbacks) and each hub's routine METARs, SPECIs and TAFs (the closures and the desk's weather news)."""
    src = pack.Sources(inputs)
    paths = [bts.ontime_path(inputs, m) for m in months]
    paths += [p for m in months for h in HUBS
              for p in (weather.metar_path(inputs, h, m), sources.speci_path(inputs, h, m),
                        weather.afos_path(inputs, f"TAF{h}", m))]
    return {str(p.relative_to(inputs)): src.file(p) for p in paths}


def tally(task: Task) -> dict:
    """A task's flights and its layers' counts: crew deadlines (outstation departures', and base departures' under a
    pool), departure-capped hours, closures, outstation-capped hours and stations, the pools, and the weather news
    per kind."""
    lay = task.layers
    return {"flights": len(task.flights), "tails": len(task.tails),
            "crew_deadlines": sum(f.crew is not None and f.crew.deadline is not None for f in task.flights),
            "pooled_departures": sum(task.reservable(f) for f in task.flights),
            "dep_capped_hours": {h: len(by) for h, by in lay.dep_caps.items()},
            "closures": {h: len(x) for h, x in lay.closures.items()},
            "outstation_capped_hours": sum(len(by) for by in lay.arr_caps.values()),
            "outstations_capped": len(lay.arr_caps), "pools": dict(lay.reserves),
            "weather": dict(sorted(Counter(kind for _, kind, _, _ in lay.weather).items()))}


def total(tasks: Iterable[Task]) -> dict:
    rows = [tally(t) for t in tasks]
    return {"tasks": len(rows), **{k: sum(r[k] for r in rows) for k in ("flights", "tails", "crew_deadlines",
                                                                          "pooled_departures",
                                                                          "outstation_capped_hours",
                                                                          "outstations_capped")},
            "dep_capped_hours": sum(sum(r["dep_capped_hours"].values()) for r in rows),
            "closures": sum(sum(r["closures"].values()) for r in rows),
            "weather": dict(sorted(sum((Counter(r["weather"]) for r in rows), Counter()).items())),
            "tasks_with": {k: sum(bool(r[k]) for r in rows) for k in ("dep_capped_hours", "closures",
                                                                        "outstation_capped_hours", "pools")}}


def refs(folder: Path, tasks: Sequence[Task], workers: int, limits: tuple[float, float],
         clock: "Clock | None" = None) -> dict[str, dict]:
    """The references on every task in a process pool, heaviest first, each result written to folder/refs/<task
    id>.json as it finishes; a result already there whose signature matches is kept. Then folder/refs.json: each task's
    anchor and follower costs, where they ran, with the task's digest. Results are returned as written (JSON)."""
    (folder / "refs").mkdir(parents=True, exist_ok=True)
    results, jobs = {}, []
    for t in tasks:
        names = REFS + (CONTROL_REFS if t.split == "control" else ())
        sig = signature(t, names, limits)
        path = folder / "refs" / f"{t.task_id}.json"
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = {}
        if old.get("signature") == sig:
            results[t.task_id] = old
        else:
            jobs.append((t.task_id, t.to_json(), sig, names, limits))
    weights = {t.task_id: world_check.weight(t) for t in tasks}
    jobs.sort(key=lambda j: weights[j[0]], reverse=True)
    t0 = time.monotonic()
    if jobs:
        with ProcessPoolExecutor(max(1, min(workers, len(jobs)))) as pool:
            for fut in as_completed([pool.submit(refs_job, j) for j in jobs]):
                r = json.loads(json.dumps(fut.result(), default=str))
                part = folder / "refs" / f"{r['task_id']}.part"
                part.write_text(json.dumps(r))
                os.replace(part, folder / "refs" / f"{r['task_id']}.json")
                results[r["task_id"]] = r
                print(f"  [{time.monotonic() - t0:8.1f}s] {len(results)}/{len(tasks)} {r['task_id']} "
                      f"{world_check.brief(r)}", flush=True)
    (folder / "refs.json").write_text(json.dumps(stored(tasks, results), indent=1) + "\n")
    if clock:
        clock.lap("refs", f"{len(jobs)} run, {len(tasks) - len(jobs)} kept, failed {failed(results)}")
    return {t.task_id: results[t.task_id] for t in tasks}


def signature(task: Task, names: tuple[str, ...], limits: tuple[float, float]) -> str:
    return hashlib.sha256(f"{task.to_json()}|{names}|{limits}|{REFS_VERSION}".encode()).hexdigest()[:16]


def refs_job(j: tuple) -> dict:
    """world_check's references of one task (its job), with the replay's fidelity and reserve calls, the probe's
    verdicts and JetBlue's own counts; a follower stopped by a watch solve that did not prove is flagged."""
    tid, line, sig, names, limits = j
    out = world_check.job((tid, line, sig, names, limits))
    task = Task.from_dict(json.loads(line))
    out["jetblue"] = {"cancelled": sum(f.actual.dep is None for f in task.flights),
                      "flown": sum(f.actual.dep is not None for f in task.flights)}
    for name, play in (("fidelity", lambda: fidelity(task)), ("probe", lambda: probe.probe(task).to_dict())):
        world_check.attempt(out, name, play)
    error = out.get("follower", {}).get("error", "")
    if error:
        out["follower"]["unproven_watch"] = error.startswith("RuntimeError") and " watch " in error
    return out


def fidelity(task: Task) -> dict:
    """Of the flights JetBlue flew, how many the airline replay flies and how many of those leave within FIDELITY_MIN
    of JetBlue's departure (exactly on it too), and the replay's reserve calls: asked for and granted."""
    plan = references.airline_plan(task)
    run = sim.execute(task, plan)
    errs = [run.flights[f.id].dep - f.actual.dep for f in task.flights
            if f.actual.dep is not None and run.flights[f.id].status == FLOWN]
    return {"jetblue_flown": sum(f.actual.dep is not None for f in task.flights), "flown": len(errs),
            "within": sum(abs(e) <= FIDELITY_MIN for e in errs), "exact": sum(e == 0 for e in errs),
            "within_min": FIDELITY_MIN, "reserves_called": sum(bool(e.get("reserve")) for e in plan.values()),
            "reserves_granted": len(run.reserved)}


def stored(tasks: Iterable[Task], results: Mapping[str, dict]) -> dict[str, dict]:
    """Per task, the costs on the truth of its anchor's plan and its follower's, where they ran without error, with the
    task's digest: what the playable day's load takes (v4day.live), so it solves no MIP."""
    out = {}
    for t in tasks:
        r = results.get(t.task_id, {})
        costs = {k: r[k]["total"] for k in ("anchor", "follower") if "total" in r.get(k, {})}
        if costs:
            out[t.task_id] = {**costs, "task": references.digest(t)}
    return out


def failed(results: Mapping[str, dict]) -> dict[str, list[str]]:
    """Per task, the references that failed or whose MIP did not prove (world_check.unproven), and the fidelity or the
    probe if they failed."""
    return {i: bad for i, r in sorted(results.items())
            if (bad := world_check.unproven(r) + [k for k in ("fidelity", "probe") if "error" in r.get(k, {})])}


def revision() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


if __name__ == "__main__":
    main()
