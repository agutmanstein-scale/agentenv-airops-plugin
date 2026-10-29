"""scripts/sft_rows.py on a pilot folder written here: one row per desk watch, the next page and the day's reward kept
out of end_turn's result, the role's tools with the env's schemas, and watches without reasoning or cut off left out."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from agentenv_airops.programs.cost import DOLLARS_PER_PAX_MINUTE

ROOT = Path(__file__).resolve().parents[2]
RUN = "b6-20250119-0400-joint"
OPS_TOOLS = ["ops_flights", "ops_program", "ops_aircraft", "ops_check", "ops_confirm", "ops_request", "ops_reply",
             "end_turn", "wait_turn", "resign"]


@pytest.fixture(scope="module")
def sft():
    spec = importlib.util.spec_from_file_location("sft_rows", ROOT / "scripts" / "sft_rows.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def said(reasoning: str, *calls: tuple[str, dict], stop: str = "tool_calls") -> dict:
    return {"role": "assistant", "content": "", "reasoning": reasoning, "stop": stop,
            "tool_calls": [{"id": f"call_{name}_{len(json.dumps(args))}", "name": name, "arguments": json.dumps(args)}
                           for name, args in calls]}


def result(m: dict, content: dict) -> dict:
    call = m["tool_calls"][0]
    return {"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": json.dumps(content)}


def ended(watch: int, **more) -> dict:
    return {"ended": "ops", "watch": watch, "opened": watch + 1, "turn": "ops", **more}


def pilot(tmp_path: Path) -> Path:
    look = said("f12 misses its slot; see who is on it", ("ops_flights", {"ids": ["f12"]}))
    close = said("nothing to hold yet", ("end_turn", {"note": None, "stand_down": False}))
    blank = said("", ("end_turn", {"note": None, "stand_down": False}))
    cut = said("weighing every hold at once", stop="length")
    short = said("hold f12", ("end_turn", {"note": "f12 held", "stand_down": False}))
    last = said("the day is ending", ("end_turn", {"note": None, "stand_down": True}))
    record = {"model": "reflection/Beam-1.0-501B-A23B:baseten", "messages": [
        {"role": "system", "content": "The rules."},
        {"role": "user", "content": "You are the ops desk.\n\n# Ops, watch 1"},
        look, result(look, {"rows": ["f12 ..."]}), close, result(close, ended(1, page="# Ops, watch 2")),
        blank, result(blank, ended(2, page="# Ops, watch 3")),
        cut, {"role": "user", "content": "Keep reasoning short now and act."}, short,
        result(short, ended(3, page="# Ops, watch 4")),
        last, result(last, ended(4, done=True, reward=0.81))]}
    folder = tmp_path / "occ3-real-beam"
    (folder / "transcripts").mkdir(parents=True)
    (folder / "bundle" / "tasks").mkdir(parents=True)
    (folder / "transcripts" / f"{RUN}-a1-ops.json").write_text(json.dumps(record))
    (folder / "bundle" / "tasks" / f"{RUN}.json").write_text(json.dumps([
        {"id": "ops-only", "type": "modify_env_tool_access", "action": "disable", "role": "ops", "tools": ["*"]},
        {"id": "ops-tools", "type": "modify_env_tool_access", "action": "enable", "role": "ops", "tools": OPS_TOOLS}]))
    (folder / "pilot.json").write_text(json.dumps({"model": record["model"]}))
    (folder / "results.jsonl").write_text(json.dumps({
        "run": RUN, "outcome": "scored", "date": "2025-01-19", "reward": 0.81, "cost": 1_600_000.0,
        "desks": {"ops": {"transcript": f"transcripts/{RUN}-a1-ops.json"}}}) + "\n")
    return folder


def test_each_watch_with_reasoning_on_every_turn_is_a_row_without_the_next_page_or_the_days_reward(sft, tmp_path):
    schemas = asyncio.run(sft.env_tools())
    rows, counts = sft.rows(pilot(tmp_path), schemas)
    assert counts == {"kept": 2, "left out: a turn without reasoning": 1, "left out: a turn cut off": 1}
    first, fourth = rows
    assert [m["role"] for m in first["messages"]] == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert first["messages"][:2] == [{"role": "system", "content": "The rules."},
                                     {"role": "user", "content": "You are the ops desk.\n\n# Ops, watch 1"}]
    assert first["messages"][2]["reasoning_content"] == "f12 misses its slot; see who is on it"
    assert first["messages"][2]["tool_calls"][0]["function"] == {"name": "ops_flights",
                                                                 "arguments": json.dumps({"ids": ["f12"]})}
    assert json.loads(first["messages"][-1]["content"]) == ended(1)
    assert fourth["messages"][1] == {"role": "user", "content": "# Ops, watch 4"}
    assert json.loads(fourth["messages"][-1]["content"]) == ended(4, done=True)
    assert (first["watch"], fourth["watch"], first["watches"], first["desk"]) == (1, 4, 4, "ops")
    assert [t["function"]["name"] for t in first["tools"]] == [n for n in OPS_TOOLS if n not in ("wait_turn",
                                                                                                 "resign")]
    assert first["tools"][0] == schemas["ops_flights"]
    assert (first["day_reward"], first["day_cost_usd"]) == (0.81, round(1_600_000.0 * DOLLARS_PER_PAX_MINUTE))
