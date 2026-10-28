"""The realistic pack (b6-v3r) from b6-v3, the realistic inputs cache and the realism report's runs: the days the
report priced, each with its layered task (v4's caps and closures), the tails that break and when, tomorrow's first
departures, each passenger group's fare and the references of JetBlue's cost J; the manifest carries the gates, the
breakdown rate and J's fitted shares, so the env reads no cache.

J's references per day: JetBlue's replay (jbfull@360, the anchor, 0.5), the best plan found under J (bestj, its plan
kept) and the cheapest of every plan the report priced (the top, 1.0). TAKE and SAVE are fitted on the stream days, as
the report fits them.

Usage: .venv/bin/python scripts/build_realistic_pack.py REALREP_DIR BESTJ_DIR [--out data/b6-v3r]
"""

import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from agentenv_airops import realistic, realworld
from agentenv_airops.occ.pack import Pack3

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "b6-v3"
JB = "jbfull@360"
LOST = 50e6 / (4100 * 7_333_000 / 68_152 * 167.69)
KEPT = 35 / 50


def fits(rows: list[dict]) -> tuple[float, float]:
    """TAKE and SAVE over the stream days' JetBlue replays: the stranded's share of the fare such that JetBlue loses
    LOST of the fares booked on its cancelled flights, and the share of FAA's fuel and maintenance rate such that a
    cancellation saves 1 - KEPT of the revenue it loses (JetBlue's Q1 2014 storms)."""
    a = [r["real"][JB]["airline"] for r in rows]
    take = sum(LOST * x["cancelled_rev"] - x["refunded_rev"] for x in a) / sum(x["stranded_rev"] for x in a)
    save = (1 - KEPT) * sum(LOST * x["cancelled_rev"] for x in a) / sum(x["saved"] for x in a)
    return take, save


def main():
    rep, bestj = Path(sys.argv[1]), Path(sys.argv[2])
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "data" / "b6-v3r"
    base = json.loads((BASE / "manifest.json").read_text())
    pack = Pack3(BASE)
    ids = {v["date"]: t for t, v in pack.tasks.items()}
    rows = {p.stem: json.load(open(p)) for p in sorted(rep.glob("*.json"))}
    take, save = fits([r for r in rows.values() if "model" in r["now"]])
    gate_caps = realistic.gates(BASE / "dates")
    rate = next(iter(rows.values()))["realistic"]["rate"]
    (out / "dates").mkdir(parents=True, exist_ok=True)
    tasks, files = {}, {}
    for date, row in rows.items():
        task_id = ids[date]
        w = pack.world(task_id)
        raw = json.loads(gzip.decompress((BASE / "dates" / f"{date}.json.gz").read_bytes()))
        task_r = realistic.layered(w.task)
        down = realistic.broken(task_r, rate)
        if down != row["realistic"]["broken"]:
            raise ValueError(f"{date}: breakdowns {down} differ from the report's {row['realistic']['broken']}")
        firsts = realistic.first_departures(w.task)
        fares = {g.id: realistic.od_fare(task_r, g.legs) for g in w.passengers.groups}
        found = json.load(open(bestj / f"{date}.json"))
        priced = {k: v["airline"] for k, v in row["real"].items() if "airline" in v} | {"best J": found["airline"]}
        cost = {k: round(realworld.jetblue(a, take, save), 1) for k, a in priced.items()}
        raw["task"] = task_r.to_dict()
        raw["realistic"] = {
            "broken": down, "firsts": [[fam, s, d] for (fam, s), d in sorted(firsts.items())],
            "fares": {str(k): round(v, 3) for k, v in fares.items()},
            "refs": {"jetblue": cost[JB], "best": cost["best J"], "top": min(cost.values()),
                     "top_from": min(cost, key=cost.get), "priced": cost,
                     "cancelled": {k: sum(x["status"] != "flown" for x in v["flights"].values())
                                   for k, v in row["real"].items() if k in priced} |
                                  {"best J": sum(x["status"] != "flown" for x in found["flights"].values())}},
            "best_plan": found["plan"]}
        body = gzip.compress(json.dumps(raw, separators=(",", ":")).encode(), mtime=0)
        (out / "dates" / f"{date}.json.gz").write_bytes(body)
        files[f"dates/{date}.json.gz"] = hashlib.sha256(body).hexdigest()
        tasks[task_id] = base["tasks"][task_id]
        print(f"{date} JB {cost[JB] * 1.07 / 1e6:.2f}M top {min(cost.values()) * 1.07 / 1e6:.2f}M "
              f"({min(cost, key=cost.get)}) broken {len(down)}", flush=True)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    manifest = {"pack": "b6-v3r", "base": "b6-v3", "base_manifest_sha256": hashlib.sha256(
                    (BASE / "manifest.json").read_bytes()).hexdigest(), "assembled_at_commit": commit,
                "realistic": {"gates": gate_caps, "rate": rate, "swaps": sorted(realworld.SWAPS), "take": take,
                              "save": save, "lost": LOST, "kept": KEPT, "usd_per_pax_min": 1.07},
                "tasks": dict(sorted(tasks.items(), key=lambda kv: kv[1]["date"])), "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(tasks)} days, take {take:.3f} save {save:.3f} -> {out}")


if __name__ == "__main__":
    main()
