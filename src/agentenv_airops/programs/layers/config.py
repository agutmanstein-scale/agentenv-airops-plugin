"""v4's layer config: the constants fitted on a pack's train dates (layers.calibrate), and the builder that turns them
into a task's Layers (docs/v4/DESIGN.md §2, §6.1).

The constants are the ramp closures' departure-rate cut per hub (`rate`, closures.fit_rate), the reserve crews per
crew base (`reserves`), and the departure and outstation caps' clauses (`short`, `min_sched`: depcaps' and arrcaps'
0.9 and 5, kept as set, not fitted), with the train `dates` they were fitted on. `build` gives a task every layer at
them: JetBlue's departure caps at the hubs (depcaps) and arrival caps at the outstations (arrcaps) from every
carrier's BTS flights of its dates, the hubs' ramp closures over its span (closures, from `spans`: the IEM routine
METARs and SPECIs), and the pool at each of the task's crew bases (none without the crew model). A base the config
leaves out keeps unlimited reserves. The desk's view goes with them (docs/v4/DESIGN.md §4): the closure rates
(Layers.rates), at which the day as known prices a closure under way over its expected end (sim.known), the hubs'
weather news (layers.weather) and the default delivery lags (model.LAGS).
"""

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ...build import weather
from ...flow import sources
from ..advisories import HUBS
from ..model import Layers, Task
from . import arrcaps, closures, depcaps
from .weather import Item


@dataclass(frozen=True)
class Config:
    rate: dict[str, float] = field(default_factory=dict)
    reserves: dict[str, int] = field(default_factory=dict)
    short: float = depcaps.SHORT
    min_sched: int = depcaps.MIN_SCHED
    dates: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: Mapping) -> "Config":
        return cls(dict(d["rate"]), dict(d["reserves"]), d["short"], d["min_sched"], tuple(d["dates"]))

    def to_dict(self) -> dict:
        return {"rate": dict(sorted(self.rate.items())), "reserves": dict(sorted(self.reserves.items())),
                "short": self.short, "min_sched": self.min_sched, "dates": list(self.dates)}


def build(task: Task, cfg: Config, frame: pd.DataFrame, spans: Mapping[str, Sequence[closures.Interval]],
          news: Sequence[Item] = ()) -> Layers:
    """The task's layers at `cfg`. `frame` holds every carrier's BTS flights of its dates (candidates.frame), `spans`
    each hub's closures in UTC epoch minutes (closures.intervals), `news` the hubs' weather (layers.weather.build)."""
    return Layers(dep_caps=depcaps.build(task, frame, cfg.short, cfg.min_sched),
                  arr_caps=arrcaps.build(task, frame, cfg.short, cfg.min_sched),
                  closures=closures.build(task, spans, cfg.rate),
                  reserves={b: n for b, n in sorted(cfg.reserves.items()) if b in task.bases},
                  weather=tuple(news), rates=dict(sorted(cfg.rate.items())))


@functools.lru_cache(maxsize=8)
def spans(inputs: Path, months: tuple[str, ...]) -> dict[str, list[closures.Interval]]:
    """Each hub's closures over `months`, from the cached routine METARs and SPECIs (IEM ASOS)."""
    return {h: closures.intervals(h, [weather.metar_path(inputs, h, m) for m in months],
                                  [sources.speci_path(inputs, h, m) for m in months]) for h in HUBS}
