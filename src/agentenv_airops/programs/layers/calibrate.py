"""v3's layer constants, fitted on a pack's train dates only (docs/v3/DESIGN.md §1, §6.1) and written to its manifest.

- The ramp closures' rate per hub is closures.fit_rate over the train dates: every carrier's pushbacks inside the
  closures that start on them over those in the same minutes just before (DECISIONS 2026-10-17, U3 Q2: refitted on
  the pack's own split, not U3's scratch rates).
- The reserve pool per crew base (§3.4), as no crew data is public, is the smallest that keeps JetBlue's airline
  replay within the airline-replay gate (world_check's and step 0's G1) on every train date: its auto-cancels among
  the flights it flew by their deadline (references.counted) at most AUTO_SHARE of them and at most AUTO_MAX
  (DECISIONS 2026-10-17, U6 Q2). A date whose replay is out of G1 even with unlimited reserves (the other layers'
  doing) holds the pool to no more auto-cancels than that. On each date, with the other layers on, the replay calls a
  reserve for each base departure its unlimited-reserve replay flies past its crew deadline
  (references.airline_plan); per base, the smallest pool in 0 up to that date's calls there at which the replay, that
  base pooled and the others unlimited, is kept so, and the base's pool is the largest of those over the dates. The
  pools together are then replayed on every date; a date they don't keep (the bases' auto-cancels adding up past G1,
  or one base's denial moving another's aircraft) lifts every base to its calls there, at which the replay is the
  unlimited one, until every date is kept.
- The departure and outstation caps' clauses stay at depcaps' and arrcaps' SHORT and MIN_SCHED (0.9 and 5), not
  fitted: the caps are floored at JetBlue's recorded pushbacks and arrivals, so any value fits the recorded day
  (DECISIONS 2026-10-17, U6 Q1).
- v4's crew cut (layers.crew, docs/v4/DESIGN.md §1), when a pack fits it (`crew_fit`), comes before the pools: on a
  seeded `sample` of at most SAMPLE train dates (SAMPLE_SEED), each date built at every Rule of the grid (BUFFERS x
  SEGMENTS) with the other layers on at the fitted rates and unlimited reserves, and scored (`score`) by its naive day,
  its recorded day, its anchor and its calm day at CREW_S. The cut is the one that maximises do-nothing's median excess
  over the anchor on the sample while docs/v4/DESIGN.md §0's guards hold there (`guards`): the calm day forces at most
  CALM_CANCELS cancellation and CALM_FERRIES ferries on at least CALM_SHARE of the dates, the recorded day costs no less
  than the anchor on at least RECORDED_SHARE of them, and the anchors cancel (auto-cancels included) no more than
  JetBlue did, pooled; ties go to the larger buffer, then the fewer segments (the laxer crew). A date counts where its
  anchor and calm day proved. None holds: no cut, and the pack stops. The pools are then fitted at that cut.
"""

import random
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from itertools import product

import pandas as pd

from .. import references
from ..model import Layers, Task
from . import closures
from .config import Config, build
from .crew import Rule

AUTO_SHARE, AUTO_MAX = 0.01, 2
BUFFERS, SEGMENTS = (0, 30, 60, 90, 120), (3, 4, 5, 6)
SAMPLE, SAMPLE_SEED, CREW_S = 12, 0, 600.0
CALM_CANCELS, CALM_FERRIES, CALM_SHARE, RECORDED_SHARE = 1, 2, 0.9, 0.95


def fit(dates: Iterable[str], tasks: Iterable[Task], frame_of: Callable[[str], pd.DataFrame],
        spans: Mapping[str, Sequence[closures.Interval]], pushed: pd.DataFrame, workers: int = 1) -> Config:
    """The constants fitted on the train `dates`: `tasks` their built tasks (crew on, no layers; others are ignored),
    `frame_of` a date's every-carrier BTS flights (candidates.frame), `spans` each hub's closures, `pushed` every
    carrier's BTS flights once each (`Origin`, `act_dep`) over the months the dates fall in."""
    return pooled_fit(rates(dates, pushed, spans), tasks, frame_of, spans, workers)


def rates(dates: Iterable[str], pushed: pd.DataFrame, spans: Mapping[str, Sequence[closures.Interval]]) -> Config:
    """The closure rates fitted on the train `dates`, no pools."""
    dates = tuple(sorted(set(dates)))
    return Config(rate=closures.fit_rate(dates, pushed, spans), dates=dates)


def pooled_fit(rated: Config, tasks: Iterable[Task], frame_of: Callable[[str], pd.DataFrame],
               spans: Mapping[str, Sequence[closures.Interval]], workers: int = 1) -> Config:
    """`rated` with the reserve pools fitted on its train dates' `tasks`, layered at its rates."""
    train = [replace(t, layers=build(t, rated, frame_of(t.date), spans)) for t in tasks if t.date in rated.dates]
    return replace(rated, reserves=pools(train, workers))


def sample(dates: Iterable[str], n: int = SAMPLE, seed: int = SAMPLE_SEED) -> tuple[str, ...]:
    """At most n of the train `dates`, drawn with `seed`, in date order."""
    dates = sorted(set(dates))
    return tuple(sorted(random.Random(seed).sample(dates, min(n, len(dates)))))


def grid(buffers: Sequence[int] = BUFFERS, segments: Sequence[int] = SEGMENTS) -> list[Rule]:
    return [Rule("v4", b, s) for b, s in product(buffers, segments)]


def crew_fit(tasks: Mapping[Rule, Sequence[Task]], workers: int = 1,
             time_limit: float = CREW_S) -> tuple[Rule | None, list[dict]]:
    """The cut of `tasks` (the sample's tasks built at each Rule, layered at the rates, unlimited reserves) that
    maximises do-nothing's median excess over the anchor while the guards hold, or None; and each Rule's row."""
    jobs = [(rule, t) for rule, ts in tasks.items() for t in ts]
    with ProcessPoolExecutor(workers) if workers > 1 and len(jobs) > 1 else nullcontext() as pool:
        scores = list(pool.map(score, [t for _, t in jobs], [time_limit] * len(jobs)) if pool
                      else (score(t, time_limit) for _, t in jobs))
    by: dict[Rule, list[dict]] = {rule: [] for rule in tasks}
    for (rule, _), s in zip(jobs, scores, strict=True):
        by[rule].append(s)
    rows = [summary(rule, s) for rule, s in by.items()]
    held = [(r, rule) for r, rule in zip(rows, by, strict=True) if r["guards"]]
    if not held:
        return None, rows
    return max(held, key=lambda x: (x[0]["do_nothing"], x[1].buffer, -x[1].max_segments))[1], rows


def score(task: Task, time_limit: float = CREW_S) -> dict:
    """What the guards and the objective read of one task: the naive, recorded and anchor costs, the anchor's and
    JetBlue's cancellations, the calm day's forced cancellations and ferries, and whether both MIPs proved."""
    sol, run = references.anchor(task, time_limit)
    calm = references.calm(task, time_limit)
    return {"date": task.date, "naive": references.naive(task).cost.total,
            "recorded": references.recorded(task)[0].total, "anchor": sol.objective,
            "proven": sol.proven and calm.solution.proven,
            "anchor_cancels": run.counts["cancelled"] + run.counts["auto_cancelled"],
            "jetblue_cancels": sum(f.actual.dep is None for f in task.flights), "calm_cancels": calm.cancelled,
            "calm_ferries": calm.ferries}


def summary(rule: Rule, scores: Sequence[dict]) -> dict:
    """A Rule's row: do-nothing's median excess over the anchor and each guard on the dates whose MIPs proved."""
    ok = [s for s in scores if s["proven"]]
    calm = share(s["calm_cancels"] <= CALM_CANCELS and s["calm_ferries"] <= CALM_FERRIES for s in ok)
    recorded = share(s["recorded"] >= s["anchor"] for s in ok)
    cancels = sum(s["anchor_cancels"] for s in ok), sum(s["jetblue_cancels"] for s in ok)
    checks = {"calm": calm is not None and calm >= CALM_SHARE,
              "recorded": recorded is not None and recorded >= RECORDED_SHARE, "cancels": cancels[0] <= cancels[1]}
    return {**rule.to_dict(), "dates": len(scores), "proven": len(ok),
            "do_nothing": round(statistics.median(s["naive"] / s["anchor"] - 1 for s in ok), 4) if ok else None,
            "calm_share": calm, "recorded_share": recorded, "anchor_cancels": cancels[0], "jetblue_cancels": cancels[1],
            "checks": checks, "guards": bool(ok) and all(checks.values()), "scores": list(scores)}


def share(flags: Iterable[bool]) -> float | None:
    flags = list(flags)
    return round(sum(flags) / len(flags), 4) if flags else None


def pools(tasks: Sequence[Task], workers: int = 1) -> dict[str, int]:
    """The smallest reserve pool per crew base of `tasks` (layered, crew on) that keeps every task's airline replay
    within G1, or no worse than with unlimited reserves where that already isn't."""
    with ProcessPoolExecutor(workers) if workers > 1 and len(tasks) > 1 else nullcontext() as pool:
        found = list(pool.map(least, tasks) if pool else map(least, tasks))
    free = [auto(t, {}) for t in tasks]
    out = {b: max((k.get(b, 0) for k, _ in found), default=0) for b in sorted({b for t in tasks for b in t.bases})}
    while True:
        short = [calls for t, (_, calls), n in zip(tasks, found, free, strict=True) if not kept(t, out, n)]
        if not short:
            return out
        for calls in short:
            out |= {b: max(out[b], n) for b, n in calls.items()}


def least(task: Task) -> tuple[dict[str, int], dict[str, int]]:
    """Per crew base of the task, the smallest pool (the others unlimited) that keeps its airline replay (`kept`), and
    the replay's reserve calls there."""
    plan = references.airline_plan(pooled(task, dict.fromkeys(task.bases, len(task.flights))))
    calls = Counter(task.flight(i).orig for i, e in plan.items() if e.get("reserve"))
    free = auto(task, {})
    return {b: next(k for k in range(calls[b] + 1) if k == calls[b] or kept(task, {b: k}, free))
            for b in task.bases}, {b: calls[b] for b in task.bases}


def kept(task: Task, pool: Mapping[str, int], free: int) -> bool:
    """Whether the airline replay with `pool` is within G1 (auto-cancels at most AUTO_SHARE of its counted flights and
    at most AUTO_MAX), or auto-cancels no more than the `free` it does with unlimited reserves."""
    replay = references.airline(pooled(task, {b: n for b, n in pool.items() if b in task.bases}))
    return replay.auto <= free or (replay.auto <= AUTO_SHARE * replay.flew and replay.auto <= AUTO_MAX)


def auto(task: Task, pool: Mapping[str, int]) -> int:
    """The airline replay's auto-cancels with `pool` at the task's crew bases it names, the others unlimited."""
    return references.airline(pooled(task, {b: n for b, n in pool.items() if b in task.bases})).auto


def pooled(task: Task, pool: Mapping[str, int]) -> Task:
    return replace(task, layers=replace(task.layers or Layers(), reserves=dict(pool)))
