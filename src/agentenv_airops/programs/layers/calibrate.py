"""v3's layer constants, fitted on a pack's train dates only (docs/v3/DESIGN.md §1, §6.1) and written to its manifest.

- The ramp closures' rate per hub is closures.fit_rate over the train dates: every carrier's pushbacks inside the
  closures that start on them over those in the same minutes just before (DECISIONS 2026-10-17, U3 Q2: refitted on
  the pack's own split, not U3's scratch rates).
- The reserve pool per crew base (§3.4), as no crew data is public, is the smallest that leaves JetBlue's airline
  replay as good as with unlimited reserves on every train date: no more of the flights it flew by their deadline
  (references.counted) auto-cancelled, so the airline-replay gate (world_check's G1) counts every date alike. On each
  date, with the other layers on, the replay calls a reserve for each base departure its unlimited-reserve replay flies
  past its crew deadline (references.airline_plan); per base, the smallest pool in 0 up to that date's calls there at
  which the replay, that base pooled and the others unlimited, keeps its auto-cancels, and the base's pool is the
  largest of those over the dates. The pools together are then replayed on every date; a date that still loses a
  flight (one base's denial moving another's aircraft) lifts every base to its calls there, at which the replay is the
  unlimited one, until no date does.
- The departure and outstation caps' clauses stay at depcaps' and arrcaps' SHORT and MIN_SCHED (0.9 and 5).
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
    """The smallest reserve pool per crew base of `tasks` (layered, crew on) that keeps every task's airline-replay
    auto-cancels at their count with unlimited reserves."""
    with ProcessPoolExecutor(workers) if workers > 1 and len(tasks) > 1 else nullcontext() as pool:
        found = list(pool.map(least, tasks) if pool else map(least, tasks))
    out = {b: max((k.get(b, 0) for k, _ in found), default=0) for b in sorted({b for t in tasks for b in t.bases})}
    while True:
        short = [calls for t, (_, calls) in zip(tasks, found, strict=True) if auto(t, out) > auto(t, {})]
        if not short:
            return out
        for calls in short:
            out |= {b: max(out[b], n) for b, n in calls.items()}


def least(task: Task) -> tuple[dict[str, int], dict[str, int]]:
    """Per crew base of the task, the smallest pool (the others unlimited) at which its airline replay auto-cancels no
    more than with unlimited reserves, and the replay's reserve calls there."""
    plan = references.airline_plan(pooled(task, dict.fromkeys(task.bases, len(task.flights))))
    calls = Counter(task.flight(i).orig for i, e in plan.items() if e.get("reserve"))
    free = auto(task, {})
    return {b: next(k for k in range(calls[b] + 1) if k == calls[b] or auto(task, {b: k}) <= free)
            for b in task.bases}, {b: calls[b] for b in task.bases}


def auto(task: Task, pool: Mapping[str, int]) -> int:
    """The airline replay's auto-cancels with `pool` at the task's crew bases it names, the others unlimited."""
    return references.airline(pooled(task, {b: n for b, n in pool.items() if b in task.bases})).auto


def pooled(task: Task, pool: Mapping[str, int]) -> Task:
    return replace(task, layers=replace(task.layers or Layers(), reserves=dict(pool)))
