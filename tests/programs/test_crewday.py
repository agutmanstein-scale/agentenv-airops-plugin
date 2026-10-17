"""Units at the start from a crew replay of the record before it, by hand: a unit follows its column (actual blocks,
its report if a reserve was called), a crew that a cancellation before the start would strand is replaced by another,
and crews done before the start or reserves whose period is over are off."""

from agentenv_airops.core.model import Airport
from agentenv_airops.programs import crewday, crewreplay, crews
from agentenv_airops.programs.crewreplay import DH, OP, Column, Leg, Problem
from agentenv_airops.programs.crews import Unit

AIRPORTS = {c: Airport(c, c, lat, lon, "America/New_York") for c, (lat, lon) in
            {"JFK": (40.64, -73.78), "BOS": (42.36, -71.01), "DCA": (38.85, -77.04)}.items()}
DATE = "2024-01-01"


def problem(legs, units, start=None):
    return Problem(legs, units, DATE, lambda s, m: m, lambda u, s: crews.reposition(u, s, AIRPORTS, DATE),
                   call=start, rests=False)


def unit(uid, kind="pilot", station="JFK", report=240, **kw) -> Unit:
    hour = kw.pop("hour", None if report is None else report // 60)
    ready = kw.pop("ready", 0 if report is None else report + crews.BRIEF)
    return Unit(uid, kind, "A32X", crews.size(kind, "A32X"), kw.pop("home", "JFK"), kw.pop("end", station), station,
                ready, report, hour, at=station, **kw)


def test_a_unit_follows_its_column_at_actual_blocks():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 300, 380, 60, "A32X", 20)]
    u = unit("P", trip=(101, 102), deadheads=(103,))
    p = problem(legs, [u])
    s = crewday.at_start(p, u, Column("P", ((101, OP),), end="BOS"), {102: 7, 103: 8}, 600)
    assert (s.station, s.ready, s.flown, s.minutes, s.landed) == ("BOS", 400, 1, 80, 380)
    assert (s.trip, s.deadheads, s.report) == ((7,), (8,), 240)


def test_a_reserve_called_before_the_start_keeps_its_report_and_its_cap():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 345, 405, 60, "A32X", 20)]
    r = unit("R", report=None, hour=None, reserve="short", rap=120, until=840, lead=150)
    p = problem(legs, [r])
    s = crewday.at_start(p, r, Column("R", ((101, OP),), report=300, end="BOS"), {}, 600)
    assert (s.report, s.hour, s.rap, s.station, s.flown) == (300, 5, 120, "BOS", 1)


def test_crews_done_before_the_start_and_spent_reserves_are_off():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 300, 380, 60, "A32X", 20)]
    done = unit("P", trip=(101,))
    p = problem(legs, [done])
    assert crewday.at_start(p, done, Column("P", ((101, OP),), end="BOS"), {}, 600) is None
    assert crewday.at_start(p, done, Column("P", ((101, OP),), end="BOS"), {}, 390) is not None
    over = unit("R", report=None, hour=None, reserve="short", rap=0, until=500, lead=150)
    assert crewday.at_start(p, over, None, {}, 600) is None
    standby = unit("S", report=300, reserve="standby", until=660)
    assert crewday.at_start(p, standby, None, {}, 600).until == 660
    moved = crewday.at_start(p, standby, Column("S", ((101, DH),), end="BOS"), {}, 600)
    assert moved.until is None and moved.station == "BOS" and moved.flown == 0


def test_a_leg_cancelled_before_the_start_does_not_strand_the_morning():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 300, 360, 60, "A32X", 20)]
    stranded = [unit(k, kind, "DCA", 200, trip=(100, 101, 102), end="JFK") for k, kind in (("P", "pilot"),
                                                                                            ("C", "cabin"))]
    standby = [unit(k, kind, "JFK", 240, reserve="standby", until=600) for k, kind in (("SP", "pilot"),
                                                                                        ("SC", "cabin"))]
    p = problem(legs, stranded + standby)
    s = crewreplay.solve(p, time_limit=30, polish=5)
    assert s.uncrewed == () and s.crews(p)[101] == {"pilot": "SP", "cabin": "SC"}
    at = {u.id: crewday.at_start(p, u, s.columns.get(u.id), {102: 7}, 600) for u in p.units}
    assert (at["SP"].station, at["P"].station, at["P"].trip) == ("BOS", "DCA", (7,))
