"""Film a sweep's control-center days as they finish: each scored run of a pilot or board folder (results/runs/<sweep>/)
not filmed yet is built into a viewer episode (occ_episodes.build, a b6-v3 day with its passengers) and
filmed, its director's cut (scripts/record.py) and its list thumbnail (scripts/occ_thumbs.py), into OUT/<sweep>/:
<run>.mp4 and <run>.jpg, the viewer folder they were filmed from (viewer/), each run's log (logs/) and what was made
from which attempt (done/). Safe to run again, and while the board plays: a run is filmed once per scored attempt, the
films appear whole, a second invocation on the same folder exits. A board's day ends on the board's means only once
every run of the sweep is final and in the viewer folder; --final films again those filmed before that.

usage: .venv/bin/python scripts/occ_films.py ~/agentenv-airops-plugin/results/runs/occ3-board-sol --out FILMS \\
           --assets VIEWER_FOLDER --inputs ~/agentenv-airops-plugin/cache/inputs [--every 300] [-- --seconds 70]
"""

import argparse
import fcntl
import functools
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path

from agentenv_airops import export
from agentenv_airops.occ.sweep import final

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_v3  # noqa: E402
import occ_episodes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
LINKED = ("stage", "twin", "radar")
"""What the viewer folder links to --assets: the airport stages, hub twins and radar frames, read only."""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("sweep", type=Path, help="A pilot or board folder: pilot.json, results.jsonl, days/.")
    p.add_argument("--out", type=Path, required=True, help="Films go to OUT/<sweep>/ (OUT/<sweep>-mock/ with --mock).")
    p.add_argument("--assets", type=Path, default=ROOT / "cache",
                   help="A folder with the airport stages, hub twins and radar frames (stage/, twin/, radar/).")
    p.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs",
                   help="The raw inputs the pack was built from: BTS on-time, the FAA registry, ATCSCC, IEM METARs.")
    p.add_argument("--every", type=float, metavar="SECONDS",
                   help="Look again this often until every run of the sweep is final and filmed.")
    p.add_argument("--jobs", type=int, default=1, help="Days filmed at once, a Chrome each.")
    p.add_argument("--only", action="append", default=[], metavar="RUN", help="Only these runs.")
    p.add_argument("--final", action="store_true", help="Film again a finished board's days filmed before it was.")
    p.add_argument("--retry", action="store_true", help="Try again the days that failed, once.")
    p.add_argument("--no-replay", action="store_true",
                   help="Show how the day flew on the board instead of each watch's projection (a few minutes a day).")
    p.add_argument("--mock", action="store_true",
                   help="Invent passengers and a customer desk on v2 days (scripts/mock_v3.py): v3's films before v3.")
    args = p.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    films = Films(args.sweep.expanduser().resolve(), args.out.resolve(), args.assets.expanduser().resolve(),
                  args.inputs.expanduser().resolve(), not args.no_replay, args.mock, rest)
    films.dir.mkdir(parents=True, exist_ok=True)
    with (films.dir / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{films.dir} is being filmed by another run of this script")
            return 0
        while not (films.sweep / "pilot.json").is_file():
            if args.every is None:
                print(f"no pilot.json in {films.sweep} yet")
                return 1
            time.sleep(args.every)
        films.prepare()
        retry = args.retry
        while True:
            todo, waiting = films.todo(args.only, args.final, retry)
            retry = False
            ready = [x for x in map(films.prepared, todo) if x]
            board = films.boarded()
            with ThreadPoolExecutor(max(1, args.jobs)) as pool:
                list(pool.map(films.film, *zip(*ready, strict=True), [board] * len(ready)))
            if args.every is None or (not waiting and not films.todo(args.only, args.final, False)[0]):
                break
            time.sleep(args.every)
    print(films.summary())
    return 0


class Films:
    def __init__(self, sweep: Path, out: Path, assets: Path, inputs: Path, replay: bool, mock: bool,
                 record: list[str]):
        self.sweep, self.assets, self.inputs, self.replay, self.mock, self.record = \
            sweep, assets, inputs, replay, mock, record
        self.dir = out / (sweep.name + ("-mock" if mock else ""))
        self.boards = "-board-" in f"{sweep.name}-"
        self.viewer = self.dir / "viewer"
        self.lock = threading.Lock()

    @functools.cached_property
    def manifest(self) -> dict:
        return json.loads((self.sweep / "pilot.json").read_text())

    def prepare(self) -> None:
        for name in ("episodes", "thumbs", "traffic", "logs", "done"):
            (self.viewer if name in ("episodes", "thumbs", "traffic") else self.dir).joinpath(name).mkdir(
                parents=True, exist_ok=True)
        for name in LINKED:
            link = self.viewer / name
            if not link.is_symlink() and (self.assets / name).is_dir():
                link.symlink_to(self.assets / name)

    def rows(self) -> list[dict]:
        path = self.sweep / "results.jsonl"
        out = []
        for line in path.read_text().splitlines() if path.is_file() else []:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def complete(self, rows: list[dict]) -> bool:
        return all(final([r for r in rows if r["run"] == run]) for run in self.manifest["runs"])

    def todo(self, only: list[str], again: bool, retry: bool) -> tuple[list[dict], bool]:
        """The scored runs to film now, and whether runs of the sweep are still to finish."""
        rows = self.rows()
        boarded = again and self.boards and self.boarded(rows)
        out = []
        scored = {r["run"]: r for r in rows if r["outcome"] == "scored" and r.get("day")}
        for run, row in scored.items():
            if not (self.sweep / row["day"]).is_file():
                continue
            if only and run not in only:
                continue
            done, failed = self.state(run, "done"), self.state(run, "failed")
            if done and done["attempt"] == row["attempt"] and (self.dir / done["film"]).is_file() \
                    and (self.dir / done["thumb"]).is_file():
                if not (boarded and done["board"] is False):
                    continue
            elif failed and failed["attempt"] == row["attempt"] and not retry:
                continue
            out.append(row)
        return out, not self.complete(rows)

    def boarded(self, rows: list[dict] | None = None) -> bool:
        """Whether a board's films may end on its means: every run of the sweep is final and the viewer folder's index,
        which the means are taken over, holds every scored one."""
        rows = self.rows() if rows is None else rows
        index = self.viewer / "episodes" / "index.json"
        held = {(e["task_id"], e["run"]) for e in json.loads(index.read_text())["episodes"]} if index.is_file() \
            else set()
        return self.complete(rows) and all((r["run"].rsplit("-", 1)[0], self.label(r["run"])) in held
                                           for r in rows if r["outcome"] == "scored")

    def label(self, run: str) -> str:
        return f"{self.sweep.name}-{run.rsplit('-', 1)[1]}"

    def state(self, run: str, kind: str) -> dict | None:
        path = self.dir / "done" / f"{run}.{kind}.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def mark(self, run: str, kind: str, doc: dict) -> None:
        path = self.dir / "done" / f"{run}.{kind}.json"
        part = path.with_suffix(".part")
        part.write_text(json.dumps(doc, indent=1) + "\n")
        os.replace(part, path)

    def prepared(self, row: dict) -> tuple[dict, str, float] | None:
        """The run's episode in the viewer folder (built, or kept from its attempt's film), with its file's stem."""
        run, start, log = row["run"], time.monotonic(), self.dir / "logs" / f"{row['run']}.log"
        with log.open("a") as f:
            f.write(f"== {time.strftime('%Y-%m-%d %H:%M:%S')} attempt {row['attempt']}\n")
        done = self.state(run, "done")
        if done and done["attempt"] == row["attempt"] and (self.dir / done["episode"]).is_file():
            return row, Path(done["episode"]).stem, start
        try:
            return row, self.build(row, log), start
        except Exception as e:
            self.failed(row, "build", e, log)
            return None

    def film(self, row: dict, stem: str, start: float, board: bool) -> None:
        run, log = row["run"], self.dir / "logs" / f"{row['run']}.log"
        fresh, stage = not (self.dir / f"{run}.jpg").is_file(), "film"
        try:
            self.shoot(stem, run, board and self.boards, fresh, log)
            if fresh:
                stage = "thumb"
                self.thumb(stem, run, log)
        except Exception as e:
            self.failed(row, stage, e, log)
            return
        self.mark(run, "done", {"attempt": row["attempt"], "day": row["day"], "day_sha256": self.digest(row),
                                "episode": f"viewer/episodes/{stem}.json", "film": f"{run}.mp4",
                                "thumb": f"{run}.jpg", "board": board if self.boards else None,
                                "seconds": round(time.monotonic() - start), "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        (self.dir / "done" / f"{run}.failed.json").unlink(missing_ok=True)
        print(f"{run}: {run}.mp4 and {run}.jpg in {time.monotonic() - start:.0f} s", flush=True)

    def failed(self, row: dict, stage: str, e: Exception, log: Path) -> None:
        error = f"{type(e).__name__}: {e}"
        self.mark(row["run"], "failed", {"attempt": row["attempt"], "stage": stage, "error": error})
        print(f"{row['run']}: failed to {stage}: {error} (log {log})", flush=True)

    def digest(self, row: dict) -> str:
        return hashlib.sha256((self.sweep / row["day"]).read_bytes()).hexdigest()

    def build(self, row: dict, log: Path) -> str:
        """The run's episode by occ_episodes.build (its projections replayed unless --no-replay) into the viewer folder,
        added to its index; its file's stem."""
        with log.open("a") as f, redirect_stdout(f):
            [ep] = occ_episodes.build([f"{self.sweep.name}/{row['run']}"], self.sweep.parent, self.inputs, self.viewer,
                                      None, self.replay)
        if self.mock and not ep.get("passengers"):
            mock_v3.mock(ep, random.Random(ep["task"]["task_id"]))
        export.write(self.viewer, [ep], self.viewer)
        with log.open("a") as f:
            f.write(f"built {export.file_name(ep)}: reward {ep['reward']:.3f}, {ep['occ']['label']}"
                    f"{', ' + mock_v3.summary(ep) if ep.get('passengers') else ''}\n")
        return export.file_name(ep)[:-5]

    def shoot(self, stem: str, run: str, board: bool, poster: bool, log: Path) -> None:
        """The director's cut, and with ``poster`` its poster frame as the thumbnail occ_thumbs.py may better."""
        part = self.dir / f"{run}.part.mp4"
        cmd = [sys.executable, str(SCRIPTS / "record.py"), "--episode", str(self.viewer / "episodes" / f"{stem}.json"),
               "--director", "--out", str(part), *([] if board else ["--no-board"]),
               *(["--thumb", str(self.viewer / "thumbs" / f"{stem}.jpg")] if poster else []), *self.record]
        for _ in range(2):
            if self.run(cmd, log) == 0:
                os.replace(part, self.dir / f"{run}.mp4")
                return
        part.unlink(missing_ok=True)
        raise RuntimeError(f"record.py failed twice, see {log}")

    def thumb(self, stem: str, run: str, log: Path) -> None:
        """occ_thumbs.py's pick over record.py's poster, which stays if no still qualifies; the index points at it."""
        cmd = [sys.executable, str(SCRIPTS / "occ_thumbs.py"), str(self.viewer), "--only", stem, "--workers", "1"]
        if self.run(cmd, log):
            with log.open("a") as f:
                f.write("occ_thumbs.py found no still: the film's poster is the thumbnail\n")
        part = self.dir / f"{run}.part.jpg"
        shutil.copyfile(self.viewer / "thumbs" / f"{stem}.jpg", part)
        os.replace(part, self.dir / f"{run}.jpg")
        with self.lock:
            index = self.viewer / "episodes" / "index.json"
            doc = json.loads(index.read_text())
            for e in doc["episodes"]:
                if e["url"] == f"{stem}.json":
                    e["thumb"] = f"../thumbs/{stem}.jpg"
            part = index.with_suffix(".part")
            part.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
            os.replace(part, index)

    def run(self, cmd: list[str], log: Path) -> int:
        with log.open("a") as f:
            f.write("$ " + " ".join(cmd) + "\n")
            f.flush()
            return subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT).returncode

    def summary(self) -> str:
        rows = self.rows()
        scored = {r["run"] for r in rows if r["outcome"] == "scored"}
        done = sorted(p.name[:-10] for p in (self.dir / "done").glob("*.done.json"))
        failed = sorted(p.name[:-12] for p in (self.dir / "done").glob("*.failed.json"))
        return (f"{self.sweep.name}: {len(done)} filmed, {len(failed)} failed"
                f"{' (' + ', '.join(failed) + ')' if failed else ''}, {len(scored)} of {len(self.manifest['runs'])} "
                f"runs scored{', the sweep is final' if self.complete(rows) else ''} -> {self.dir}")


if __name__ == "__main__":
    sys.exit(main())
