"""Both stage-2 runs of AirOpsEnv v3 in one go, for the box's run.sh (one unit, one upload, one power-off):
1. the passenger-aware aircraft plans under V7 for every date (scripts/pax_step0.py --split all), every candidate plan
   stored, into <out>/aircraft;
2. the reference, the bound, the consistency checks and the reported outputs (scripts/pax_stage2.py) from them, into
   <out>/reference;
then <out>/guards.json with both runs' guards.

    python scripts/pax_stage2_box.py --out cache/v3/stage2 --workers 48 [--aircraft-workers 35] [--seeds 4]
        [--time-limit 1800] [--search-s 1800] [--resume]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=48, help="reference jobs at once")
    ap.add_argument("--aircraft-workers", type=int, default=35, help="aircraft jobs at once, each --seeds searches")
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--time-limit", type=float, default=1800.0)
    ap.add_argument("--search-s", type=float, default=1800.0)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    resume = ["--resume"] if a.resume else []
    aircraft, reference = a.out / "aircraft", a.out / "reference"
    run([str(HERE / "pax_step0.py"), "--out", str(aircraft), "--workers", str(a.aircraft_workers), "--seeds",
         str(a.seeds), "--split", "all", "--time-limit", str(a.time_limit), *resume])
    run([str(HERE / "pax_stage2.py"), "--out", str(reference), "--aircraft", str(aircraft), "--workers",
         str(a.workers), "--split", "all", "--search-s", str(a.search_s), *resume])
    (a.out / "guards.json").write_text(json.dumps({
        "aircraft": json.loads((aircraft / "guards.json").read_text()),
        "reference": json.loads((reference / "guards.json").read_text())}, indent=1))


def run(args: list[str]) -> None:
    print(" ".join(args), flush=True)
    subprocess.run([sys.executable, "-u", *args], check=True)


if __name__ == "__main__":
    main()
