import math
import random

import pytest
from day_helpers import network_day, random_day
from test_reference_plays import committed_then_stopped, released_early, stopped_then_released

from agentenv_airops.core.known import FREEZE
from agentenv_airops.core.sim import AUTO_CANCELLED
from agentenv_airops.programs import references, sim
from agentenv_airops.programs.cost import NOTICE
from agentenv_airops.v3day.day import Day, play


def nothing(day: Day) -> dict:
    return {}


def airline(day: Day) -> dict:
    """JetBlue's decisions: its recorded departures as holds from the first watch, each cancellation at the last
    watch at or before it was due (sched - NOTICE)."""
    upto = math.inf if day.last else day.watches[day.watch + 1].t
    return {f.id: {"cancel": True} if f.actual.dep is None else {"not_before": f.actual.dep}
            for f in day.task.flights if f.actual.dep is not None or max(f.sched_dep - NOTICE, day.task.start) < upto}


def airline_expected(task) -> dict:
    times = references.watch_times(task)
    return {f.id: {"not_before": f.actual.dep} if f.actual.dep is not None else
            {"cancel": True, "decided": max(t for t in times if t <= max(f.sched_dep - NOTICE, task.start))}
            for f in task.flights}


def rule(day: Day) -> dict:
    """references.rule as a desk: cancel what the plan auto-cancels on the day as known while it is still open."""
    run = day.project(day.plan)
    return day.plan | {i: {"cancel": True} for i, r in run.flights.items()
                       if r.status == AUTO_CANCELLED and i not in day.frozen and r.decided >= day.t + FREEZE}


def hand_and_random_days():
    rng = random.Random(9)
    return ([stopped_then_released(), committed_then_stopped(), released_early()]
            + [random_day(rng) for _ in range(12)] + [network_day(rng) for _ in range(12)])


@pytest.mark.parametrize("name", ["task", "crewed"])
def test_airline_replay_through_the_day_costs_what_the_sim_says(name, request):
    task = request.getfixturevalue(name)
    d = play(task, airline, anchor=0)
    expected = airline_expected(task)
    assert d.refusals == [] and [c for c in d.changes if c["by"] == "freeze_line"] == []
    assert d.plan == expected and d.result["cost"] == sim.execute(task, expected).cost.to_dict()


def test_the_airline_replay_on_random_network_days_costs_what_the_sim_says():
    rng = random.Random(4)
    for _ in range(20):
        task = network_day(rng)
        d = play(task, airline, anchor=0)
        assert d.plan == airline_expected(task) and d.refusals == []


@pytest.mark.parametrize("name", ["task", "crewed"])
def test_doing_nothing_on_the_fixture_costs_the_naive_day(name, request):
    task = request.getfixturevalue(name)
    d = play(task, nothing, anchor=0)
    assert d.plan == {} and d.result["cost"]["total"] == d.result["naive"] == references.naive(task).cost.total


def test_doing_nothing_costs_the_naive_day_and_scores_zero():
    for task in hand_and_random_days():
        d = play(task, nothing)
        assert d.plan == {} and d.result["cost"]["total"] == d.result["naive"] == references.naive(task).cost.total
        assert d.result["reward"] == (1.0 if d.result["naive"] <= d.result["anchor"] else 0.0)


def test_the_rule_played_through_the_day_is_the_rule_reference():
    """Except on day 15, where the reference's cancellation of f12 at 08:00 reroutes N1 and auto-cancels f6, which
    left at 03:38: the desk can't reach back past the day so far, so the Day refuses it."""
    differ = []
    for k, task in enumerate(hand_and_random_days()):
        d, ref = play(task, rule, anchor=0), references.rule(task)
        if d.plan == ref.plan:
            assert d.result["cost"]["total"] == ref.cost and d.refusals == []
        else:
            differ.append(k)
            assert {r["flight"] for r in d.refusals} == {"f12"} and "move f6" in d.refusals[0]["reason"]
            assert d.plan == {i: e for i, e in ref.plan.items() if i != 12}
            assert sim.execute(task, ref.plan).flights[6].status == AUTO_CANCELLED
            assert sim.execute(task, d.plan).flights[6].dep == 218
    assert differ == [15]
