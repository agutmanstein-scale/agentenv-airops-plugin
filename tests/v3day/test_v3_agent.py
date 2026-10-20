"""airops-llm on scripted turns from the fake LiteLLM plays a v3 day end to end against a real airops-v3-day env
serving the fixture slice's JFK evening: the v3 rules and briefing as its prompts, a cancellation at the first watch,
then advance to the last watch's done. The reward it reports is the env's grade, and the grade's cost is
sim.execute of the plan on the truth. No model is called."""

import json

import airops_llm
import pytest
from agentenv_protocol.a2a_agent import TaskOutcome, TaskRequest, TextPart
from fake_litellm import FakeLiteLLM
from served_v3 import served_v3

from agentenv_airops.programs import sim
from agentenv_airops.v3day import tasks as v3_tasks
from agentenv_airops.v3day.clock import virtual_time
from agentenv_airops.v3day.day import Day
from agentenv_airops.v3day.grade import grade
from agentenv_airops.v3day.live import ENV

pytestmark = pytest.mark.anyio
SONNET = "anthropic/claude-sonnet-5-5"


def call(name: str, arguments: dict, i: int) -> dict:
    return {"id": f"call_{name}_{i}", "name": name, "arguments": json.dumps(arguments)}


def turn(*calls: dict) -> dict:
    return {"content": "", "reasoning": "", "stop": "tool_use", "tool_calls": list(calls), "usage": {}}


@pytest.fixture(autouse=True)
def environ(monkeypatch):
    for var in ["LITELLM_BASE_URL", "LITELLM_API_KEY", "AIROPS_MAX_COST_USD", "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "HF_BILL_TO"]:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("name", ["task", "crewed"])
async def test_an_llm_plays_a_v3_day_through_the_server_graded_on_the_truth(name, request, monkeypatch, tmp_path):
    task = request.getfixturevalue(name)
    watches = len(Day(task).watches)
    cancel = [{"flight": "f3", "action": "cancel", "not_before": None}]
    turns = [turn(call("get_situation", {}, 0), call("check_plan", {"changes": cancel}, 1),
                  call("confirm_changes", {"changes": cancel}, 2), call("advance", {}, 3))]
    turns += [turn(call("advance", {}, 4 + k)) for k in range(watches - 1)]
    with served_v3(monkeypatch, tmp_path, [task]) as (url, env, puts), FakeLiteLLM(turns) as fake:
        monkeypatch.setenv("LITELLM_BASE_URL", fake.url)
        monkeypatch.setenv("LITELLM_API_KEY", "sk-fake")
        result = await airops_llm.AirOpsLLM().run(TaskRequest(
            task_id="t", context_id="c", parts=(TextPart(text=v3_tasks.briefing(Day(task))),),
            config=airops_llm.AirOpsConfig(model=SONNET, system_prompt=v3_tasks.rules(task),
                                           max_turns=v3_tasks.max_turns(Day(task))),
            mcp_servers={ENV: {"url": f"{url}/mcp"}}))
    d, summary = env.day, result.parts[1].data["structured_output"]
    first = fake.requests[0][2]
    assert first["system"] == v3_tasks.rules(task)
    assert first["messages"][0]["content"][0]["text"] == v3_tasks.briefing(Day(task))
    assert result.outcome is TaskOutcome.SUCCEEDED and len(fake.requests) == watches
    assert (summary["end_reason"], summary["checks"], summary["confirms"]) == ("done", 1, 1)
    plan = {3: {"cancel": True, "decided": task.start + 2}}
    assert d.done and d.end_reason == "done" and d.plan == plan and d.refusals == []
    assert d.result == grade(task, plan) and d.result["cost"] == sim.execute(task, plan).cost.to_dict()
    assert summary["reward"] == d.result["reward"]
    assert puts == [{"virtual_time": virtual_time(task, w.t), "virtual_seconds_per_real_second": 0}
                    for w in d.watches[1:]]
