from day_helpers import day, flight, tail

from agentenv_airops.programs import probe, references, sim
from agentenv_airops.programs.model import Outcome


def cancelled(i, orig, dest, dep, block=60):
    return flight(i, orig, dest, dep, block, actual=Outcome(True, False, "B", None, None, None, "N1"))


def out_and_back():
    """N1 flies JFK-BUF and back; JetBlue cancelled the JFK-BOS turn after it (the aircraft is due at JFK)."""
    return day([flight(0, "JFK", "BUF", 100, 60), flight(1, "BUF", "JFK", 200, 60),
                cancelled(2, "JFK", "BOS", 320), cancelled(3, "BOS", "JFK", 440)],
               [tail("N1", "JFK", end="JFK")])


def test_an_out_and_back_flies_for_free_only_as_a_pair_and_its_restore_counts_once():
    task = out_and_back()
    plan = references.airline_plan(task)
    base = sim.execute(task, plan)
    alone = probe.restore(task, plan, base, [2])
    assert alone.flew and not alone.knocked and alone.d_ferry > 0 and alone.verdict == "strands"
    assert probe.restore(task, plan, base, [3]).verdict == "forced"
    assert probe.partner(task.flight(3), [task.flight(2), task.flight(3)]) == task.flight(2)
    p = probe.probe(task)
    assert p.rows == {2: "pair_clean", 3: "pair_clean"} and p.restores == {(2, 3): "pair_clean"}
    assert (p.cancelled, p.feasible, p.feasible_rows) == (2, 1, 2)
    assert p.to_dict()["pairs"] == 1 and p.to_dict()["restore_verdicts"] == {"pair_clean": 1}


def test_a_restore_that_takes_a_flown_flights_aircraft_displaces_it():
    task = day([cancelled(0, "JFK", "BOS", 100), flight(1, "JFK", "PWM", 110, 60, deadline=200)],
               [tail("N1", "JFK", end="BOS")])
    p = probe.probe(task)
    assert p.rows == {0: "displaces"} and p.feasible == 0
    plan = references.airline_plan(task)
    r = probe.restore(task, plan, sim.execute(task, plan), [0])
    assert r.knocked == (1,) and r.flew


def test_restoring_nothing_is_clean_and_a_day_without_cancellations_has_none():
    task = out_and_back()
    plan = references.airline_plan(task)
    none = probe.restore(task, plan, sim.execute(task, plan), [])
    assert none.verdict == "clean" and none.d_cost == 0
    flown = day([flight(0, "JFK", "BUF", 100, 60)], [tail("N1", "JFK", end="BUF")])
    assert probe.probe(flown).to_dict() == {"cancelled": 0, "feasible": 0, "feasible_rows": 0, "restores": 0,
                                            "pairs": 0, "verdicts": {}, "restore_verdicts": {}}


def test_a_restore_that_moves_another_flight_past_shift_ok_displaces_it():
    late = probe.Restore((5,), True, (), probe.SHIFT_OK + 1, 0, 0)
    assert late.verdict == "displaces" and probe.Restore((5,), True, (), probe.SHIFT_OK, 0, 0).verdict == "clean"
    assert probe.Restore((5,), False, (), 0, 0, 0).verdict == "forced"
