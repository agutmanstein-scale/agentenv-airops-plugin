"""Ship a v3 pack (scripts/v3_pack.py --bases none --refs) as data/b6-v3, the pack the airops-v3-day image and the
airops-v3 bundle read: its tasks gzipped (tasks.jsonl.gz, byte for byte the pack's lines, mtime 0 so a rebuild of the
same pack ships the same bytes), its stored reference costs (refs.json, the pack's own: each entry carries the digest
of the task it ran on, so the env uses it only on that very task) and a manifest of what it is: the commit and inputs
the pack was built from, its constants and scale, and each task's date and split.

    python scripts/ship_v3_pack.py ~/handoff/v3-loop/r1/pack-v3 --out data/b6-v3
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

from agentenv_airops.programs import references
from agentenv_airops.programs.model import Task

PACK = "b6-v3"
KEPT = ("code", "seed", "months", "bases", "crew", "scale", "constants", "inputs", "controls", "split", "refs")


def ship(pack: Path, out: Path) -> dict:
    """Write data/b6-v3 from `pack`; every task must have its anchor's stored cost, run on that task."""
    lines = [line for line in (pack / "tasks.jsonl").read_text().splitlines() if line.strip()]
    tasks = [Task.from_dict(json.loads(line)) for line in lines]
    refs = json.loads((pack / "refs.json").read_text())
    stale = [t.task_id for t in tasks if refs.get(t.task_id, {}).get("task") != references.digest(t)
             or "anchor" not in refs[t.task_id]]
    if stale:
        sys.exit(f"ERROR: no stored anchor run on these tasks (rerun v3_pack.py --refs): {', '.join(stale)}")
    built = json.loads((pack / "manifest.json").read_text())
    manifest = {"pack": PACK, **{k: built[k] for k in KEPT if k in built},
                "tasks": {t.task_id: {"date": t.date, "split": t.split} for t in tasks}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "tasks.jsonl.gz").write_bytes(gzip.compress("".join(line + "\n" for line in lines).encode(), mtime=0))
    (out / "refs.json").write_text(json.dumps({t.task_id: refs[t.task_id] for t in tasks}, indent=1) + "\n")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", type=Path, help="a v3_pack.py --refs output folder")
    ap.add_argument("--out", type=Path, default=Path("data") / PACK)
    args = ap.parse_args(argv)
    manifest = ship(args.pack, args.out)
    splits = {}
    for v in manifest["tasks"].values():
        splits[v["split"]] = splits.get(v["split"], 0) + 1
    print(f"Wrote {args.out}: {len(manifest['tasks'])} tasks {splits}, built at {manifest.get('code')}")


if __name__ == "__main__":
    main()
