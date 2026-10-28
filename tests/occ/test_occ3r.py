"""airops-occ3 in the realistic world (b6-v3r): a day with no desks graded on JetBlue's own cost as the realism report
priced the standard desks; the day as known holding a breakdown back until it happens, and no page before it changing
with it; the desks' cost lines in dollars; the bundle's realistic days and the env loading one; a named replay of a
model's day (the audit's A5) handing out the aircraft as the day did, which only the day's order makes so."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from occ_helpers import ROLE

from agentenv_airops import realworld
from agentenv_airops.occ import run, tasks3
from agentenv_airops.occ.day3r import CRITERION, OccDay3R
from agentenv_airops.occ.live3 import OccEnv3
from agentenv_airops.occ.pack import PACK3R, Pack3R
from agentenv_airops.programs import crewday, crewplan, crewsim

END = {"note": None, "stand_down": False}
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture(scope="module")
def pack():
    return Pack3R(PACK3R)


def task_on(pack, date: str) -> str:
    return next(k for k, v in pack.tasks.items() if v["date"] == date)


def test_a_day_with_no_desks_is_graded_on_jetblues_cost_as_the_report_priced_the_standard_desks(pack):
    w = pack.world(task_on(pack, "2025-02-06"))
    refs = w.real["refs"]
    d = OccDay3R(w, "idle")
    g = d.grade
    assert abs(g["cost"] - refs["priced"]["desks"]) < 0.5
    assert g["best"] == refs["top"] and g["anchor"] == refs["jetblue"]
    assert g["reward"] == round(realworld.reward(g["cost"], refs["top"], refs["jetblue"]), 6)
    assert 0.5 < g["reward"] < 1.0 and g["reward_L"] is None and g["criterion"] == CRITERION
    assert set(g["P"]) == {"cost", "reward", "reward_L", "best"} and g["P"]["cost"] > g["cost"]
    assert g["costs"]["total"] == g["realistic"]["usd"] and d.audit()["ok"]


def test_the_day_as_known_holds_a_breakdown_back_until_it_happens(pack):
    w = pack.world(task_on(pack, "2026-07-09"))
    d = OccDay3R(w, "idle")
    tail, at = max(w.real["broken"].items(), key=lambda kv: kv[1])
    assert d.world.broken == w.real["broken"] and d.world.swaps == realworld.SWAPS and d.world.gates
    assert tail not in d.known_world(at - 1).broken and d.known_world(at).broken[tail] == at
    assert d.known_world(at - 1).gates == d.world.gates and d.known_world(at - 1).swaps == realworld.SWAPS


def pages_until(w, stop: int) -> list[str]:
    d = OccDay3R(w, "one")
    out = []
    while not d.done and d.t <= stop:
        for n in list(d.pending):
            d.notice(n, "trigger")
        out.append(d.call(ROLE["one"], "wait_turn", {})["page"])
        d.call(ROLE["one"], "end_turn", END)
    return out


def test_no_page_before_a_breakdown_changes_with_it_and_the_cost_lines_are_jetblues_in_dollars(pack):
    w = pack.world(task_on(pack, "2026-07-15"))
    used = {i for t in w.task.tails if t.tail in w.real["broken"] for i in t.legs}
    tail = next(t for t in w.task.tails if t.tail not in w.real["broken"] and t.legs and
                not used & set(t.legs) and w.task.flight(t.legs[0]).sched_dep > w.task.start + 240)
    at = w.task.flight(tail.legs[0]).sched_dep - 60
    more = replace(w, real={**w.real, "broken": {**w.real["broken"], tail.tail: at}})
    a, b = pages_until(w, at + 60), pages_until(more, at + 60)
    before = [k for k, t in enumerate(OccDay3R(w, "idle").times) if t < at][-1] + 1
    assert a[:before] == b[:before] and a[before:] != b[before:]
    assert "JetBlue's cost of the plan as projected: $" in a[0] and "pax-min" not in a[0]
    assert "Crew cost of the day as projected: deadhead $" in a[0] and "Passengers as projected: of " in a[0]


def test_the_bundle_writes_realistic_days_that_load_the_realistic_world_under_jetblues_cost(pack, tmp_path):
    task = pack.world(task_on(pack, "2023-06-27")).task
    steps = tasks3.steps(task, "joint", realistic=True)
    [load] = [s for s in steps if s["type"] == "apply_server_config" and s["id"] == "load-task"]
    assert load["directives"][0]["args"] == {"task_id": task.task_id, "mode": "joint", "realistic": True}
    plays = [s for s in steps if s["type"] == "prompt_agent"]
    assert all(s["prompt_id"].endswith(f"-joint-real-{s['agent_name'].split('-')[0]}") for s in plays)
    assert all("JetBlue's own, in dollars" in s["system_prompt"] and "passenger-minutes" not in s["system_prompt"]
               and "aircraft that break" in s["system_prompt"] for s in plays)
    assert tasks3.RULES_R["ops"] != tasks3.RULES["ops"] and "interchangeable" not in tasks3.RULES_R["ops"]
    names = tasks3.generate(tmp_path, task_ids=[task.task_id], realistic=True, modes=("one",))
    assert names == [f"{task.task_id}-one-real"] and (tmp_path / "tasks" / f"{names[0]}.json").exists()


def test_the_env_loads_a_realistic_day(pack):
    env = OccEnv3()
    out = env.occ_load(task_on(pack, "2023-07-01"), "one", realistic=True)
    assert out["realistic"] and isinstance(env.day, OccDay3R) and env.day.w.real is not None


def test_a_named_replay_hands_out_the_aircraft_as_the_day_did_where_only_the_hubs_swap(pack):
    raw = json.loads(FIXTURES.joinpath("occ3r-2025-01-19-astra-plan.json").read_text())
    w = pack.world(raw["task_id"])
    plan = {int(k) if k.isdigit() else k: v for k, v in raw["plan"].items()}
    world = realworld.truth(w.task, w.real["gates"], w.real["broken"])
    day = run.truth(w, plan, world)
    named = crewday.named(day)
    again = crewsim.execute(w.task, named, w.units, world, w.local, fill=False, airports=w.airports, pax=w.passengers)
    assert again.run.rows() == day.run.rows() and crewplan.worth(again, False) == crewplan.worth(day, False)
    unordered = {i: {k: v for k, v in e.items() if k != "order"} if isinstance(i, int) else e for i, e in named.items()}
    loose = crewsim.execute(w.task, unordered, w.units, world, w.local, fill=False, airports=w.airports,
                            pax=w.passengers)
    assert loose.run.rows() != day.run.rows()
