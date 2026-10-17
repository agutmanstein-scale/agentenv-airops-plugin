"""Build v3's pack: every whole-network storm date with crew and every v3 layer, its constants fitted on the train
dates alone (docs/v3/DESIGN.md §2, §6.1). No reference runs here (scripts/v3_check.py reads the pack).

    python scripts/v3_pack.py --out DIR [--only DATE,...] [--workers 12] [--seed 0] [--resume]

1. every storm date over the 19 cached months (network.storms) built as a whole-network task with Edgar's crew model at
   JetBlue's bases (repairs.BASES), at scale 1 and the 03:00 cutoff, as world_check builds them; each date's build is
   written to DIR/built/ as it finishes, and `--resume` keeps those already there. A date whose inputs can't build it
   (a missing file, say the FAA registry a tail B-43 doesn't type needs) is unbuilt, its error the reason;
2. the seeded whole-event split over the built dates, as world_check draws it (network.split, `--seed`, default 0), and
   the unseen-traffic scale fitted on the train dates (unseen.fit_scale), every task rescaled to it;
3. the layer constants fitted on the train dates only (layers.calibrate.fit): the ramp closures' rate per hub, the
   reserve pool per base, the cap clauses kept at 0.9 and 5;
4. every date of the split (eval and train; `--only` a few of them) given every layer at those constants
   (network.layered, as network.build(layers=) does), written to DIR/tasks.jsonl, and DIR/manifest.json: the inputs the
   pack reads beyond each task's own pins (`sources`) with their sha256 (BTS on-time per month, the hubs' IEM METARs
   and SPECIs per month), the seed, the split and the scale, the constants with the dates they were fitted on, the
   counts per task and per layer, and the wall clock per phase.

`--only` still builds and splits every storm date: the split and the constants are the whole pack's, so its tasks are
the ones the full pack writes for those dates.
"""

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import pandas as pd

from agentenv_airops.build import bts, pack, weather
from agentenv_airops.flow import sources
from agentenv_airops.programs import candidates, network, rbs, repairs, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.candidates import Built
from agentenv_airops.programs.layers import calibrate, config
from agentenv_airops.programs.model import Task, write_tasks

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="", help="comma-separated storm dates to write (default: every split date)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="keep the builds already under DIR/built/")
    args = ap.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    clock = Clock()

    fits = candidates.e_table(INPUTS, DERIVED, workers=args.workers)
    e_fit, e_median = unseen.table(fits)
    storms, counts = network.storms(INPUTS)
    clock.lap("storms", f"{len(storms)} storm dates, {len(fits)} advisories fitted")
    built = build(out / "built", storms, e_fit, e_median, args.workers, args.resume)
    tasks = {d: b.task for d, b in built.items() if b.task}
    clock.lap("build", f"{len(tasks)} of {len(storms)} storm dates built")

    event = candidates.events(storms)
    drawn = network.split({d: t.storms for d, t in tasks.items()}, event, args.seed)
    train = sorted(d for d, v in drawn.items() if v == "train")
    scale = unseen.fit_scale([tasks[d] for d in train], fits)
    if set(scale) != set(HUBS):
        sys.exit(f"ERROR: no train dates to fit the scale at {sorted(set(HUBS) - set(scale))}")
    shipped_e = unseen.table(fits, scale)
    shipped = {d: rbs.rescale(replace(tasks[d], split=v), scale, *shipped_e) for d, v in drawn.items()}
    clock.lap("split", f"eval {len(drawn) - len(train)}, train {len(train)}, scale {scale}")

    spans = config.spans(INPUTS, candidates.MONTHS)
    pushed = pd.concat([candidates.month_frame(INPUTS, DERIVED, m)[["Origin", "act_dep"]] for m in candidates.MONTHS],
                       ignore_index=True)
    constants = calibrate.fit(train, [shipped[d] for d in train], frame_of, spans, pushed, args.workers)
    clock.lap("calibrate", json.dumps({k: v for k, v in constants.to_dict().items() if k != "dates"}))

    only = [x.strip() for x in args.only.split(",") if x.strip()]
    if missing := [d for d in only if d not in shipped]:
        sys.exit(f"ERROR: not in the split: {', '.join(f'{d} ({why(d, storms, built)})' for d in missing)}")
    chosen = sorted(only or shipped)
    layered = [network.layered(shipped[d], constants, INPUTS, DERIVED) for d in chosen]
    write_tasks(layered, out / "tasks.jsonl")
    clock.lap("layers", f"{len(layered)} tasks written")

    manifest = {"code": revision(), "seed": args.seed, "months": list(candidates.MONTHS), "bases": list(repairs.BASES),
                "only": only, "storm_dates": len(storms), "built": len(tasks), "candidates": counts,
                "unbuilt": {d: b.row["reason"] for d, b in built.items() if not b.task},
                "split": dict(sorted(drawn.items())), "unsplit": sorted(set(tasks) - set(drawn)), "scale": scale,
                "constants": constants.to_dict(), "inputs": inputs_of(INPUTS, candidates.MONTHS),
                "counts": {"total": total(layered), "tasks": {t.task_id: tally(t) for t in layered}},
                "wall_s": clock.laps}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"constants": manifest["constants"] | {"dates": len(constants.dates)},
                      "counts": manifest["counts"], "wall_s": clock.laps}, indent=1), flush=True)


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


def build(folder: Path, storms: Mapping[str, tuple[str, ...]], e_fit: Mapping[str, float],
          e_median: Mapping[str, float], workers: int, resume: bool) -> dict[str, Built]:
    """Every storm date's whole-network task with crew, at scale 1, one JSON per date under `folder` as it
    finishes; with `resume` a date already there is read back."""
    folder.mkdir(parents=True, exist_ok=True)
    out: dict[str, Built] = {}
    for d in storms:
        path = folder / f"{d}.json"
        if resume and path.exists():
            x = json.loads(path.read_text())
            out[d] = Built(Task.from_dict(x["task"]) if x["task"] else None, x["row"])
    ones = dict.fromkeys(HUBS, 1.0)
    jobs = [(d, hubs, INPUTS, DERIVED, dict(e_fit), dict(e_median), ones, repairs.CUTOFF, None, "", repairs.BASES,
             None) for d, hubs in storms.items() if d not in out]
    with ProcessPoolExecutor(max(1, workers)) as pool:
        for job in as_completed([pool.submit(build_one, j) for j in jobs]):
            b = job.result()
            d = b.row["date"]
            (folder / f"{d}.json").write_text(json.dumps({"row": b.row, "task": b.task.to_dict() if b.task else None},
                                                         default=str))
            out[d] = b
    return dict(sorted(out.items()))


def build_one(job: tuple) -> Built:
    """network.build_one, a date whose inputs fail to build it unbuilt with the error as its reason."""
    try:
        return network.build_one(job)
    except (OSError, ValueError, KeyError) as e:
        return Built(None, {"date": job[0], "hubs": list(job[1]), "task_id": None,
                            "reason": f"error: {type(e).__name__}: {e}"})


def frame_of(d: str) -> pd.DataFrame:
    return candidates.frame(INPUTS, DERIVED, d)


def why(d: str, storms: Mapping[str, tuple[str, ...]], built: Mapping[str, Built]) -> str:
    if d not in storms:
        return "not a storm date"
    if not built[d].task:
        return built[d].row["reason"]
    return "an event with an eval date elsewhere"


def inputs_of(inputs: Path, months: Iterable[str]) -> dict[str, str]:
    """The sha256 of each input the pack reads beyond the tasks' own pins: every month's BTS on-time (the closure
    rates' pushbacks) and each hub's routine METARs and SPECIs."""
    src = pack.Sources(inputs)
    paths = [bts.ontime_path(inputs, m) for m in months]
    paths += [p for m in months for h in HUBS
              for p in (weather.metar_path(inputs, h, m), sources.speci_path(inputs, h, m))]
    return {str(p.relative_to(inputs)): src.file(p) for p in paths}


def tally(task: Task) -> dict:
    """A task's flights and its layers' counts: crew deadlines (outstation departures', and base departures' under a
    pool), departure-capped hours, closures, outstation-capped hours and stations, and the pools."""
    lay = task.layers
    return {"flights": len(task.flights), "tails": len(task.tails),
            "crew_deadlines": sum(f.crew is not None and f.crew.deadline is not None for f in task.flights),
            "pooled_departures": sum(task.reservable(f) for f in task.flights),
            "dep_capped_hours": {h: len(by) for h, by in lay.dep_caps.items()},
            "closures": {h: len(x) for h, x in lay.closures.items()},
            "outstation_capped_hours": sum(len(by) for by in lay.arr_caps.values()),
            "outstations_capped": len(lay.arr_caps), "pools": dict(lay.reserves)}


def total(tasks: Iterable[Task]) -> dict:
    rows = [tally(t) for t in tasks]
    return {"tasks": len(rows), **{k: sum(r[k] for r in rows) for k in ("flights", "tails", "crew_deadlines",
                                                                          "pooled_departures",
                                                                          "outstation_capped_hours",
                                                                          "outstations_capped")},
            "dep_capped_hours": sum(sum(r["dep_capped_hours"].values()) for r in rows),
            "closures": sum(sum(r["closures"].values()) for r in rows),
            "tasks_with": {k: sum(bool(r[k]) for r in rows) for k in ("dep_capped_hours", "closures",
                                                                        "outstation_capped_hours", "pools")}}


def revision() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


if __name__ == "__main__":
    main()
