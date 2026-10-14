"""The hard gate: the reference follower's decisions, confirmed watch by watch through the day and through the env's
tools, are never refused or held, freeze what the follower pinned, and fly to the follower's cost exactly. HiGHS can
return a different optimal solution to a watch from run to run, so each run is replayed against itself."""

import importlib.util
import json
from pathlib import Path

import pytest

from agentenv_airops.core.known import frozen, view
from agentenv_airops.core.model import load_pack
from agentenv_airops.core.reward import reward
from agentenv_airops.core.sim import execute
from agentenv_airops.day import _entry

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tasks.jsonl"
SMALL = "b6-jfk-20230626-1400-1700"


def load_script():
    spec = importlib.util.spec_from_file_location("live_e2e", ROOT / "scripts" / "live_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


e2e = load_script()


@pytest.fixture(scope="module")
def followed():
    return {}


def plans_of(task, cache: dict) -> tuple[list[dict], object]:
    if task.task_id not in cache:
        cache[task.task_id] = e2e.follower_plans(task)
    return cache[task.task_id]


@pytest.mark.parametrize("task_id", [t.task_id for t in load_pack(FIXTURES)])
def test_the_followers_decisions_replayed_through_the_day_cost_what_the_follower_costs(fixtures, followed, task_id):
    task = fixtures[task_id]
    plans, run = plans_of(task, followed)
    day, rows = e2e.played(task, plans)
    assert len(rows) == len(plans) == len(day.watches) and rows[0]
    assert day.refusals == [] and {c["by"] for c in day.changes} == {"planner"}
    assert {i: _entry(e) for i, e in day.plan.items()} == {
        i: _entry(e) for i, e in plans[-1].items() if _entry(e) != _entry(None)}
    pinned = set()
    for k, w in enumerate(day.watches[1:], 1):
        pinned |= frozen(task, execute(task, plans[k - 1], *view(task, w.t)), w.t)
        assert day.freezes[k]["frozen"] == sorted(pinned)
    ref = task.reference
    assert day.grade["cost"]["total"] == run.cost == execute(task, plans[-1]).cost.total
    assert day.grade["reward"] == round(reward(run.cost, ref["anchor"], ref["floor"]), 6)
    assert day.audit() == {"ok": True, "problems": []}


async def test_the_same_through_the_tools_and_the_clock(fixtures, followed, env, tools, gateway, http):
    task = fixtures[SMALL]
    plans, run = plans_of(task, followed)
    played, rows = e2e.played(task, plans)
    env.live_load(SMALL)
    await env.sync_time(f"{gateway.url}/clock/time")
    for k, changes in enumerate(rows):
        if changes:
            out = await tools("confirm_changes", changes=changes)
            assert (out["refused"], out["problems"], out["held_to_freeze_line"]) == ([], [], [])
        out = await tools("advance")
        if k < len(rows) - 1:
            assert out["watch"] == k + 1
    assert out == {"done": True, "cost": run.cost, "reward": played.grade["reward"], "messages": []}
    assert len(gateway.puts) == len(rows) - 1
    data = (await http.post("/agentenv", json={"jsonrpc": "2.0", "id": 1, "method": "data/get"})).json()
    got = data["result"]["parts"][0]["data"]
    assert got["grade"] == json.loads(json.dumps(played.grade))
    assert got["calls_used"] == sum(bool(c) for c in rows) + len(rows)
