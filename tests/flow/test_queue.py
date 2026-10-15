from agentenv_airops.flow import queue


def test_slots_spread_each_bins_capacity_over_its_fifteen_minutes():
    assert queue.slots([4, 0, 3]) == [0, 3, 7, 11, 30, 35, 40]
    assert queue.slots([16])[:3] == [0, 0, 1]


def test_flights_land_first_come_first_served_and_hold_for_the_first_free_slot():
    offered = queue.slots([2] * 40)
    got = queue.land([(0, (2,), 2), (0, (1,), 1), (1, (0,), 0), (20, (3,), 3)], offered)
    assert got == {1: 0, 2: 7, 0: 15, 3: 22}


def test_a_flight_past_the_diversion_limit_diverts_and_leaves_its_slot_to_the_next():
    offered = queue.slots([1] * 20)
    arrivals = [(0, (k,), k) for k in range(6)]
    got = queue.land(arrivals, offered)
    assert [got[k] for k in range(6)] == [0, 15, 30, 45, 60, None]
    assert queue.land(arrivals + [(31, (9,), 9)], offered)[9] == 75
    assert queue.land(arrivals, offered, divert_after=45)[4] is None
    assert queue.land(arrivals, offered, divert_after=90)[5] == 75
