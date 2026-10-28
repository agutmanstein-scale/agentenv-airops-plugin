"""Realistic mode's world and cost at run time (REALISTIC.md), with nothing of the inputs cache: the realistic truth
and the day as known at a watch, tomorrow's cost, JetBlue's cost J of a played day and the reward on it. realistic.py
builds what these read (the layers, breakdowns, tomorrow's first departures, fares) from the cache."""

from collections import Counter, defaultdict
from dataclasses import replace

from .core.cost import FERRY_BASE_MIN, FERRY_MILES_PER_MIN, K_CANCEL, miles
from .core.known import CAP_LOOK
from .core.sim import FLOWN, _assign
from .programs import pax, sim
from .programs.cost import RATES, ferry_cost
from .programs.model import Task

SWAPS = frozenset({"JFK", "BOS"})


def truth(task: Task, gate_caps: dict[str, int], down: dict[str, int]) -> sim.World:
    """The realistic truth of a layered task whose tails `down` break at their minutes."""
    return replace(sim.truth(task), gates=gate_caps, broken=down, swaps=SWAPS)


def known(task: Task, t: int, gate_caps: dict[str, int], down: dict[str, int]) -> sim.World:
    """The realistic day as known at t: sim.known's programs as issued and closures begun, the gates and the hub-only
    swaps, which are rules, the tails broken by t, and each station's caps as they stand in t's hour carried over the
    next CAP_LOOK minutes (a persistence forecast), not the caps those hours turn out to have."""
    w, layers = sim.known(task, t), task.layers
    return replace(w, gates=gate_caps, broken={p: m for p, m in down.items() if m <= t}, swaps=SWAPS,
                   dep_caps=persisted(layers.dep_caps, t),
                   nominal=sim.nominal_caps(task) | persisted(layers.arr_caps, t))


def persisted(caps: dict[str, dict[int, int]], t: int) -> dict[str, dict[int, int]]:
    """Per station, the caps of the hours begun by t as they ran, and the cap of t's hour, if any, for every hour
    starting within CAP_LOOK of t."""
    now, out = t // 60, {}
    for s, by in caps.items():
        row = {h: c for h, c in by.items() if h < now}
        if now in by:
            row |= {h: by[now] for h in range(now, (t + CAP_LOOK) // 60 + 1)}
        if row:
            out[s] = row
    return out


def tomorrow(task: Task, run: sim.Execution, firsts: dict[tuple[str, str], list[int]], airline: bool = False) -> int:
    """The cost of covering tomorrow's first departures with the day's end-of-day aircraft: per family a min-cost
    matching of aircraft (where and when each is ready) to needs (station, departure), each pair the ferry when the
    aircraft is elsewhere plus the first flight's passengers' delay when it is ready late, at most cancelling it;
    `airline`: the first flight's lateness at the aircraft's own crew and fixed cost (RATES gate) in place of its
    passengers' delay."""
    pax = defaultdict(list)
    for f in task.flights:
        pax[f.family].append(f.pax)
    seats = {fam: round(sum(v) / len(v)) for fam, v in pax.items()}
    family = {t.tail: t.family for t in task.tails}
    ready = {}
    for t in task.tails:
        legs = [i for i in run.legs.get(t.tail, ()) if run.flights[i].status == FLOWN]
        ready[t.tail] = (run.flights[legs[-1]].arr + task.turn_for(t.family)) if legs else t.ready
    total = 0
    for fam in sorted(set(family.values())):
        planes = sorted(p for p in run.ends if family.get(p) == fam)
        needs = [(s, d) for (g, s), n in sorted(task.needed.items()) if g == fam
                 for d in (firsts.get((g, s), []) + [None] * n)[:n]]
        size = max(len(planes), len(needs))
        matrix = [[0] * size for _ in range(size)]
        for a, p in enumerate(planes):
            for b, (s, d) in enumerate(needs):
                e = run.ends[p]
                move = 0 if e == s else FERRY_BASE_MIN + miles(task.airports[e], task.airports[s]) / FERRY_MILES_PER_MIN
                late = 0 if d is None else max(0, ready[p] + move - d)
                wait = RATES[fam].gate * late if airline else min(seats[fam] * late, seats[fam] * K_CANCEL)
                matrix[a][b] = ferry_cost(task, fam, e, s) + wait
        total += sum(matrix[a][b] for a, b in enumerate(_assign(matrix)))
    return total


def airline(task: Task, day, firsts: dict, passengers: pax.Passengers, fares: dict[int, float]) -> dict:
    """JetBlue's own cost of a played day before its lost revenue is priced, in passenger-minutes: its aircraft's
    minutes (gate, overrun, less what its cancellations saved, with that saving apart), its crews' deadheads, hotels
    and end of day, tomorrow
    at the aircraft's own cost; with the passenger counts and the fares that lost revenue is priced on (the report's
    JetBlue cost): the fares of the refunded, of the stranded to a later day, and of every passenger booked on a
    flight cancelled (`fares`: each group's itinerary fare)."""
    run = day.run
    c = run.cost
    group = {g.id: g for g in passengers.groups}
    rev = Counter()
    for part in run.pax.parts:
        rev[part.kind] += part.pax * fares[part.group]
    gone = {i for i, r in run.flights.items() if r.status != FLOWN}
    return {"aircraft": c.gate + c.overrun - c.saved, "saved": c.saved, "crew": day.crew_cost,
            "tomorrow": tomorrow(task, run, firsts, airline=True),
            "cancelled_pax": sum(f.pax for f in task.flights if f.id in gone),
            "cancelled_rev": sum(g.pax * fares[g.id] for g in group.values() if gone.intersection(g.legs)),
            "refunded_rev": rev[pax.REFUNDED], "stranded_rev": rev[pax.STRANDED], **run.pax.outcomes()}


def jetblue(a: dict, take: float, save: float) -> float:
    """JetBlue's cost J of a day priced by `airline`, in passenger-minutes: its aircraft's minutes with `save` of what
    its cancellations saved at FAA's rates, its crews, tomorrow, the refunded passengers' fares and `take` of the
    stranded's."""
    return (a["aircraft"] + (1 - save) * a["saved"] + a["crew"] + a["tomorrow"] + a["refunded_rev"]
            + take * a["stranded_rev"])


def reward(cost: float, top: float, anchor: float) -> float:
    """The realistic reward: 1.0 at the cheapest plan known (`top`) or under it, 0.5 at JetBlue's replay (`anchor`),
    toward the 0.2 floor beyond it, as `score` of the cost over the top."""
    return score(cost - top, max(anchor - top, 1.0))


def score(cost: float, anchor: float, k: float = 2.0) -> float:
    """The realistic reward: 0.5 at JetBlue's replayed cost (`anchor`), toward 1 as cost falls to 0 and toward the
    0.2 floor as it grows, as a function of cost / anchor alone so days compare."""
    return 0.2 + 0.8 / (1 + 5 / 3 * (max(cost, 0) / anchor) ** k)
