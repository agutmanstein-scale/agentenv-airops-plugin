import random
from dataclasses import replace

import numpy as np
import pytest
from day_helpers import day, flight, network_day, random_day, tail
from test_hindsight_mip import feasible
from v2_digest import DATE, digests

from agentenv_airops.occ.pack import Pack
from agentenv_airops.programs import crews, crewsim, mip, pax, sim
from agentenv_airops.programs.cost import REFUND
from agentenv_airops.programs.pax import Group, Passengers, Seat

V2 = {"naive": "d514334e73cae4473da59d945583944ae5e99a25a2b61df00b341bb9e2cea0a2",
      "B0": "a241a221e71e2d363ecec96cf67cb4af37f3185755430e6519bc2d09c4b5608e",
      "best": "5bdc773ac67cb7eb85e82133b3a6fe28b7a55343ee31c00d319825a81cec2949",
      "mip": "66fc1a5baa54666e77fe4bba422036e106ac229449da028cc72016bc18bb1488"}


def test_passengers_off_leave_every_v2_output_byte_identical_on_a_pack_date():
    """The digests are v2's own (main at 6bc7139) on the same date."""
    assert digests() == V2
    pack = Pack()
    w = pack.world(next(t for t, v in pack.tasks.items() if v["date"] == DATE))
    b0 = crewsim.execute(w.task, {}, w.units, local=w.local, airports=w.airports, desk=True)
    best = crewsim.execute(w.task, w.best_plan, w.units, local=w.local, airports=w.airports, fill=False)
    assert b0.total == pytest.approx(w.refs["B0"]["notice"], abs=0.05)
    assert best.total == pytest.approx(w.refs["best"], abs=0.5)
    assert set(b0.run.cost.to_dict()) == {"delay", "cancel", "notice", "ferry", "gate", "overrun", "saved", "total",
                                          "flat"}


def test_a_pack_date_books_its_loads_deterministically_into_connections_as_sold():
    pack = Pack()
    w = pack.world(next(t for t, v in pack.tasks.items() if v["date"] == DATE))
    task = w.task
    flows = pax.Flows.load(DATE)
    one, two = (pax.book(task, flows, pax.Dwell.load()) for _ in range(2))
    assert one == two and flows.label == "DB1B 2024Q3"
    booked = one.booked()
    assert all(booked[f.id] == f.pax for f in task.flights)
    pairs = [g for g in one.groups if len(g.legs) == 2]
    assert pairs and sum(g.pax for g in pairs) < 0.1 * sum(g.pax for g in one.groups)
    for g in pairs:
        i, j = task.flight(g.legs[0]), task.flight(g.legs[1])
        assert i.dest == j.orig in pax.CONNECT and i.orig != j.dest
        assert pax.mct(j.orig) <= j.sched_dep - i.sched_arr <= pax.DWELL
    double = pax.book(task, flows.scaled(2), pax.Dwell.load())
    assert sum(g.pax for g in double.groups if len(g.legs) == 2) > sum(g.pax for g in pairs)


def test_a_date_reads_its_own_survey_period_or_the_nearest():
    periods = {"2023Q3", "2024Q3", "2025Q2", "2025-07", "2026-01"}
    assert pax.period("2024-07-13", periods) == "2024Q3"
    assert pax.period("2025-07-08", periods) == "2025-07"
    assert pax.period("2025-06-30", periods) == "2025Q2"
    assert pax.period("2026-01-19", periods) == "2026-01"
    assert pax.period("2024-06-07", periods) == "2024Q3"
    assert pax.period("2025-12-02", periods) == "2026-01"


def connecting_day(lb: int = 0, cancel: int | None = None) -> tuple:
    """BUF-JFK lands at 360 (+ lb) for 20 connecting onto JFK-PWM at 420; the next JFK-PWM is at 600."""
    flights = [flight(0, "BUF", "JFK", 300, 60, pax=100, lb=lb, deadline=900),
               flight(1, "JFK", "PWM", 420, 60, pax=100, deadline=900),
               flight(2, "JFK", "PWM", 600, 60, pax=100, deadline=900)]
    task = day(flights, [tail("N1", "BUF", end="JFK"), tail("N2", "JFK", end="PWM"), tail("N3", "JFK", end="PWM")],
               start=240)
    groups = (Group(0, (0,), 80), Group(1, (0, 1), 20), Group(2, (1,), 80), Group(3, (2,), 100))
    plan = {} if cancel is None else {cancel: {"cancel": True, "decided": 240}}
    return task, Passengers(groups), plan


def test_a_missed_connection_is_rebooked_onto_the_next_flight_and_its_delay_counted_at_the_destination():
    task, passengers, plan = connecting_day(lb=60)
    run = sim.execute(task, plan, pax=passengers)
    d = run.pax
    assert d.misconnected() == 20 and d.outcomes() == {"flew": 260, "rebooked": 20, "stranded": 0}
    rebooked = next(p for p in d.parts if p.kind == "rebooked")
    assert rebooked.legs == (0, 2) and rebooked.delay == 660 - 480
    assert run.cost.delay == d.delay == 80 * 60 + 20 * 180 and run.cost.passengers
    assert run.cost.to_dict()["lodging"] == 0 and run.cost.cancel == 0


def test_crews_keep_their_seats_and_who_finds_none_today_takes_tomorrows_first_seat_and_a_hotel():
    task, passengers, plan = connecting_day(lb=60)
    runs = sim.execute(task, plan).flights
    d = pax.fly(task, passengers, runs, {2: 40})
    assert d.outcomes() == {"flew": 260, "rebooked": 10, "stranded": 10}
    night = next(p for p in d.parts if p.kind == "stranded")
    assert night.at == "JFK" and night.arr == 480 + 1440 and night.delay == 1440
    assert d.lodging == round(10 * crews.lodging("JFK", task.date))
    tomorrow = (Seat("B6 9 JFK", "JFK", "PWM", 1440 + 360, 1440 + 420, 4), Seat("B6 11 JFK", "JFK", "PWM", 2000, 2060,
                                                                              50))
    d = pax.fly(task, replace(passengers, tomorrow=tomorrow), runs, {2: 40})
    assert sorted((p.arr, p.pax) for p in d.parts if p.kind == "stranded") == [(1860, 4), (2060, 6)]


def test_a_cancellation_rebooks_first_come_first_served_and_costs_the_refund_exposure_not_523():
    task, passengers, plan = connecting_day(cancel=1)
    run = sim.execute(task, plan, pax=passengers)
    d = run.pax
    assert d.outcomes() == {"flew": 180, "rebooked": 50, "stranded": 50}
    assert {(p.group, p.kind, p.pax) for p in d.parts if p.kind != "flew"} == {
        (2, "rebooked", 50), (2, "stranded", 30), (1, "stranded", 20)}
    assert run.cost.cancel == REFUND * 100
    assert sim.execute(task, plan).cost.cancel == 523 * 100


def test_optimal_rebooking_takes_the_earlier_arrival_first_come_first_served_by_departure_passes_by():
    flights = [flight(0, "JFK", "PWM", 400, 60, pax=100, deadline=900),
               flight(1, "JFK", "BOS", 500, 60, pax=100, deadline=900),
               flight(2, "JFK", "PWM", 520, 60, pax=100, deadline=900),
               flight(3, "BOS", "PWM", 620, 60, pax=100, deadline=900)]
    task = day(flights, [tail(f"N{i}", f.orig, end=f.dest) for i, f in enumerate(flights)], start=240)
    passengers = Passengers(tuple(Group(i, (i,), 100 if i else 30) for i in range(4)))
    runs = sim.execute(task, {0: {"cancel": True, "decided": 240}}).flights
    fcfs, best = pax.fly(task, passengers, runs), pax.optimal(task, passengers, runs)
    assert next(p for p in fcfs.parts if p.group == 0).legs == (1, 3)
    assert next(p for p in best.parts if p.group == 0).legs == (2,)
    assert best.delay == fcfs.delay - 30 * 100


def mixed(seed: int):
    task = network_day(random.Random(seed), 6) if seed % 2 else random_day(random.Random(seed), 4)
    return task, pax.book(task, pax.Flows.synthetic(task, 0.4), pax.Dwell.uniform())


@pytest.mark.parametrize("seed", range(10))
def test_every_day_the_sim_runs_is_a_solution_of_the_passenger_model_at_its_proxy_cost(seed):
    task, passengers = mixed(seed)
    proxy = pax.Proxy(task, passengers)
    rng = random.Random(seed)
    for _ in range(5):
        plan = {f.id: {"cancel": rng.random() < 0.25, "decided": rng.randrange(0, 200),
                       "not_before": rng.randrange(0, 400)} for f in task.flights if rng.random() < 0.6}
        run = sim.execute(task, plan)
        net = mip.Network(task, price={f.id: proxy.price(f, run.flights[f.id].decided or 0) for f in task.flights},
                          pax=proxy)
        x = net.vector(run)
        assert x is not None and feasible(net, x)
        assert float(np.dot(net.m.cost, x)) == pytest.approx(proxy.cost(run), abs=1e-6)


@pytest.mark.parametrize("seed", range(10))
def test_every_passenger_optimum_replays_at_its_objective(seed):
    task, passengers = mixed(seed)
    proxy = pax.Proxy(task, passengers)
    sol = mip.solve(task, pax=proxy, start=sim.execute(task, {}))
    run = sim.execute(task, sol.plan(task.start), pax=passengers)
    assert sol.proven and abs(sol.objective - proxy.cost(run)) <= 1
    assert sol.objective <= proxy.cost(sim.execute(task, mip.solve(task).plan(task.start))) + 1
    assert sol.bound <= sol.objective + 1e-6


@pytest.mark.parametrize("seed", range(4))
def test_the_rounded_relaxation_is_a_plan_the_sim_plays_and_a_start_the_model_holds(seed):
    task, passengers = mixed(seed)
    proxy = pax.Proxy(task, passengers)
    net = mip.Network(task, pax=proxy)
    run = sim.execute(task, net.rounded())
    x = net.vector(run)
    assert x is not None and feasible(net, x)
    assert float(np.dot(net.m.cost, x)) == pytest.approx(proxy.cost(run), abs=1e-6)
