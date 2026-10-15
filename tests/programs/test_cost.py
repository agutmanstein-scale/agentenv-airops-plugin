from day_helpers import day, flight, tail

from agentenv_airops.programs import cost, sim


def test_a_cancellation_costs_523_a_passenger_plus_a_minute_for_each_minute_past_the_notice_line():
    f = flight(0, "JFK", "BUF", 600, 60, pax=100)
    assert cost.cancel_price(f, 480) == cost.cancel_price(f, 300) == 52_300
    assert cost.cancel_price(f, 600) == 100 * (523 + 120) == 64_300
    assert cost.cancel_price(f, 600 + 900) == 100 * 1_543
    assert cost.flat_price(f) == 52_300


def test_the_day_adds_delay_cancellations_their_notice_and_ferries():
    a, b, c = flight(0, "JFK", "BUF", 600, 60, pax=100), flight(1, "BUF", "JFK", 700, 60, pax=50), \
        flight(2, "JFK", "MCO", 800, 150, pax=10)
    task = day([a, b, c], [tail("N1", "JFK")])
    got = cost.priced(task, {0: 700, 1: None, 2: None}, {1: 700, 2: 600}, [("BUF", "JFK")])
    assert (got.delay, got.cancel, got.notice) == (100 * 40, 523 * 60, 50 * 120)
    assert got.ferry > 0 and got.total == got.flat + got.notice


def test_an_auto_cancel_is_decided_at_its_deadline():
    f = flight(0, "BUF", "JFK", 600, 60, pax=100, deadline=900)
    task = day([f], [tail("N1", "JFK", end="JFK")])
    run = sim.execute(task, {})
    assert run.flights[0].status == "auto_cancelled" and run.flights[0].decided == 900
    assert run.cost.notice == 100 * (900 - 600 + 120) and run.cost.flat == 52_300 + run.cost.ferry
