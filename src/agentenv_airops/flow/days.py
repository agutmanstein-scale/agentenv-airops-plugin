"""The premise check's 20 day-hubs and its train days, drawn once with a fixed seed from step 0's built candidates.

The draw takes 10 day-hubs per hub, split by season (summer Jun-Aug, winter Dec-Feb) by largest remainder in proportion
to the hub's eligible day-hubs, and at most one day-hub per storm event, events pooled across hubs (step 0's rule:
program dates chained within 2 days). Eligible are the built non-control candidates minus the previous check's 20
day-hubs and minus JFK's 2025 day-hubs, whose BTS rows lost Endeavor and SkyWest. One seeded generator walks JFK then
BOS, summer then winter: it shuffles the stratum's events no earlier stratum took, keeps the first k and picks one
day-hub uniformly in each. Train is every built non-control day-hub of an event with no drawn day-hub, JFK 2025 left
out; the previous 20 may be train. Everything the world calibrates reads train dates only.
"""

import json
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..programs import candidates
from ..programs.advisories import HUBS

SEED = 2026
PER_HUB = 10
SEASONS = ("summer", "winter")
PREVIOUS = frozenset({(d, "BOS") for d in ("2023-07-29", "2023-08-17", "2024-06-22", "2024-06-23", "2024-06-30",
                                            "2024-08-06", "2024-08-15", "2024-08-18", "2024-08-19", "2025-07-07")}
                     | {(d, "JFK") for d in ("2023-07-07", "2023-07-13", "2023-07-29", "2024-06-14", "2024-07-10",
                                             "2024-07-17", "2024-07-22", "2024-08-03", "2024-08-18", "2025-07-08")})

DayHub = tuple[str, str]


@dataclass(frozen=True)
class Draw:
    drawn: tuple[DayHub, ...]
    train: tuple[DayHub, ...]
    quotas: dict[str, dict[str, int]]
    eligible: tuple[DayHub, ...]
    excluded: tuple[DayHub, ...]
    event: dict[str, int]


def lost(d: str, hub: str) -> bool:
    """A JFK 2025 day-hub, out of the draw and of train."""
    return hub == "JFK" and d.startswith("2025")


def read(path: Path) -> tuple[list[DayHub], list[DayHub]]:
    """Step 0's non-control candidates and the built ones among them."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    keys = [(r["date"], r["hub"]) for r in rows if r.get("split") != "control"]
    return keys, [(r["date"], r["hub"]) for r in rows if r.get("split") != "control" and r["built"]]


def make(keys: Sequence[DayHub], built: Sequence[DayHub], seed: int = SEED) -> Draw:
    event = candidates.events(d for d, _ in keys)
    eligible = tuple(sorted(k for k in built if k not in PREVIOUS and not lost(*k)))
    q = quotas(eligible)
    drawn = draw(eligible, event, q, seed)
    return Draw(drawn, train(built, event, drawn), q, eligible, tuple(sorted(k for k in built if lost(*k))), event)


def quotas(eligible: Iterable[DayHub], per_hub: int = PER_HUB) -> dict[str, dict[str, int]]:
    """Per hub, per_hub split over the seasons by largest remainder (ties to summer)."""
    out = {}
    for hub in HUBS:
        n = Counter(candidates.season(d) for d, h in eligible if h == hub)
        total = sum(n[s] for s in SEASONS)
        exact = {s: per_hub * n[s] / total for s in SEASONS}
        q = {s: int(exact[s]) for s in SEASONS}
        for s in sorted(SEASONS, key=lambda s: (-(exact[s] - q[s]), SEASONS.index(s)))[:per_hub - sum(q.values())]:
            q[s] += 1
        out[hub] = q
    return out


def draw(eligible: Sequence[DayHub], event: Mapping[str, int], quota: Mapping[str, Mapping[str, int]],
         seed: int) -> tuple[DayHub, ...]:
    rng = random.Random(seed)
    taken, out = set(), []
    for hub in HUBS:
        for s in SEASONS:
            by = defaultdict(list)
            for d, h in sorted(eligible):
                if h == hub and candidates.season(d) == s and event[d] not in taken:
                    by[event[d]].append((d, h))
            order = sorted(by)
            if len(order) < quota[hub][s]:
                raise ValueError(f"{hub} {s}: {len(order)} events left for a quota of {quota[hub][s]}")
            rng.shuffle(order)
            for e in order[:quota[hub][s]]:
                out.append(rng.choice(sorted(by[e])))
                taken.add(e)
    return tuple(out)


def train(built: Iterable[DayHub], event: Mapping[str, int], drawn: Iterable[DayHub]) -> tuple[DayHub, ...]:
    held = {event[d] for d, _ in drawn}
    return tuple(sorted(k for k in built if event[k[0]] not in held and not lost(*k)))


def write(path: Path, result: Draw, seed: int = SEED, source: str = "") -> dict:
    ev = result.event

    def row(k: DayHub) -> dict:
        return {"date": k[0], "hub": k[1], "season": candidates.season(k[0]), "event": ev[k[0]]}

    held = sorted({ev[d] for d, _ in result.drawn})
    doc = {"seed": seed, "source": source, "rule": __doc__.strip().replace("\n", " "),
           "previous": [row(k) for k in sorted(PREVIOUS)],
           "excluded_e4": [row(k) for k in result.excluded],
           "eligible": {h: {s: sum(1 for d, x in result.eligible if x == h and candidates.season(d) == s)
                            for s in SEASONS} for h in HUBS},
           "quotas": result.quotas, "events": ev, "held_out_events": held,
           "drawn": [row(k) for k in result.drawn], "train": [row(k) for k in result.train]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def load(path: Path) -> tuple[list[DayHub], list[DayHub]]:
    """The drawn and train day-hubs of a days.json."""
    doc = json.loads(path.read_text())
    return [(r["date"], r["hub"]) for r in doc["drawn"]], [(r["date"], r["hub"]) for r in doc["train"]]
