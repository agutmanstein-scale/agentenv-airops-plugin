"""Which of JetBlue's cancellations could have flown on the day as it happened (the v2 probe, ~/handoff/out/v2_probe.py,
and docs/v4/DESIGN.md §0's could-fly-for-free share).

Each JetBlue cancellation is put back on the airline replay (references.airline_plan, played on the truth by
sim.execute): its plan entry becomes {}, so it flies as early as it can and takes whatever slot the sim gives it, and
the day is flown again. A restore that flies every restored leg is `clean` when no flight the replay flew is knocked out
and none moves more than SHIFT_OK minutes and the end-of-day ferries cost no more, `strands` when only the ferries cost
more, `displaces` otherwise; one that leaves a restored leg unflown is `forced`. A cancellation that is forced or
strands alone is tried again with its out-and-back partner (`partner`: the cancelled leg back the other way, same
family, within PAIR_WINDOW of it), and takes the pair's verdict as `pair_<verdict>` unless the pair is forced too.

A cancellation could have flown for free when its verdict is clean or pair_clean. A pair is one restore however many of
its legs reach it: `Probe.restores` keys each restore by the flights it put back, so the share over JetBlue's
cancellations counts a restored pair once (`Probe.feasible`), where the v2 probe's rows count it once per leg
(`Probe.feasible_rows`).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..core.sim import FLOWN
from . import references, sim
from .model import Flight, Task

SHIFT_OK = 15
PAIR_WINDOW = 360
VERDICTS = ("clean", "strands", "displaces", "forced", "pair_clean", "pair_strands", "pair_displaces")
FEASIBLE = ("clean", "pair_clean")


@dataclass(frozen=True)
class Restore:
    """What putting `restored` back on the replay did: whether each restored leg flew, the replay's flights it knocked
    out, the most another flown flight moved, and the change in the day's cost and its ferries' cost."""

    restored: tuple[int, ...]
    flew: bool
    knocked: tuple[int, ...]
    max_shift: int
    d_cost: int
    d_ferry: int

    @property
    def verdict(self) -> str:
        if not self.flew:
            return "forced"
        if self.knocked or self.max_shift > SHIFT_OK:
            return "displaces"
        return "clean" if self.d_ferry <= 0 else "strands"


@dataclass(frozen=True)
class Probe:
    """Each JetBlue cancellation's verdict (`rows`, flight id -> verdict) and the restore it took (`restores`, the
    flights put back -> verdict, a pair once)."""

    rows: dict[int, str]
    restores: dict[tuple[int, ...], str]

    @property
    def cancelled(self) -> int:
        return len(self.rows)

    @property
    def feasible(self) -> int:
        """The restores that fly for free, a pair once."""
        return sum(v in FEASIBLE for v in self.restores.values())

    @property
    def feasible_rows(self) -> int:
        """The cancellations whose verdict flies for free, as the v2 probe counted them (a pair once per leg)."""
        return sum(v in FEASIBLE for v in self.rows.values())

    def to_dict(self) -> dict:
        return {"cancelled": self.cancelled, "feasible": self.feasible, "feasible_rows": self.feasible_rows,
                "restores": len(self.restores), "pairs": sum(len(k) > 1 for k in self.restores),
                "verdicts": counted(self.rows.values()), "restore_verdicts": counted(self.restores.values())}


def probe(task: Task) -> Probe:
    """Every JetBlue cancellation of `task` restored on the airline replay, alone and, when that is forced or strands,
    with its partner."""
    plan = references.airline_plan(task)
    base = sim.execute(task, plan)
    cancelled = [f for f in task.flights if f.actual.dep is None]
    rows, restores = {}, {}
    for f in cancelled:
        one = restore(task, plan, base, [f.id])
        use = one
        g = partner(f, cancelled) if one.verdict in ("forced", "strands") else None
        if g is not None:
            two = restore(task, plan, base, [f.id, g.id])
            if two.verdict != "forced":
                use = two
        v = use.verdict if use is one else f"pair_{use.verdict}"
        rows[f.id] = v
        restores.setdefault(tuple(sorted(use.restored)), v)
    return Probe(rows, restores)


def restore(task: Task, plan: Mapping[int, Mapping], base: sim.Execution, ids: Sequence[int]) -> Restore:
    """The replay `plan` (run as `base`) with the flights `ids` put back ({}) and the day flown again."""
    run = sim.execute(task, {**plan, **{i: {} for i in ids}})
    others = [i for i in run.flights if i not in ids]
    knocked = tuple(i for i in others if base.flights[i].status == FLOWN and run.flights[i].status != FLOWN)
    shift = max((run.flights[i].dep - base.flights[i].dep for i in others
                 if base.flights[i].status == FLOWN and run.flights[i].status == FLOWN), default=0)
    return Restore(tuple(ids), all(run.flights[i].status == FLOWN for i in ids), knocked, shift,
                   run.cost.total - base.cost.total, run.cost.ferry - base.cost.ferry)


def partner(f: Flight, cancelled: Sequence[Flight]) -> Flight | None:
    """v1's rule: the cancelled leg back the other way, same family, within PAIR_WINDOW of this one's arrival or
    departure; the return after it first, else the leg before it."""
    back = [g for g in cancelled if g.orig == f.dest and g.dest == f.orig and g.family == f.family]
    after = [g for g in back if f.sched_arr <= g.sched_dep <= f.sched_arr + PAIR_WINDOW]
    before = [g for g in back if g.sched_arr <= f.sched_dep <= g.sched_arr + PAIR_WINDOW]
    pool = sorted(after, key=lambda g: g.sched_dep) + sorted(before, key=lambda g: -g.sched_arr)
    return pool[0] if pool else None


def counted(verdicts) -> dict[str, int]:
    c = Counter(verdicts)
    return {v: c[v] for v in VERDICTS if c[v]}
