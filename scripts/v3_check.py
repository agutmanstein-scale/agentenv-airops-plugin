"""Is v3 done? docs/v3/DESIGN.md §0's criteria measured on the v3 pack against v2 on the same dates, and world_check's
gates on the v3 pack (§6.2). No LLM is called; only the reference solvers run (HiGHS), and the suite's agreement and
live-play tests as evidence.

    python scripts/v3_check.py PACK --v2 DIR [--workers 14] [--prove-s 1800] [--watch-s 900] [--no-tests]

1. the v3 pack's tasks (scripts/v3_pack.py) and their references under PACK/refs/ (v3_pack.refs: those there whose
   signature matches are kept, the others run, so a pack built with `--refs` runs nothing here);
2. v2's tasks of the same dates in DIR: read from DIR/tasks.jsonl where it has them, the others built (network.build
   without crew bases or layers, at scale 1 and the 03:00 cutoff as the pack builds them, the controls at the pack's
   control windows, then rescaled to the pack's scale) and written there, and their references under DIR/refs/ the
   same way;
3. PACK/check.json and PACK/check.md: every criterion with its values, its threshold and pass/fail, then world_check's
   gates on the v3 pack.

The criteria, on the storm dates both packs have the numbers for (the controls only in `sound`):
- harder: do-nothing's median excess over the anchor at least DO_NOTHING x v2's; the follower's median excess
  over the anchor (its gap) at least FOLLOWER x v2's;
- realistic: the anchors' cancellations (auto-cancels included) at least OPTIMUM_SHARE of JetBlue's, pooled; the
  could-fly-for-free share (programs.probe: the feasible restores, a pair once, over JetBlue's cancellations, pooled)
  at least FREE_DROP below v2's; the recorded day's median excess over the anchor below v2's; the airline replay flies
  at least FLOWN_SHARE of the flights JetBlue flew, pooled; and of the flights it flies, the share leaving within 15
  min of JetBlue's minute is not below v2's;
- sound: every anchor of the pack proven to its gap, or flagged (listed) where it isn't, and none failed; every anchor
  replays exactly (its plan's cost on the sim is its objective, every departure as the MIP has it); the suite's
  sim/MIP agreement tests (tests/programs/test_hindsight_mip.py, layered days included) pass;
- playable: the live-server test in which an LLM (a fake LiteLLM) plays a v3 day end to end, graded on the truth
  (tests/v3day/test_v3_agent.py), passes.
The gates, as world_check registers them (airline_replay, fidelity, calm_day, controls, references) on the pack's kept
tasks, a task being kept when every MIP it ran proved and no reference failed; `days` reports the pack's dates.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import v3_pack
import world_check

from agentenv_airops.programs import candidates, network, rbs, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.model import Task, load_tasks, write_tasks
from agentenv_airops.programs.repairs import CUTOFF

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
DO_NOTHING, FOLLOWER = 1.4, 1.5
OPTIMUM_SHARE, FREE_DROP, FLOWN_SHARE = 0.35, 0.10, 0.97
AGREEMENT = ("tests/programs/test_hindsight_mip.py",)
PLAYABLE = ("tests/v3day/test_v3_agent.py",)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack", type=Path)
    ap.add_argument("--v2", type=Path, required=True, help="v2's folder: its tasks.jsonl and refs/, built if missing")
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--prove-s", type=float, default=v3_pack.PROVE_S, help="time limit of each anchor, flat or calm")
    ap.add_argument("--watch-s", type=float, default=v3_pack.WATCH_S, help="time limit of each follower watch solve")
    ap.add_argument("--no-tests", action="store_true", help="skip the agreement and live-play tests")
    args = ap.parse_args(argv)
    limits = (args.prove_s, args.watch_s)
    clock = v3_pack.Clock()
    manifest = json.loads((args.pack / "manifest.json").read_text())
    v3 = load_tasks(args.pack)
    v3_refs = v3_pack.refs(args.pack, v3, args.workers, limits, clock)
    v2 = v2_tasks(args.v2, v3, manifest, args.workers)
    clock.lap("v2 tasks", f"{len(v2)} in {args.v2}")
    v2_refs = v3_pack.refs(args.v2, v2, args.workers, limits, clock)
    tests = {} if args.no_tests else {"agreement": pytest(AGREEMENT), "playable": pytest(PLAYABLE)}
    clock.lap("tests", json.dumps({k: v["pass"] for k, v in tests.items()}))
    out = {"criteria": criteria(v3_refs, v2_refs, tests), "gates": gates(v3, v3_refs, manifest),
           "parameters": {"pack": str(args.pack), "v2": str(args.v2), "time_limits_s": limits,
                          "scale": manifest["scale"], "code": v3_pack.revision(), "pack_code": manifest.get("code")},
           "tasks": [row(t, v3_refs.get(t.task_id, {}), v2_refs.get(t.task_id, {})) for t in v3],
           "wall_s": clock.laps}
    out["done"] = all(c["pass"] for c in out["criteria"].values())
    (args.pack / "check.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    (args.pack / "check.md").write_text(markdown(out))
    print(json.dumps({k: {"pass": c["pass"], "value": c.get("value")} for k, c in out["criteria"].items()}
                     | {"gates": {k: g["pass"] for k, g in out["gates"].items()}, "done": out["done"]}, indent=1),
          flush=True)


def v2_tasks(folder: Path, v3: Iterable[Task], manifest: Mapping, workers: int) -> list[Task]:
    """v2's task of each v3 task's date, in the v3 pack's order: read from folder/tasks.jsonl, else built without crew
    or layers at scale 1 and the cutoff (a control at the pack's control windows), rescaled to the pack's scale with
    the v3 task's split, and folder/tasks.jsonl written with every one."""
    v3 = list(v3)
    path = folder / "tasks.jsonl"
    have = {t.task_id: t for t in load_tasks(path)} if path.exists() else {}
    todo = [t for t in v3 if t.task_id not in have]
    if todo:
        fits = candidates.e_table(INPUTS, DERIVED, workers=workers)
        e_fit, e_median = unseen.table(fits)
        scale = manifest["scale"]
        controls = manifest.get("controls", {})
        jobs = [(t.date, t.storms, None, "") if t.split != "control" else
                (t.date, tuple(controls[t.date]["hubs"]), {h: tuple(w) for h, w in controls[t.date]["windows"].items()},
                 "control") for t in todo]
        built = network.build_all(jobs, INPUTS, DERIVED, e_fit, e_median, dict.fromkeys(HUBS, 1.0), CUTOFF,
                                  workers=max(1, workers))
        split = {(t.date, t.split == "control"): t.split for t in todo}
        shipped = unseen.table(fits, scale)
        for b in built:
            if b.task is None:
                print(f"  v2 {b.row['date']} not built: {b.row['reason']}", flush=True)
                continue
            s = split[(b.task.date, b.task.split == "control")]
            have[b.task.task_id] = rbs.rescale(replace(b.task, split=s), scale, *shipped)
    out = [have[t.task_id] for t in v3 if t.task_id in have]
    if todo:
        write_tasks(out, path)
    return out


def pytest(paths: Iterable[str]) -> dict:
    """The suite's tests at `paths`, run as the gates run them: pass/fail and pytest's last line."""
    t0 = time.monotonic()
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths], cwd=ROOT,
                         capture_output=True, text=True)
    lines = [x for x in run.stdout.splitlines() if x.strip()]
    return {"tests": list(paths), "pass": run.returncode == 0, "summary": lines[-1] if lines else run.stderr[-500:],
            "wall_s": round(time.monotonic() - t0, 1)}


def criteria(v3: Mapping[str, dict], v2: Mapping[str, dict], tests: Mapping[str, dict]) -> dict:
    """§0's criteria from the two packs' references by task id (refs/<id>.json each) and the tests' outcomes."""
    storm = {i: (r, v2[i]) for i, r in v3.items() if r.get("split") != "control" and i in v2}
    out = {
        "harder_do_nothing": ratio_of(storm, lambda r: excess(r, "naive"), DO_NOTHING,
                                      "do-nothing's median excess over the anchor, v3 >= 1.4 x v2"),
        "harder_follower": ratio_of(storm, lambda r: excess(r, "follower"), FOLLOWER,
                                    "the follower's median excess over the anchor, v3 >= 1.5 x v2"),
        "realistic_optimum_cancels": pooled_of(storm, anchor_cancels, lambda r: r["jetblue"]["cancelled"],
                                               lambda a, b: a >= OPTIMUM_SHARE,
                                               "the anchors' cancellations over JetBlue's, v3 >= 35%"),
        "realistic_free_share": pooled_of(storm, lambda r: r["probe"]["feasible"], lambda r: r["probe"]["cancelled"],
                                          lambda a, b: b is not None and a <= b - FREE_DROP,
                                          "JetBlue's cancellations that could fly for free (a pair once), v3 at least "
                                          "10 points below v2"),
        "realistic_recorded_excess": median_of(storm, lambda r: excess(r, "recorded"), lambda a, b: a < b,
                                               "the recorded day's median excess over the anchor, v3 < v2"),
        "realistic_replay_flown": pooled_of(storm, lambda r: r["fidelity"]["flown"],
                                            lambda r: r["fidelity"]["jetblue_flown"], lambda a, b: a >= FLOWN_SHARE,
                                            "the replay flies >= 97% of the flights JetBlue flew"),
        "realistic_replay_within_15": pooled_of(storm, lambda r: r["fidelity"]["within"],
                                                lambda r: r["fidelity"]["flown"], lambda a, b: b is not None and a >= b,
                                                "of the replay's flown flights, the share leaving within 15 min of "
                                                "JetBlue's minute, v3 >= v2"),
        "sound_anchors_proven": proven(v3),
        "sound_anchors_exact": exact(v3),
        "sound_agreement_tests": tested(tests, "agreement", "the suite's sim/MIP agreement tests pass"),
        "playable": tested(tests, "playable", "an LLM (fake LiteLLM) plays a v3 day end to end through the server"),
    }
    return out


def excess(r: dict, k: str) -> float | None:
    """r[k]'s cost over the proven anchor's, less 1; None where either is missing."""
    a = r.get("anchor", {})
    if not a.get("proven") or "total" not in r.get(k, {}) or not a.get("objective"):
        return None
    return r[k]["total"] / a["objective"] - 1


def anchor_cancels(r: dict) -> int | None:
    a = r.get("anchor", {})
    return a["cancelled"] + a["auto_cancelled"] if a.get("proven") and "cancelled" in a else None


def values(pairs: Mapping[str, tuple[dict, dict]], f: Callable[[dict], float | None]) -> tuple[list, list, int]:
    """f on both packs' references of every task where it is defined on both, and the tasks it is defined on."""
    a, b = [], []
    for r3, r2 in pairs.values():
        x, y = safe(f, r3), safe(f, r2)
        if x is not None and y is not None:
            a.append(x)
            b.append(y)
    return a, b, len(a)


def safe(f: Callable[[dict], float | None], r: dict) -> float | None:
    try:
        return f(r)
    except (KeyError, TypeError):
        return None


def ratio_of(pairs: Mapping[str, tuple[dict, dict]], f: Callable, times: float, rule: str) -> dict:
    a, b, n = values(pairs, f)
    m3, m2 = med(a), med(b)
    ratio = round(m3 / m2, 3) if m3 is not None and m2 else None
    return {"rule": rule, "n": n, "v3": m3, "v2": m2, "value": ratio, "threshold": times,
            "pass": ratio is not None and m2 > 0 and ratio >= times}


def median_of(pairs: Mapping[str, tuple[dict, dict]], f: Callable, ok: Callable[[float, float], bool],
              rule: str) -> dict:
    a, b, n = values(pairs, f)
    m3, m2 = med(a), med(b)
    return {"rule": rule, "n": n, "v3": m3, "v2": m2, "value": m3,
            "pass": m3 is not None and m2 is not None and ok(m3, m2)}


def pooled_of(pairs: Mapping[str, tuple[dict, dict]], num: Callable, den: Callable,
              ok: Callable[[float, float | None], bool], rule: str) -> dict:
    """sum(num) / sum(den) on each pack over the tasks where both are defined on both."""
    rows = [(safe(num, r3), safe(den, r3), safe(num, r2), safe(den, r2)) for r3, r2 in pairs.values()]
    rows = [x for x in rows if None not in x]
    n3, d3, n2, d2 = (sum(x[k] for x in rows) for k in range(4))
    s3 = round(n3 / d3, 4) if d3 else None
    s2 = round(n2 / d2, 4) if d2 else None
    return {"rule": rule, "n": len(rows), "v3": s3, "v2": s2, "value": s3, "v3_counts": [n3, d3],
            "v2_counts": [n2, d2], "pass": s3 is not None and ok(s3, s2)}


def proven(v3: Mapping[str, dict]) -> dict:
    """Every anchor of the pack proven, or flagged where it isn't; it fails only on an anchor that failed or is
    missing."""
    failed = sorted(i for i, r in v3.items() if "proven" not in r.get("anchor", {}))
    flagged = {i: {k: r["anchor"].get(k) for k in ("status", "gap_pct")} for i, r in sorted(v3.items())
               if i not in failed and not r["anchor"]["proven"]}
    return {"rule": "every anchor proven to its gap, or flagged", "n": len(v3), "value": len(v3) - len(failed) -
            len(flagged), "flagged": flagged, "failed": failed,
            "max_gap_pct": max((r["anchor"].get("gap_pct") or 0 for i, r in v3.items() if i not in failed),
                               default=None), "pass": bool(v3) and not failed}


def exact(v3: Mapping[str, dict]) -> dict:
    ran = {i: r["anchor"] for i, r in v3.items() if "exact" in r.get("anchor", {})}
    off = sorted(i for i, a in ran.items() if not a["exact"])
    return {"rule": "every anchor's plan replays exactly in the sim", "n": len(ran), "value": len(ran) - len(off),
            "not_exact": off, "pass": bool(ran) and not off and len(ran) == len(v3)}


def tested(tests: Mapping[str, dict], name: str, rule: str) -> dict:
    t = tests.get(name)
    return {"rule": rule, "value": t["summary"] if t else "not run", **(t or {}), "pass": bool(t and t["pass"])}


def gates(tasks: Iterable[Task], results: Mapping[str, dict], manifest: Mapping) -> dict:
    """world_check's gates (world_check.summary's) on the pack: its kept storm tasks, its controls."""
    tasks = {t.task_id: t for t in tasks}
    storms = {i: t for i, t in tasks.items() if t.split != "control"}
    held = [i for i, t in tasks.items() if t.split == "control"]
    kept = {i for i, r in results.items() if not world_check.unproven(r)}
    pool = {i: r for i, r in results.items() if i in storms and i in kept}
    autos = [(r["airline"]["auto"], r["airline"]["flew"]) for r in pool.values() if "auto" in r.get("airline", {})]
    calm = [r["calm"]["cancelled"] <= world_check.CALM_CANCELS and r["calm"]["ferries"] <= world_check.CALM_FERRIES
            for r in pool.values() if "cancelled" in r.get("calm", {})]
    ctl = {i: world_check.control_checks(results.get(i, {}), i in kept) for i in held}
    fid = {(i, h): unseen.program_delay(t, hub=h) for i, t in storms.items() if i in pool for h in t.storms}
    left = {i: world_check.unproven(r) for i, r in results.items() if i not in kept}
    return {
        "airline_replay": world_check.gate(world_check.share([world_check.replays(a, n) for a, n in autos]),
                                           len(autos), f"auto-cancels <= {world_check.AUTO_SHARE:.0%} of the flights "
                                           f"JetBlue flew by their deadline and <= {world_check.AUTO_MAX}, per kept "
                                           "storm date", {"auto_cancels": sum(a for a, _ in autos),
                                                          "flew": sum(n for _, n in autos)}),
        "fidelity": world_check.fidelity_gate(fid, {i: t.split for i, t in storms.items()}, True),
        "calm_day": world_check.gate(world_check.share(calm), len(calm), f"<= {world_check.CALM_CANCELS} forced "
                                     f"cancellation and <= {world_check.CALM_FERRIES} ferries, per kept storm date"),
        "controls": {"controls": ctl, "n": len(ctl), "threshold": "both control dates: recorded >= anchor and naive "
                     f"within {world_check.CONTROL_MARGIN:.0%} of the flat anchor",
                     "pass": len(ctl) == world_check.CONTROL_DATES and all(c["ok"] for c in ctl.values())},
        "references": world_check.references_gate({i: r for i, r in results.items() if i in kept}, left),
        "days": {"storm_dates": manifest.get("storm_dates"), "built": manifest.get("built"),
                 "split": {s: sum(t.split == s for t in storms.values()) for s in ("eval", "train")},
                 "in_pack": len(storms), "controls": len(held), "kept": len(kept & set(storms)),
                 "by": {k: sum(network.label(t) == k for t in storms.values())
                        for k in sorted({network.label(t) for t in storms.values()})},
                 "unbuilt": len(manifest.get("unbuilt", {})), "pass": True},
    }


def row(t: Task, r3: dict, r2: dict) -> dict:
    """One task's numbers on both packs."""
    def nums(r):
        return {"naive_x": rnd(excess(r, "naive")), "follower_x": rnd(excess(r, "follower")),
                "recorded_x": rnd(excess(r, "recorded")), "anchor": r.get("anchor", {}).get("objective"),
                "proven": r.get("anchor", {}).get("proven"), "exact": r.get("anchor", {}).get("exact"),
                "anchor_cancels": safe(anchor_cancels, r), "jetblue_cancelled": r.get("jetblue", {}).get("cancelled"),
                "free": r.get("probe", {}).get("feasible"), "flown": r.get("fidelity", {}).get("flown"),
                "jetblue_flown": r.get("fidelity", {}).get("jetblue_flown"),
                "within_15": r.get("fidelity", {}).get("within"), "watches": r.get("watches"),
                "left_out": world_check.unproven(r) if r else ["missing"], "seconds": r.get("seconds")}
    return {"task_id": t.task_id, "split": t.split, "hubs": "+".join(t.storms), "v3": nums(r3), "v2": nums(r2)}


def med(v: list[float]) -> float | None:
    return round(statistics.median(v), 4) if v else None


def rnd(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def markdown(out: dict) -> str:
    lines = [f"# v3 check ({datetime.now(UTC).date().isoformat()})", "",
             f"Done (every §0 criterion passes): **{out['done']}**", "", "## §0 criteria", "",
             "| Criterion | Rule | n | v3 | v2 | Value | Pass |", "|---|---|---|---|---|---|---|"]
    for k, c in out["criteria"].items():
        lines.append(f"| {k} | {c['rule']} | {c.get('n', '')} | {c.get('v3', '')} | {c.get('v2', '')} | "
                     f"{c.get('value')} | {c['pass']} |")
    flagged = out["criteria"]["sound_anchors_proven"]
    lines += ["", f"Anchors flagged (not proven): {json.dumps(flagged['flagged'])}; failed: {flagged['failed']}", "",
              "## world_check's gates on the v3 pack", "", "| Gate | Value | Threshold | Pass |", "|---|---|---|---|"]
    for k, g in out["gates"].items():
        value = {x: v for x, v in g.items() if x not in ("threshold", "pass", "rule", "controls", "left_out_tasks")}
        lines.append(f"| {k} | {json.dumps(value, default=str)} | {g.get('threshold', '')} | {g['pass']} |")
    lines += ["", "## Controls", "", f"```\n{json.dumps(out['gates']['controls']['controls'], indent=1)}\n```", "",
              "## Per task (v3 / v2)", "",
              "| task | split | hubs | naive x | follower x | recorded x | anchor cancels / JetBlue | free | "
              "flown / JetBlue | within 15 | proven | exact |", "|" + "---|" * 12]
    for r in out["tasks"]:
        a, b = r["v3"], r["v2"]
        lines.append(f"| {r['task_id']} | {r['split']} | {r['hubs']} | {a['naive_x']} / {b['naive_x']} | "
                     f"{a['follower_x']} / {b['follower_x']} | {a['recorded_x']} / {b['recorded_x']} | "
                     f"{a['anchor_cancels']} / {b['anchor_cancels']} of {a['jetblue_cancelled']} | "
                     f"{a['free']} / {b['free']} | {a['flown']} / {b['flown']} of {a['jetblue_flown']} | "
                     f"{a['within_15']} / {b['within_15']} | {a['proven']} / {b['proven']} | "
                     f"{a['exact']} / {b['exact']} |")
    lines += ["", "## Parameters and wall clock", "",
              f"```\n{json.dumps({**out['parameters'], 'wall_s': out['wall_s']}, indent=1, default=str)}\n```"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
