"""The two-seat premise check (the Command Center model), part 1: the draw, the world and its guards. No LLM is called.

    python scripts/flow_premise.py draw --out DIR

draw writes DIR/days.json: the 20 day-hubs (seed 2026) and the train day-hubs, from step 0's built candidates. It runs
before anything else is computed, and never again.
"""

import argparse
import json
from pathlib import Path

from agentenv_airops.flow import days

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
STEP0 = ROOT / "cache" / "step0" / "full"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="stage", required=True)
    d = sub.add_parser("draw")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--seed", type=int, default=days.SEED)
    args = ap.parse_args()
    if args.stage == "draw":
        draw(args.out, args.seed)


def draw(out: Path, seed: int) -> None:
    path = out / "days.json"
    if path.exists():
        raise SystemExit(f"{path} exists: the draw runs once")
    source = STEP0 / "candidates.jsonl"
    keys, built = days.read(source)
    doc = days.write(path, days.make(keys, built, seed), seed, str(source.relative_to(ROOT)))
    print(json.dumps({k: doc[k] for k in ("quotas", "eligible", "held_out_events")}, indent=1))
    print("drawn", [(r["date"], r["hub"], r["season"], r["event"]) for r in doc["drawn"]])
    print("train", len(doc["train"]))


if __name__ == "__main__":
    main()
