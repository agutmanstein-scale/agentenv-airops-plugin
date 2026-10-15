"""The v2 hindsight MIP (HiGHS): v1's time-line network (refs.mip) by split family, inside the programs.

Each flight gets a copy per departure minute (every `step` minutes, at the EDCT of every slot into its destination, the
slot less its scheduled block, and at the end of its crew's rest) from its earliest to its deadline, the hub cutoff
included, less the minutes the world forbids: those a ground stop holds it, those at which a segment governs it with
its slot time (the minute plus the scheduled block) inside the period but on none of JetBlue's slots, and those after
its crew deadline before its crew's rest ends. A copy on a slot counts in that slot's row, at most one flight per slot;
any other copy into a hub counts in the nominal row of its arrival hour (the minute plus the block). A copy costs its
pax x minutes late at arrival and its aircraft's minutes (cost.flown_price: the minute's gate delay, and the world's
block overrun, the same for every copy of a flight); a cancellation costs `price`, the notice price at the solve time,
which keeps the model linear, or the flat price, either less the cancellation's saving, and a caller pins a
cancellation the sim decides (an auto-cancel) at the price the sim gives it (Task.auto_at). Pinned flights keep their
minute or their cancellation, and use up the slots and nominal room they take.

Aircraft flow as in v1, over nodes per (family, station) at every event time, with the Moves on top; a copy of a leg
with a busy block reaches its destination's node at the block's ready time when that is later than its turn. A Move
adds an aircraft at its destination at `ready`; at its origin it takes the first aircraft free from `leave` on, before
any departure at that minute. While it waits its station has no aircraft free, so it fires at `leave` if an aircraft
idles there, else at the first arrival that no other waiting Move takes: per Move a monotone 0/1 fired-by variable F
runs over `leave` and the later nodes an aircraft can reach, each increment drawn from that node's flow, and at each
of them the aircraft reaching it (the idle ones too at `leave`), less those the station's other Moves take there, are
at most M x F, M the most that can reach it. One that never fires leaves an aircraft spare at the end of the day,
absorbed at no cost. Overnight nodes feed v1's ferries onto tomorrow's needs. Every solution is a day
sim.execute reproduces from its departure minutes as holds, at the same cost, and every day sim.execute runs is a
solution (`Network.vector`), which also starts the search.

Proven to GAP (0.01%); `canonical` then re-solves among the optimal plans for the one with the earliest weighted
departures (v1's second pass, started from the first), so the plan, not only its cost, is the same on every run. A
second pass that runs out of time leaves the proven cost solve's status and plan, flagged as not canonical.
"""

import time
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import highspy
import numpy as np

from ..core.sim import FLOWN
from ..refs.mip import GAP, _Model
from . import sim
from .cost import cancel_price, ferry_cost, flown_price
from .model import Flight, Task

INF = highspy.kHighsInf


@dataclass(frozen=True)
class Solution:
    status: str
    objective: int
    bound: float
    gap_pct: float
    wall_s: float
    deps: dict[int, int | None]
    cols: int
    rows: int
    copies: int
    started: bool
    canonical: bool

    @property
    def proven(self) -> bool:
        return self.status == "Optimal" and self.gap_pct <= 100 * GAP

    def plan(self, decided: int) -> dict[int, dict]:
        """The solution as holds to each departure minute, its cancellations decided at `decided`."""
        return {i: {"cancel": True, "decided": decided} if d is None else {"not_before": d}
                for i, d in self.deps.items()}

    def to_dict(self) -> dict:
        return {"status": self.status, "objective": self.objective, "bound": round(self.bound, 1),
                "gap_pct": self.gap_pct, "wall_s": self.wall_s, "cols": self.cols, "rows": self.rows,
                "copies": self.copies, "started": self.started, "canonical": self.canonical}


def solve(task: Task, world: sim.World | None = None, price: Mapping[int, int] | None = None,
          fixed: Mapping[int, int | None] | None = None, earliest: int | None = None, step: int = 1,
          time_limit: float = 600.0, gap: float = GAP, threads: int = 1, canonical: bool = True,
          start: sim.Execution | None = None) -> Solution:
    """The optimum in `world` (the truth by default) with each cancel arc at `price` (default: notice-priced at the
    first watch). `fixed` pins flights to a departure minute (None: cancelled); the others leave at or after
    `earliest`. `start` is a day sim.execute ran in `world` that the model holds, to start the search from."""
    net = Network(task, world, price, fixed, earliest, step)
    return net.solve(time_limit, gap, threads, canonical, None if start is None else net.vector(start))


class Network:
    """The MIP of a task in a world, its columns kept so that any day the sim runs can be written as a solution."""

    def __init__(self, task: Task, world: sim.World | None = None, price: Mapping[int, int] | None = None,
                 fixed: Mapping[int, int | None] | None = None, earliest: int | None = None, step: int = 1):
        self.task = task
        self.world = world = sim.truth(task) if world is None else world
        price = {f.id: cancel_price(f, task.start) for f in task.flights} if price is None else price
        fixed = fixed or {}
        rules = sim.Rules(task, world)
        pools = {s.id: set(s.slots) for segs in world.segments.values() for s in segs}
        self.m = m = _Started()
        copies: dict[int, list[tuple[int, tuple]]] = {}
        events: dict[tuple[str, str], set[int]] = defaultdict(set)
        for f in task.flights:
            if f.id in fixed:
                d = fixed[f.id]
                copies[f.id] = [] if d is None else [(d, _place(f, d, rules, pools) or ("free", None))]
            else:
                lo = max(task.earliest(f, world.lb[f.id]), earliest or 0)
                jumps = {s - f.sched_block for seg in world.segments.get(f.dest, ()) for s in seg.slots}
                if f.crew and f.crew.rest is not None:
                    jumps.add(f.crew.rest)
                minutes = set(range(lo, f.deadline + 1, step)) | {d for d in jumps if lo <= d <= f.deadline}
                copies[f.id] = [(d, at) for d in sorted(minutes)
                                if rules.held(f, d) == d and f.legal(d) == d and (at := _place(f, d, rules, pools))]
            for d, _ in copies[f.id]:
                events[(f.family, f.orig)].add(d)
                events[(f.family, f.dest)].add(task.back(f, d + world.block[f.id]))
        self.moves = moves = [(f"{t.tail}~{k}", t.family, mv) for t in task.tails for k, mv in enumerate(t.repositions)]
        for t in task.tails:
            events[(t.family, t.pos)].add(t.ready)
        for _, fam, mv in moves:
            events[(fam, mv.dest)].add(mv.ready)
            events[(fam, mv.orig)].add(mv.leave)
        self.times = {key: sorted(ts) for key, ts in events.items()}
        node = {(key, x): None for key, ts in self.times.items() for x in ts}
        node = {k: n for n, k in enumerate(node)}
        over = {key: len(node) + n for n, key in enumerate(self.times)}
        flow: dict[int, dict[int, int]] = defaultdict(dict)

        def arc(u: int | None, v: int | None, c: int) -> None:
            if u is not None:
                flow[u][c] = flow[u].get(c, 0) - 1
            if v is not None:
                flow[v][c] = flow[v].get(c, 0) + 1

        self.xs: dict[tuple[int, int], int] = {}
        self.cancel: dict[int, int] = {}
        arriving: dict[int, list[int]] = defaultdict(list)
        rows: dict[tuple, dict[int, int]] = defaultdict(dict)
        pinned: Counter = Counter()
        cover = []
        for f in task.flights:
            w = _weight(task, f.id)
            r = {}
            for d, (kind, key) in copies[f.id]:
                arr = d + world.block[f.id]
                c = m.col(flown_price(f, d, arr), 1, True, (d - f.sched_dep + 1) * w)
                self.xs[(f.id, d)] = c
                v = node[((f.family, f.dest), task.back(f, arr))]
                arc(node[((f.family, f.orig), d)], v, c)
                arriving[v].append(c)
                r[c] = 1
                if kind != "free":
                    if f.id in fixed:
                        pinned[(kind, key)] += 1
                    else:
                        rows[(kind, key)][c] = 1
            flies = f.id in fixed and fixed[f.id] is not None
            self.cancel[f.id] = c = m.col(price[f.id], 0 if flies else 1, True, (f.deadline - f.sched_dep + 2) * w)
            r[c] = 1
            cover.append(r)
        tokens = Counter(t.family for t in task.tails) + Counter(fam for _, fam, _ in moves)
        self.ground: dict[tuple, list[int]] = {}
        idle = {}
        for key, ts in self.times.items():
            cs = []
            for a, b in zip(ts, ts[1:], strict=False):
                idle[node[(key, b)]] = c = m.col(0, tokens[key[0]], False)
                arc(node[(key, a)], node[(key, b)], c)
                cs.append(c)
            cs.append(m.col(0, tokens[key[0]], False))
            arc(node[(key, ts[-1])], over[key], cs[-1])
            self.ground[key] = cs
        supply: Counter = Counter()
        for t in task.tails:
            supply[node[((t.family, t.pos), t.ready)]] += 1
        for _, fam, mv in moves:
            supply[node[((fam, mv.dest), mv.ready)]] += 1
        self.fired: dict[str, list[tuple[int, int]]] = {}
        spare: dict[str, dict[int, int]] = defaultdict(dict)
        fires: dict[int, dict[str, dict[int, int]]] = defaultdict(dict)
        for name, fam, mv in moves:
            key = (fam, mv.orig)
            later = [x for x in self.times[key] if x > mv.leave]
            ts = [mv.leave] + [x for x in later if arriving[node[(key, x)]] or supply[node[(key, x)]]]
            last = None
            self.fired[name] = []
            for j, x in enumerate(ts):
                u = node[(key, x)]
                c = m.col(0, 1, True)
                arc(u, node[(key, ts[j + 1])] if j + 1 < len(ts) else None, c)
                if last is not None:
                    m.row({c: 1, last: -1}, 0, INF)
                fires[u][name] = {c: 1} if last is None else {c: 1, last: -1}
                self.fired[name].append((x, c))
                last = c
            spare[fam][last] = 1
        for name, fam, mv in moves:
            for j, (x, c) in enumerate(self.fired[name]):
                u = node[((fam, mv.orig), x)]
                reach: Counter = Counter(dict.fromkeys(arriving[u], 1))
                if j == 0 and u in idle:
                    reach[idle[u]] += 1
                for other, fire in fires[u].items():
                    if other != name:
                        reach.update({col: -v for col, v in fire.items()})
                most = tokens[fam] if j == 0 else min(tokens[fam], len(arriving[u]) + supply[u])
                reach[c] -= most
                m.row(dict(reach), -INF, -supply[u])
        self.wild: dict[tuple[str, str], int] = {}
        for fam in spare:
            for key in self.times:
                if key[0] == fam:
                    self.wild[key] = c = m.col(0, tokens[fam], True)
                    arc(over[key], None, c)
                    spare[fam][c] = 1
        self.need = need = Counter((t.family, t.end_req) for t in task.tails)
        self.ferry: dict[tuple[str, str, str], int] = {}
        into: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
        for fam, a in self.times:
            for fam2, b in need:
                if fam2 == fam:
                    self.ferry[(fam, a, b)] = c = m.col(ferry_cost(task, fam, a, b), tokens[fam], True)
                    arc(over[(fam, a)], None, c)
                    into[(fam, b)][c] = 1
        for key, n in need.items():
            m.row(into[key], n, n)
        n_moves = Counter(fam for _, fam, _ in moves)
        for fam, r in spare.items():
            m.row(r, n_moves[fam], n_moves[fam])
        for v in range(len(node) + len(over)):
            m.row(flow[v], -supply[v], -supply[v])
        for r in cover:
            m.row(r, 1, 1)
        for (kind, key), r in rows.items():
            room = 1 if kind == "slot" else world.nominal[key[0]][key[1]]
            m.row(r, -INF, max(0, room - pinned[(kind, key)]))
        self.copies = copies

    def solve(self, time_limit: float = 600.0, gap: float = GAP, threads: int = 1, canonical: bool = True,
              start: np.ndarray | None = None) -> Solution:
        m = self.m
        status, objective, bound, mip_gap, wall, x = m.run(time_limit, gap, threads, start)
        settled = False
        if canonical and status == "Optimal":
            m.row({c: v for c, v in enumerate(m.cost) if v}, -INF, round(objective) + 0.5)
            m.cost, main = m.second, m.cost
            tied, _, _, _, second_wall, y = m.run(time_limit, 0.0, threads, x)
            m.cost, wall = main, wall + second_wall
            m.rows.pop()
            if tied == "Optimal":
                x, settled = y, True
        deps = {}
        for f in self.task.flights:
            flown = [d for d, _ in self.copies[f.id] if x[self.xs[(f.id, d)]] > 0.5]
            deps[f.id] = flown[0] if flown else None
        return Solution(status, round(objective), bound, round(100 * mip_gap, 4), round(wall, 2), deps, len(m.cost),
                        len(m.rows), sum(len(v) for v in self.copies.values()), start is not None, settled)

    def vector(self, run: sim.Execution) -> np.ndarray | None:
        """`run` (a day sim.execute ran in this world, holding every pin) as a solution of the model, or None if a
        departure has no copy here."""
        task, world = self.task, self.world
        x = np.zeros(len(self.m.cost))
        delta: dict[tuple, Counter] = defaultdict(Counter)
        for f in task.flights:
            r = run.flights[f.id]
            if r.status != FLOWN:
                x[self.cancel[f.id]] = 1
                continue
            c = self.xs.get((f.id, r.dep))
            if c is None:
                return None
            x[c] = 1
            delta[(f.family, f.orig)][r.dep] -= 1
            delta[(f.family, f.dest)][task.back(f, r.dep + world.block[f.id])] += 1
        for t in task.tails:
            delta[(t.family, t.pos)][t.ready] += 1
        for name, fam, mv in self.moves:
            delta[(fam, mv.dest)][mv.ready] += 1
            when = run.moves[name]
            if when is not None:
                delta[(fam, mv.orig)][when] -= 1
            for at, c in self.fired[name]:
                x[c] = when is not None and at >= when
        ends: dict[tuple, int] = {}
        for key, ts in self.times.items():
            held = 0
            for t, c in zip(ts, self.ground[key], strict=True):
                held += delta[key][t]
                x[c] = held
            ends[key] = held
        out = Counter((fy.family, fy.orig, fy.dest) for fy in run.ferries)
        for (fam, a), n in ends.items():
            stay = min(n, self.need[(fam, a)])
            if stay:
                x[self.ferry[(fam, a, a)]] = stay
            gone = sum(v for (g, o, _), v in out.items() if (g, o) == (fam, a))
            for (g, o, b), v in out.items():
                if (g, o) == (fam, a):
                    x[self.ferry[(fam, a, b)]] = v
            if n - stay - gone:
                x[self.wild[(fam, a)]] = n - stay - gone
        return x


class _Started(_Model):
    """v1's model, run from a start solution when one is given."""

    def run(self, time_limit: float, gap: float, threads: int,
            start: np.ndarray | None = None) -> tuple[str, float, float, float, float, np.ndarray]:
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", float(time_limit))
        h.setOptionValue("threads", threads)
        h.setOptionValue("mip_rel_gap", gap)
        n = len(self.cost)
        h.addVars(n, np.zeros(n), np.array(self.ub, dtype=float))
        h.changeColsCost(n, np.arange(n, dtype=np.int32), np.array(self.cost, dtype=float))
        starts, idx, val = [], [], []
        for coefs, _, _ in self.rows:
            starts.append(len(idx))
            for c, v in coefs.items():
                if v:
                    idx.append(c)
                    val.append(v)
        h.addRows(len(self.rows), np.array([r[1] for r in self.rows], dtype=float),
                  np.array([r[2] for r in self.rows], dtype=float), len(idx), np.array(starts, dtype=np.int32),
                  np.array(idx, dtype=np.int32), np.array(val, dtype=float))
        ints = np.array([c for c in range(n) if self.integer[c]], dtype=np.int32)
        h.changeColsIntegrality(len(ints), ints, np.array([highspy.HighsVarType.kInteger] * len(ints)))
        if start is not None:
            h.setSolution(n, np.arange(n, dtype=np.int32), np.asarray(start, dtype=float))
        t0 = time.monotonic()
        h.run()
        wall = time.monotonic() - t0
        status = h.modelStatusToString(h.getModelStatus())
        info = h.getInfo()
        if info.primal_solution_status != 2:
            raise RuntimeError(f"no feasible solution: {status}")
        return (status, info.objective_function_value, info.mip_dual_bound, info.mip_gap, wall,
                np.array(h.getSolution().col_value))


def _place(f: Flight, d: int, rules: sim.Rules, pools: Mapping[str, set[int]]) -> tuple[str, tuple | None] | None:
    """The row a copy of f leaving at d counts in: its slot, its nominal hour, or none; None when d is forbidden."""
    seg = rules.governs(f, d)
    if seg is not None:
        cta = d + f.sched_block
        if cta <= seg.end:
            return ("slot", (seg.id, cta)) if cta in pools[seg.id] else None
    cap = rules.hour(f, d)
    return ("hour", cap[0]) if cap else ("free", None)


def _weight(task: Task, i: int) -> float:
    return 1 + i / (len(task.flights) + 1)
