"""Units at the start from a crew replay of the record before it with the rest of the day as scheduled, by hand: a
unit follows its column up to the start (actual blocks, its report if a reserve was called) and takes the column's
legs from the start as its trip; the replay looks ahead, so a crew too near its limits is not sent where the day goes
on; a crew that a cancellation before the start would strand is replaced; crews released before the start with
nothing left, and reserves whose period is over, are off."""

from agentenv_airops.core.model import Airport
from agentenv_airops.programs import crewday, crewreplay, crews
from agentenv_airops.programs.crewday import ahead
from agentenv_airops.programs.crewreplay import DH, OP, Column, Leg, Problem
from agentenv_airops.programs.crews import Unit

AIRPORTS = {c: Airport(c, c, lat, lon, "America/New_York") for c, (lat, lon) in
            {"JFK": (40.64, -73.78), "BOS": (42.36, -71.01), "DCA": (38.85, -77.04),
             "BUF": (42.94, -78.73)}.items()}
DATE = "2024-01-01"


def problem(legs, units):
    return Problem(legs, units, DATE, lambda s, m: m, lambda u, s: crews.reposition(u, s, AIRPORTS, DATE))


def unit(uid, kind="pilot", station="JFK", report=240, **kw) -> Unit:
    hour = kw.pop("hour", None if report is None else report // 60)
    ready = kw.pop("ready", 0 if report is None else report + crews.BRIEF)
    return Unit(uid, kind, "A32X", crews.size(kind, "A32X"), kw.pop("home", "JFK"), kw.pop("end", station), station,
                ready, report, hour, at=station, **kw)


def later(fid: int, orig: str, dest: str, dep: int, block: int) -> Leg:
    return Leg(ahead(fid), f"B6 {fid}", orig, dest, dep, dep + block, block, "A32X", 20, price=crewday.AHEAD)


def test_a_unit_follows_its_column_and_takes_its_trip_from_the_plan():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 300, 380, 60, "A32X", 20), later(7, "BOS", "DCA", 700, 60),
            later(8, "DCA", "JFK", 800, 60)]
    u = unit("P", trip=(101, ahead(7)))
    p = problem(legs, [u])
    c = Column("P", ((101, OP), (ahead(7), OP), (ahead(8), DH)), end="JFK")
    s = crewday.at_start(p, u, c, 600)
    assert (s.station, s.ready, s.flown, s.minutes, s.landed) == ("BOS", 400, 1, 80, 380)
    assert (s.trip, s.deadheads, s.report) == ((7,), (8,), 240)


def test_a_reserve_is_called_before_the_start_only_if_it_flew_before_it():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 345, 405, 60, "A32X", 20), later(7, "JFK", "DCA", 705, 60)]
    r = unit("R", report=None, hour=None, reserve="short", rap=120, until=840, lead=150)
    p = problem(legs, [r])
    s = crewday.at_start(p, r, Column("R", ((101, OP),), report=300, end="BOS"), 600)
    assert (s.report, s.hour, s.rap, s.station, s.flown) == (300, 5, 120, "BOS", 1)
    s = crewday.at_start(p, r, Column("R", ((ahead(7), OP),), report=660, end="DCA"), 600)
    assert (s.report, s.station, s.trip) == (None, "JFK", (7,))


def test_crews_done_before_the_start_and_spent_reserves_are_off():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 300, 380, 60, "A32X", 20)]
    done = unit("P", trip=(101,))
    p = problem(legs, [done])
    assert crewday.at_start(p, done, Column("P", ((101, OP),), end="BOS"), 600) is None
    assert crewday.at_start(p, done, Column("P", ((101, OP),), end="BOS"), 390) is not None
    assert crewday.at_start(p, done, None, 600) is not None
    over = unit("R", report=None, hour=None, reserve="short", rap=0, until=500, lead=150)
    assert crewday.at_start(p, over, None, 600) is None
    standby = unit("S", report=300, reserve="standby", until=660)
    assert crewday.at_start(p, standby, None, 600).until == 660
    moved = crewday.at_start(p, standby, Column("S", ((101, DH),), end="BOS"), 600)
    assert moved.until is None and moved.station == "BOS" and moved.flown == 0


def test_the_replay_looks_ahead_and_sends_the_crew_that_can_go_on():
    legs = [Leg(101, "B6 1", "JFK", "BUF", 650, 740, 90, "A32X", 0), later(1, "BUF", "JFK", 800, 90)]
    tired = [unit(k, kind, report=-30, hour=20, flown=3, minutes=400, trip=(101,)) for k, kind in
             (("PA", "pilot"), ("CA", "cabin"))]
    fresh = [unit(k, kind, report=605, trip=(ahead(1),)) for k, kind in (("PB", "pilot"), ("CB", "cabin"))]
    p = problem(legs, tired + fresh)
    s = crewreplay.solve(p, time_limit=30, polish=5)
    assert s.uncrewed == () and s.crews(p)[101]["pilot"] == "PB"
    b = crewday.at_start(p, p.unit["PB"], s.columns["PB"], 760)
    assert (b.station, b.trip) == ("BUF", (1,))


def test_a_leg_cancelled_before_the_start_does_not_strand_the_morning():
    legs = [Leg(101, "B6 1", "JFK", "BOS", 300, 360, 60, "A32X", 20), later(7, "BOS", "JFK", 700, 60)]
    stranded = [unit(k, kind, "DCA", 200, trip=(100, 101, ahead(7)), end="JFK") for k, kind in
                (("P", "pilot"), ("C", "cabin"))]
    standby = [unit(k, kind, "JFK", 240, reserve="standby", until=600) for k, kind in
               (("SP", "pilot"), ("SC", "cabin"))]
    p = problem(legs, stranded + standby)
    s = crewreplay.solve(p, time_limit=30, polish=5)
    assert s.uncrewed == () and s.crews(p)[101] == {"pilot": "SP", "cabin": "SC"}
    at = {u.id: crewday.at_start(p, u, s.columns.get(u.id), 600) for u in p.units}
    assert (at["SP"].station, at["SP"].trip, at["P"].station, at["P"].trip) == ("BOS", (7,), "DCA", ())


def test_a_flight_left_without_a_crew_names_the_crews_there_and_their_rule():
    legs = [Leg(1, "B6 1", "BOS", "JFK", 1131, 1131 + 255, 255, "A32X", 20)]
    tired = [unit(k, kind, "BOS", 540, hour=9, flown=1, minutes=295, landed=1084, ready=1104, end="BOS")
             for k, kind in (("P", "pilot"), ("C", "cabin"))]
    p = problem(legs, tired)
    s = crewreplay.solve(p, time_limit=30, polish=5)
    assert s.uncrewed == (1,)
    cause = crewday.uncrewed_causes(p, s)[0]
    assert cause["missing"] == {"pilot": "no unit's plan has it; P Table A: 295 + 255 > 540",
                                "cabin": "no unit's plan has it; C 14 h duty from report 540"}
    assert cause["could_alone"] == {"pilot": [], "cabin": []}
