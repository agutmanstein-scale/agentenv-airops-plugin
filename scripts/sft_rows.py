"""A pilot's desk days as teacher rows for supervised fine-tuning: one row per desk watch, in the OpenAI chat format
TRL's SFTTrainer reads (messages and tools), each assistant turn with the reasoning_content the model wrote, as
Reflection's tool-calling reasoning data has it.

A row is the desk's rules, that watch's page and the desk's turns until it ended the watch with end_turn: the page is
a whole snapshot of the day as the desk knows it (the plan's cost, messages, flights at risk), so a watch stands on its
own. end_turn's result keeps its fields but not the next page, and a day's last result not its reward. The tools are
the ones the desk's role had in the run's own task (modify_env_tool_access), with the env's schemas, less wait_turn and
resign, which the desk agent plays unseen by the model. A watch is left out when one of its turns came without
reasoning or was cut off at the output limit. Each row carries the day's reward and cost J, so a trainer can keep the
better days.

Usage: .venv/bin/python scripts/sft_rows.py RUNS_DIR/PILOT OUT.jsonl
"""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from fastmcp import Client

from agentenv_airops.occ.live3 import OccEnv3
from agentenv_airops.programs.cost import DOLLARS_PER_PAX_MINUTE

HIDDEN = ("wait_turn", "resign")
DROPPED_FROM_END = ("page", "reward")


async def env_tools() -> dict[str, dict]:
    """Each env tool in the function format the desk agent sends it."""
    async with Client(OccEnv3().create_app()) as c:
        listed = await c.list_tools()
    return {t.name: {"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                       "parameters": t.inputSchema}} for t in listed}


def role_tools(task: list[dict], role: str, schemas: dict[str, dict]) -> list[dict]:
    """The tools a role was enabled for in the task, in that order."""
    names = [n for s in task if s.get("type") == "modify_env_tool_access" and s.get("role") == role
             and s.get("action") == "enable" for n in s["tools"]]
    return [schemas[n] for n in names if n not in HIDDEN]


def assistant(m: dict) -> dict:
    out = {"role": "assistant", "content": m.get("content") or "", "reasoning_content": m.get("reasoning") or ""}
    if m.get("tool_calls"):
        out["tool_calls"] = [{"id": c["id"], "type": "function",
                              "function": {"name": c["name"], "arguments": c["arguments"]}} for c in m["tool_calls"]]
    return out


def ended(content: str) -> dict | None:
    """end_turn's result when it ended the watch, else None (an error leaves the watch open)."""
    try:
        res = json.loads(content)
    except json.JSONDecodeError:
        return None
    return res if isinstance(res, dict) and "error" not in res and ("page" in res or res.get("done")) else None


def watches(record: dict) -> list[tuple[str, list[dict], bool]]:
    """The record's watches: each one's page, the turns until end_turn ended it (with that reply's results), and
    whether a turn of it was cut off at the output limit."""
    _, first, *rest = record["messages"]
    out, page, nxt, turns, cut, closing = [], first["content"], None, [], False, False
    for m in rest:
        if closing and m["role"] != "tool":
            out.append((page, turns, cut))
            page, turns, cut, closing = nxt, [], False, False
        if m["role"] == "assistant":
            turns.append(assistant(m))
            cut |= (m.get("stop") or "").startswith("length")
        elif m["role"] == "tool":
            content = m["content"]
            if m["name"] == "end_turn" and (res := ended(content)) is not None:
                nxt, closing = res.get("page"), True
                content = json.dumps({k: v for k, v in res.items() if k not in DROPPED_FROM_END})
            turns.append({"role": "tool", "tool_call_id": m["tool_call_id"], "name": m["name"], "content": content})
        else:
            turns.append({"role": "user", "content": m["content"]})
    if closing:
        out.append((page, turns, cut))
    return [w for w in out if w[0]]


def rows(pilot_dir: Path, schemas: dict[str, dict]) -> tuple[list[dict], Counter]:
    pilot = json.loads((pilot_dir / "pilot.json").read_text())
    out, counts = [], Counter()
    for line in (pilot_dir / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r.get("outcome") != "scored":
            continue
        task = json.loads((pilot_dir / "bundle" / "tasks" / f"{r['run']}.json").read_text())
        for desk, d in sorted((r.get("desks") or {}).items()):
            record = json.loads((pilot_dir / d["transcript"]).read_text())
            tools = role_tools(task, desk, schemas)
            system = record["messages"][0]["content"]
            played = watches(record)
            for k, (page, turns, cut) in enumerate(played):
                if cut:
                    counts["left out: a turn cut off"] += 1
                    continue
                if not all(t["reasoning_content"] for t in turns if t["role"] == "assistant"):
                    counts["left out: a turn without reasoning"] += 1
                    continue
                counts["kept"] += 1
                out.append({"messages": [{"role": "system", "content": system}, {"role": "user", "content": page},
                                         *turns],
                            "tools": tools, "model": pilot["model"], "pilot": pilot_dir.name, "run": r["run"],
                            "date": r["date"], "desk": desk, "watch": k + 1, "watches": len(played),
                            "day_reward": r["reward"], "day_cost_usd": round(r["cost"] * DOLLARS_PER_PAX_MINUTE)})
    return out, counts


def main():
    pilot_dir, out = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    made, counts = rows(pilot_dir, asyncio.run(env_tools()))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in made))
    chars = [len(json.dumps(r["messages"])) for r in made]
    thoughts = [len(m["reasoning_content"]) for r in made for m in r["messages"] if m["role"] == "assistant"]
    print(f"{out}: {len(made)} rows from {pilot_dir.name}; {dict(counts)}")
    if made:
        print(f"  about {sum(chars) / 3.5 / 1e6:.2f}M tokens, a row's mean {sum(chars) / len(chars) / 3.5:,.0f} and "
              f"largest {max(chars) / 3.5:,.0f}; {len(thoughts)} thoughts, mean {sum(thoughts) / len(thoughts):,.0f} "
              "characters")


if __name__ == "__main__":
    main()
