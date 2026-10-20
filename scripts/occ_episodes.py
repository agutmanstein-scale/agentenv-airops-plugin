"""Recorded ops control center days as viewer episodes (agentenv_airops.occ.episode): each run's day record from a
pilot or board folder under --runs, the hub's traffic baked into --cache/traffic, the episodes and what they point at
written to --out as the viewer reads them (episodes/, stage/, twin/, radar/); --space-out also assembles the static
Space around them (the page, the viewer under viewer/, the episodes under data/) with a Dockerfile that serves it the
way the Space is served, as static files.

    python scripts/occ_episodes.py occ-pilot-sol/b6-20230709-0400-joint occ-pilot-sol/b6-20260715-0400-joint \\
        --runs ~/agentenv-airops-plugin/results/runs --inputs ~/agentenv-airops-plugin/cache/inputs \\
        --out cache/occ/viewer --space-out cache/occ/space
    docker build -t airops-space-preview cache/occ/space && docker run --rm -p 8742:80 airops-space-preview
"""

import argparse
import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from agentenv_airops import export
from agentenv_airops.occ import episode
from agentenv_airops.occ.pack import Pack

ROOT = Path(__file__).resolve().parents[1]
PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>AirOpsEnv</title>
  <script>
    const q = new URLSearchParams(location.search);
    const index = new URL("data/viewer/episodes/index.json", location.href).href;
    if (!q.has("episode") && !q.has("index")) q.set("index", index);
    location.replace(`viewer/index.html?${q}`);
  </script>
</head>
<body><noscript>The replay viewer needs JavaScript.</noscript></body>
</html>
"""
DOCKERFILE = """# The replay Space as static files, served as the Space serves them.
FROM nginx:1.27-alpine
COPY . /usr/share/nginx/html
EXPOSE 80
"""


def expand(runs: Path, arg: str) -> list[str]:
    """FOLDER/RUN as given, or every scored run of FOLDER."""
    if "/" in arg:
        return [arg]
    rows = [json.loads(line) for line in (runs / arg / "results.jsonl").read_text().splitlines() if line.strip()]
    return [f"{arg}/{r}" for r in dict.fromkeys(r["run"] for r in rows if r["outcome"] == "scored")]


def load(runs: Path, name: str) -> tuple[dict, dict, dict]:
    """A run named <folder>/<run>: its day record (the scored attempt's), its results row and the folder's manifest."""
    folder, run = name.split("/", 1)
    manifest = json.loads(((runs / folder) / "pilot.json").read_text())
    rows = [json.loads(line) for line in (runs / folder / "results.jsonl").read_text().splitlines() if line.strip()]
    row = next((r for r in reversed(rows) if r["run"] == run and r["outcome"] == "scored"), None)
    if row is None:
        raise SystemExit(f"{name}: no scored attempt in {runs / folder / 'results.jsonl'}")
    data = json.loads((runs / folder / "days" / f"{run}-a{row['attempt']}.json").read_text())
    return data, row, manifest


def replay_all(names: list[str], runs: Path, cache: Path, workers: int) -> list[str]:
    """Every run's projections into the cache, `workers` replays at once; the runs that failed, each with why."""
    failed = []
    with ProcessPoolExecutor(workers) as pool:
        for name, error in pool.map(project, names, [runs] * len(names), [cache] * len(names)):
            print(f"{name}: {error or 'projected'}", flush=True)
            if error:
                failed.append(f"{name}: {error}")
    return failed


def build(names: list[str], runs: Path, inputs: Path, cache: Path, hub: str | None, replay: bool,
          labels: dict[str, str] | None = None) -> list[dict]:
    pack, out = Pack(), []
    for name in names:
        data, row, manifest = load(runs, name)
        world = pack.world(data["task_id"])
        source = name.split("/", 1)[0]
        folder = (labels or {}).get(source, source)
        projected = projections(world, data, projection_path(cache, name, row)) if replay else None
        record = {"folder": source, "run": row["run"], "attempt": row["attempt"], "cost_usd": row.get("cost_usd"),
                  "wall_seconds": row.get("wall_seconds"), "desks": row.get("desks")}
        ep = episode.episode(world, data, model=manifest["model"], run=f"{folder}-{data['mode']}", inputs=inputs,
                             cache=cache, hub=hub, record=record, projected=projected)
        doc = episode.traffic(world, ep, inputs)
        path = episode.write_traffic(doc, cache)
        ep["stage"] = episode.stage_of(ep, cache)
        print(f"{name}: {ep['task']['hub']}, reward {ep['reward']:.3f}, {len(ep['occ']['requests'])} requests, "
              f"{len(ep['occ']['decisions'])} decisions, {len(ep['occ']['crew'])} flights with crew tags, "
              f"{len(doc['aircraft'])} other aircraft at the hub -> {path}")
        out.append(ep)
    return out


def project(name: str, runs: Path, cache: Path) -> tuple[str, str | None]:
    """One run's projections into the cache (a worker of --workers), or why not."""
    try:
        data, row, _ = load(runs, name)
        projections(Pack().world(data["task_id"]), data, projection_path(cache, name, row))
    except Exception as e:
        return name, f"{type(e).__name__}: {e}"
    return name, None


def projection_path(cache: Path, name: str, row: dict) -> Path:
    return cache / "occ" / "projections" / f"{name.split('/', 1)[0]}.{row['run']}.json"


def projections(world, data: dict, path: Path) -> list[list[dict]]:
    """The day's projections by watch (episode.projections), kept at ``path`` once made: a replay takes minutes."""
    if path.is_file():
        return json.loads(path.read_text())
    out = episode.projections(world, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")))
    return out


def feature(index: bytes, stems: list[str]) -> bytes:
    """The index with the runs named first, in order (`featured`: 1, 2, ...), as the list shows them."""
    doc = json.loads(index)
    rank = {f"{stem}.json": k for k, stem in enumerate(stems, 1)}
    missing = set(rank) - {e["url"] for e in doc["episodes"]}
    if missing:
        raise SystemExit(f"not in the index: {', '.join(sorted(missing))}")
    doc["episodes"] = [e | ({"featured": rank[e["url"]]} if e["url"] in rank else {}) for e in doc["episodes"]]
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode()


def write(files: dict[str, bytes], out: Path) -> None:
    for path, content in files.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(content)


def space(files: dict[str, bytes], out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    write({f"data/viewer/{p}": c for p, c in files.items()}, out)
    write({f"viewer/{p.relative_to(export.WEB).as_posix()}": p.read_bytes()
           for p in sorted(export.WEB.rglob("*")) if p.is_file()}, out)
    (out / "index.html").write_text(PAGE)
    (out / "Dockerfile").write_text(DOCKERFILE)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("runs_named", nargs="+", metavar="FOLDER[/RUN]",
                   help="e.g. occ-pilot-sol/b6-20230709-0400-joint, or occ-board-sol for all its scored runs")
    p.add_argument("--runs", type=Path, default=ROOT / "results" / "runs")
    p.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs",
                   help="The raw inputs the pack was built from: BTS on-time, the FAA registry, ATCSCC, IEM METARs.")
    p.add_argument("--cache", type=Path, default=ROOT / "cache",
                   help="Where the airport stages and hub twins are; the traffic is baked into its traffic/.")
    p.add_argument("--out", type=Path, help="The viewer folder to write.")
    p.add_argument("--space-out", type=Path, help="Assemble the static Space here too, with its Dockerfile.")
    p.add_argument("--hub", choices=("JFK", "BOS"), help="The airport shown in 3D; default the task's.")
    p.add_argument("--replay", action="store_true",
                   help="Replay each day's changes to give every watch the desks' projection (a few minutes a day, "
                        "kept under --cache/occ/projections); else the board shows how the day flew.")
    p.add_argument("--workers", type=int, default=1, help="Replay this many days at once.")
    p.add_argument("--exclude", action="append", default=[], metavar="FOLDER/RUN", help="Leave this run out.")
    p.add_argument("--featured", action="append", default=[], metavar="STEM",
                   help="An episode the list shows first, in the order given (its file name without .json).")
    p.add_argument("--label", action="append", default=[], metavar="FOLDER=AS",
                   help="List a folder's runs as another's, e.g. a retry folder as the board it reruns.")
    p.add_argument("--projections-only", action="store_true",
                   help="Only replay the runs into --cache/occ/projections (the slow part), e.g. on a large machine.")
    args = p.parse_args(argv)
    names = [n for arg in args.runs_named for n in expand(args.runs, arg) if n not in args.exclude]
    if args.replay or args.projections_only:
        failed = replay_all(names, args.runs, args.cache, args.workers)
        if failed:
            print("not replayed:\n" + "\n".join(failed))
            return 1
    if args.projections_only:
        return 0
    if args.out is None:
        p.error("--out is required unless --projections-only")
    episodes = build(names, args.runs, args.inputs, args.cache, args.hub, args.replay,
                     dict(x.split("=", 1) for x in args.label))
    files = export.folder(episodes, args.cache)
    files["episodes/index.json"] = feature(files["episodes/index.json"], args.featured)
    shutil.rmtree(args.out, ignore_errors=True)
    write(files, args.out)
    print(f"{len(files)} files into {args.out}")
    if args.space_out:
        space(files, args.space_out)
        print(f"the Space into {args.space_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
