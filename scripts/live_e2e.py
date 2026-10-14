"""Play a live AirOps day through `agent-env run` with no model spend: the task deploys airops-day on the gateway and
airops-llm on local Docker, and tests/fake_litellm.py, on this machine, answers the agent with the reference follower's
decisions (the MIP re-solved at every watch on the day as known), confirmed through the real tools at one turn per
watch. It fails unless each watch's trigger fired once and delivered that watch's news, the agent never saw
airops_notice, each watch's news came with the first tool result after its advance and no other, get_time read the
watch's minute and held still between turns, data/get held the day as played in process, and the day scored the
follower's reward.

Run it from the checkout after `agent-env airops setup --agent`, with the dev extra installed:

    PYTHONPATH=tests python scripts/live_e2e.py [--task ID] [--pack PATH] [--out day.json]

The day must carry its reference (anchor and floor), and the env image must hold the same pack.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from agent_env.config import get_config
from agent_env.store.routing import namespace_routing
from agent_env.task.store import get_task_instance_store
from fake_litellm import FakeLiteLLM

from agentenv_airops import tasks
from agentenv_airops.core.model import Task, hhmm
from agentenv_airops.core.reward import reward
from agentenv_airops.day import Day, changes_to, play
from agentenv_airops.refs import follower, mip
from agentenv_airops.schedule import ENV, NOTICE_TOOL, timetable, virtual_time

MODEL = "anthropic/claude-sonnet-5-5"
PAUSE = 2.0


class PausingFake(FakeLiteLLM):
    """Answers each request PAUSE seconds late, so real time passes between turns while the clock should not."""

    def _route(self, build):
        endpoint = super()._route(build)

        async def paused(request):
            await asyncio.sleep(PAUSE)
            return await endpoint(request)
        return paused


def follower_plans(task: Task) -> tuple[list[dict[int, dict]], follower.Followed]:
    """The follower's plan after each watch, read off its solves, and its run. HiGHS can return a different optimal
    solution to the same watch from run to run, so a run is replayed against itself, not the stored reference."""
    solves = []

    def solve(*args, **kwargs):
        solution = mip.solve(*args, **kwargs)
        solves.append((set(kwargs["fixed"] if "fixed" in kwargs else args[3]), solution))
        return solution

    with mock.patch.object(follower, "solve", solve):
        followed = follower.follow(task)
    plans, plan = [], {}
    for pinned, solution in solves:
        plan = {i: plan[i] if i in pinned else d for i, d in solution.plan.items()}
        plans.append(plan)
    assert plans[-1] == followed.plan
    return plans, followed


def played(task: Task, plans: list[dict]) -> tuple[Day, list[list[dict]]]:
    """The follower's day played in process, and the changes it confirms at each watch."""
    rows = []

    def policy(day: Day) -> dict:
        rows.append(changes_to(day, plans[day.watch]))
        return plans[day.watch]
    day = play(task, policy)
    assert day.refusals == [] and all(c["by"] == "planner" for c in day.changes)
    return day, rows


def script(rows: list[list[dict]]) -> list[dict]:
    """One turn per watch: read the clock, confirm the follower's changes, advance, then read the news and the clock."""
    turns = []
    for k, changes in enumerate(rows):
        confirm = [("confirm_changes", {"changes": changes})] if changes else []
        calls = [("get_time", {}), *confirm, ("advance", {})]
        if k < len(rows) - 1:
            calls += [("get_situation", {}), ("get_time", {})]
        turns.append({"stop": "tool_use", "tool_calls": [
            {"id": f"toolu_{k}_{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(calls)]})
    return turns


def context(instance: str) -> dict:
    with namespace_routing():
        return get_task_instance_store().get(instance).context or {}


def stored(uri: str) -> bytes:
    with namespace_routing():
        return get_config().get_object_store_at(uri).get(uri)


def held(data: dict) -> dict:
    """data/get's day but the call counts, which only the env's middleware makes."""
    return {"plan": data["plan"], "frozen": data["frozen"], "changes": [
        {k: v for k, v in c.items() if k != "call"} for c in data["changes"]],
        "notices": [{k: v for k, v in n.items() if k != "call"} for n in data["notices"]],
        "refusals": data["refusals"], "estimates": data["estimates"], "grade": data["grade"]}


def check(task: Task, fake: FakeLiteLLM, ctx: dict, expected: dict, day: Day) -> dict:
    watches = timetable(task)
    v = ctx["metadata"]["verifications"]["airops"]
    episode = v["results"][0]["episode"]
    response = next(p for p in ctx["prompt_responses"] if p.get("step_id") == "play")
    summary = response["structured_output"]
    entry = ctx["metadata"]["env_trigger_state"][ENV]
    capture = json.loads(stored(entry["object_url"]))
    problems = []

    fired, actions = {}, {}
    for e in capture["state"]["events"]:
        if e["kind"] == "fired":
            fired.setdefault(e["trigger_id"], []).append(e["virtual_time"])
        elif e["kind"] == "action_ok":
            actions.setdefault(e["trigger_id"], []).append(e["detail"]["args"]["news_id"])
    for w in watches[1:]:
        if not w.news:
            continue
        tid, at = f"watch-{w.index}", virtual_time(task, w.t)
        t = entry["triggers"][tid]
        if (t["status"], t["fire_count"], fired.get(tid)) != ("fired", 1, [at]):
            problems.append(f"{tid}: {t}, fired at {fired.get(tid)}, not once at {at}")
        if actions.get(tid) != list(w.news):
            problems.append(f"{tid} delivered {actions.get(tid)}, not {list(w.news)}")

    seen = sorted({tuple(t["name"] for t in body["tools"]) for _, _, body in fake.requests})
    if any(NOTICE_TOOL in names for names in seen):
        problems.append(f"the agent was offered {NOTICE_TOOL}: {seen}")
    if held(episode) != held(json.loads(json.dumps(day.data()))):
        problems.append("data/get differs from the day played in process")

    record = json.loads(stored(response["agent_trajectory_object_url"]))
    calls = [(m["name"], json.loads(m["content"])) for m in record["messages"] if m["role"] == "tool"]
    watch, times, delivered, due = 0, {}, [], []
    for name, result in calls:
        if name == "get_time":
            times.setdefault(watch, []).append(result["current_time"])
            continue
        if result["messages"] != due:
            problems.append(f"{name} in watch {watch} carried {len(result['messages'])} messages, not {len(due)}")
        if due:
            delivered.append((watch, name, len(due)))
        due = []
        if name == "advance" and "watch" in result:
            watch = result["watch"]
            due = news(day, watch)
    if calls[-1] != ("advance", {"done": True, "cost": expected["cost"], "reward": expected["reward"],
                                 "messages": []}):
        problems.append(f"the episode ended on {calls[-1]}")
    for k, read in times.items():
        if set(read) != {virtual_time(task, watches[k].t)}:
            problems.append(f"get_time in watch {k} read {read}, not {virtual_time(task, watches[k].t)}")
    clock = capture["clock"]
    if clock.get("virtual_seconds_per_real_second") != 0 or clock.get("t0") != virtual_time(task, watches[-1].t):
        problems.append(f"the clock at the end: {clock}")

    if (v["score"], episode["grade"]["cost"]["total"], summary["reward"]) != (
            expected["reward"], expected["cost"], expected["reward"]):
        problems.append(f"scored {v['score']} (cost {episode['grade']['cost']['total']}, agent {summary['reward']}); "
                        f"expected {expected['reward']} (cost {expected['cost']})")
    if not episode["audit"]["ok"] or episode["end_reason"] != "done" or summary["end_reason"] != "done":
        problems.append(f"audit {episode['audit']}, env {episode['end_reason']}, agent {summary['end_reason']}")
    if len(fake.requests) != summary["turns"]:
        problems.append(f"{len(fake.requests)} requests for {summary['turns']} turns")
    return {"problems": problems, "score": v["score"], "expected": expected, "fired": fired, "actions": actions,
            "tools": seen, "news_delivered": delivered, "get_time": times, "clock": clock, "turns": summary["turns"],
            "tool_calls": summary["tool_calls"], "calls_used": episode["calls_used"],
            "planning_calls": episode["planning_calls"], "frozen_calls": [f["call"] for f in episode["freezes"]],
            "data_get_sha256": hashlib.sha256(json.dumps(episode, sort_keys=True).encode()).hexdigest()}


def news(day: Day, watch: int) -> list[dict]:
    """The messages a watch's news makes, as the env queues them."""
    return [{"at": hhmm(day.news[n].at), "from": day.news[n].speaker, "kind": day.news[n].kind,
             "text": day.news[n].text} for n in day.watches[watch].news]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.partition("\n\n")[0])
    parser.add_argument("--task", help="the day to play; default: the eval day with the fewest flights")
    parser.add_argument("--pack", type=Path, help="the pack the env image holds; default: the packaged b6-v1")
    parser.add_argument("--out", type=Path, help="write the day's data/get here")
    args = parser.parse_args()
    pack = tasks.pack_tasks(args.pack)
    pick = [t for t in pack if t.task_id == args.task] if args.task else sorted(
        (t for t in pack if t.split == "eval"), key=lambda t: (len(t.flights), t.task_id))[:1]
    if not pick:
        sys.exit(f"no task {args.task} in the pack")
    [task] = pick
    if "anchor" not in task.reference:
        sys.exit(f"{task.task_id} has no reference yet: the day can't be graded")
    started = time.monotonic()
    plans, followed = follower_plans(task)
    day, rows = played(task, plans)
    cost = day.grade["cost"]["total"]
    expected = {"cost": cost, "reward": round(reward(cost, task.reference["anchor"], task.reference["floor"]), 6),
                "reference_follower": task.reference["follower"]}
    if cost != followed.cost:
        sys.exit(f"the follower's decisions played in process cost {cost}, the follower {followed.cost}")
    with tempfile.TemporaryDirectory() as tmp:
        tasks.generate(Path(tmp), task_ids=[task.task_id], pack=args.pack)
        with PausingFake(script(rows), host="0.0.0.0") as fake:
            port = fake.url.rpartition(":")[2]
            run = subprocess.run([sys.executable, "-m", "agent_env.cli", "run", tmp, "--task", task.task_id,
                                  "--model", MODEL], capture_output=True, text=True,
                                 env={**os.environ, "LITELLM_BASE_URL": f"http://host.docker.internal:{port}",
                                      "LITELLM_API_KEY": "sk-fake-live"})
    print(run.stdout + run.stderr)
    instance = re.findall(r"instance (\S+)", run.stdout)
    if not instance:
        sys.exit("the run stored no instance")
    ctx = context(instance[-1])
    report = check(task, fake, ctx, expected, day)
    if args.out:
        args.out.write_text(json.dumps(ctx["metadata"]["verifications"]["airops"]["results"][0]["episode"],
                                       indent=2, sort_keys=True) + "\n")
    print(json.dumps({"instance": instance[-1], "task_id": task.task_id, "watches": len(day.watches),
                      "flights": len(task.flights), "wall_s": round(time.monotonic() - started, 1), **report},
                     indent=1, default=str))
    if report["problems"]:
        sys.exit(f"{len(report['problems'])} problems")


if __name__ == "__main__":
    main()
