"""Build the reference follower's day as an episode for the viewer, from this repo's own references: the follower (the
time-line MIP re-solved at every watch on the day as known, refs.follower) plays a day of the pack watch by watch as a
planner would (get_situation, confirm_changes with its new plan, advance), its record is replayed and exported as a
recorded run is (agentenv_airops.episodes, agentenv_airops.export) into cache/episodes/, with the radar frames and hub
twin found under cache/. A day without its reference gets one computed in memory, as scripts/references.py computes
it. --small also writes a trimmed copy for the viewer's tests.

usage: python scripts/fixture_episode.py [--pack data/b6-v1] [--task ID] [--out cache]
           [--small tests/viewer/fixtures/episode_small.json]
"""

import argparse
import copy
import json
import sys
from pathlib import Path
from unittest import mock

from agentenv_airops import export
from agentenv_airops.core.model import Task, load_pack
from agentenv_airops.day import Day, changes_to
from agentenv_airops.episodes import play, replay
from agentenv_airops.live import _json
from agentenv_airops.refs import follower, mip
from agentenv_airops.tasks import AIROPS_RULES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from references import compute  # noqa: E402

MODEL = "follower"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--pack", type=Path, default=ROOT / "data" / "b6-v1")
    p.add_argument("--task", help="Default: the pack's eval day with the fewest flights.")
    p.add_argument("--out", type=Path, default=ROOT / "cache", help="The viewer folder: episodes/ go under it.")
    p.add_argument("--cache", type=Path, default=ROOT / "cache", help="Where the radar frames and hub twins are.")
    p.add_argument("--small", type=Path, help="Also write a trimmed copy here, with its index.json beside it.")
    args = p.parse_args(argv)
    days = load_pack(args.pack)
    task = next(t for t in days if t.task_id == args.task) if args.task else min(
        (t for t in days if t.split == "eval"), key=lambda t: (len(t.flights), t.task_id))
    if "anchor" not in task.reference:
        task = Task.from_dict(task.to_dict() | {"reference": compute(task.to_dict())["reference"]})
    plans, followed = follower_plans(task)
    played = replay(task, record(task, plans)["messages"])
    if played.day.grade["cost"]["total"] != followed.cost:
        raise SystemExit(f"the follower's plans played through the day cost {played.day.grade['cost']['total']}, "
                         f"the follower {followed.cost}")
    episode = export.episode(task, played.day.data(), played.steps, model=MODEL, run="fixture", cache=args.cache,
                             record={"note": "the reference follower's plans, played through the env's tools"})
    export.write(args.out, [episode], args.cache)
    path = args.out / "episodes" / export.file_name(episode)
    print(f"wrote {path} ({path.stat().st_size / 1e3:.0f} kB): reward {episode['reward']}, cost "
          f"{episode['cost']['total']}, {len(episode['watches'])} watches, {len(episode['steps'])} steps")
    if args.small:
        small = trim(episode)
        export.validate(small)
        args.small.parent.mkdir(parents=True, exist_ok=True)
        args.small.write_text(json.dumps(small, indent=1) + "\n")
        index = {"version": 1, "episodes": [export.entry(small, args.small.name)]}
        (args.small.parent / "index.json").write_text(json.dumps(index, indent=1) + "\n")
        print(f"wrote {args.small}")
    return 0


def follower_plans(task: Task) -> tuple[list[dict[int, dict]], follower.Followed]:
    """The follower's plan after each watch, read off its solves, and its run."""
    solves = []

    def solve(*a, **k):
        solution = mip.solve(*a, **k)
        solves.append((set(k["fixed"] if "fixed" in k else a[3]), solution))
        return solution

    with mock.patch.object(follower, "solve", solve):
        followed = follower.follow(task)
    plans, plan = [], {}
    for pinned, solution in solves:
        plan = {i: plan[i] if i in pinned else d for i, d in solution.plan.items()}
        plans.append(plan)
    return plans, followed


def record(task: Task, plans: list[dict[int, dict]]) -> dict:
    """airops-llm's record of a planner that confirms each watch's plan, answered as the env answers, the watch's news
    delivered after its advance as the triggers deliver it."""
    day = Day(task)
    messages = [{"role": "system", "content": AIROPS_RULES}, {"role": "user", "content": day.briefing()}]
    while not day.done:
        k = day.watch
        calls = [("get_situation", {})]
        if rows := changes_to(day, plans[k]):
            calls.append(("confirm_changes", {"changes": rows}))
        calls.append(("advance", {}))
        ids = [f"call_{k}_{i}" for i in range(len(calls))]
        pairs = list(zip(ids, calls, strict=True))
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": i, "name": name, "arguments": json.dumps(args)} for i, (name, args) in pairs]})
        for i, (name, args) in pairs:
            day.calls += 1
            messages.append({"role": "tool", "tool_call_id": i, "name": name,
                             "content": _json(play(day, name, args.get("changes")))})
        if not day.done:
            for n in day.watches[day.watch].news:
                day.notice(n, "trigger")
    return {"model": MODEL, "messages": messages}


def trim(episode: dict) -> dict:
    """A small, self-contained copy: six aircraft (and those their flights touch), their flights, the first three
    watches, no radar or twin."""
    e = copy.deepcopy(episode)
    first = {t["tail"] for t in e["task"]["tails"][:6]}
    flown_on = {x["id"]: x["tail"] for x in e["executed"]}
    keep = {s["id"] for s in e["schedule"] if s["tail"] in first or flown_on[s["id"]] in first}
    tails = first | {flown_on[i] for i in keep if flown_on[i]} | {s["tail"] for s in e["schedule"] if s["id"] in keep}
    e["task"]["tails"] = [t for t in e["task"]["tails"] if t["tail"] in tails]
    used = {a for s in e["schedule"] if s["id"] in keep for a in (s["orig"], s["dest"])} | {e["task"]["hub"]}
    e["task"]["airports"] = {a: v for a, v in e["task"]["airports"].items() if a in used}
    e["schedule"] = [s for s in e["schedule"] if s["id"] in keep]
    e["executed"] = [x for x in e["executed"] if x["id"] in keep]
    e["frozen"] = [x for x in e["frozen"] if x["tail"] in tails]
    e["ferries"] = []
    e["watches"] = e["watches"][:3]
    last = e["watches"][-1]["t"]
    for w in e["watches"]:
        w["projected"] = [x for x in w["projected"] if x["id"] in keep]
        w["changes"] = [c for c in w["changes"] if c["id"] in keep]
        w["news"] = w["news"][:4]
    e["steps"] = [s for s in e["steps"] if s["t"] <= last]
    e["news"] = [n for w in e["watches"] for n in w["news"]]
    e["radar"], e["twin"] = None, None
    e["run"] = "fixture-small"
    return e


if __name__ == "__main__":
    sys.exit(main())
