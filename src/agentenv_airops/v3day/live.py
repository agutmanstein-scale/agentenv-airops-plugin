"""AirOps v3 live: one storm date on the programs world (docs/v3/DESIGN.md §5), played on the gateway's virtual clock
as live.py plays v1's day, beside it.

urn:airops:v3-live-load/v1 starts a day on a task of AIROPS_V3_TASKS (programs.model tasks, layered or not); the
planner's tools are v1's (get_situation, check_plan, confirm_changes, advance, with v1's change lists) on v3day.Day.
Every call costs the Day CALL_MIN minutes of its watch; before a call moves the Day to a new watch (advance, or a
call that runs the watch's time out) the env re-arms the gateway clock frozen at that watch, as v1's advance does, so a
call that could end any watch but the last needs the clock synced, and a failed re-arm leaves the Day where it was
(a call that fails after the re-arm arms the clock back at its watch). Planner calls run one at a time.
The Day delivers each watch's advisories itself, in every tool result's messages, so there are no news triggers. A
reply in which the day ends carries done, cost and reward. The grade needs the anchor's cost: the load takes it (and
the follower's) when the pack has it and otherwise solves the hindsight MIP then, before the clock starts, so the
day's last call never waits on a solve. urn:airops:v3-end-day/v1 grades the plan on the truth (v3day.grade) when the
planner stopped early. data/get reports the Day with its reward and an audit of its news log, as v1's verifier reads
a day, and never the truth the desk doesn't know yet."""

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from agentenv_protocol import AgentEnvEnvironment, DataPart, environment_card, extension, get_data, reset_data, tool
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from pydantic import BaseModel, ConfigDict, Field

from ..live import _raw, _strict
from ..programs import references
from ..programs.model import Task, load_tasks
from ..schedule import CLOCK_URI
from . import situation as view
from .clock import CALL_MIN, virtual_time
from .day import Day


class Change(BaseModel):
    """v1's change (live.Change) with v3's reserve calls: a flight leaving a crew base past its pairing's crew deadline
    flies only on a reserve crew, while the base's pool lasts."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_strict)

    flight: str = Field(description='flight id from the table, e.g. "f12"')
    action: Literal["cancel", "restore", "hold", "call_reserve", "release_reserve"] = Field(
        description="cancel the flight; restore a cancelled flight; hold a flight until not_before (restoring it if "
                    "cancelled); call_reserve: give a flight leaving a crew base a reserve crew, so it may leave past "
                    "its crew's deadline; release_reserve: hand the reserve back before that deadline")
    not_before: str | None = Field(
        default=None,
        description='local time the flight may not leave before, "HH:MM" ("00:30+1" after midnight); null to let it '
                    "leave as early as it can, and with cancel and the reserve actions")


ChangesArg = Annotated[list[Change] | str, Field(
    description='[{"flight": "f12", "action": "cancel", "not_before": null}, {"flight": "f30", "action": "hold", '
                '"not_before": "15:40"}, {"flight": "f7", "action": "call_reserve", "not_before": null}, ...]; '
                "flights left out keep their plan")]


ENV = "airops-v3-day"
LIVE_LOAD_URI = "urn:airops:v3-live-load/v1"
END_DAY_URI = "urn:airops:v3-end-day/v1"
TASKS_VAR = "AIROPS_V3_TASKS"
TOOLS = ("get_situation", "check_plan", "confirm_changes", "advance")
EMPTY = {"task_id": None, "watches": None, "watch": None, "t": None, "clock": None, "calls": 0, "done": False,
         "end_reason": None, "plan": [], "frozen": [], "notices": [], "freezes": [], "changes": [], "refusals": [],
         "grade": None, "reward": None, "audit": None}


def tasks_by_id() -> dict[str, Task]:
    """The programs tasks of AIROPS_V3_TASKS (pack folders or .jsonl files, separated by ':'); none when unset."""
    paths = os.environ.get(TASKS_VAR)
    return {t.task_id: t for p in paths.split(":") for t in load_tasks(Path(p))} if paths else {}


def audit(day: Day) -> dict:
    """Whether every watch reached so far delivered its news, and only it, at that watch."""
    problems = []
    for k in range(day.watch + 1):
        got = [e["news_id"] for e in day.log if e["watch"] == k]
        if got != list(day.watches[k].news):
            problems.append(f"watch {k} got {got} instead of {list(day.watches[k].news)}")
    return {"ok": not problems, "problems": problems}


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@environment_card(name=ENV)
class AirOpsV3LiveEnv(AgentEnvEnvironment):
    def __init__(self):
        self.tasks = tasks_by_id()
        self.set_time_url: str | None = None
        self.loaded = False
        self.playing = asyncio.Lock()
        self.reset()

    def create_app(self) -> FastMCP:
        return self.mount(FastMCP(ENV, middleware=[ToolOrder()]))

    @tool(description="The current watch: the time, the freeze line, the FAA programs as issued (GDPs with JetBlue's "
                      "slots, ground stops), the departure caps known so far, every flight (its status, its delay once "
                      "known, your plan, where your plan has it leave and land on the day as known, its slot and its "
                      f"crew's deadline), where the aircraft are, and new messages. Costs {CALL_MIN} min of the watch.")
    async def get_situation(self) -> str:
        return await self._play(_situation)

    @tool(description="Check changes without confirming them: your changes over your plan, run on the day as known "
                      "now. Gives the plan's cost, the flights whose plan or times change, the automatic "
                      "cancellations, the flights the env would hold to the freeze line, and the changes "
                      f"confirm_changes would refuse. Costs {CALL_MIN} min of the watch.")
    async def check_plan(self, changes: ChangesArg) -> str:
        return await self._play(lambda day: day.check(_raw(changes)))

    @tool(description="Commit changes to your plan; flights you leave out keep theirs. From watch 1 on, frozen flights "
                      "(those your plan has leaving before the freeze line, an hour after the watch) can't be "
                      "changed, a cancellation is final, and the env holds any other flight your plan would have "
                      f"leaving before the freeze line to it. Costs {CALL_MIN} min of the watch.")
    async def confirm_changes(self, changes: ChangesArg) -> str:
        return await self._play(lambda day: day.confirm(_raw(changes)))

    @tool(description="End this watch: the clock moves to the next watch, flights leave as your plan has them, and "
                      "flights your plan has leaving before the new freeze line freeze. The watch's news comes in "
                      "this result's messages. After the last watch the day is graded on your plan.")
    async def advance(self) -> str:
        return await self._play(lambda day: day.advance(), ends=True)

    @extension(LIVE_LOAD_URI, description="Start a live v3 day on the task with this id; an env plays one day.")
    def live_load(self, task_id: str, anchor: int | None = None, follower: int | None = None) -> dict:
        """`anchor` and `follower` are those references' costs on the truth when the pack has them; without the anchor's
        the load solves it (references.anchor, up to its time limit). Loading again would replay the day with its news
        already seen; the news before the first watch is in the opening prompt (tasks.briefing), so it is not repeated
        in the first reply."""
        if self.loaded:
            raise ValueError("a day was already loaded in this env: each live day needs a new deploy")
        if task_id not in self.tasks:
            raise ValueError(f"unknown task id {task_id!r}")
        self.reset()
        task = self.tasks[task_id]
        self.day = Day(task, references.anchor(task)[1].cost.total if anchor is None else anchor, follower)
        self.day.drain()
        self.loaded = True
        return {"task_id": task_id, "watches": len(self.day.watches)}

    @extension(END_DAY_URI, description="Grade the plan on the truth if the planner didn't end the day.")
    def end_day(self) -> dict:
        day = self._day()
        if not day.done:
            day.finish("end_day")
        return {"end_reason": day.end_reason, "watch": day.watch, "cost": day.result["cost"]["total"],
                "reward": day.result["reward"], "audit": audit(day)}

    @extension(CLOCK_URI, description="Follow the gateway's virtual clock; each new watch re-arms it.")
    async def sync_time(self, env_get_time_url: str) -> dict:
        self.set_time_url = env_get_time_url.removesuffix("/clock/time") + "/clock/set-time"
        async with httpx.AsyncClient() as http:
            response = await http.get(env_get_time_url, timeout=10)
            response.raise_for_status()
        return {"env_get_time_url": env_get_time_url, "virtual_time": response.json()["virtual_time"]}

    @reset_data
    def reset(self) -> None:
        self.day: Day | None = None

    @get_data
    def get(self) -> list[DataPart]:
        if self.day is None:
            return [DataPart(data=EMPTY)]
        reward = self.day.result["reward"] if self.day.result else None
        return [DataPart(data=self.day.data() | {"reward": reward, "audit": audit(self.day)})]

    def _day(self, open_only: bool = False) -> Day:
        if self.day is None:
            raise ValueError(f"no day loaded: load one with {LIVE_LOAD_URI}")
        if open_only and self.day.done:
            raise ValueError("the day is over")
        return self.day

    async def _play(self, act, ends: bool = False) -> str:
        """Runs a planner call on the Day, one at a time, first re-arming the gateway clock at the next watch if the
        call will open it, and arming it back at this watch if the call then fails."""
        async with self.playing:
            day = self._day(open_only=True)
            rearm = not day.last and (ends or day.clock + CALL_MIN >= day.ends)
            if rearm:
                await self._arm(day.ends)
            try:
                out = act(day)
            except Exception:
                if rearm:
                    await self._arm(day.t)
                raise
            if day.done:
                out |= {"done": True, "end_reason": day.end_reason, "cost": day.result["cost"]["total"],
                        "reward": day.result["reward"]}
            return _json({**out, "messages": day.drain()})

    async def _arm(self, t: int) -> None:
        """Freezes the gateway's clock at minute `t` of the day."""
        if self.set_time_url is None:
            raise ValueError(f"the gateway clock is not synced: call {CLOCK_URI} sync_time first")
        async with httpx.AsyncClient() as http:
            response = await http.put(self.set_time_url, timeout=10, json={
                "virtual_time": virtual_time(self.day.task, t), "virtual_seconds_per_real_second": 0})
            response.raise_for_status()


def _situation(day: Day) -> dict:
    s = day.situation()
    out = {"watch": s["watch"], "time": s["time"], "clock": s["clock"], "freeze_line": s["freeze_line"],
           "situation": view.render(day.task, s), "now": s["now"]}
    return out | ({"watch_over": s["watch_over"]} if "watch_over" in s else {})


class ToolOrder(Middleware):
    """Lists the planner's tools in the order of the rules."""

    async def on_list_tools(self, context, call_next):
        return sorted(await call_next(context), key=lambda t: TOOLS.index(t.name))
