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


def build(names: list[str], runs: Path, inputs: Path, cache: Path, hub: str | None, replay: bool) -> list[dict]:
    pack, out = Pack(), []
    for name in names:
        data, row, manifest = load(runs, name)
        world = pack.world(data["task_id"])
        folder = name.split("/", 1)[0]
        projected = projections(world, data, cache / "occ" / "projections" / f"{folder}.{row['run']}.json") \
            if replay else None
        record = {"folder": folder, "run": row["run"], "attempt": row["attempt"], "cost_usd": row.get("cost_usd"),
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


def projections(world, data: dict, path: Path) -> list[list[dict]]:
    """The day's projections by watch (episode.projections), kept at ``path`` once made: a replay takes minutes."""
    if path.is_file():
        return json.loads(path.read_text())
    out = episode.projections(world, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")))
    return out


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
    p.add_argument("runs_named", nargs="+", metavar="FOLDER/RUN", help="e.g. occ-pilot-sol/b6-20230709-0400-joint")
    p.add_argument("--runs", type=Path, default=ROOT / "results" / "runs")
    p.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs",
                   help="The raw inputs the pack was built from: BTS on-time, the FAA registry, ATCSCC, IEM METARs.")
    p.add_argument("--cache", type=Path, default=ROOT / "cache",
                   help="Where the airport stages and hub twins are; the traffic is baked into its traffic/.")
    p.add_argument("--out", type=Path, required=True, help="The viewer folder to write.")
    p.add_argument("--space-out", type=Path, help="Assemble the static Space here too, with its Dockerfile.")
    p.add_argument("--hub", choices=("JFK", "BOS"), help="The airport shown in 3D; default the task's.")
    p.add_argument("--replay", action="store_true",
                   help="Replay each day to give every watch the desks' projection (about 20 min a day, kept under "
                        "--cache/occ/projections); else the board shows how the day flew.")
    args = p.parse_args(argv)
    episodes = build(args.runs_named, args.runs, args.inputs, args.cache, args.hub, args.replay)
    files = export.folder(episodes, args.cache)
    shutil.rmtree(args.out, ignore_errors=True)
    write(files, args.out)
    print(f"{len(files)} files into {args.out}")
    if args.space_out:
        space(files, args.space_out)
        print(f"the Space into {args.space_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
