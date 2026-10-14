"""Recorded runs replayed: a run's tool calls played back through a Day in the order the env received them, each
output checked byte for byte against the recording, each watch's news delivered after its advance as the triggers
deliver it, and the rest of the day run as end-day runs it. Runs gathers the scored runs of the sweeps under
results/runs and replays each once, for the viewer and the dataset."""

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import click
from pydantic import TypeAdapter, ValidationError

from . import export
from .core.model import Task
from .day import Day
from .live import ChangesArg, _json, _raw
from .schedule import TOOLS
from .sweep import RUNS, Sweep, load, results

CHANGES = TypeAdapter(ChangesArg)
PLANNING = ("check_plan", "confirm_changes")


class ReplayError(Exception):
    """A recording the day doesn't reproduce: made before the env changed, or on another pack."""


@dataclass(frozen=True)
class Replayed:
    day: Day
    steps: list[dict]


def replay(task: Task, messages: list[dict]) -> Replayed:
    """``messages`` is airops-llm's record: each assistant turn's tool calls, and a tool message per call with what it
    returned. Calls to other tools (the gateway's get_time) and calls the agent never sent (arguments that aren't a
    JSON object) are skipped; a call the tools' argument validation refused must have been recorded as an error."""
    day, steps, calls, turn = Day(task), [], {}, 0
    for m in messages:
        if m["role"] == "assistant":
            turn += 1
            calls |= {c["id"]: (turn, c) for c in m.get("tool_calls") or []}
            continue
        if m["role"] != "tool" or day.done:
            continue
        at, call = calls[m["tool_call_id"]]
        args = _parsed(call["arguments"] or "{}")
        if call["name"] not in TOOLS or not isinstance(args, dict):
            continue
        day.calls += 1
        step = {"t": day.t, "watch": day.watch, "turn": at, "call_id": call["id"], "tool": call["name"],
                "args": {"changes": _parsed(args["changes"])} if "changes" in args else args}
        accepted, raw = _validated(call["name"], args)
        if not accepted:
            recorded = _parsed(m["content"])
            if not (isinstance(recorded, dict) and set(recorded) == {"error"}):
                raise ReplayError(f"{call['name']} {call['id']} has arguments its schema refuses, yet recorded "
                                  f"{m['content'][:200]}")
            steps.append(step | {"result": recorded})
            continue
        result = _json(play(day, call["name"], raw))
        if result != m["content"]:
            raise ReplayError(f"{call['name']} {call['id']} in watch {step['watch']} returned {result[:300]}, "
                              f"recorded {m['content'][:300]}")
        steps.append(step | {"result": json.loads(result)})
        if call["name"] == "advance" and not day.done:
            for n in day.watches[day.watch].news:
                day.notice(n, "trigger")
    if not day.done:
        day.end()
    return Replayed(day, steps)


def play(day: Day, name: str, raw) -> dict:
    """What the env's tool returns, before it is serialized."""
    if name == "get_situation":
        result = day.situation()
    elif name == "advance":
        result = day.finish("done") if day.last else day.open()
    elif name == "check_plan":
        result = day.check(raw)
    else:
        result = day.confirm(raw)
    return {**result, "messages": day.drain()}


def _validated(name: str, args: dict) -> tuple[bool, object]:
    """Whether the tool's argument validation passes the call, and the changes it hands the day."""
    if name not in PLANNING:
        return not args, None
    if set(args) != {"changes"}:
        return False, None
    try:
        return True, _raw(CHANGES.validate_python(args["changes"]))
    except ValidationError:
        return False, None


def _parsed(value):
    """A JSON string as what it encodes; anything else as it is."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


@dataclass(frozen=True)
class Run:
    """One scored attempt: the sweep it belongs to and its row of results.jsonl."""

    sweep: Sweep
    row: dict

    @property
    def id(self) -> str:
        return self.sweep.name if self.sweep.k == 1 else f"{self.sweep.name}.r{self.row['rep']}"

    @property
    def key(self) -> tuple[str, str, str]:
        return self.id, self.row["model"], self.row["task_id"]


class Runs:
    """The scored attempt of each model, day and rep in the sweeps under ``root`` (or the named ones), replayed once."""

    def __init__(self, root: Path = RUNS, names: list[str] | None = None, cache: Path = export.CACHE):
        self.root, self.cache = root, cache
        found = sorted(p.parent.name for p in root.glob("*/sweep.json")) if names is None else names
        self.sweeps = [load(name, root) for name in found]
        self.runs = [Run(s, r) for s in self.sweeps for r in results(root / s.name) if r["outcome"] == "scored"]
        self.failed: list[dict] = []

    @cached_property
    def days(self) -> dict[str, dict[str, Task]]:
        return {s.name: s.days() for s in self.sweeps}

    def transcript(self, run: Run) -> dict:
        return json.loads((self.root / run.sweep.name / run.row["transcript"]).read_text())

    def replayed(self, run: Run) -> Replayed:
        return replay(self.days[run.sweep.name][run.row["task_id"]], self.transcript(run)["messages"])

    def episode(self, run: Run) -> dict:
        r = run.row
        played = self.replayed(run)
        ep = export.episode(self.days[run.sweep.name][r["task_id"]], played.day.data(), played.steps, model=r["model"],
                            run=run.id, cache=self.cache,
                            record={"sweep": run.sweep.name, "rep": r["rep"], "attempt": r["attempt"],
                                    "instance": r["instance"], "end_reason": r["end_reason"], "turns": r["turns"],
                                    "tool_calls": r["tool_calls"], "cost_usd": r["cost_usd"]})
        if ep["reward"] != r["reward"]:
            raise ReplayError(f"{run.key} replays to reward {ep['reward']}, scored {r['reward']}")
        return ep

    def episodes(self) -> list[dict]:
        """Every run's episode; a run that doesn't replay goes to ``failed`` with its error."""
        out = []
        for run in self.runs:
            try:
                out.append(self.episode(run))
            except ReplayError as e:
                self.failed.append({"run": run.id, "model": run.row["model"], "task_id": run.row["task_id"],
                                    "error": str(e)})
        return out


def require(runs: Runs) -> Runs:
    if not runs.runs:
        raise click.UsageError(f"no scored runs in the sweeps under {runs.root}: play one with `agent-env airops "
                               "sweep run`")
    return runs
