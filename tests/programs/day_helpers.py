"""Small hand-made v2 days for the sim, MIP and reference tests: a few flights and tails at JFK with the programs
given, every program covering every origin."""

from dataclasses import replace

from program_helpers import EVERY, adv

from agentenv_airops.core.model import Airport
from agentenv_airops.programs.advisories import HUBS, Advisory, Stop
from agentenv_airops.programs.model import Busy, Flight, Move, Outcome, Programs, Segment, Tail, Task

PLACES = {"JFK": (40.64, -73.78), "BOS": (42.36, -71.01), "BUF": (42.94, -78.73), "DCA": (38.85, -77.04),
          "MCO": (28.43, -81.31), "PWM": (43.65, -70.31)}
TURN = 30


def flight(i: int, orig: str, dest: str, dep: int, block: int, pax: int = 100, lb: int = 0,
           deadline: int | None = None, sched_block: int | None = None, actual: Outcome | None = None) -> Flight:
    sb = block if sched_block is None else sched_block
    return Flight(id=i, flight=f"B6 {100 + i}", orig=orig, dest=dest, sched_dep=dep, sched_arr=dep + sb,
                  sched_block=sb, block=block, pax=pax, pax_raw=pax, family="A320", lb=lb, nas_removed=0,
                  controlled=None, gs_held=None, exempt=False, pair=None,
                  deadline=dep + 240 if deadline is None else deadline,
                  actual=actual or Outcome(False, False, "", dep, dep + block, dest, "N1"))


def tail(name: str, pos: str, ready: int = 0, end: str | None = None, repositions: tuple[Move, ...] = (),
         busy: tuple[Busy, ...] = (), seats: int = 150) -> Tail:
    return Tail(name, "A320", "A320-232", seats, pos, ready, (), busy, repositions, end or pos, end or pos)


def segment(id: str, since: int, until: int, start: int, end: int, slots: tuple[int, ...]) -> Segment:
    return Segment(id, "JFK", since, until, start, end, 0.0, slots, ())


def stop(start: int, end: int, hub: str = "JFK") -> Stop:
    return Stop("gs", hub, start, end, EVERY)


def day(flights: list[Flight], tails: list[Tail], segments: tuple[Segment, ...] = (), stops: tuple[Stop, ...] = (),
        aar: int = 99, start: int = 0, cutoff: int = 1620, advisories: tuple[Advisory, ...] = ()) -> Task:
    """`advisories` (at JFK) add to one GDP advisory per segment; the forecast reads them, the truth `segments` and
    `stops`."""
    advisories = tuple(adv(s.id, "gdp", s.since, s.start, s.end, (30,) * (s.end // 60 - s.start // 60 + 1))
                       for s in segments) + advisories
    programs = {h: Programs(h, aar, 1.0, 0.0, {}, advisories if h == "JFK" else (),
                            tuple(s for s in segments if s.hub == h), tuple(s for s in stops if s.hub == h), ())
                for h in HUBS}
    return Task(task_id="b6-jfk-20240101-0100", split="", carrier="B6", hub="JFK", date="2024-01-01",
                tz="America/New_York", base=0, window=(start + 60, start + 360), start=start, slide=0, dmax=900,
                cutoff=cutoff, turn={"A320": TURN},
                airports={c: Airport(c, c, lat, lon, "America/New_York") for c, (lat, lon) in PLACES.items()},
                artcc={}, tails=tuple(tails), flights=tuple(flights), programs=programs, clauses={}, sources={})


GDP = segment("g", 0, 1000, 150, 400, (180, 240, 300))


def three_into_a_program(**kw) -> Task:
    """Three flights into JFK, each scheduled to land at 160 inside a program with slots at 180, 240 and 300."""
    flights = [flight(0, "BUF", "JFK", 100, 60), flight(1, "DCA", "JFK", 100, 60),
               flight(2, "MCO", "JFK", 100, 60, deadline=600, **kw)]
    return day(flights, [tail("N1", "BUF", end="JFK"), tail("N2", "DCA", end="JFK"), tail("N3", "MCO", end="JFK")],
               segments=(GDP,))


def shortened(task: Task, slack: int = 150) -> Task:
    """The task with every deadline at most `slack` after the earliest departure, to keep a test's MIP small."""
    return replace(task, flights=tuple(replace(f, deadline=min(f.deadline, task.earliest(f, f.lb) + slack))
                                       for f in task.flights))


def busy_day() -> Task:
    """N2 lands late at JFK at 220 and is busy there until 400, while N1 idles at JFK for the 170 departure."""
    flights = [flight(0, "BUF", "JFK", 100, 60, lb=60), flight(1, "JFK", "DCA", 170, 60),
               flight(2, "JFK", "MCO", 430, 150)]
    return day(flights, [tail("N1", "JFK", end="DCA"), tail("N2", "BUF", busy=(Busy(0, 400),), end="MCO")])
