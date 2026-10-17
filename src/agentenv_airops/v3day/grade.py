"""The playable day's grade (docs/v3/DESIGN.md §5): the desk's final plan run on the truth by sim.execute, against
doing nothing (`references.naive`) and the hindsight optimum (`references.anchor`), with the rule (`references.rule`)
and, when its cost is given, the follower as reference scores: each one's cost on the truth and its reward on the
same curve. The rule is `references.rule` as v2 plays it, its day so far recomputed under its later cancellations
(the Day refuses such a change), so on a rare day it is not quite the desk's rule.

reward = (naive - cost) / (naive - anchor), never above 1 (a plan can't beat a proven optimum, and an unproven one
earns no credit for it) and never below 0 (doing nothing or worse). §5 says "clipped as v1's core.reward", whose
curve floors at 0.2; this reads the clip as [0, 1], so doing nothing scores 0, not 0.2. A day where the anchor is no
better than doing nothing scores 1 for matching it and 0 otherwise."""

from collections.abc import Mapping

from ..programs import references, sim
from ..programs.model import Task


def reward(cost: float, naive: float, anchor: float) -> float:
    if naive <= anchor:
        return 1.0 if cost <= anchor else 0.0
    return min(1.0, max(0.0, (naive - cost) / (naive - anchor)))


def grade(task: Task, plan: Mapping[int, Mapping], anchor: int | None = None, follower: int | None = None) -> dict:
    """The plan's cost on the truth with the references it is scored against; `anchor` is the anchor's cost when
    known (references.anchor solves it otherwise), `follower` the follower's (a MIP solve per watch, so only when
    given)."""
    run = sim.execute(task, plan)
    naive = references.naive(task).cost.total
    best = references.anchor(task)[1].cost.total if anchor is None else anchor
    refs = {k: None if c is None else {"cost": c, "reward": round(reward(c, naive, best), 6)}
            for k, c in (("rule", references.rule(task).cost), ("follower", follower))}
    return {"cost": run.cost.to_dict(), "counts": run.counts, "naive": naive, "anchor": best, "references": refs,
            "reward": round(reward(run.cost.total, naive, best), 6), "executed": run.rows(),
            "ferries": [x.to_dict() for x in run.ferries]}
