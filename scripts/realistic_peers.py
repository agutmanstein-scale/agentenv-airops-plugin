"""The peer-airline check of the realistic world (REALISTIC.md): where JetBlue cancelled a flight, what every other
carrier did at the same airport in the same hour, set against what the world does with the flight.

For each of JetBlue's flights on the days realistic_compare.py replayed, the other carriers' cancellation share at its
worse end (the departures from its origin within an hour of its scheduled departure, or the arrivals at its
destination within an hour of its scheduled arrival, each counted with at least 3 flights; BTS, every carrier). Rows:
the flights JetBlue flew; JetBlue's cancellations (and diversions) the world flies within an hour of schedule in a
scenario (the standard desks by default; `--current`, the current mode's world); and those the world flies an hour or
more late or cancels. A world that reads the day right flies, within an hour, the cancellations whose airport the other
carriers kept running, and holds up those where they cancelled too.

Usage: .venv/bin/python scripts/realistic_peers.py REALREP_DIR [--scenario desks] [--current]
"""

import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from agentenv_airops import realistic
from agentenv_airops.programs import candidates

ROOT = Path(__file__).resolve().parents[1]
DATES = ROOT / "data" / "b6-v3" / "dates"
HUBS = ("JFK", "BOS")
FLEW = "JetBlue flew"
EASY = "JetBlue cancelled, world flies within 1 h"
HARD = "JetBlue cancelled, world 1 h+ or cancels"


def peers(rep: Path, scenario: str = "desks", mode: str = "real") -> tuple[dict[str, np.ndarray], Counter]:
    """Per row the other carriers' cancellation share at each flight's worse end, and the worse ends of the world's
    easy flights where the other carriers cancelled 20% or more."""
    rows, ends = defaultdict(list), Counter()
    for p in sorted(rep.glob("*.json")):
        day = json.load(open(p))
        task = json.load(gzip.open(DATES / f"{day['date']}.json.gz"))["task"]
        x = candidates.frame(realistic.INPUTS, realistic.DERIVED, day["date"])
        x = x[x.Reporting_Airline != "B6"]
        dep, arr = defaultdict(list), defaultdict(list)
        for o, d, sd, sa, c in zip(x.Origin, x.Dest, x.sched_dep - task["base"], x.sched_arr - task["base"],
                                   x.Cancelled, strict=True):
            dep[o].append((sd, c))
            arr[d].append((sa, c))
        dep = {k: np.array(v) for k, v in dep.items()}
        arr = {k: np.array(v) for k, v in arr.items()}
        world = day[mode][scenario]["flights"]
        for f in task["flights"]:
            rd, ra = share(dep, f["orig"], f["sched_dep"]), share(arr, f["dest"], f["sched_arr"])
            if rd is None and ra is None:
                continue
            worse = max(v for v in (rd, ra) if v is not None)
            a, w = f["actual"], world[str(f["id"])]
            if not (a["cancelled"] or a["diverted"]):
                rows[FLEW].append(worse)
                continue
            easy = w["status"] == "flown" and w["dep"] - f["sched_dep"] < 60
            rows[EASY if easy else HARD].append(worse)
            if easy and worse >= 0.2:
                at_dep = (rd or 0) >= (ra or 0)
                ends[("departure from " if at_dep else "arrival at ") +
                     ("a hub" if (f["orig"] if at_dep else f["dest"]) in HUBS else "an outstation")] += 1
    return {k: np.array(v) for k, v in rows.items()}, ends


def share(table: dict, station: str, t: int) -> float | None:
    a = table.get(station)
    if a is None:
        return None
    near = np.abs(a[:, 0] - t) <= 60
    return float(a[near, 1].mean()) if near.sum() >= 3 else None


def main():
    rep = Path(sys.argv[1]).expanduser()
    scenario = sys.argv[sys.argv.index("--scenario") + 1] if "--scenario" in sys.argv else "desks"
    mode = "now" if "--current" in sys.argv else "real"
    rows, ends = peers(rep, scenario, mode)
    print(f"other carriers' cancellation share at the flight's worse end, within an hour ({rep.name}, {scenario}, "
          f"{'current' if mode == 'now' else 'realistic'} mode)")
    print(f"  {'':42} {'flights':>7} {'median':>7} {'mean':>6} {'20%+':>5} {'<5%':>5}")
    for k in (FLEW, EASY, HARD):
        v = rows[k]
        print(f"  {k:42} {len(v):7} {np.median(v):7.1%} {v.mean():6.1%} {(v >= 0.2).mean():5.0%} "
              f"{(v < 0.05).mean():5.0%}")
    print(f"  the world's easy flights where the other carriers cancelled 20%+, by worse end: {dict(ends)}")


if __name__ == "__main__":
    main()
