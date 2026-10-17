"""airops-v3-day served on a free port: its card, routes and tools beside v1's, a layered task loaded from a
tasks.jsonl line, a scripted desk playing whole days through the tools to the sim's cost on the truth, bad changes,
the clock (each call's minute, the re-arm at every new watch), the end-day grade and the v3 prompts."""

from dataclasses import replace

import httpx
import pytest
from agentenv_protocol import client
from day_helpers import capped, day, flight, tail
from fastmcp import Client
from served_v3 import Tools, served_v3
from test_v3_play import airline, airline_expected

from agentenv_airops import live as v1_live
from agentenv_airops import tasks as v1_tasks
from agentenv_airops.core.model import hhmm
from agentenv_airops.programs import sim
from agentenv_airops.programs.model import LAYERED, Layers, load_tasks
from agentenv_airops.schedule import CLOCK_URI
from agentenv_airops.v3day import tasks as v3_tasks
from agentenv_airops.v3day.clock import CALL_MIN, virtual_time
from agentenv_airops.v3day.day import Day, changes_to
from agentenv_airops.v3day.grade import grade
from agentenv_airops.v3day.live import END_DAY_URI, ENV, LIVE_LOAD_URI, TOOLS, AirOpsV3LiveEnv

pytestmark = pytest.mark.anyio


def two_flights():
    """f0 JFK-BUF at 01:40 and f1 JFK-MCO at 08:20, each on its own aircraft at JFK: hourly watches from 00:00."""
    return day([flight(0, "JFK", "BUF", 100, 60), flight(1, "JFK", "MCO", 500, 150)],
               [tail("N1", "JFK", end="BUF"), tail("N2", "JFK", end="MCO")])


async def invoke(http, uri: str, **params) -> httpx.Response:
    card = (await http.get("/.well-known/agent-env.json")).json()
    return await http.post(client.extension_params(card, uri)["endpoint"], json=params)


async def data(http) -> dict:
    response = await http.post("/agentenv", json={"jsonrpc": "2.0", "id": 1, "method": "data/get"})
    return response.json()["result"]["parts"][0]["data"]


async def test_the_card_routes_and_tools_sit_beside_v1s(monkeypatch, tmp_path):
    with served_v3(monkeypatch, tmp_path, [two_flights()]) as (url, env, _):
        async with httpx.AsyncClient(base_url=url) as http:
            card = (await http.get("/.well-known/agent-env.json")).json()
        async with Client(f"{url}/mcp") as mcp:
            listed = await mcp.list_tools()
    assert card["name"] == ENV != v1_live.ENV and client.mcp_path(card) == "/mcp"
    assert {e["uri"] for e in card["capabilities"]["extensions"]} == {LIVE_LOAD_URI, END_DAY_URI, CLOCK_URI}
    assert LIVE_LOAD_URI != v1_live.LIVE_LOAD_URI and END_DAY_URI != v1_live.END_DAY_URI
    assert [t.name for t in listed] == list(TOOLS) == ["get_situation", "check_plan", "confirm_changes", "advance"]
    assert all("\n" not in t.description for t in listed)
    item = listed[1].inputSchema["properties"]["changes"]["anyOf"][0]["items"]
    assert (item["required"], item["additionalProperties"]) == (["flight", "action", "not_before"], False)
    assert item["properties"]["action"]["enum"] == ["cancel", "restore", "hold"]


async def test_a_layered_task_loads_from_a_tasks_jsonl_line_and_its_caps_show(monkeypatch, tmp_path, task):
    layered = replace(capped(task, {"JFK": {18: 1, 19: 2}}), task_id=f"{task.task_id}-capped")
    with served_v3(monkeypatch, tmp_path, [task, layered], layered.task_id) as (url, env, _):
        async with Client(f"{url}/mcp") as mcp:
            out = await Tools(mcp)("get_situation")
    line = (tmp_path / "tasks.jsonl").read_text().splitlines()[1]
    assert '"version":4' in line.replace(" ", "") and LAYERED == 4
    assert load_tasks(tmp_path / "tasks.jsonl") == [task, layered] and set(env.tasks) == {task.task_id,
                                                                                          layered.task_id}
    assert env.day.task.layers == Layers(dep_caps={"JFK": {18: 1, 19: 2}})
    assert "## Departure caps" in out["situation"] and "JFK: 18h 1 19h 2" in out["situation"]
    assert (out["watch"], out["clock"], out["now"]) == (0, hhmm(task.start), hhmm(task.start + CALL_MIN))


@pytest.mark.parametrize("name", ["task", "crewed"])
async def test_a_scripted_desk_plays_the_day_through_the_tools_to_the_sims_cost_on_the_truth(
        name, request, monkeypatch, tmp_path):
    task = request.getfixturevalue(name)
    with served_v3(monkeypatch, tmp_path, [task]) as (url, env, puts):
        async with Client(f"{url}/mcp") as mcp, httpx.AsyncClient(base_url=url) as http:
            tools, d = Tools(mcp), env.day
            while True:
                if rows := changes_to(d, airline(d)):
                    assert (await tools("confirm_changes", changes=rows))["refused"] == []
                out = await tools("advance")
                if out.get("done"):
                    break
            got = await data(http)
            assert await tools.error("advance") == "Error calling tool 'advance': the day is over"
    expected = airline_expected(task)
    assert d.plan == expected and out["cost"] == sim.execute(task, expected).cost.total
    assert out["reward"] == d.result["reward"] and got["grade"] == grade(task, expected) == d.result
    assert got["done"] and got["reward"] == out["reward"] and got["audit"] == {"ok": True, "problems": []}
    assert puts == [{"virtual_time": virtual_time(task, w.t), "virtual_seconds_per_real_second": 0}
                    for w in d.watches[1:]]


async def test_bad_changes_come_back_as_errors_problems_and_refusals(monkeypatch, tmp_path):
    with served_v3(monkeypatch, tmp_path, [two_flights()]) as (url, env, _):
        async with Client(f"{url}/mcp") as mcp:
            tools = Tools(mcp)
            bad = await tools("confirm_changes", changes="[{flight")
            assert bad["error"].startswith("changes are not valid JSON")
            unknown = await tools("check_plan", changes=[{"flight": "f9", "action": "cancel", "not_before": None}])
            assert unknown["problems"] == ['unknown flight "f9": use the ids from the flight table (f0, f1, ...)']
            soon = await tools("confirm_changes", changes=[{"flight": "f1", "action": "hold", "not_before": "soon"}])
            assert soon["confirmed"] == [] and soon["problems"] == ['f1: not_before "soon" is not a time: write HH:MM, '
                                                                   "or HH:MM+1 after midnight"]
            assert "validation error" in await tools.error("confirm_changes", changes=[{"flight": "f1"}])
            await tools("advance")
            frozen = await tools("confirm_changes", changes=[{"flight": "f0", "action": "cancel", "not_before": None}])
            assert env.day.plan == {} and frozen["confirmed"] == []
            assert [r["flight"] for r in frozen["refused"]] == ["f0"] and "is frozen" in frozen["refused"][0]["reason"]
    assert env.day.calls == 4 and env.day.refusals[0]["flight"] == "f0"


async def test_each_call_costs_a_minute_and_the_watch_running_out_rearms_the_clock(monkeypatch, tmp_path):
    task = two_flights()
    with served_v3(monkeypatch, tmp_path, [task]) as (url, env, puts):
        async with Client(f"{url}/mcp") as mcp:
            tools, step = Tools(mcp), env.day.watches[1].t - env.day.watches[0].t
            for k in range(1, step // CALL_MIN):
                assert (await tools("get_situation"))["now"] == hhmm(k * CALL_MIN) and puts == []
            out = await tools("check_plan", changes=[])
            assert out["watch_over"] == {"watch": 1, "time": hhmm(step), "freeze_line": hhmm(step + 60),
                                         "frozen": 1, "news": []}
            assert puts == [{"virtual_time": virtual_time(task, step), "virtual_seconds_per_real_second": 0}]
            assert (env.day.watch, env.day.clock) == (1, step)
            out = await tools("advance")
            assert (out["watch"], out["time"]) == (2, hhmm(2 * step)) and len(puts) == 2


async def test_a_watch_change_needs_the_synced_clock_except_after_the_last_watch(monkeypatch, tmp_path):
    with served_v3(monkeypatch, tmp_path, [two_flights()], synced=False) as (url, env, puts):
        async with Client(f"{url}/mcp") as mcp:
            tools = Tools(mcp)
            assert (await tools("get_situation"))["watch"] == 0
            assert await tools.error("advance") == ("Error calling tool 'advance': the gateway clock is not synced: "
                                                    f"call {CLOCK_URI} sync_time first")
            while not env.day.last:
                env.day.advance()
            out = await tools("advance")
    assert out["done"] and out["end_reason"] == "done" and out["cost"] == env.day.result["cost"]["total"]
    assert puts == []


async def test_sync_time_reads_the_gateway_clock(monkeypatch, tmp_path):
    task = two_flights()
    with served_v3(monkeypatch, tmp_path, [task], synced=False) as (url, env, _):
        async with httpx.AsyncClient(base_url=url) as http:
            got = (await invoke(http, CLOCK_URI, env_get_time_url=f"{url}/clock/time")).json()
    assert got == {"env_get_time_url": f"{url}/clock/time", "virtual_time": virtual_time(task, task.start)}
    assert env.set_time_url == f"{url}/clock/set-time"


async def test_end_day_grades_the_plan_on_the_truth_and_data_get_reports_it(monkeypatch, tmp_path):
    task = two_flights()
    cancel = [{"flight": "f1", "action": "cancel", "not_before": None}]
    with served_v3(monkeypatch, tmp_path, [task]) as (url, env, _):
        async with Client(f"{url}/mcp") as mcp, httpx.AsyncClient(base_url=url) as http:
            assert (await Tools(mcp)("confirm_changes", changes=cancel))["confirmed"] == ["f1"]
            out = (await invoke(http, END_DAY_URI)).json()
            got = await data(http)
    plan = {1: {"cancel": True, "decided": 0}}
    assert env.day.plan == plan and got["grade"] == grade(task, plan)
    assert out == {"end_reason": "end_day", "watch": 0, "cost": sim.execute(task, plan).cost.total,
                   "reward": got["reward"], "audit": {"ok": True, "problems": []}}


async def test_no_day_loaded_and_one_day_per_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AIROPS_V3_TASKS", str(tmp_path / "none.jsonl"))
    (tmp_path / "none.jsonl").write_text("")
    env = AirOpsV3LiveEnv()
    env.create_app()
    async with Client(env.mcp) as mcp:
        no_day = f"no day loaded: load one with {LIVE_LOAD_URI}"
        assert await Tools(mcp).error("get_situation") == f"Error calling tool 'get_situation': {no_day}"
    with pytest.raises(ValueError, match="unknown task id 'x'"):
        env.live_load("x")
    env.tasks = {"d": two_flights()}
    assert env.live_load("d") == {"task_id": "d", "watches": len(env.day.watches)}
    with pytest.raises(ValueError, match="already loaded"):
        env.live_load("d")


def test_the_v3_prompts_describe_the_programs_world(task, crewed):
    rules, crew_rules = v3_tasks.rules(task), v3_tasks.rules(crewed)
    for words in ["ground delay programs", "EDCT", "Ground stops", "Departure caps", "check_plan(changes)",
                  "decided later than 2 hours"]:
        assert words in rules
    assert "Crews:" in crew_rules and "Crews:" not in rules and rules != v1_tasks.AIROPS_RULES
    d = Day(task)
    assert v3_tasks.briefing(d).startswith(d.text()) and d.calls == 0
    steps = v3_tasks.steps(task)
    assert [s["id"] for s in steps] == ["deploy", "load-task", "deploy-agent", "clock", "play", "end-day", "grade"]
    assert steps[1]["directives"] == [{"service": ENV, "uri": LIVE_LOAD_URI, "args": {"task_id": task.task_id}}]
    assert steps[3]["virtual_time"] == virtual_time(task, task.start)
    assert steps[4]["system_prompt"] == rules and steps[4]["max_turns"] == 5 * len(d.watches) + 4
