"""Recorded runs replayed through a Day: the e2e run's record byte for byte to its reward, and scripted records made on
the real env in process (refused, bad and late calls, string changes, planning calls run out, a frozen flight, an
agent that stops early) to the env's own day, field for field; a record the day doesn't reproduce raises."""

import copy
import json

import pytest
from recorder import DAY, record

from agentenv_airops.day import Day
from agentenv_airops.episodes import ReplayError, replay
from agentenv_airops.schedule import TOOLS

ID = "b6-jfk-20240109-1800"
CANCEL_F6 = {"flight": "f6", "action": "cancel", "not_before": None}


def test_the_e2e_record_replays_byte_for_byte_to_its_reward(task, e2e):
    played = replay(task, e2e["messages"])
    day = played.day
    assert (day.done, day.end_reason, day.grade["reward"]) == (True, "done", e2e["reward"]) == (True, "done", 0.840588)
    assert day.grade["cost"]["total"] == 691780 and day.audit() == {"ok": True, "problems": []}
    env_calls = [m for m in e2e["messages"] if m["role"] == "tool" and m["name"] in TOOLS]
    assert [(s["tool"], s["result"]) for s in played.steps] == [
        (m["name"], json.loads(m["content"])) for m in env_calls]
    assert [s["t"] for s in played.steps] == sorted(s["t"] for s in played.steps)
    assert {s["watch"] for s in played.steps} == set(range(len(day.watches)))
    assert [e["via"] for e in day.log if e["watch"] > 0] == ["trigger"] * sum(len(w.news) for w in day.watches[1:])
    assert day.calls == len(env_calls) and day.planning_calls == [1] * len(day.watches)


def test_a_record_the_day_does_not_reproduce_raises(task, e2e):
    messages = copy.deepcopy(e2e["messages"])
    confirm = next(m for m in messages if m["role"] == "tool" and m["name"] == "confirm_changes")
    confirm["content"] = confirm["content"].replace('"planning_calls_left":2', '"planning_calls_left":1')
    with pytest.raises(ReplayError, match="confirm_changes toolu_0_1 in watch 0 returned"):
        replay(task, messages)
    other = Day(task).task.to_dict()
    other["flights"][0]["lb"] += 30
    with pytest.raises(ReplayError):
        replay(type(task).from_dict(other), e2e["messages"])


def _frozen_at_watch_1(task, changes: list[dict]) -> str:
    day = Day(task)
    day.confirm(changes)
    day.open()
    return f"f{sorted(day.frozen)[0]}"


def test_a_scripted_day_replays_to_the_envs_own_day(task):
    first = [CANCEL_F6, {"flight": "f8", "action": "hold", "not_before": "21:30"}]
    frozen = _frozen_at_watch_1(task, first)
    turns = [
        [("get_situation", {}), ("check_plan", {"changes": [CANCEL_F6]}),
         ("confirm_changes", {"changes": json.dumps(first)}), ("check_plan", {"changes": 5}),
         ("get_situation", {"why": "refused"}), ("get_time", {}), ("advance", {})],
        [("confirm_changes", {"changes": [{"flight": "f99", "action": "cancel", "not_before": None},
                                          {"flight": "f0", "action": "hold", "not_before": "25:99"}]}),
         ("confirm_changes", {"changes": [{"flight": frozen, "action": "cancel", "not_before": None},
                                          {"flight": "f8", "action": "restore", "not_before": None}]}),
         ("confirm_changes", "{not json"), ("check_plan", {"changes": [CANCEL_F6]}),
         ("check_plan", {"changes": [CANCEL_F6]}), ("advance", {})],
        *([("get_situation", {}), ("advance", {})] for _ in range(7)),
        [("advance", {}), ("get_situation", {})],
    ]
    rec, data = record(DAY, ID, turns)
    assert data["end_reason"] == "done" and data["refusals"] and data["planning_calls"][:2] == [2, 3]
    played = replay(task, rec["messages"])
    assert json.loads(json.dumps(played.day.data())) == data
    refused = [s for s in played.steps if "error" in s["result"]]
    assert [(s["tool"], s["watch"]) for s in refused] == [("check_plan", 0), ("get_situation", 0), ("check_plan", 1)]
    assert refused[-1]["result"]["error"].startswith("no planning calls left")
    assert played.steps[2]["args"] == {"changes": first}
    sent = [name for t in turns[:-1] for name, args in t if name in TOOLS and isinstance(args, dict)]
    assert [s["tool"] for s in played.steps] == sent
    assert rec["messages"][-1]["content"] == '{"error": "the episode is over"}'


def test_a_day_the_agent_left_early_is_ended_as_end_day_ends_it(task):
    rec, data = record(DAY, ID, [[("confirm_changes", {"changes": [CANCEL_F6]}), ("advance", {})], [("advance", {})]])
    assert (data["end_reason"], data["audit"]["ok"]) == ("end_day", True)
    played = replay(task, rec["messages"])
    assert json.loads(json.dumps(played.day.data())) == data
    assert {e["via"] for e in played.day.log if e["watch"] > 2} == {"env"}
