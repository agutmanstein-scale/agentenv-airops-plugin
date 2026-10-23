"""Is v4 done? docs/v4/DESIGN.md §0's criteria measured on the v4 pack against v2 on the same dates, and world_check's
gates on the v4 pack (§6.2). No LLM is called; only the reference solvers run (HiGHS), and the suite's agreement and
live-play tests as evidence.

    python scripts/v4_check.py PACK --v2 DIR [--nocrew DIR] [--workers 14] [--prove-s 1800] [--watch-s 900]
                               [--no-tests]

1. the v4 pack's tasks (scripts/v4_pack.py) and their references under PACK/refs/ (v4_pack.refs: those there whose
   signature matches are kept, the others run, so a pack built with `--refs` runs nothing here);
2. v2's tasks of the same dates in DIR: read from DIR/tasks.jsonl where it has them, the others built (network.build
   without crew bases or layers, at scale 1 and the 03:00 cutoff as the pack builds them, the controls at the pack's
   control windows, then rescaled to the pack's scale) and written there, and their references under DIR/refs/ the
   same way;
3. PACK/check.json and PACK/check.md: every criterion with its values, its threshold and pass/fail, then world_check's
   gates on the v4 pack.

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
- playable: the live-server test in which an LLM (a fake LiteLLM) plays a v4 day end to end, graded on the truth
  (tests/v4day/test_v4_agent.py), passes.
On a pack with crews (v4's, or Edgar's: its manifest names crew bases), docs/v4/CREW.md §0's guards are criteria
too, on the same storm dates: the recorded day costs no less than the anchor on at least RECORDED_SHARE of them; the
anchors cancel (auto-cancels included) no more than JetBlue did, pooled; on every date whose calm day in the no-crew
pack (`--nocrew`, built with --bases none, its references read under DIR/refs/, never run) is within the fair-weather
bar below, the crew pack's is too, so its crews push no fair day past JetBlue's own; and of the replay's flown flights
the share leaving within 15 min of JetBlue's is within WITHIN_SLACK of v2's.
The gates, as world_check registers them (airline_replay, fidelity, calm_day, controls, references) on the pack's kept
tasks, a task being kept when every MIP it ran proved and no reference failed; `days` reports the pack's dates.
`calm_day` holds each date to JetBlue's fair-weather bar (layers.fairday: the share of a fair day's flights JetBlue
itself cancels, at its 90th percentile, as the pack's manifest records it under `calm`, at least 1, and FERRIES
ferries) rather than world_check's <= 1 and <= 2, which a hub window of 60 to 190 flights was set for and a whole day
of several hundred is not: JetBlue meets <= 1 on only 69% of its own fair days.
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

import v4_pack
import world_check

from agentenv_airops.programs import candidates, network, rbs, unseen
from agentenv_airops.programs.advisories import HUBS
from agentenv_airops.programs.layers import fairday
from agentenv_airops.programs.model import Task, load_tasks, write_tasks
from agentenv_airops.programs.repairs import CUTOFF

ROOT = Path(__file__).resolve().parents[1]
INPUTS, DERIVED = ROOT / "cache" / "inputs", ROOT / "cache" / "derived"
DO_NOTHING, FOLLOWER = 1.4, 1.5
OPTIMUM_SHARE, FREE_DROP, FLOWN_SHARE = 0.35, 0.10, 0.97
RECORDED_SHARE, WITHIN_SLACK = 0.95, 0.01
AGREEMENT = ("tests/programs/test_hindsight_mip.py",)
PLAYABLE = ("tests/v4day/test_v4_agent.py",)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pack", type=Path)
    ap.add_argument("--v2", type=Path, required=True, help="v2's folder: its tasks.jsonl and refs/, built if missing")
    ap.add_argument("--nocrew", type=Path,
                    help="the pack built with --bases none, whose calm days a crew pack's are read against")
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--prove-s", type=float, default=v4_pack.PROVE_S, help="time limit of each anchor, flat or calm")
    ap.add_argument("--watch-s", type=float, default=v4_pack.WATCH_S, help="time limit of each follower watch solve")
    ap.add_argument("--no-tests", action="store_true", help="skip the agreement and live-play tests")
    args = ap.parse_args(argv)
    limits = (args.prove_s, args.watch_s)
    clock = v4_pack.Clock()
    manifest = json.loads((args.pack / "manifest.json").read_text())
    bar = manifest.get("calm")
    if not bar:
        sys.exit("ERROR: the pack's manifest has no calm-day bar (scripts/v4_pack.py writes it as `calm`)")
    v4 = load_tasks(args.pack)
    v4_refs = v4_pack.refs(args.pack, v4, args.workers, limits, clock)
    v2 = v2_tasks(args.v2, v4, manifest, args.workers)
    clock.lap("v2 tasks", f"{len(v2)} in {args.v2}")
    v2_refs = v4_pack.refs(args.v2, v2, args.workers, limits, clock)
    tests = {} if args.no_tests else {"agreement": pytest(AGREEMENT), "playable": pytest(PLAYABLE)}
    clock.lap("tests", json.dumps({k: v["pass"] for k, v in tests.items()}))
    crewed = bool(manifest.get("bases"))
    plain = read_refs(args.nocrew, [t.task_id for t in v4]) if crewed and args.nocrew else None
    out = {"criteria": criteria(v4_refs, v2_refs, tests, crewed, plain, bar["rate"]),
           "gates": gates(v4, v4_refs, manifest),
           "parameters": {"pack": str(args.pack), "v2": str(args.v2),
                          "nocrew": str(args.nocrew) if args.nocrew else None, "calm": bar,
                          "crewed": crewed, "time_limits_s": limits,
                          "scale": manifest["scale"], "code": v4_pack.revision(), "pack_code": manifest.get("code")},
           "tasks": [row(t, v4_refs.get(t.task_id, {}), v2_refs.get(t.task_id, {})) for t in v4],
           "wall_s": clock.laps}
    out["done"] = all(c["pass"] for c in out["criteria"].values())
    (args.pack / "check.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    (args.pack / "check.md").write_text(markdown(out))
    print(json.dumps({k: {"pass": c["pass"], "value": c.get("value")} for k, c in out["criteria"].items()}
                     | {"gates": {k: g["pass"] for k, g in out["gates"].items()}, "done": out["done"]}, indent=1),
          flush=True)


def v2_tasks(folder: Path, v4: Iterable[Task], manifest: Mapping, workers: int) -> list[Task]:
    """v2's task of each v4 task's date, in the v4 pack's order: read from folder/tasks.jsonl, else built without crew
    or layers at scale 1 and the cutoff (a control at the pack's control windows), rescaled to the pack's scale with
    the v4 task's split, and folder/tasks.jsonl written with every one."""
    v4 = list(v4)
    path = folder / "tasks.jsonl"
    have = {t.task_id: t for t in load_tasks(path)} if path.exists() else {}
    todo = [t for t in v4 if t.task_id not in have]
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
    out = [have[t.task_id] for t in v4 if t.task_id in have]
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


def read_refs(folder: Path, ids: Iterable[str]) -> dict[str, dict]:
    """The references a pack holds under folder/refs/ for each of `ids` it has."""
    out = {}
    for i in ids:
        try:
            out[i] = json.loads((folder / "refs" / f"{i}.json").read_text())
        except (OSError, ValueError):
            continue
    return out


def criteria(v4: Mapping[str, dict], v2: Mapping[str, dict], tests: Mapping[str, dict], crewed: bool = False,
             plain: Mapping[str, dict] | None = None, rate: float | None = None) -> dict:
    """§0's criteria from the two packs' references by task id (refs/<id>.json each) and the tests' outcomes; on a
    `crewed` pack the crew's guards too, the calm day against the no-crew pack's references `plain` at the
    fair-weather bar `rate`."""
    storm = {i: (r, v2[i]) for i, r in v4.items() if r.get("split") != "control" and i in v2}
    out = {
        "harder_do_nothing": ratio_of(storm, lambda r: excess(r, "naive"), DO_NOTHING,
                                      "do-nothing's median excess over the anchor, v4 >= 1.4 x v2"),
        "harder_follower": ratio_of(storm, lambda r: excess(r, "follower"), FOLLOWER,
                                    "the follower's median excess over the anchor, v4 >= 1.5 x v2"),
        "realistic_optimum_cancels": pooled_of(storm, anchor_cancels, lambda r: r["jetblue"]["cancelled"],
                                               lambda a, b: a >= OPTIMUM_SHARE,
                                               "the anchors' cancellations over JetBlue's, v4 >= 35%"),
        "realistic_free_share": pooled_of(storm, lambda r: r["probe"]["feasible"], lambda r: r["probe"]["cancelled"],
                                          lambda a, b: b is not None and a <= b - FREE_DROP,
                                          "JetBlue's cancellations that could fly for free (a pair once), v4 at least "
                                          "10 points below v2"),
        "realistic_recorded_excess": median_of(storm, lambda r: excess(r, "recorded"), lambda a, b: a < b,
                                               "the recorded day's median excess over the anchor, v4 < v2"),
        "realistic_replay_flown": pooled_of(storm, lambda r: r["fidelity"]["flown"],
                                            lambda r: r["fidelity"]["jetblue_flown"], lambda a, b: a >= FLOWN_SHARE,
                                            "the replay flies >= 97% of the flights JetBlue flew"),
        "realistic_replay_within_15": pooled_of(storm, lambda r: r["fidelity"]["within"],
                                                lambda r: r["fidelity"]["flown"], lambda a, b: b is not None and a >= b,
                                                "of the replay's flown flights, the share leaving within 15 min of "
                                                "JetBlue's minute, v4 >= v2"),
        "sound_anchors_proven": proven(v4),
        "sound_anchors_exact": exact(v4),
        "sound_agreement_tests": tested(tests, "agreement", "the suite's sim/MIP agreement tests pass"),
        "playable": tested(tests, "playable", "an LLM (fake LiteLLM) plays a v4 day end to end through the server"),
    }
    if crewed:
        out |= guards(storm, plain, rate)
    return out


def guards(storm: Mapping[str, tuple[dict, dict]], plain: Mapping[str, dict] | None, rate: float | None) -> dict:
    """docs/v4/CREW.md §0's guards on a crew pack's storm dates (each its references and v2's), the calm day at the
    fair-weather bar `rate`."""
    rec = [x >= 0 for r, _ in storm.values() if (x := safe(lambda r: excess(r, "recorded"), r)) is not None]
    rec_share = round(sum(rec) / len(rec), 4) if rec else None
    calm = {}
    for i, (r, _) in storm.items():
        b = (plain or {}).get(i, {})
        if rate is not None and "cancelled" in r.get("calm", {}) and "cancelled" in b.get("calm", {}):
            calm[i] = within(r, rate) or not within(b, rate)
    worse = sorted(i for i, ok in calm.items() if not ok)
    return {
        "guard_recorded": {"rule": "the recorded day costs >= the anchor on >= 95% of dates", "n": len(rec),
                           "value": rec_share, "threshold": RECORDED_SHARE,
                           "pass": rec_share is not None and rec_share >= RECORDED_SHARE},
        "guard_anchor_cancels": pooled_of(storm, anchor_cancels, lambda r: r["jetblue"]["cancelled"],
                                          lambda a, b: a <= 1, "the anchors' cancellations over JetBlue's, <= 100%"),
        "guard_calm_vs_nocrew": {"rule": "on every date whose no-crew calm day (--nocrew) is within JetBlue's "
                                         "fair-weather bar, the crew's is too", "n": len(calm),
                                 "value": len(calm) - len(worse), "worse": worse,
                                 "pass": plain is not None and bool(calm) and not worse},
        "guard_within_15": pooled_of(storm, lambda r: r["fidelity"]["within"], lambda r: r["fidelity"]["flown"],
                                     lambda a, b: b is not None and a >= b - WITHIN_SLACK,
                                     "of the replay's flown flights, the share leaving within 15 min of JetBlue's "
                                     "minute, within 1 point of v2's"),
    }


def within(r: dict, rate: float) -> bool:
    """Whether the references `r`'s calm day is within JetBlue's fair-weather bar at `rate` (layers.fairday)."""
    return fairday.within(r["calm"]["cancelled"], r["calm"]["ferries"], r["flights"], rate)


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
    for r4, r2 in pairs.values():
        x, y = safe(f, r4), safe(f, r2)
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
    m4, m2 = med(a), med(b)
    ratio = round(m4 / m2, 3) if m4 is not None and m2 else None
    return {"rule": rule, "n": n, "v4": m4, "v2": m2, "value": ratio, "threshold": times,
            "pass": ratio is not None and m2 > 0 and ratio >= times}


def median_of(pairs: Mapping[str, tuple[dict, dict]], f: Callable, ok: Callable[[float, float], bool],
              rule: str) -> dict:
    a, b, n = values(pairs, f)
    m4, m2 = med(a), med(b)
    return {"rule": rule, "n": n, "v4": m4, "v2": m2, "value": m4,
            "pass": m4 is not None and m2 is not None and ok(m4, m2)}


def pooled_of(pairs: Mapping[str, tuple[dict, dict]], num: Callable, den: Callable,
              ok: Callable[[float, float | None], bool], rule: str) -> dict:
    """sum(num) / sum(den) on each pack over the tasks where both are defined on both."""
    rows = [(safe(num, r4), safe(den, r4), safe(num, r2), safe(den, r2)) for r4, r2 in pairs.values()]
    rows = [x for x in rows if None not in x]
    n4, d4, n2, d2 = (sum(x[k] for x in rows) for k in range(4))
    s4 = round(n4 / d4, 4) if d4 else None
    s2 = round(n2 / d2, 4) if d2 else None
    return {"rule": rule, "n": len(rows), "v4": s4, "v2": s2, "value": s4, "v4_counts": [n4, d4],
            "v2_counts": [n2, d2], "pass": s4 is not None and ok(s4, s2)}


def proven(v4: Mapping[str, dict]) -> dict:
    """Every anchor of the pack proven, or flagged where it isn't; it fails only on an anchor that failed or is
    missing."""
    failed = sorted(i for i, r in v4.items() if "proven" not in r.get("anchor", {}))
    flagged = {i: {k: r["anchor"].get(k) for k in ("status", "gap_pct")} for i, r in sorted(v4.items())
               if i not in failed and not r["anchor"]["proven"]}
    return {"rule": "every anchor proven to its gap, or flagged", "n": len(v4), "value": len(v4) - len(failed) -
            len(flagged), "flagged": flagged, "failed": failed,
            "max_gap_pct": max((r["anchor"].get("gap_pct") or 0 for i, r in v4.items() if i not in failed),
                               default=None), "pass": bool(v4) and not failed}


def exact(v4: Mapping[str, dict]) -> dict:
    ran = {i: r["anchor"] for i, r in v4.items() if "exact" in r.get("anchor", {})}
    off = sorted(i for i, a in ran.items() if not a["exact"])
    return {"rule": "every anchor's plan replays exactly in the sim", "n": len(ran), "value": len(ran) - len(off),
            "not_exact": off, "pass": bool(ran) and not off and len(ran) == len(v4)}


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
    bar = manifest["calm"]
    calm = [within(r, bar["rate"]) for r in pool.values() if "cancelled" in r.get("calm", {})]
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
        "calm_day": world_check.gate(world_check.share(calm), len(calm), f"forced cancellations <= {bar['rate']:.2%} "
                                     f"of the day's flights (JetBlue's {bar['quantile']:.0%} fair-day share, at least "
                                     f"1) and <= {fairday.FERRIES} ferries, per kept storm date"),
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


def row(t: Task, r4: dict, r2: dict) -> dict:
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
    return {"task_id": t.task_id, "split": t.split, "hubs": "+".join(t.storms), "v4": nums(r4), "v2": nums(r2)}


def med(v: list[float]) -> float | None:
    return round(statistics.median(v), 4) if v else None


def rnd(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def markdown(out: dict) -> str:
    lines = [f"# v4 check ({datetime.now(UTC).date().isoformat()})", "",
             f"Done (every §0 criterion passes): **{out['done']}**", "", "## §0 criteria", "",
             "| Criterion | Rule | n | v4 | v2 | Value | Pass |", "|---|---|---|---|---|---|---|"]
    for k, c in out["criteria"].items():
        lines.append(f"| {k} | {c['rule']} | {c.get('n', '')} | {c.get('v4', '')} | {c.get('v2', '')} | "
                     f"{c.get('value')} | {c['pass']} |")
    flagged = out["criteria"]["sound_anchors_proven"]
    lines += ["", f"Anchors flagged (not proven): {json.dumps(flagged['flagged'])}; failed: {flagged['failed']}", "",
              "## world_check's gates on the v4 pack", "", "| Gate | Value | Threshold | Pass |", "|---|---|---|---|"]
    for k, g in out["gates"].items():
        value = {x: v for x, v in g.items() if x not in ("threshold", "pass", "rule", "controls", "left_out_tasks")}
        lines.append(f"| {k} | {json.dumps(value, default=str)} | {g.get('threshold', '')} | {g['pass']} |")
    lines += ["", "## Controls", "", f"```\n{json.dumps(out['gates']['controls']['controls'], indent=1)}\n```", "",
              "## Per task (v4 / v2)", "",
              "| task | split | hubs | naive x | follower x | recorded x | anchor cancels / JetBlue | free | "
              "flown / JetBlue | within 15 | proven | exact |", "|" + "---|" * 12]
    for r in out["tasks"]:
        a, b = r["v4"], r["v2"]
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
