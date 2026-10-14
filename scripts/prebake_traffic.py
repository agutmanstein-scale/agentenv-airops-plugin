"""Pre-bake the 3D viewer's traffic for tasks: every carrier's movements at the hub from BTS on-time on their real
times, typed from the FAA registry, the task's flights and frozen legs there, the METARs and the ground stops
(agentenv_airops.stage.traffic), written as cache/traffic/<task_id>.json.gz. The viewer lays each run over it.

usage: python scripts/prebake_traffic.py TASK_ID... | --sweep NAME
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

from agentenv_airops import stage
from agentenv_airops.core.model import load_pack
from agentenv_airops.sweep import RUNS, load

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("tasks", nargs="*", help="Task ids of the pack.")
    p.add_argument("--sweep", help="Every task of this sweep under results/runs.")
    p.add_argument("--pack", type=Path, default=ROOT / "data" / "b6-v1" / "tasks.jsonl")
    p.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs")
    p.add_argument("--out", type=Path, default=ROOT / "cache" / "traffic")
    args = p.parse_args(argv)
    wanted = list(args.tasks) + (load(args.sweep, ROOT / RUNS).tasks if args.sweep else [])
    if not wanted:
        p.error("name tasks or a --sweep")
    days = {t.task_id: t for t in load_pack(args.pack)}
    missing = [t for t in wanted if t not in days]
    if missing:
        p.error(f"not in {args.pack}: {', '.join(missing)}")
    args.out.mkdir(parents=True, exist_ok=True)
    for task_id in dict.fromkeys(wanted):
        doc = stage.traffic(days[task_id], args.inputs)
        path = args.out / f"{task_id}.json.gz"
        with gzip.GzipFile(path, "wb", mtime=0) as f:
            f.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode())
        others = doc["aircraft"]
        print(f"{task_id}: {len(others)} aircraft ({sum(a['jetblue'] for a in others)} JetBlue outside the task), "
              f"{sum(len(a['visits']) for a in others)} visits, {len(doc['weather'])} METARs, "
              f"{len(doc['stops'])} ground stops -> {path} ({path.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
