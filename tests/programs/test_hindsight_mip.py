import random
from dataclasses import replace

import numpy as np
import pytest
from day_helpers import GDP, busy_day, crewed, day, flight, segment, shortened, stop, tail, three_into_a_program

from agentenv_airops.programs import mip, references, sim
from agentenv_airops.programs.cost import cancel_price
from agentenv_airops.programs.model import Busy, Move, Task


def days() -> dict[str, Task]:
    moved = [flight(0, "MCO", "JFK", 120, 150), flight(1, "JFK", "BUF", 100, 60), flight(2, "BUF", "JFK", 320, 60),
             flight(3, "JFK", "PWM", 140, 70, deadline=700), flight(4, "PWM", "JFK", 260, 70)]
    tails = [tail("N1", "JFK", repositions=(Move("BUF", "MCO", 300, 100),)),
             tail("N2", "JFK", busy=(Busy(3, 300),), end="BOS")]
    stopped = [flight(0, "BUF", "JFK", 120, 60, pax=150), flight(1, "DCA", "JFK", 110, 60, pax=40),
               flight(2, "JFK", "DCA", 250, 60), flight(3, "JFK", "BUF", 260, 60, pax=20)]
    waiting = [flight(0, "BUF", "JFK", 100, 60), flight(1, "DCA", "JFK", 150, 60), flight(2, "MCO", "JFK", 100, 150),
               flight(3, "PWM", "JFK", 120, 70), flight(4, "JFK", "BOS", 300, 60, deadline=900)]
    two = [tail("N1", "BUF", repositions=(Move("JFK", "MCO", 100, 50),)),
           tail("N2", "DCA", repositions=(Move("JFK", "PWM", 110, 60),))]
    return {"slots": three_into_a_program(lb=100),
            "stop": day(stopped, [tail("N1", "BUF", end="DCA"), tail("N2", "DCA", end="BUF")],
                        segments=(segment("g", 150, 1000, 150, 400, (190, 200, 260)),), stops=(stop(100, 160),)),
            "nominal": day([flight(0, "BUF", "JFK", 100, 60, lb=30), flight(1, "DCA", "JFK", 130, 60),
                            flight(2, "MCO", "JFK", 140, 60), flight(3, "JFK", "MCO", 200, 150, pax=180)],
                           [tail("N1", "BUF"), tail("N2", "DCA"), tail("N3", "MCO")], aar=1),
            "moves": day(moved, tails, segments=(GDP,)), "two_moves": day(waiting, two), "busy": busy_day(),
            "families": families(), "crew": crew()}


def crew() -> Task:
    """Behind a ground stop, N1's crew at BUF times out and rests to 730; N2 lands from DCA in time for JFK's reserves
    to take it to PWM, whose crew must leave by 480 or cancel, its rest ending past its deadline."""
    flights = [crewed(flight(0, "BUF", "JFK", 100, 60, lb=50, deadline=900), 130),
               crewed(flight(1, "JFK", "PWM", 250, 70, deadline=700), None),
               crewed(flight(2, "PWM", "JFK", 400, 70, deadline=640), 480),
               crewed(flight(3, "DCA", "JFK", 120, 60, deadline=820), 200)]
    return day(flights, [tail("N1", "BUF", end="JFK"), tail("N2", "DCA", end="JFK")], stops=(stop(100, 160),))


def families() -> Task:
    """A320s and E190s whose blocks overrun, behind a ground stop, each family with a ferry to end the day."""
    def e190(f):
        return replace(f, family="E190")

    flights = [flight(0, "BUF", "JFK", 100, 80, lb=20, sched_block=60), e190(flight(1, "DCA", "JFK", 110, 90, pax=90,
                                                                                     sched_block=70)),
               flight(2, "JFK", "MCO", 250, 160, sched_block=150), e190(flight(3, "JFK", "PWM", 260, 70, pax=60))]
    tails = [tail("N1", "BUF", end="BOS"), replace(tail("N2", "DCA", end="BOS"), family="E190", seats=100)]
    return replace(day(flights, tails, stops=(stop(100, 160),)), turn={"A320": 30, "E190": 25})


@pytest.fixture(scope="module", params=["task", "crewed"])
def fixture_day(request) -> Task:
    """The fixture day, and the same day with the crew model."""
    return shortened(request.getfixturevalue(request.param))


def feasible(net: mip.Network, x) -> bool:
    return all(lo - 1e-6 <= sum(v * x[c] for c, v in coefs.items()) <= up + 1e-6 for coefs, lo, up in net.m.rows)


def replays(task: Task, sol: mip.Solution) -> bool:
    run = sim.execute(task, sol.plan(task.start))
    return run.cost.total == sol.objective and all(
        (run.flights[i].dep if run.flights[i].status == "flown" else None) == d for i, d in sol.deps.items())


@pytest.mark.parametrize("name", ["slots", "stop", "nominal", "moves", "two_moves", "busy", "families", "crew"])
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


@pytest.mark.parametrize("name", ["slots", "stop", "nominal", "moves", "two_moves", "busy", "families", "crew"])
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


@pytest.mark.parametrize("name", ["slots", "stop", "moves", "busy", "crew"])
def test_a_day_with_its_history_pinned_is_a_solution_of_the_model_with_those_pins(name):
    task = days()[name]
    first = sim.execute(task, {})
    plan = {i: {"left": r.dep - 5} for i, r in first.flights.items() if r.status == "flown" and i % 2 == 0}
    run = sim.execute(task, plan)
    decided = {f.id: run.flights[f.id].decided for f in task.flights}
    net = mip.Network(task, price={f.id: cancel_price(f, decided[f.id] or 0) for f in task.flights},
                      fixed={i: run.flights[i].dep for i in plan})
    x = net.vector(run)
    assert x is not None and feasible(net, x)
    assert round(sum(c * v for c, v in zip(net.m.cost, x, strict=True))) == run.cost.total


def test_a_canonical_pass_out_of_time_keeps_the_proven_plan_flagged_as_not_canonical(monkeypatch):
    task = days()["slots"]
    proven = mip.solve(task, canonical=False)
    assert mip.solve(task).canonical and not proven.canonical
    run, calls = mip._Started.run, []

    def out_of_time(self, *args):
        calls.append(args)
        got = run(self, *args)
        return ("Time limit reached", *got[1:5], np.zeros_like(got[5])) if len(calls) == 2 else got

    monkeypatch.setattr(mip._Started, "run", out_of_time)
    sol = mip.solve(task)
    assert len(calls) == 2 and sol.proven and not sol.canonical
    assert (sol.status, sol.objective, sol.deps) == ("Optimal", proven.objective, proven.deps)


def test_a_coarse_step_keeps_every_slots_edct():
    task = three_into_a_program()
    sol = mip.solve(task, step=7)
    assert sol.objective == mip.solve(task).objective and replays(task, sol)
    assert {d for d in sol.deps.values()} == {120, 180, 240}


def test_pins_and_the_earliest_minute_are_kept(fixture_day):
    task = fixture_day
    a, b = task.flights[3], task.flights[10]
    sol = mip.solve(task, fixed={a.id: None, b.id: b.deadline}, earliest=task.start + 120)
    assert sol.deps[a.id] is None and sol.deps[b.id] == b.deadline
    assert all(d is None or d >= task.start + 120 for i, d in sol.deps.items() if i not in (a.id, b.id))
