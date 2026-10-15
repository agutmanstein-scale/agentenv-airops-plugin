from dataclasses import replace

from day_helpers import GDP, busy_day, day, flight, stop, tail, three_into_a_program

from agentenv_airops.programs import sim
from agentenv_airops.programs.model import Move


def slots(run):
    return {i: (r.dep, r.slot) for i, r in run.flights.items()}


def test_controlled_flights_leave_at_the_edct_of_the_earliest_free_slot_and_a_cancellation_moves_the_queue_up():
    task = three_into_a_program()
    assert slots(sim.execute(task, {})) == {0: (120, 180), 1: (180, 240), 2: (240, 300)}
    run = sim.execute(task, {0: {"cancel": True, "decided": 0}})
    assert slots(run) == {0: (None, None), 1: (120, 180), 2: (180, 240)}
    assert run.flights[1].arr == 180 and run.flights[1].program == "g"


def test_claims_bind_first_and_a_claim_the_flight_cannot_make_is_lost_and_its_slot_wasted():
    task = three_into_a_program()
    assert slots(sim.execute(task, {2: {"claim": 180}})) == {0: (180, 240), 1: (240, 300), 2: (120, 180)}
    late = three_into_a_program(lb=100)
    run = sim.execute(late, {2: {"claim": 180}})
    assert slots(run) == {0: (180, 240), 1: (240, 300), 2: (341, None)}
    assert run.wasted == (("g", 180),) and run.flights[2].arr == 401


def test_a_flight_that_has_left_leaves_then_on_its_slot_if_it_is_one_and_on_nothing_if_not():
    task = three_into_a_program()
    run = sim.execute(task, {2: {"left": 110}, 0: {"left": 180}})
    assert slots(run) == {0: (180, 240), 1: (120, 180), 2: (110, None)}
    held = day([flight(0, "BUF", "JFK", 120, 60)], [tail("N1", "BUF")], stops=(stop(100, 200),))
    assert sim.execute(held, {0: {"left": 150}}).flights[0].dep == 150


def test_a_ground_stop_holds_departures_to_its_hub_only():
    flights = [flight(0, "BUF", "JFK", 120, 60), flight(1, "DCA", "BOS", 120, 60), flight(2, "MCO", "JFK", 90, 150)]
    task = day(flights, [tail("N1", "BUF"), tail("N2", "DCA"), tail("N3", "MCO")], stops=(stop(100, 200),))
    run = sim.execute(task, {})
    assert [run.flights[i].dep for i in range(3)] == [200, 120, 90]


def test_the_nominal_cap_holds_arrivals_beyond_the_hours_schedule():
    flights = [flight(0, "BUF", "JFK", 100, 60, lb=30), flight(1, "DCA", "JFK", 130, 60),
               flight(2, "MCO", "JFK", 140, 60)]
    task = day(flights, [tail("N1", "BUF"), tail("N2", "DCA"), tail("N3", "MCO")], aar=1)
    assert [sim.execute(task, {}).flights[i].arr for i in range(3)] == [190, 190, 240]


def test_a_hub_departure_auto_cancels_at_the_cutoff_and_counts_as_decided_there():
    flights = [flight(0, "BOS", "JFK", 1300, 70, lb=300), flight(1, "JFK", "MCO", 1500, 150, pax=10),
               flight(2, "MCO", "BUF", 1400, 150)]
    task = sim.with_cutoff(day(flights, [tail("N1", "BOS", end="MCO"), tail("N2", "MCO", end="BUF")]), 1620)
    assert [f.deadline for f in task.flights] == [1620, 1620, 2300]
    run = sim.execute(task, {})
    assert run.flights[0].dep == 1600 and run.flights[1].status == "auto_cancelled"
    assert run.flights[1].decided == 1620 and run.cost.notice == 10 * (1620 - 1500 + 120)
    later = sim.execute(sim.with_cutoff(task, 1740), {})
    assert later.flights[1].dep == 1700 and later.cost.notice == 0


def test_every_confirmed_cancellation_stands_whatever_the_day_does():
    task = three_into_a_program()
    run = sim.execute(task, {1: {"cancel": True, "decided": 130, "not_before": 100, "claim": 180}})
    assert run.flights[1].status == "cancelled" and run.flights[1].decided == 130
    assert run.cost.notice == 100 * (130 - 100 + 120)


def test_a_move_adds_an_aircraft_at_its_destination_and_takes_the_first_one_at_its_origin_from_leave():
    move = Move("BUF", "MCO", 300, 100)
    flights = [flight(0, "MCO", "JFK", 120, 150), flight(1, "JFK", "BUF", 100, 60), flight(2, "BUF", "JFK", 320, 60)]
    task = day(flights, [tail("N1", "JFK", repositions=(move,))])
    run = sim.execute(task, {})
    assert run.flights[0].tail == "N1~0" and run.moves == {"N1~0": 300}
    assert run.flights[2].status == "auto_cancelled" and not run.spare and not run.ferries
    early = sim.execute(replace(task, flights=(*flights[:2], flight(2, "BUF", "JFK", 290, 60))), {})
    assert early.flights[2].dep == 290 and early.moves == {"N1~0": None} and early.spare == {"A320": 1}
    assert not early.ferries


def test_a_waiting_move_takes_the_aircraft_before_a_flight_at_the_same_minute():
    flights = [flight(0, "JFK", "BUF", 100, 60, deadline=600)]

    def moved(leave):
        return day(flights, [tail("N1", "JFK", repositions=(Move("JFK", "DCA", leave, 50),)), tail("N2", "JFK", 400)])

    run = sim.execute(moved(100), {})
    assert (run.flights[0].dep, run.moves) == (400, {"N1~0": 100})
    run = sim.execute(moved(101), {})
    assert (run.flights[0].dep, run.moves) == (100, {"N1~0": 400})


def test_a_busy_block_keeps_the_aircraft_that_flew_its_leg_and_a_cancelled_leg_has_none():
    task = busy_day()
    run = sim.execute(task, {})
    assert [(r.tail, r.dep) for r in run.flights.values()] == [("N2", 160), ("N1", 170), ("N2", 430)]
    assert not run.moves and not run.spare
    run = sim.execute(task, {0: {"cancel": True}})
    assert [(r.status, r.tail, r.dep) for r in run.flights.values()] == [
        ("cancelled", None, None), ("flown", "N1", 170), ("auto_cancelled", None, None)]


def test_the_day_as_known_hides_delays_and_true_blocks_until_look():
    flights = [flight(0, "BUF", "JFK", 100, 80, lb=20, sched_block=60), flight(1, "DCA", "JFK", 400, 90, lb=40,
                                                                              sched_block=60)]
    task = day(flights, [tail("N1", "BUF"), tail("N2", "DCA")], segments=(GDP,))
    world = sim.known(task, 0)
    assert (world.lb, world.block) == ({0: 20, 1: 0}, {0: 80, 1: 60})
    assert sim.known(task, 0, programs=False).segments == {"JFK": (), "BOS": ()}
    assert sim.calm(task).lb == {0: 0, 1: 0} and sim.truth(task).segments["JFK"] == (GDP,)


def test_two_waiting_moves_take_the_arriving_aircraft_one_at_a_time_in_the_order_they_began():
    flights = [flight(0, "BUF", "JFK", 100, 60), flight(1, "PWM", "JFK", 120, 70)]
    tails = [tail("N1", "BUF", repositions=(Move("JFK", "MCO", 100, 50),)),
             tail("N2", "PWM", repositions=(Move("JFK", "DCA", 110, 60),))]
    assert sim.execute(day(flights, tails), {}).moves == {"N1~0": 190, "N2~0": 220}
