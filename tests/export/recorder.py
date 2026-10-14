"""airops-llm's record of a scripted planner, made on the real env in process: each call goes through the env's MCP
tools (argument validation, the middleware's count), its text recorded as the agent records it (an error as
{"error": text}), each watch's news delivered through airops_notice after its advance as the triggers deliver it, and
the gateway clock answered in process. Calls the agent never sends (arguments that aren't a JSON object, calls after
the day is done) and the gateway's get_time are recorded without reaching the env."""

import asyncio
import json
import os
from pathlib import Path
from unittest import mock

import httpx
from fastmcp import Client

from agentenv_airops.live import AirOpsLiveEnv
from agentenv_airops.tasks import AIROPS_RULES

MODEL = "anthropic/claude-sonnet-5-5"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DAY = FIXTURES / "e2e-day.jsonl"


def record(pack: Path, task_id: str, turns: list[list[tuple[str, object]]], end: bool = True) -> tuple[dict, dict]:
    """The agent's record of ``turns`` (each a list of (tool, arguments) calls, arguments a dict or the raw text the
    model sent) and the env's data/get after it, with the day ended as end-day ends it when ``end``."""
    return asyncio.run(_record(pack, task_id, turns, end))


async def _record(pack: Path, task_id: str, turns: list, end: bool) -> tuple[dict, dict]:
    real = httpx.AsyncClient
    clock = httpx.MockTransport(lambda request: httpx.Response(200, json={"armed": True}))
    with mock.patch.dict(os.environ, {"AIROPS_TASKS_DIR": str(pack)}), \
            mock.patch("httpx.AsyncClient", lambda *a, **k: real(transport=clock)):
        env = AirOpsLiveEnv()
        env.create_app()
        env.live_load(task_id)
        env.set_time_url = "http://gateway/clock/set-time"
        out = {"model": MODEL, "messages": [{"role": "system", "content": AIROPS_RULES},
                                            {"role": "user", "content": env.day.briefing()}],
               "steps": [], "reward": None, "usage": {"input_tokens": 0, "output_tokens": 0}, "turns": 0,
               "end_reason": None, "errors": [], "seconds": 1.0}
        done = False
        async with Client(env.mcp) as client:
            for k, calls in enumerate(turns):
                ids = [f"toolu_{k}_{i}" for i in range(len(calls))]
                out["turns"] += 1
                out["messages"].append({"role": "assistant", "content": "", "reasoning": "", "stop": "tool_use",
                                        "tool_calls": [{"id": i, "name": name, "arguments": raw(args)}
                                                       for i, (name, args) in zip(ids, calls, strict=True)]})
                for i, (name, args) in zip(ids, calls, strict=True):
                    content = await answer(env, client, name, args, done)
                    result = json.loads(content)
                    if isinstance(result, dict) and result.get("done") is True and not done:
                        done, out["reward"], out["end_reason"] = True, result["reward"], "done"
                    out["messages"].append({"role": "tool", "tool_call_id": i, "name": name, "content": content})
        if end and not env.day.done:
            env.end_day()
        return out, json.loads(json.dumps(env.day.data()))


async def answer(env: AirOpsLiveEnv, client: Client, name: str, args, done: bool) -> str:
    if done:
        return json.dumps({"error": "the episode is over"})
    if not isinstance(args, dict):
        return json.dumps({"error": "arguments are not valid JSON: Expecting value"})
    if name == "get_time":
        return json.dumps({"current_time": "2024-01-09T22:00:00Z"}, indent=2)
    result = await client.call_tool_mcp(name, args)
    text = "\n".join(c.text for c in result.content if c.type == "text")
    if result.isError:
        return json.dumps({"error": text})
    reply = json.loads(text)
    if name == "advance" and "watch" in reply:
        for n in env.day.watches[env.day.watch].news:
            await client.call_tool_mcp("airops_notice", {"news_id": n})
    return text


def raw(args) -> str:
    return args if isinstance(args, str) else json.dumps(args)
