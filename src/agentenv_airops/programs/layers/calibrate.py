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
"""

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from dataclasses import replace

import pandas as pd

from .. import references
from ..model import Layers, Task
from . import closures
from .config import Config, build

AUTO_SHARE, AUTO_MAX = 0.01, 2


def fit(dates: Iterable[str], tasks: Iterable[Task], frame_of: Callable[[str], pd.DataFrame],
        spans: Mapping[str, Sequence[closures.Interval]], pushed: pd.DataFrame, workers: int = 1) -> Config:
    """The constants fitted on the train `dates`: `tasks` their built tasks (crew on, no layers; others are ignored),
    `frame_of` a date's every-carrier BTS flights (candidates.frame), `spans` each hub's closures, `pushed` every
    carrier's BTS flights once each (`Origin`, `act_dep`) over the months the dates fall in."""
    dates = tuple(sorted(set(dates)))
    rated = Config(rate=closures.fit_rate(dates, pushed, spans), dates=dates)
    train = [replace(t, layers=build(t, rated, frame_of(t.date), spans)) for t in tasks if t.date in dates]
    return replace(rated, reserves=pools(train, workers))


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
