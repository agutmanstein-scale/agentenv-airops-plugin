"""JetBlue's departure cap per hour at JFK and BOS, from what the airport actually pushed back (docs/v3/DESIGN.md
§3.1).

v2 caps nothing leaving a hub. An hour is constrained when every carrier together pushed back (BTS gate departure)
less than `short` of what it had scheduled to leave the hub in it, with at least `min_sched` scheduled. JetBlue's cap
there is its in-scope share of the hour's schedule (the task's flights scheduled to leave the hub in it) times the
hour's actual pushbacks, rounded to the nearest flight (so a cap of 0 means JetBlue's expected share was under
half a flight), and never below what those flights actually pushed back in it, so the recorded
day stays feasible. Other hours, and hours JetBlue scheduled nothing in, stay uncapped. Hours are counted from the
task's midnight, over the task's span: from its first watch to the hour after its last deadline.
"""

import math
from collections import Counter

import pandas as pd

from ..advisories import HUBS
from ..model import Task

SHORT = 0.9
MIN_SCHED = 5


def build(task: Task, frame: pd.DataFrame, short: float = SHORT,
          min_sched: int = MIN_SCHED) -> dict[str, dict[int, int]]:
    """The task's departure caps per hub and hour. `frame` holds every carrier's BTS flights of the task's dates
    (candidates.frame: `Origin`, and `sched_dep` and `act_dep` in UTC epoch minutes, `act_dep` NaN when none)."""
    lo, hi = task.start // 60, max(f.deadline for f in task.flights) // 60 + 1
    out: dict[str, dict[int, int]] = {}
    for hub in HUBS:
        x = frame[frame.Origin == hub]
        sched = Counter(((x.sched_dep - task.base) // 60).astype(int))
        pushed = Counter(((x.act_dep.dropna() - task.base) // 60).astype(int))
        mine = Counter(f.sched_dep // 60 for f in task.flights if f.orig == hub)
        flew = Counter(f.actual.dep // 60 for f in task.flights if f.orig == hub and f.actual.dep is not None)
        caps = {h: max(flew[h], math.floor(mine[h] / sched[h] * pushed[h] + 0.5)) for h in range(lo, hi + 1)
                if mine[h] and sched[h] >= min_sched and pushed[h] < short * sched[h]}
        if caps:
            out[hub] = caps
    return out
