"""What a flow manager knows of the hub's arrivals at a minute of the Command Center model's day: which flights are off
the ground, when each will reach the queue, and how many hold in the air now.

A flight that has left is known by its wheels-off. One that has not is known by its d0 once its scheduled departure is
within LOOK of the minute (step 0's rule for revealing delays, the airline's updated departure time), before that by
its schedule (scheduled departure plus taxi-out), and never before the minute itself; it reaches the queue tau later.
The unseen stream is known at its own time. The same rule serves the FAA's real programs, reactive and TAF-based.
"""

from collections.abc import Mapping

from ..core.known import LOOK
from . import queue
from .demand import Arrival
from .world import World


def eta(a: Arrival, t: int, gone: Mapping[int, int]) -> int:
    """When arrival a is expected at the queue as known at minute t (`gone`: the wheels-off of flights that left)."""
    if a.unseen:
        return a.eta0
    if a.id in gone:
        return gone[a.id] + a.tau
    d = a.d0 if a.sched_dep <= t + LOOK else a.sched_dep + a.taxi_out
    return max(d, t) + a.tau


def airborne(world: World, gone: Mapping[int, int], t: int) -> int:
    """The flights at the queue before minute t that land at or after it: the airborne queue now."""
    etas = [(a.eta0, a.key, a.id) for a in world.arrivals if a.unseen and a.eta0 < t]
    etas += [(e, world.arrivals[i].key, i) for i, w in gone.items() if (e := w + world.arrivals[i].tau) < t]
    landed = queue.land(etas, world.slots)
    return sum(1 for m in landed.values() if m is not None and m >= t)
