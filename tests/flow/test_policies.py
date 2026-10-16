from collections import Counter

import pytest
from flow_helpers import arrival, world

from agentenv_airops.flow import forecast, policies, queue
from agentenv_airops.flow import world as flow_world


def test_with_room_the_plan_holds_nobody_and_short_of_it_holds_on_the_ground():
    demand, exempt = [0] * 60, [0] * 60
    demand[0] = 3
    plenty = [(1.0, [60, 60, 60, 60])]
    x = policies.solve(demand, exempt, 0, plenty, 168.0, 229.0)
    assert x[0] == 3 and sum(x) == 3
    tight = [(1.0, [1, 1, 1, 1])]
    x = policies.solve(demand, exempt, 0, tight, 168.0, 229.0)
    assert [m for m, v in enumerate(x) for _ in range(v)] == [0, 15, 30]


def test_the_plan_hedges_between_scenarios_and_stays_integral():
    demand, exempt = [0] * 60, [0] * 60
    demand[0] = 4
    good, bad = [4, 4, 4, 4], [1, 1, 1, 1]
    x = policies.solve(demand, exempt, 0, [(0.5, good), (0.5, bad)], 168.0, 229.0)
    held = [m for m, v in enumerate(x) for _ in range(v)]
    assert all(isinstance(v, int) for v in x) and len(held) == 4
    assert held != [0, 0, 0, 0] and held != [0, 15, 30, 45]
    x = policies.solve(demand, exempt, 2, [(1.0, good)], 168.0, 229.0)
    assert sum(x) == 4 and min(m for m, v in enumerate(x) if v) >= 1


def test_ration_by_schedule_takes_the_first_planned_slot_at_or_after_each_arrival():
    flights = [arrival(k, 70, 0) for k in range(3)]
    got = policies.ration(flights, {0: 62, 1: 60, 2: 60}, [0, 0, 0, 1] + [0] * 11 + [1] + [0] * 14 + [1], 60)
    assert got == {0: 63, 1: 75, 2: 90}
    assert policies.ration(flights[:1], {0: 95}, [1] + [0] * 29, 60) == {0: 95}


def test_hourly_programs_on_the_true_capacity_hold_on_the_ground_and_release_the_rest():
    flights = [arrival(k, 70, 0) for k in range(4)] + [arrival(4, 300, 200)]
    w = world(flights, [1] * 192)

    def truth(world, t, k_end):
        return [(1.0, list(world.caps[t // 15:k_end + 1]))]

    releases, issues = policies.run(w, truth, honest=True)
    assert releases == {0: 0, 1: 15, 2: 30, 3: 45, 4: 210}
    out = flow_world.evaluate(w, releases)
    assert out.totals["air"] == 0 and out.totals["diversions"] == 0
    first = issues[0]
    assert first.at == 0 and set(first.edct) == {1, 2, 3, 4} and first.program == frozenset({2, 3})
    assert first.end == 105 and first.honest == pytest.approx(105)


def test_reactive_holds_the_capacity_observed_now_three_hours_then_the_train_median():
    w = world([arrival(0, 70, 0)], [13] * 192)
    train = {"JFK": {"by_hour": [20.0] * 24, "day": 20.0}}
    [(p, caps)] = forecast.reactive(train)(w, 60, 30)
    assert p == 1.0 and len(caps) == 27
    assert sum(caps[:12]) == 3 * 52 and sum(caps[12:16]) == 20


def test_classes_leads_quantiles_and_pooling():
    assert [forecast.klass(s) for s in (0.95, 0.9, 0.8, 0.7, 0.5)] == [0, 0, 1, 2, 3]
    assert [forecast.lead(h) for h in (1, 2, 4, 5, 8, 9, 30)] == [0, 1, 1, 2, 2, 3, 3]
    p = [0.5, 0.2, 0.2, 0.1]
    assert [forecast.quantile(p, u) for u in forecast.QUANTILES] == [3, 2, 1, 0, 0]
    rows = [forecast.Row("BOS", 0, None, 0, 0)] * 25 + [forecast.Row("BOS", 0, 2, 0, 2)] * 5
    rows += [forecast.Row("BOS", 3, None, 3, 3)] * 5
    tab = forecast.table(rows, [("BOS", 0, 61.0), ("BOS", 0, 59.0), ("BOS", 3, 30.0)])
    assert tab.dist("BOS", 0, None, 0) == ([1.0, 0.0, 0.0, 0.0], "cell")
    assert tab.dist("BOS", 0, 2, 0) == ([25 / 30, 0.0, 5 / 30, 0.0], "lead")
    assert tab.dist("BOS", 3, None, 3)[1] == "hub"
    assert tab.capacity["BOS"] == (60.0, 0.825 * 61, 0.675 * 61, 30.0)
    assert forecast.Table.from_dict(tab.to_dict()) == tab


def test_rates_become_landings_per_bin_by_cumulative_rounding():
    caps = forecast.bins([38.0] * 8)
    assert sum(caps) == 76 and set(caps) <= {9, 10}
    assert Counter(queue.slots(caps[:4]))[0] == 1
