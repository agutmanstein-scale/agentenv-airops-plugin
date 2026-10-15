"""The hub's landing queue, one model for the world and every optimiser: a bin of capacity c offers landing slots at
15b + floor(15j / c), j < c; flights take the first free slot at or after their arrival at the queue, in arrival
order (ties by key); a flight whose first free slot is more than `divert_after` minutes after its arrival diverts and
takes no slot."""

import bisect
from collections.abc import Sequence

BIN = 15
DIVERT_AFTER = 60


def slots(caps: Sequence[int], width: int = BIN) -> list[int]:
    return [width * b + width * j // c for b, c in enumerate(caps) for j in range(c)]


def land(arrivals: Sequence[tuple[int, tuple, int]], offered: Sequence[int],
         divert_after: int = DIVERT_AFTER) -> dict[int, int | None]:
    """Each (arrival minute, key, id)'s landing minute, None when it diverts; `offered` sorted and long enough."""
    nxt = list(range(len(offered) + 1))

    def free(i: int) -> int:
        root = i
        while nxt[root] != root:
            root = nxt[root]
        while nxt[i] != root:
            nxt[i], i = root, nxt[i]
        return root

    out: dict[int, int | None] = {}
    for eta, _, i in sorted(arrivals, key=lambda a: (a[0], a[1])):
        k = free(bisect.bisect_left(offered, eta))
        if k == len(offered):
            raise ValueError("the queue outlasts the slots offered")
        if offered[k] - eta > divert_after:
            out[i] = None
        else:
            out[i] = offered[k]
            nxt[k] = k + 1
    return out
