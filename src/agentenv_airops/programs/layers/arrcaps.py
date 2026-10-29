"""JetBlue's arrival cap per hour at its outstations, from what actually arrived there (docs/v4/DESIGN.md §3.3).

v2 models the FAA's programs at JFK and BOS only, so a leg into an outstation under its own GDP, ground stop or weather
flies free. This is §3.1's rule on arrivals at every non-hub destination of the task's flights: an hour is constrained
when every carrier together arrived there (BTS gate arrival) less than `short` of what it had scheduled to arrive in it,
with at least `min_sched` scheduled. JetBlue's cap there is its in-scope share of the hour's schedule (the task's
flights scheduled to arrive at the station in it) times the hour's actual arrivals, rounded to the nearest flight, and
never below the task's flights into the station the airline replay lands in it. The replay lands a flight that pushed
back at its pushback plus its truth block, whatever its gate-in: a diversion flies to its scheduled destination, and a
gate-in held long after landing counts in the hour it landed. The floor keeps the recorded day's arrivals within the
caps; a replayed flight that a GDP slot or a late aircraft moves into a capped hour can still be held there, so the
recorded day is close to feasible, not feasible by construction (as with closures, §3.2). Other hours, and hours JetBlue
scheduled nothing in, stay uncapped. Hours are counted from the task's midnight, over the span of the hubs' nominal caps
(rbs.nominal): from the first watch's hour through the hour of the latest arrival a deadline allows. The caps join the
world's nominal arrival caps (sim.truth), so the sim and the MIP enforce them as they do a hub's.
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
    """The task's arrival caps per outstation and hour. `frame` holds every carrier's BTS flights of the task's dates
    (candidates.frame: `Dest`, and `sched_arr` and `act_arr` in UTC epoch minutes, `act_arr` NaN when none)."""
    lo, hi = task.start // 60, max(f.deadline + f.block for f in task.flights) // 60
    out: dict[str, dict[int, int]] = {}
    for station in sorted({f.dest for f in task.flights} - set(HUBS)):
        x = frame[frame.Dest == station]
        sched = Counter(((x.sched_arr - task.base) // 60).astype(int))
        arrived = Counter(((x.act_arr.dropna() - task.base) // 60).astype(int))
        mine = Counter(f.sched_arr // 60 for f in task.flights if f.dest == station)
        landed = Counter((f.actual.dep + f.block) // 60 for f in task.flights
                         if f.dest == station and f.actual.dep is not None)
        caps = {h: max(landed[h], math.floor(mine[h] / sched[h] * arrived[h] + 0.5)) for h in range(lo, hi + 1)
                if mine[h] and sched[h] >= min_sched and arrived[h] < short * sched[h]}
        if caps:
            out[station] = caps
    return out
