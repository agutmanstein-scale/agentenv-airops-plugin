"""airops-desk on scripted model turns from the fake LiteLLM against a served airops-occ day (no gateway, so every tool
is listed): the model sees its page as the result of end_turn, never sees wait_turn or resign, and the day goes on
without a desk that stops at its cost cap, which it leaves as played. No model is called."""

import json
import sys
from pathlib import Path

import pytest
from agentenv_protocol.a2a_agent import TaskOutcome, TaskRequest, TextPart
from occ_helpers import desk_agent, serve, storm, write_pack

from agentenv_airops.occ.day import OccDay
from agentenv_airops.occ.live import OccEnv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fake_litellm import FakeLiteLLM  # noqa: E402

pytestmark = pytest.mark.anyio
SOL = "openai/gpt-6.1-sol"


def end(i: int, big: bool = False) -> dict:
    usage = {"input": 100000, "output": 10000} if big else {"input": 1000, "output": 100}
    return {"content": "", "reasoning": "", "stop": "completed", "usage": usage,
            "tool_calls": [{"id": f"call_{i}", "name": "end_turn", "arguments": json.dumps(
                {"note": None, "stand_down": False})}]}


async def run(tmp_path, monkeypatch, cap: str | None = None):
    """The one-desk day served, its advisories delivered by the env as each watch opens (no gateway, no trigger)."""
    monkeypatch.setenv("AIROPS_OCC_PACK", str(write_pack(tmp_path / "pack", storm())))
    opened = OccDay._open
    monkeypatch.setattr(OccDay, "_open", lambda self, by: opened(self, "env"))
    env = OccEnv()
    mcp = env.create_app()
    env.occ_load(storm().task.task_id, "one")
    with serve(mcp.http_app()) as url, FakeLiteLLM([end(i, cap is not None) for i in range(60)]) as fake:
        monkeypatch.setenv("LITELLM_BASE_URL", fake.url)
        monkeypatch.setenv("LITELLM_API_KEY", "sk-fake")
        if cap:
            monkeypatch.setenv("AIROPS_MAX_COST_USD", cap)
        result = await desk_agent.AirOpsDesk().run(TaskRequest(
            task_id="t", context_id="c", parts=(TextPart(text="You run both desks."),),
            config=desk_agent.DeskConfig(model=SOL, system_prompt="The rules.", role="both", max_turns=200),
            mcp_servers={"airops-occ": {"url": f"{url}/mcp", "headers": {}}}))
    return result, fake, env


async def test_the_model_reads_each_page_as_end_turns_result(tmp_path, monkeypatch):
    result, fake, env = await run(tmp_path, monkeypatch)
    s = result.parts[1].data["structured_output"]
    day = env.day
    assert result.outcome is TaskOutcome.SUCCEEDED and day.done and day.audit()["ok"]
    assert (s["end_reason"], s["desk"], s["policy"], s["desk_turns"]) == ("done", "one", "llm", len(day.turns))
    assert s["turns"] == len(day.turns) and s["context_max"] == 1000 and s["cost_usd"] == pytest.approx(
        len(day.turns) * (1000 * 2.0 + 100 * 10.0) / 1e6)
    first = fake.requests[0][2]
    assert first["input"][0]["content"][0]["text"].startswith("You run both desks.\n\n# Ops control, JetBlue")
    names = {t["name"] for t in first["tools"]}
    assert {"end_turn", "ops_confirm", "crew_confirm"} <= names and not {"wait_turn", "resign"} & names
    out = json.loads(fake.requests[1][2]["input"][-1]["output"])
    assert out["ended"] == "one" and out["page"].startswith("# Ops control") and out["watch"] == 1
    assert day.absent["one"]["reason"] == "done"


async def test_a_desk_at_its_cost_cap_leaves_and_the_day_goes_on_without_it(tmp_path, monkeypatch):
    result, fake, env = await run(tmp_path, monkeypatch, cap="1.0")
    s = result.parts[1].data["structured_output"]
    assert s["end_reason"] == "cost_cap" and result.outcome is TaskOutcome.SUCCEEDED and len(fake.requests) == 3
    assert env.day.absent["one"]["reason"] == "cost_cap" and env.day.done and env.day.audit()["ok"]
    assert env.day.grade["end_reason"] == "done" and env.day.grade["cost"] == env.day.w.refs["idle"]
