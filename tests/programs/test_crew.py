from dataclasses import replace

import pandas as pd
import pytest
from day_helpers import BASE, CHAIN, ZONES, chain_day, legs, reserved

from agentenv_airops.programs import references, repairs
from agentenv_airops.programs.layers import crew
from agentenv_airops.programs.layers.crew import Rule
from agentenv_airops.programs.model import EXTENSION, REST, Crew


def legal(crews: dict, x: pd.DataFrame, buffer: int) -> bool:
    """Every duty ends on schedule `buffer` inside its limit, and every crew deadline is the report + the limit + the
    extension less the leg's block, from an outstation, with the rest REST after it."""
    by = {}
    for i, c in crews.items():
        by.setdefault(c.pairing, []).append(i)
    for ids in by.values():
        c = crews[ids[0]]
        if max(int(x.sd[i] + x.CRSElapsedTime[i]) for i in ids) + buffer > c.report + c.fdp:
            return False
        if c.report != int(x.sd[min(ids, key=lambda i: x.sd[i])]) - repairs.REPORT or len(ids) != c.segments:
            return False
    return all((c.deadline, c.rest) == ((None, None) if x.Origin[i] in repairs.BASES else
                                        (c.report + c.fdp + EXTENSION - int(x.CRSElapsedTime[i]),
                                         c.report + c.fdp + EXTENSION - int(x.CRSElapsedTime[i]) + REST))
               for i, c in crews.items())


def test_a_long_outstation_chain_is_split_where_edgars_runs_past_its_limit_on_schedule():
    x = legs(CHAIN)
    edgar = repairs.crews(x, BASE, repairs.BASES, ZONES)
    assert {c.pairing for c in edgar.values()} == {"N1/0"} and edgar[5].deadline < 1230
    v4 = crew.crews(x, BASE, repairs.BASES, ZONES)
    assert [v4[k].pairing for k in range(6)] == ["N1/0"] * 4 + ["N1/1"] * 2
    assert (v4[0].report, v4[0].fdp, v4[4].report, v4[4].fdp) == (455, 780, 1035, 720)
    assert legal(v4, x, crew.BUFFER) and not legal(edgar, x, 0)
    assert crew.crews(x, BASE, repairs.BASES, ZONES, buffer=0, max_segments=6)[4].pairing == "N1/0"


def test_a_duty_takes_no_leg_that_would_end_inside_the_buffer_of_its_limit():
    rows = [("BUF", "DCA", 500, 80), ("DCA", "PWM", 640, 90), ("PWM", "BUF", 800, 70), ("BUF", "RDU", 960, 260)]
    x = legs(rows)
    # Reported at 455 (07:35), four legs may run 13 h, to 1235: the fourth ends at 1220, inside it but not by 30.
    assert [c.pairing for c in crew.crews(x, BASE, (), ZONES, buffer=0).values()] == ["N1/0"] * 4
    assert [c.pairing for c in crew.crews(x, BASE, (), ZONES, buffer=30).values()] == ["N1/0"] * 3 + ["N1/1"]
    assert legal(crew.crews(x, BASE, (), ZONES, buffer=30), x, 30)


def test_a_duty_holds_at_most_max_segments_and_a_rest_long_gap_starts_another():
    rows = [("BUF", "DCA", 300, 40), ("DCA", "PWM", 360, 40), ("PWM", "BUF", 420, 40), ("BUF", "DCA", 480, 40),
            ("DCA", "PWM", 540, 40), ("PWM", "BUF", 1180, 40)]
    x = legs(rows)
    assert [c.pairing for c in crew.crews(x, BASE, (), ZONES, max_segments=3).values()] == \
        ["N1/0"] * 3 + ["N1/1"] * 2 + ["N1/2"]
    six = crew.crews(x, BASE, (), ZONES, max_segments=6)
    assert [c.pairing for c in six.values()] == ["N1/0"] * 5 + ["N1/1"]
    assert 1180 - (540 + 40) == REST and six[0].segments == 5


def test_every_base_departure_starts_a_duty_and_gets_its_deadline_under_a_pool():
    rows = [("JFK", "BUF", 300, 70), ("BUF", "JFK", 420, 70), ("JFK", "DCA", 540, 60), ("DCA", "BOS", 660, 80),
            ("BOS", "PWM", 800, 50)]
    v4 = crew.crews(legs(rows), BASE, repairs.BASES, ZONES)
    assert [v4[k].pairing for k in range(5)] == ["N1/0", "N1/0", "N1/1", "N1/1", "N1/2"]
    assert [v4[k].deadline is None for k in range(5)] == [True, False, True, False, True]
    task = chain_day(Rule("v4"))
    pooled = reserved(task, {"JFK": 0})
    f = task.flight(0)
    assert task.crew(f).deadline is None and pooled.reservable(f)
    assert pooled.crew(f).deadline == f.crew.report + f.crew.fdp + EXTENSION - f.sched_block >= f.sched_dep + 60


def test_the_rule_is_edgars_crews_by_default_and_names_its_mode():
    x = legs(CHAIN)
    assert Rule().crews(x, BASE, repairs.BASES, ZONES) == repairs.crews(x, BASE, repairs.BASES, ZONES)
    assert Rule("v4", 30, 5).crews(x, BASE, repairs.BASES, ZONES) == crew.crews(x, BASE, repairs.BASES, ZONES, 30, 5)
    assert Rule().to_dict() == {"crew": "edgar", "buffer": None, "max_segments": None}
    assert Rule("v4").to_dict() == {"crew": "v4", "buffer": 60, "max_segments": 4}
    with pytest.raises(ValueError):
        Rule("strict")


def test_the_calm_day_of_a_v4_crewed_chain_cancels_nothing_where_edgars_must():
    for pools in ({}, {"JFK": 0}):
        assert references.calm(reserved(chain_day(Rule("v4")), pools), 60).cancelled == 0
    assert references.calm(chain_day(Rule()), 60).cancelled >= 1


def test_a_v4_crewed_build_changes_only_the_crews_and_without_bases_nothing(task, crewed, v4crewed, v4plain):
    assert v4crewed.bases == crewed.bases == repairs.BASES
    assert replace(v4crewed, bases=(), flights=tuple(replace(f, crew=None) for f in v4crewed.flights)) == task
    assert v4plain.to_json() == task.to_json()
    assert all(isinstance(f.crew, Crew) for f in v4crewed.flights)
    for f in v4crewed.flights:
        c = f.crew
        assert c.report <= f.sched_dep - repairs.REPORT
        assert f.sched_dep + f.sched_block + crew.BUFFER <= c.report + c.fdp
        assert (c.deadline is None) == (f.orig in repairs.BASES)


def test_the_calm_day_of_the_v4_crewed_fixture_day_cancels_nothing_for_crews(task, v4crewed):
    none = references.calm(task, 120)
    for t in (v4crewed, reserved(v4crewed, dict.fromkeys(v4crewed.bases, 0))):
        calm = references.calm(t, 120)
        assert (sorted(i for i, d in calm.solution.deps.items() if d is None), calm.ferries) == \
            (sorted(i for i, d in none.solution.deps.items() if d is None), none.ferries)


def test_a_delay_still_times_a_v4_crew_out_past_its_limit():
    task = chain_day(Rule("v4"), lb=450, slack=900)
    run = references.naive(task)
    assert run.flights[3].status == "auto_cancelled" and task.flight(3).crew.deadline == 1285
    assert references.naive(chain_day(Rule("v4"), slack=900)).flights[3].status == "flown"
