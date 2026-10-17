"""The crew sim by hand: trips, time-outs, the extension, reserves (standby and short-call, its cap), the rest move
with its hotel, deadhead seats, swaps, (b)(14), named crews, the day so far, costs and the event log."""

import pytest
from day_helpers import day, flight, tail

from agentenv_airops.core.sim import AUTO_CANCELLED, FLOWN
from agentenv_airops.programs import crews, crewsim
from agentenv_airops.programs.crews import Unit


def unit(uid: str, kind: str = "pilot", station: str = "JFK", report: int | None = 540, hour: int | None = None,
         trip: tuple = (), home: str = "JFK", end: str | None = None, **kw) -> Unit:
    hour = report // 60 % 24 if hour is None and report is not None else hour
    ready = kw.pop("ready", report + crews.BRIEF if report is not None else 0)
    return Unit(uid, kind, "A32X", crews.size(kind, "A32X"), home, end or station, station, ready, report, hour,
                at=station, trip=tuple(trip), **kw)


def pair(trip: tuple, station: str = "JFK", report: int = 540, **kw) -> list[Unit]:
    return [unit("P1", "pilot", station, report, trip=trip, **kw), unit("C1", "cabin", station, report, trip=trip,
                                                                          **kw)]


def test_trip_crews_fly_their_trip_and_the_log_has_each_duty():
    task = day([flight(0, "JFK", "BOS", 600, 60), flight(1, "BOS", "JFK", 720, 60)], [tail("N1", "JFK")])
    d = crewsim.execute(task, {}, pair((0, 1)))
    assert [d.run.flights[i].dep for i in (0, 1)] == [600, 720]
    assert d.crews == {0: {"pilot": "P1", "cabin": "C1"}, 1: {"pilot": "P1", "cabin": "C1"}}
    assert d.uncrewed == () and d.crew_cost == 0
    assert d.units["P1"]["duties"] == [{"report": 540, "at": "JFK", "begins": "on duty", "legs": [(0, 600, 660),
                                        (1, 720, 780)], "deadheads": [], "release": 795}]
    limit = crews.pilot_limit(540, 9, 1) - 60
    assert d.status[0] == [(0, "times out", limit), (600, "flown", None)]
    assert crewsim.statuses(d)[0][0] == {"at": "00:00", "status": "times out", "until": crewsim.clock(limit)}


def test_a_flight_waits_for_a_crew_and_auto_cancels_when_its_crew_times_out():
    task = day([flight(0, "JFK", "BOS", 700, 60, deadline=760)], [tail("N1", "JFK")])
    d = crewsim.execute(task, {}, pair((0,), report=0, hour=0, ready=120, landed=100))
    assert crews.pilot_limit(0, 0, 1) == 660
    assert d.run.flights[0].status == AUTO_CANCELLED
    assert d.status[0][0] == (0, "no crew", None)


def test_the_extension_lets_a_late_leg_leave():
    crew = pair((0,), report=300, hour=5, ready=1020, landed=1000)
    task = day([flight(0, "JFK", "BOS", 1080, 60, deadline=1200)], [tail("N1", "JFK")])
    d = crewsim.execute(task, {}, crew)
    assert d.run.flights[0].dep == 1080 and 1080 + 60 > 300 + crews.fdp(5, 1)
    late = day([flight(0, "JFK", "BOS", 1081, 60, deadline=1200)], [tail("N1", "JFK")])
    assert crewsim.execute(late, {}, crew).run.flights[0].status == AUTO_CANCELLED


def test_table_a_counts_the_actual_blocks_flown():
    flights = [flight(0, "JFK", "BOS", 400, 400, sched_block=300), flight(1, "BOS", "JFK", 900, 150)]
    task = day(flights, [tail("N1", "JFK")])
    d = crewsim.execute(task, {}, pair((0, 1), report=340, hour=5))
    assert d.run.flights[1].status == AUTO_CANCELLED
    assert d.units["P1"]["duties"][0]["legs"] == [(0, 400, 800)]


def test_standby_reserve_takes_the_flight_of_a_timed_out_crew_until_its_shift_ends():
    standby = [unit("RP", "pilot", report=300, reserve="standby", until=660),
               unit("RC", "cabin", report=300, reserve="standby", until=660)]
    crew = [unit("P1", report=0, hour=0, ready=320, landed=300, trip=(0,)), unit("C1", "cabin", trip=(0,))]
    task = day([flight(0, "JFK", "BOS", 600, 90)], [tail("N1", "JFK")])
    d = crewsim.execute(task, {}, crew + standby)
    assert 600 + 90 > crews.pilot_limit(0, 0, 1) and d.crews[0] == {"pilot": "RP", "cabin": "C1"}
    late = day([flight(0, "JFK", "BOS", 670, 90, deadline=800)], [tail("N1", "JFK")])
    d = crewsim.execute(late, {}, crew + standby)
    assert d.run.flights[0].status == AUTO_CANCELLED


def test_short_call_reserve_reports_on_the_hour_its_lead_after_the_call():
    reserve = unit("RP", "pilot", report=None, hour=None, reserve="short", rap=0, until=720, lead=150)
    task = day([flight(0, "JFK", "BOS", 600, 60, deadline=1000)], [tail("N1", "JFK")])
    cabin = [unit("C1", "cabin", trip=(0,))]
    d = crewsim.execute(task, {}, [reserve] + cabin)
    assert d.run.flights[0].dep == 780 + crews.BRIEF
    assert d.units["RP"]["call"] == 600
    assert d.units["RP"]["duties"][0]["report"] == 780 and d.units["RP"]["duties"][0]["begins"] == "call"
    named = {0: {"crew": {"pilot": "RP"}}, "reports": {"RP": 540}}
    d = crewsim.execute(task, named, [reserve] + cabin)
    assert d.run.flights[0].dep == 600 and d.units["RP"]["duties"][0]["report"] == 540


def test_short_call_cap_ends_the_duty_by_its_period_start_plus_sixteen_hours():
    reserve = unit("RP", "pilot", report=None, hour=None, reserve="short", rap=0, until=720, lead=150)
    cabin = [unit("C1", "cabin", report=840, trip=(0, 1))]
    plan = {0: {"crew": {"pilot": "RP"}}, 1: {"crew": {"pilot": "RP"}}, "reports": {"RP": 840}}
    assert crews.pilot_limit(840, 14, 2, 0) == 960 + 120 < 840 + crews.fdp(14, 2) + 120
    for dep, status in ((1020, FLOWN), (1021, AUTO_CANCELLED)):
        task = day([flight(0, "JFK", "BOS", 900, 60), flight(1, "BOS", "JFK", dep, 60, deadline=1100)],
                   [tail("N1", "JFK")])
        d = crewsim.execute(task, plan, [reserve] + cabin)
        assert d.run.flights[0].dep == 900 and d.run.flights[1].status == status


def test_rest_move_flies_next_morning_after_ten_hours_with_a_hotel():
    units = [unit(k, kind, "BOS", 400, 6, ready=1020, landed=1000, trip=(0,) if kind == "pilot" else ())
             for k, kind in (("P1", "pilot"), ("C1", "cabin"))]
    task = day([flight(0, "BOS", "JFK", 1650, 60)], [tail("N1", "BOS")])
    d = crewsim.execute(task, {}, units)
    assert d.run.flights[0].dep == 1665
    for k in ("P1", "C1"):
        duties = d.units[k]["duties"]
        assert duties[0]["release"] == 1015 and duties[-1] == {"report": 1620, "at": "BOS", "begins": "rest",
                                                                "legs": [(0, 1665, 1725)], "deadheads": [],
                                                                "release": 1740}
        assert d.units[k]["hotels"] == [{"station": "BOS", "from": 1015, "to": 1620}]
    assert d.cost["hotel"] == pytest.approx(6 * crews.hotel("BOS", task.date))


def test_deadhead_rides_only_within_free_seats_and_costs_off_its_trip():
    flights = [flight(0, "JFK", "BOS", 600, 60, pax=147), flight(1, "JFK", "DCA", 700, 60)]
    riders = [unit("P2", "pilot", end="BOS"), unit("C2", "cabin", end="BOS")]
    task = day(flights, [tail("N1", "JFK"), tail("N2", "JFK")])
    d = crewsim.execute(task, {0: {"deadhead": ["P2", "C2"]}}, pair((0, 1)) + riders)
    assert d.riders == {0: ("P2",)}
    assert d.cost["deadhead"] == pytest.approx(crews.deadhead("pilot", "A32X", 60, task.date))
    planned = [unit("P2", "pilot", end="BOS", deadheads=(0,))]
    d = crewsim.execute(task, {0: {"deadhead": ["P2"]}}, pair((0, 1)) + planned)
    assert d.riders == {0: ("P2",)} and d.cost["deadhead"] == 0


def test_swap_a_crew_with_no_trip_left_takes_a_stranded_crews_flight():
    task = day([flight(0, "JFK", "DCA", 600, 60)], [tail("N1", "JFK")])
    units = pair((0,), station="BOS") + [unit("P2"), unit("C2", "cabin")]
    d = crewsim.execute(task, {}, units)
    assert d.crews[0] == {"pilot": "P2", "cabin": "C2"}
    assert d.status[0][0] == (0, "no crew", None)
    assert d.cost["end"] == pytest.approx(2 * crews.reposition(units[2], "DCA", task.airports, task.date) / 2
                                          + crews.reposition(units[3], "DCA", task.airports, task.date))


def test_b14_attendants_fly_their_own_late_leg_but_take_no_other_past_fourteen_hours():
    task = day([flight(0, "JFK", "BOS", 900, 60, deadline=950)], [tail("N1", "JFK")])
    pilot = [unit("P1", report=600, trip=(0,))]
    own = [unit("C1", "cabin", report=0, hour=0, ready=320, landed=300, trip=(0,))]
    assert crewsim.execute(task, {}, pilot + own).run.flights[0].dep == 900
    other = [unit("C1", "cabin", report=0, hour=0, ready=320, landed=300, trip=())]
    assert crewsim.execute(task, {}, pilot + other).run.flights[0].status == AUTO_CANCELLED


def test_a_named_crew_is_waited_for():
    flights = [flight(0, "BOS", "JFK", 500, 60), flight(1, "JFK", "DCA", 570, 60)]
    task = day(flights, [tail("N1", "BOS"), tail("N2", "JFK")])
    units = pair((1,), report=500) + [unit("P2", station="BOS", report=400, trip=(0,)),
                                      unit("C2", "cabin", "BOS", 400, trip=(0,))]
    d = crewsim.execute(task, {1: {"crew": {"pilot": "P2"}}}, units)
    assert d.run.flights[1].dep == 560 + crews.CONNECT and d.crews[1] == {"pilot": "P2", "cabin": "C1"}


def test_the_day_so_far_leaves_without_a_crew_and_says_so():
    task = day([flight(0, "JFK", "BOS", 600, 60)], [tail("N1", "JFK")])
    d = crewsim.execute(task, {0: {"left": 600}}, [])
    assert d.run.flights[0].status == FLOWN and d.uncrewed == (0,)
    assert d.status[0][-1] == (600, "flown without a legal crew", None)


def test_cancelled_flights_leave_their_crews_and_log_it():
    task = day([flight(0, "JFK", "BOS", 600, 60), flight(1, "JFK", "DCA", 700, 60)], [tail("N1", "JFK")])
    d = crewsim.execute(task, {0: {"cancel": True}}, pair((0, 1)))
    assert d.crews[1] == {"pilot": "P1", "cabin": "C1"}
    assert d.status[0][-1] == (0, "cancelled", None)
