from dataclasses import replace

import pytest
from day_helpers import day, flight, shortened, stop, tail
from program_helpers import adv

from agentenv_airops.programs import references, sim
from agentenv_airops.programs.model import Outcome


@pytest.fixture(scope="module")
def fixture_day(task):
    return shortened(task)


def stopped_then_released():
    """A ground stop issued to 10:00 and cancelled at 03:20; the desk sees the cancellation only from 03:20 on."""
    flights = [flight(0, "BUF", "JFK", 100, 60, deadline=500), flight(1, "JFK", "MCO", 500, 150)]
    return day(flights, [tail("N1", "BUF", end="JFK"), tail("N2", "JFK", end="MCO")], stops=(stop(0, 200),),
               advisories=(adv("s1", "gs", -30, 0, 600), adv("c1", "gs_cnx", 200, 200, None)))


def delay_revealed_late():
    """A flight whose 5-h delay, known two hours before its departure, puts it past its deadline."""
    return day([flight(0, "DCA", "JFK", 400, 60, lb=300, deadline=640)], [tail("N1", "DCA")])


def test_watches_run_hourly_until_every_delay_is_known():
    task = stopped_then_released()
    assert references.watch_times(task) == [0, 60, 120, 180, 240, 300, 360, 420]
    assert references.watch_times(delay_revealed_late()) == [0, 60, 120, 180, 240, 300]


def test_a_confirmed_cancellation_is_final_even_when_the_day_turns_out_better():
    task = stopped_then_released()
    followed = references.follow(task)
    assert followed.plan[0] == {"cancel": True, "decided": 0} and followed.run.flights[0].status == "cancelled"
    assert [w["cancelled"] for w in followed.watches] == [[0]] + [[]] * 7
    assert references.rule(task).plan[0] == {"cancel": True, "decided": 0}
    assert references.naive(task).flights[0].dep == 200
    sol, run = references.anchor(task)
    assert run.flights[0].dep == 200 and sol.objective < followed.cost


def test_each_cancellation_is_priced_at_the_watch_that_confirms_it():
    task = delay_revealed_late()
    followed, rule = references.follow(task), references.rule(task)
    assert followed.plan[0] == rule.plan[0] == {"cancel": True, "decided": 300}
    assert followed.run.cost.notice == rule.run.cost.notice == 100 * (300 - 280)
    assert references.naive(task).cost.notice == 100 * (640 - 400 + 120)
    sol, run = references.anchor(task)
    assert sol.objective == run.cost.total == 52_300 and run.cost.notice == 0


def test_the_recorded_day_prices_jetblues_outcome_like_for_like():
    flown = flight(0, "BUF", "JFK", 100, 60, actual=Outcome(False, False, "", 100, 190, "JFK", "N1"))
    cancelled = flight(1, "JFK", "DCA", 300, 60, pax=50, actual=Outcome(True, False, "B", None, None, None, "N1"))
    diverted = flight(2, "JFK", "MCO", 180, 150, pax=10, actual=Outcome(False, True, "", 200, None, "PWM", "N2"))
    tails = [tail("N1", "BUF", end="JFK"), replace(tail("N2", "JFK", end="MCO"), actual_end="PWM")]
    got, ferries = references.recorded(day([flown, cancelled, diverted], tails))
    assert (got.delay, got.cancel, got.notice, ferries) == (100 * 30, 523 * 60, 10 * (200 - 60), 1)


def test_the_airline_replay_holds_jetblues_departures_and_claims_its_arrival_order(fixture_day):
    task = fixture_day
    plan = references.airline_plan(task)
    for f in task.flights:
        if f.actual.dep is None:
            assert plan[f.id] == {"cancel": True, "decided": f.sched_dep - 120}
        else:
            assert plan[f.id] == {"not_before": f.actual.dep, "claim": f.actual.dep + f.sched_block}
    replay = references.airline(task)
    assert replay.flew == sum(f.actual.dep is not None and f.actual.dep <= f.deadline for f in task.flights)
    assert 0 <= replay.auto <= replay.flew


def test_no_reference_costs_less_than_the_anchor(fixture_day):
    task = fixture_day
    sol, run = references.anchor(task)
    assert sol.proven and run.cost.total == sol.objective
    played = {"naive": references.naive(task), "airline": references.airline(task).run,
              "rule": references.rule(task).run, "follower": references.follow(task).run,
              "blind": references.follow(task, programs=False).run}
    assert references.below_anchor({k: r.cost.total for k, r in played.items()}, sol.objective) == {}
    assert all(references.seats_ok(task, r) for r in [run, *played.values()])
    flat = references.flat_anchor(task)
    assert flat.objective <= played["naive"].cost.flat and flat.objective <= sol.objective


def test_the_calm_day_flies_a_feasible_schedule_without_cancellations_or_ferries():
    flights = [flight(0, "JFK", "BUF", 100, 60, lb=90), flight(1, "BUF", "JFK", 200, 60, lb=50)]
    calm = references.calm(day(flights, [tail("N1", "JFK")]))
    assert (calm.cancelled, calm.ferries) == (0, 0) and calm.run.cost.total == 0
    assert calm.solution.objective == 0


def test_past_midnight_counts_hub_movements_after_2400(fixture_day):
    task = fixture_day
    run = sim.execute(task, {})
    late = sum((r.dep or 0) >= 1440 and task.flight(i).orig in ("JFK", "BOS") for i, r in run.flights.items())
    late += sum((r.arr or 0) >= 1440 and task.flight(i).dest in ("JFK", "BOS") for i, r in run.flights.items())
    assert references.past_midnight(task, run) == late
