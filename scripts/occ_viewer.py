"""A board's scored runs as viewer episodes with their list stills, nothing filmed, into OUT/<sweep>/viewer
(occ_films.py's folder), which hub_dataset.py --occ-viewer takes: each run's projections replayed (--workers at once),
its episode built with the airport's passengers and traffic and added to the index (--builds at once, never two of a
date at once: they share its traffic), then occ_thumbs.py's stills and the index pointed at them. The list labels each
card with its model's name (export.NAMES) and its mode. Safe to run again: a run already in the index is kept, a card
with a still keeps it.

usage: .venv/bin/python scripts/occ_viewer.py ~/agentenv-airops-plugin/results/runs/occ3-board-opus --out FILMS \\
           --assets VIEWER_FOLDER --inputs ~/agentenv-airops-plugin/cache/inputs [--workers 8] [--builds 3]
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import redirect_stdout
from pathlib import Path

from agentenv_airops import export

sys.path.insert(0, str(Path(__file__).resolve().parent))
import occ_episodes  # noqa: E402
import occ_films  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("sweep", type=Path, help="A board folder: pilot.json, results.jsonl, days/.")
    p.add_argument("--out", type=Path, required=True, help="The episodes go to OUT/<sweep>/viewer.")
    p.add_argument("--assets", type=Path, default=ROOT / "cache",
                   help="A folder with the airport stages, hub twins and radar frames (stage/, twin/, radar/).")
    p.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs",
                   help="The raw inputs the pack was built from: BTS on-time, the FAA registry, ATCSCC, IEM METARs.")
    p.add_argument("--workers", type=int, default=4, help="Days replayed for their projections at once.")
    p.add_argument("--builds", type=int, default=3, help="Episodes built at once.")
    p.add_argument("--thumbs", type=int, default=3, help="Days rendered for their stills at once, a Chrome each.")
    p.add_argument("--only", action="append", default=[], metavar="RUN", help="Only these runs.")
    args = p.parse_args(argv)
    films = occ_films.Films(args.sweep.expanduser().resolve(), args.out.resolve(), args.assets.expanduser().resolve(),
                            args.inputs.expanduser().resolve(), True, False, [])
    films.dir.mkdir(parents=True, exist_ok=True)
    with (films.dir / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{films.dir} is being written by another run of this script or occ_films.py")
            return 1
        films.prepare()
        index = films.viewer / "episodes" / "index.json"
        held = {(e["task_id"], e["run"]) for e in json.loads(index.read_text())["episodes"]} if index.is_file() \
            else set()
        rows = [r for r in films.rows() if r["outcome"] == "scored" and (not args.only or r["run"] in args.only)]
        todo = [r["run"] for r in rows if (r["run"].rsplit("-", 1)[0], films.label(r["run"])) not in held]
        failed = build(films, list(dict.fromkeys(todo)), args.workers, args.builds)
        stems = [Path(e["url"]).stem for e in json.loads(index.read_text())["episodes"]] if index.is_file() else []
        bare = [s for s in stems if not (films.viewer / "thumbs" / f"{s}.jpg").is_file()]
        if bare:
            cmd = [sys.executable, str(ROOT / "scripts" / "occ_thumbs.py"), str(films.viewer), "--workers",
                   str(args.thumbs), "--picks", str(films.dir / "done" / "thumb-picks.json"), "--sheet",
                   str(films.dir / "thumbs.jpg"), *(x for s in bare for x in ("--only", s))]
            subprocess.run(cmd, cwd=ROOT)
        unthumbed = point(index)
    print(f"{films.sweep.name}: {len(todo) - len(failed)} of {len(todo)} built"
          f"{' (failed: ' + ', '.join(failed) + ')' if failed else ''}, {len(held)} already in the index, "
          f"{len(unthumbed)} cards without a still{': ' + ', '.join(unthumbed) if unthumbed else ''} -> {films.viewer}")
    return 1 if failed or unthumbed else 0


def build(films: occ_films.Films, runs: list[str], workers: int, builds: int) -> list[str]:
    """``runs``' projections, then their episodes into the viewer folder, each with its log under logs/; the runs that
    failed."""
    names = [f"{films.sweep.name}/{run}" for run in runs]
    failed = [n.split(": ", 1)[0].split("/", 1)[1] for n in
              occ_episodes.replay_all(names, films.sweep.parent, films.viewer, workers)] if names else []
    todo, busy, pending, builds = [r for r in runs if r not in failed], set(), {}, max(1, builds)
    with ProcessPoolExecutor(builds) as pool:
        while todo or pending:
            for run in [r for r in todo if r.rsplit("-", 1)[0] not in busy][: builds - len(pending)]:
                todo.remove(run)
                busy.add(run.rsplit("-", 1)[0])
                pending[pool.submit(episode, str(films.sweep), run, str(films.inputs), str(films.viewer),
                                    str(films.dir / "logs"))] = run
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                run = pending.pop(future)
                try:
                    ep = future.result()
                    export.write(films.viewer, [ep], films.viewer)
                except Exception as e:
                    failed.append(run)
                    print(f"{run}: FAILED {type(e).__name__}: {e}", flush=True)
                else:
                    print(f"{run}: built {export.file_name(ep)}, {ep['model_label']} · {ep['occ']['label']}, "
                          f"reward {ep['reward']:.3f}", flush=True)
                busy.discard(run.rsplit("-", 1)[0])
    return failed


def episode(sweep: str, run: str, inputs: str, viewer: str, logs: str) -> dict:
    """One run's episode, built from its cached projections (a worker of --builds)."""
    with (Path(logs) / f"{run}.log").open("a") as f, redirect_stdout(f):
        print(f"== {time.strftime('%Y-%m-%d %H:%M:%S')} occ_viewer.py")
        [ep] = occ_episodes.build([f"{Path(sweep).name}/{run}"], Path(sweep).parent, Path(inputs), Path(viewer), None,
                                  True)
    return ep


def point(index: Path) -> list[str]:
    """The index with each card pointing at its still where there is one; the cards without."""
    if not index.is_file():
        return []
    doc = json.loads(index.read_text())
    bare = []
    for e in doc["episodes"]:
        stem = Path(e["url"]).stem
        if (index.parent.parent / "thumbs" / f"{stem}.jpg").is_file():
            e["thumb"] = f"../thumbs/{stem}.jpg"
        else:
            bare.append(stem)
    part = index.with_suffix(".part")
    part.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    os.replace(part, index)
    return bare


if __name__ == "__main__":
    sys.exit(main())
