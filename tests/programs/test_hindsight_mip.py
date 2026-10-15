import random

import pytest
from day_helpers import GDP, day, flight, segment, shortened, stop, tail, three_into_a_program

from agentenv_airops.programs import mip, references, sim
from agentenv_airops.programs.cost import cancel_price
from agentenv_airops.programs.model import Move, Task


def days() -> dict[str, Task]:
    moved = [flight(0, "MCO", "JFK", 120, 150), flight(1, "JFK", "BUF", 100, 60), flight(2, "BUF", "JFK", 320, 60),
             flight(3, "JFK", "PWM", 140, 70, deadline=700), flight(4, "PWM", "JFK", 260, 70)]
    tails = [tail("N1", "JFK", repositions=(Move("BUF", "MCO", 300, 100),)),
             tail("N2", "JFK", busy=(Move("JFK", "JFK", 150, 420),), end="BOS")]
    stopped = [flight(0, "BUF", "JFK", 120, 60, pax=150), flight(1, "DCA", "JFK", 110, 60, pax=40),
               flight(2, "JFK", "DCA", 250, 60), flight(3, "JFK", "BUF", 260, 60, pax=20)]
    return {"slots": three_into_a_program(lb=100),
            "stop": day(stopped, [tail("N1", "BUF", end="DCA"), tail("N2", "DCA", end="BUF")],
                        segments=(segment("g", 150, 1000, 150, 400, (190, 200, 260)),), stops=(stop(100, 160),)),
            "nominal": day([flight(0, "BUF", "JFK", 100, 60, lb=30), flight(1, "DCA", "JFK", 130, 60),
                            flight(2, "MCO", "JFK", 140, 60), flight(3, "JFK", "MCO", 200, 150, pax=180)],
                           [tail("N1", "BUF"), tail("N2", "DCA"), tail("N3", "MCO")], aar=1),
            "moves": day(moved, tails, segments=(GDP,))}


@pytest.fixture(scope="module")
def fixture_day(task) -> Task:
    return shortened(task)


def feasible(net: mip.Network, x) -> bool:
    return all(lo - 1e-6 <= sum(v * x[c] for c, v in coefs.items()) <= up + 1e-6 for coefs, lo, up in net.m.rows)


def replays(task: Task, sol: mip.Solution) -> bool:
    run = sim.execute(task, sol.plan(task.start))
    return run.cost.total == sol.objective and all(
        (run.flights[i].dep if run.flights[i].status == "flown" else None) == d for i, d in sol.deps.items())


@pytest.mark.parametrize("name", ["slots", "stop", "nominal", "moves"])
def test_every_optimum_replays_exactly_in_the_sim(name):
    task = days()[name]
    sol = mip.solve(task)
    assert sol.proven and replays(task, sol)
    assert sol.objective <= sim.execute(task, {}).cost.total


def test_the_fixture_optimum_replays_and_no_plan_beats_it(fixture_day):
    task = fixture_day
    sol = mip.solve(task, start=sim.execute(task, {}))
    assert sol.proven and sol.started and replays(task, sol)
    rng = random.Random(5)
    plans = [{}, references.airline_plan(task)]
    for _ in range(12):
        ids = rng.sample([f.id for f in task.flights], rng.randrange(1, len(task.flights)))
        plans.append({i: {"cancel": rng.random() < 0.2, "decided": task.start + rng.randrange(0, 300),
                          "not_before": task.start + rng.randrange(0, 400)} for i in ids})
    for plan in plans:
        run = sim.execute(task, plan)
        assert run.cost.total >= sol.objective
        price = {f.id: cancel_price(f, task.start if run.flights[f.id].decided is None else run.flights[f.id].decided)
                 for f in task.flights}
        net = mip.Network(task, price=price)
        x = net.vector(run)
        assert x is not None and feasible(net, x)
        assert round(sum(c * v for c, v in zip(net.m.cost, x, strict=True))) == run.cost.total


@pytest.mark.parametrize("name", ["slots", "stop", "nominal", "moves"])
def test_every_day_the_sim_runs_is_a_solution_of_the_model_at_its_cost(name):
    task = days()[name]
    rng = random.Random(11)
    for _ in range(8):
        plan = {f.id: {"cancel": rng.random() < 0.2, "decided": rng.randrange(0, 200),
                       "not_before": rng.randrange(0, 400), "claim": rng.choice([None, 180, 240, 300])}
                for f in task.flights if rng.random() < 0.6}
        run = sim.execute(task, plan)
        decided = {f.id: run.flights[f.id].decided for f in task.flights}
        net = mip.Network(task, price={f.id: cancel_price(f, decided[f.id] or 0) for f in task.flights})
        x = net.vector(run)
        assert x is not None and feasible(net, x)
        assert round(sum(c * v for c, v in zip(net.m.cost, x, strict=True))) == run.cost.total


def test_pins_and_the_earliest_minute_are_kept(fixture_day):
    task = fixture_day
    a, b = task.flights[3], task.flights[10]
    sol = mip.solve(task, fixed={a.id: None, b.id: b.deadline}, earliest=task.start + 120)
    assert sol.deps[a.id] is None and sol.deps[b.id] == b.deadline
    assert all(d is None or d >= task.start + 120 for i, d in sol.deps.items() if i not in (a.id, b.id))
