"""A linear program on HiGHS's dual simplex, which ends on a vertex: the transportation and ground-holding problems the
Command Center model solves have totally unimodular constraints, so with integer data that vertex is integral, and
`integral` checks it rather than trusting it."""

from collections.abc import Sequence
from dataclasses import dataclass, field

import highspy
import numpy as np

INF = highspy.kHighsInf
TOLERANCE = 1e-6


@dataclass
class Lp:
    cost: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    starts: list[int] = field(default_factory=list)
    index: list[int] = field(default_factory=list)
    value: list[float] = field(default_factory=list)
    lo: list[float] = field(default_factory=list)
    hi: list[float] = field(default_factory=list)

    def col(self, cost: float, upper: float = INF) -> int:
        self.cost.append(cost)
        self.upper.append(upper)
        return len(self.cost) - 1

    def row(self, cols: Sequence[int], coefs: Sequence[float], lo: float, hi: float) -> None:
        self.starts.append(len(self.index))
        self.index += cols
        self.value += coefs
        self.lo.append(lo)
        self.hi.append(hi)

    def solve(self) -> tuple[np.ndarray, float]:
        """The optimal vertex and its objective; anything but an optimum raises."""
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("solver", "simplex")
        h.setOptionValue("threads", 1)
        n = len(self.cost)
        h.addVars(n, np.zeros(n), np.array(self.upper, dtype=float))
        h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(self.cost, dtype=float))
        h.addRows(len(self.lo), np.array(self.lo, dtype=float), np.array(self.hi, dtype=float), len(self.index),
                  np.array(self.starts, dtype=np.int32), np.array(self.index, dtype=np.int32),
                  np.array(self.value, dtype=float))
        h.run()
        status = h.modelStatusToString(h.getModelStatus())
        if status != "Optimal":
            raise RuntimeError(f"the LP ended {status}")
        return np.array(h.getSolution().col_value), h.getInfo().objective_function_value


def integral(x: np.ndarray) -> np.ndarray:
    """x rounded, raising when a value is not an integer."""
    r = np.round(x)
    worst = float(np.abs(x - r).max()) if len(x) else 0.0
    if worst > TOLERANCE:
        raise RuntimeError(f"the LP's vertex is fractional (off by {worst:.3g})")
    return r.astype(int)
