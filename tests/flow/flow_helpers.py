"""What the flow tests share: the cache paths and small hand-made weather, arrivals and worlds."""

from pathlib import Path

import pytest

from agentenv_airops.flow import demand
from agentenv_airops.flow.capacity import Reductions
from agentenv_airops.flow.demand import Arrival
from agentenv_airops.flow.weather import Bin, Obs
from agentenv_airops.flow.world import World

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "inputs"
DERIVED = ROOT / "cache" / "derived"
needs_cache = pytest.mark.skipif(not (CACHE / "iem" / "n0q").is_dir() or not (CACHE / "bts").is_dir(),
                                 reason="needs the local input cache")


def obs(wind_dir=None, kt=0.0, ceiling=None, vis=10.0, ts=False, vicinity=False, frozen=False, at=0) -> Obs:
    return Obs(at, wind_dir, kt, None, ceiling, vis, ts, vicinity, frozen)


def arrival(id: int, sched_arr: int, d0: int, tau: int = 60, cls: str = "narrowbody 165k lb or more", pax: int = 150,
            unseen: bool = False, cancelled: bool = False, taxi_in: int = 10) -> Arrival:
    return Arrival(id, "~unseen" if unseen else "AA", id, "" if unseen else "ORD", None if unseen else "ZAU", 640.0,
                   sched_arr - tau - 25, sched_arr, cls, float(pax), pax, cancelled, unseen, d0, 0 if unseen else tau,
                   15, taxi_in, False, None, None, None, False, 0, "test")


def world(arrivals: list[Arrival], caps: list[int], hub: str = "JFK") -> World:
    timeline = tuple(Bin(obs(), 0.0) for _ in caps)
    return World("2024-07-12", hub, 0, tuple(arrivals), timeline, (30.0,) * demand.HOURS, Reductions(), tuple(caps),
                 (), (), 2.0, (obs(),))
