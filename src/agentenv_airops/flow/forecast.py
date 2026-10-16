"""The capacity forecasts the Command Center model's online policies plan on: the reactive one (P5) and the TAF's,
verified on train days (P7) and turned into scenarios (P8).

A capacity is classed by its share of the hub's best visual rate in its profile (JFK 52, BOS 61): class 0 at 0.9 or
more, 1 at 0.75-0.9, 2 at 0.6-0.75, 3 below 0.6. A decision at minute t reads the latest TAF or amendment issued by
then; for each later hour it gives the prevailing class (the TAF's prevailing conditions at the hour's middle through
capacity.hourly, at the hour's departure demand) and the class of the worst TEMPO or PROB alternative there (None
without one), at a lead of whole hours bucketed 0-2, 2-5, 5-9 and 9 or more. On the train day-hubs every decision
hour 00:00-23:00 and every later hour of the world's 48 add a count to P(observed class | prevailing class, worst
alternative, lead), per hub; a cell with fewer than MIN_CELL counts pools over the alternative, then over the lead,
then over everything at the hub. A class's capacity is its median observed capacity (landings an hour) on the hub's
train days, each day-hub's hours 00:00-23:00, or the class's middle share of the best rate where the hub never saw
it.

TAF scenarios: five comonotone quantile paths, u = 0.1, 0.3, ..., 0.9, each of probability 0.2; in every later hour
the class at quantile u of that hour's verified distribution, worst class first, so a pessimistic path stays
pessimistic. Reactive: one path, the capacity observed at the decision (the latest METAR or SPECI, the last radar
frame) for REACTIVE_HOURS, then the hub's train-day median landings for each later local hour. In both the decision's
own hour is observed. Rates become integer landings per bin by cumulative rounding from the decision's bin.
"""

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from . import capacity, taf
from .weather import Bin, Obs
from .world import World

CLASSES = (0.9, 0.75, 0.6)
MIDDLE = (0.95, 0.825, 0.675, 0.5)
LEADS = (2, 5, 9)
MIN_CELL = 20
QUANTILES = (0.1, 0.3, 0.5, 0.7, 0.9)
REACTIVE_HOURS = 3
CLEAR = Obs(0, None, 0.0, None, None, 10.0, False, False, False)

Scenarios = list[tuple[float, list[int]]]
Forecast = Callable[[World, int, int], Scenarios]


def best(hub: str) -> float:
    """The hub's best visual rate: the rate under calm, clear weather at the lightest departure demand."""
    return capacity.rate(hub, CLEAR, 0.0)[0]


def departures(world: World, hour: int) -> float:
    return world.departures[min(hour, len(world.departures) - 1)]


def rate(world: World, b: Bin, hour: int) -> float:
    return capacity.hourly(world.hub, b, departures(world, hour), world.reductions)


def observed(world: World, hour: int) -> float:
    """The hour's capacity in the world, its four bins' rates averaged."""
    return statistics.fmean(rate(world, world.timeline[k], hour) for k in range(4 * hour, 4 * hour + 4))


def now(world: World, t: int) -> Bin:
    """The weather as observed at minute t: the latest METAR or SPECI and the last radar frame before it."""
    k = t // 15
    return Bin(world.obs_at(t), world.timeline[max(k - 1, 0)].share)


def klass(share: float) -> int:
    return next((c for c, lo in enumerate(CLASSES) if share >= lo), len(CLASSES))


def lead(hours: int) -> int:
    return next((b for b, hi in enumerate(LEADS) if hours < hi), len(LEADS))


def classes(world: World, tafs: Sequence[taf.Taf], t: int, hour: int) -> tuple[int, int | None]:
    """The TAF in force at minute t's prevailing class at the hour, and its worst alternative's."""
    forecast = taf.in_force(tafs, t)
    if forecast is None:
        raise ValueError(f"no TAF issued by minute {t} at {world.hub} {world.date}")
    p, alts = taf.state(forecast, 60 * hour + 30)
    top = best(world.hub)
    worst = min((rate(world, Bin(a, None), hour) for a in alts), default=None)
    return klass(rate(world, Bin(p, None), hour) / top), None if worst is None else klass(worst / top)


@dataclass(frozen=True)
class Row:
    hub: str
    prevailing: int
    alternative: int | None
    lead: int
    observed: int


def sample(world: World, tafs: Sequence[taf.Taf]) -> list[Row]:
    """The verification pairs of one train day-hub."""
    seen = [klass(observed(world, h) / best(world.hub)) for h in range(len(world.timeline) // 4)]
    out = []
    for d in range(24):
        for h in range(d + 1, len(seen)):
            p, a = classes(world, tafs, 60 * d, h)
            out.append(Row(world.hub, p, a, lead(h - d), seen[h]))
    return out


def capacities(world: World) -> list[tuple[int, float]]:
    """(class, capacity) of the day-hub's hours 00:00-23:00."""
    out = []
    for h in range(24):
        c = observed(world, h)
        out.append((klass(c / best(world.hub)), c))
    return out


@dataclass(frozen=True)
class Table:
    counts: Mapping[str, Mapping[tuple, Counter]]
    capacity: Mapping[str, Sequence[float]]

    def dist(self, hub: str, p: int, a: int | None, ld: int) -> tuple[list[float], str]:
        """P(observed class) for the cell and the pooling level used."""
        for level, key in (("cell", (p, a, ld)), ("lead", (p, "*", ld)), ("class", (p, "*", "*")),
                           ("hub", ("*", "*", "*"))):
            c = self.counts[hub].get(key)
            n = sum(c.values()) if c else 0
            if n >= MIN_CELL or level == "hub":
                return [c[k] / n for k in range(len(MIDDLE))], level
        raise AssertionError("unreachable")

    def to_dict(self) -> dict:
        return {"counts": {h: [[*k, [c[i] for i in range(len(MIDDLE))]] for k, c in sorted(t.items(), key=str)]
                           for h, t in self.counts.items()},
                "capacity": {h: list(v) for h, v in self.capacity.items()}}

    @classmethod
    def from_dict(cls, d: Mapping) -> "Table":
        counts = {h: {tuple(r[:3]): Counter(dict(enumerate(r[3]))) for r in rows} for h, rows in d["counts"].items()}
        return cls(counts, {h: tuple(v) for h, v in d["capacity"].items()})


def table(rows: Iterable[Row], seen: Iterable[tuple[str, int, float]]) -> Table:
    """The verification table from the train pairs and the train hours' (hub, class, capacity)."""
    counts: dict[str, dict[tuple, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for r in rows:
        for key in ((r.prevailing, r.alternative, r.lead), (r.prevailing, "*", r.lead), (r.prevailing, "*", "*"),
                    ("*", "*", "*")):
            counts[r.hub][key][r.observed] += 1
    by: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for hub, c, s in seen:
        by[hub][c].append(s)
    level = {hub: tuple(statistics.median(v[c]) if v[c] else MIDDLE[c] * best(hub) for c in range(len(MIDDLE)))
             for hub, v in by.items()}
    return Table({h: dict(v) for h, v in counts.items()}, level)


def quantile(p: Sequence[float], u: float) -> int:
    """The class at quantile u, worst first."""
    acc = 0.0
    for c in reversed(range(len(p))):
        acc += p[c]
        if acc >= u - 1e-12:
            return c
    return 0


def taf_based(tafs_of: Callable[[World], Sequence[taf.Taf]], tab: Table) -> Forecast:
    def forecast(world: World, t: int, k_end: int) -> Scenarios:
        tafs = tafs_of(world)
        h0 = t // 60
        observed_now = rate(world, now(world, t), h0)
        cells = {}
        for h in range(h0 + 1, k_end // 4 + 1):
            p, a = classes(world, tafs, t, h)
            cells[h] = tab.dist(world.hub, p, a, lead(h - h0))[0]
        out = []
        for u in QUANTILES:
            rates = [observed_now if k // 4 == h0 else tab.capacity[world.hub][quantile(cells[k // 4], u)]
                     for k in range(t // 15, k_end + 1)]
            out.append((1 / len(QUANTILES), bins(rates)))
        return out

    return forecast


def reactive(train_capacity: Mapping[str, Mapping]) -> Forecast:
    def forecast(world: World, t: int, k_end: int) -> Scenarios:
        h0 = t // 60
        observed_now = rate(world, now(world, t), h0)
        typical = train_capacity[world.hub]["by_hour"]
        rates = [observed_now if k // 4 < h0 + REACTIVE_HOURS else typical[(k // 4) % 24]
                 for k in range(t // 15, k_end + 1)]
        return [(1.0, bins(rates))]

    return forecast


def bins(rates: Sequence[float]) -> list[int]:
    """Hourly rates per bin as integer landings per bin, by cumulative rounding."""
    out, total = [], 0.0
    for r in rates:
        before = math.floor(total + 0.5)
        total += r / 4
        out.append(math.floor(total + 0.5) - before)
    return out
