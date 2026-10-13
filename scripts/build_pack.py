"""Rebuild a data pack (default data/b6-v1) from the pinned public inputs.

Downloads what is missing into cache/inputs/ (BTS on-time months, B-43, T-100, the FAA registry, FAA ATCSCC advisories,
IEM METARs, TAFs and forecast discussions), checks every input against the pins in the pack's manifest.json, builds
the tasks (reference left empty for scripts/references.py) and writes tasks.jsonl, manifest.json and
sources/<source>/SOURCE.md. A rerun on the same inputs writes the same bytes.

    python scripts/build_pack.py [--months 2022-12,2023-06,2024-01] [--hubs JFK,BOS] [--offline] [--repin]
"""

import argparse
import json
import sys
from pathlib import Path

from agentenv_airops.build import fetch, manifest, pack

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--months", default=",".join(pack.MONTHS), help="comma-separated YYYY-MM")
    ap.add_argument("--hubs", default=",".join(pack.HUBS), help="comma-separated, from " + ", ".join(pack.HUBS))
    ap.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs")
    ap.add_argument("--derived", type=Path, default=ROOT / "cache" / "derived")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "b6-v1")
    ap.add_argument("--offline", action="store_true", help="download nothing: every input must be in --inputs")
    ap.add_argument("--repin", action="store_true", help="accept inputs whose sha256 differs from the manifest's pin")
    ap.add_argument("--keep-references", action="store_true",
                    help="carry each task's reference block over from the existing tasks.jsonl")
    args = ap.parse_args(argv)
    months, hubs = args.months.split(","), args.hubs.split(",")
    if not args.offline:
        pack.fetch_inputs(args.inputs, months, hubs)
    current = manifest.pins(args.inputs, months, hubs)
    old = args.out / "manifest.json"
    if old.exists() and not args.repin:
        changed = manifest.verify(current, json.loads(old.read_text())["inputs"])
        if changed:
            print("inputs differ from their pins (--repin accepts them):", *changed, sep="\n  ", file=sys.stderr)
            return 1
    tasks, tried = pack.build(args.inputs, args.derived, months, hubs)
    previous = args.out / "tasks.jsonl"
    if args.keep_references and previous.exists():
        kept = {t["task_id"]: t["reference"] for t in map(json.loads, previous.read_text().splitlines())}
        for t in tasks:
            t["reference"] = kept.get(t["task_id"], {})
    path = pack.write(tasks, args.out)
    m = manifest.build_manifest(args.out.name, tasks, tried, current, fetch.fetched(args.inputs), fetch.sha256(path),
                                months, hubs)
    manifest.write(args.out, m)
    print(json.dumps({"tasks": m["tasks"], "splits": m["splits"], "sha256": m["sha256"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
