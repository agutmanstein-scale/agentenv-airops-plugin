"""Costs in passenger-minutes, cancellations priced by notice.

A cancelled passenger costs v1's K_CANCEL (523) plus one passenger-minute for every minute the cancellation was decided
later than NOTICE minutes before the flight's scheduled departure: a cancellation decided two hours or more ahead costs
the flat 523 per passenger, one decided at the scheduled departure 643. Every confirmed cancellation is final and
counts as decided at its confirm; an auto-cancel counts as decided at its deadline, 523 + (deadline - sched_dep + 120)
per passenger (1,543 at a 15-h deadline). Delay is v1's (pax x minutes late at arrival), and so are the end-of-day
ferries, 92 x (30 + miles / 8). The flat cost, every cancellation at 523 whenever decided, is v1's and is kept for
the leverage and control clauses, which the notice term would otherwise decide.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..core.cost import K_CANCEL, ferry_cost
from .model import Flight, Task

NOTICE = 120


@dataclass(frozen=True)
class Cost:
    delay: int
    cancel: int
    notice: int
    ferry: int

    @property
    def total(self) -> int:
        return self.delay + self.cancel + self.notice + self.ferry

    @property
    def flat(self) -> int:
        return self.delay + self.cancel + self.ferry

    def to_dict(self) -> dict:
        return {"delay": self.delay, "cancel": self.cancel, "notice": self.notice, "ferry": self.ferry,
                "total": self.total, "flat": self.flat}


def late(f: Flight, decided: int) -> int:
    """The minutes a cancellation decided at `decided` came after the notice line."""
    return max(0, decided - (f.sched_dep - NOTICE))


def cancel_price(f: Flight, decided: int) -> int:
    return f.pax * (K_CANCEL + late(f, decided))


def flat_price(f: Flight) -> int:
    return f.pax * K_CANCEL


def priced(task: Task, arr: Mapping[int, int | None], decided: Mapping[int, int],
           ferries: Iterable[tuple[str, str]]) -> Cost:
    """The cost of a day: `arr` maps each flight to its arrival, None when cancelled, `decided` each cancelled flight
    to the minute its cancellation was decided, `ferries` are the end-of-day (from, to) moves."""
    delay = cancel = notice = 0
    for i, a in arr.items():
        f = task.flight(i)
        if a is None:
            cancel += K_CANCEL * f.pax
            notice += f.pax * late(f, decided[i])
        else:
            delay += f.pax * max(0, a - f.sched_arr)
    return Cost(delay, cancel, notice, sum(ferry_cost(task, a, b) for a, b in ferries))
