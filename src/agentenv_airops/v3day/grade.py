"""The playable day's grade (docs/v3/DESIGN.md §5): the desk's final plan run on the truth by sim.execute, against
doing nothing (`references.naive`) and the hindsight optimum (`references.anchor`).

reward = (naive - cost) / (naive - anchor), clipped as v1's core.reward clips at the anchor: never above 1 (a plan
can't beat a proven optimum, and an unproven one earns no credit for it), and never below 0 (doing nothing or worse).
A day where the anchor is no better than doing nothing scores 1 for matching it and 0 otherwise."""

from collections.abc import Mapping

from ..programs import references, sim
from ..programs.model import Task


def reward(cost: float, naive: float, anchor: float) -> float:
    if naive <= anchor:
        return 1.0 if cost <= anchor else 0.0
    return min(1.0, max(0.0, (naive - cost) / (naive - anchor)))


def grade(task: Task, plan: Mapping[int, Mapping], anchor: int | None = None) -> dict:
    """The plan's cost on the truth with the references it is scored against; `anchor` is the anchor's cost when
    known (references.anchor solves it otherwise)."""
    run = sim.execute(task, plan)
    naive = references.naive(task).cost.total
    best = references.anchor(task)[1].cost.total if anchor is None else anchor
    return {"cost": run.cost.to_dict(), "counts": run.counts, "naive": naive, "anchor": best,
            "reward": round(reward(run.cost.total, naive, best), 6), "executed": run.rows(),
            "ferries": [x.to_dict() for x in run.ferries]}
