"""The playable day's grade (docs/v4/DESIGN.md §5): the desk's final plan run on the truth by sim.execute, against
doing nothing (`references.naive`) and the hindsight optimum (`references.anchor`), with the rule (`references.rule`)
and, when its cost is given, the follower as reference scores: each one's cost on the truth and its reward on the
same curve. The rule is `references.rule` as v2 plays it, its day so far recomputed under its later cancellations
(the Day refuses such a change), so on a rare day it is not quite the desk's rule.

The reward is v2's (scripts/step0.py `reward`), so a v4 score sits on step 0's curve: 0.2 + 0.8 exp(-g / 0.5), g the
plan's excess over the anchor in units of doing nothing's excess (at least FLOOR_SHARE of the anchor): 1 at the
anchor, about 0.31 at doing nothing, never below 0.2 (v1's floor: the env never strands a flight)."""

import math
from collections.abc import Mapping

from ..programs import references, sim
from ..programs.model import Task

TEMPERATURE, FLOOR_SHARE = 0.5, 0.1


def reward(cost: float, naive: float, anchor: float) -> float:
    """On a day that costs nothing at the anchor and doing nothing, any plan at the anchor's cost scores 1 and any
    other 0.2."""
    scale = max(naive - anchor, FLOOR_SHARE * anchor)
    if scale <= 0:
        return 1.0 if cost <= anchor else 0.2
    g = max(0.0, cost - anchor) / scale
    return 0.2 + 0.8 * math.exp(-g / TEMPERATURE)


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
