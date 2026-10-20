"""Film a list of episodes N at a time with scripts/record.py's director (a laptop, or a cloud box with Chrome, a GPU
and ffmpeg): each EPISODE.json to OUT/<stem>.mp4, or OUT/NAME.mp4 given EPISODE.json=NAME, with its log beside it;
arguments after -- go to every record.py, e.g. --seconds 70 or --size 1920x1080.

usage: .venv/bin/python scripts/film_runs.py cache/occ/viewer-all/episodes/occ-board-*joint*.json --out cache/films \
           [--jobs 2] [--thumbs cache/thumbs] [-- --seconds 70]
"""

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "record.py"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("episodes", nargs="+", help="Episode JSONs, each optionally =NAME for its film's name.")
    p.add_argument("--out", type=Path, required=True, help="Where the films and their logs go.")
    p.add_argument("--jobs", type=int, default=2, help="Films at once; each is a headless Chrome on the GPU.")
    p.add_argument("--thumbs", type=Path, help="Also write each episode's list thumbnail here as <stem>.jpg.")
    args = p.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for spec in args.episodes:
        path, _, name = spec.partition("=")
        path = Path(path)
        cmd = [sys.executable, str(SCRIPT), "--episode", str(path), "--director",
               "--out", str(args.out / f"{name or path.stem}.mp4"), *rest]
        if args.thumbs:
            cmd += ["--thumb", str(args.thumbs / f"{path.stem}.jpg")]
        jobs.append((name or path.stem, cmd))
    with ThreadPoolExecutor(args.jobs) as pool:
        codes = list(pool.map(lambda job: run(*job, args.out), jobs))
    failed = [name for (name, _), code in zip(jobs, codes, strict=True) if code]
    print(f"{len(jobs) - len(failed)} of {len(jobs)} filmed into {args.out}" + (f"; failed: {', '.join(failed)}"
                                                                                 if failed else ""))
    return 1 if failed else 0


def run(name: str, cmd: list[str], out: Path) -> int:
    start = time.monotonic()
    with (out / f"{name}.log").open("w") as log:
        code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    print(f"{name}: {'ok' if code == 0 else f'exit {code}'} in {time.monotonic() - start:.0f} s", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
