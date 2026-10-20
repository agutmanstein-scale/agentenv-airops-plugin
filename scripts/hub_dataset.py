"""Build the Hugging Face dataset earakely-scale/AirOpsEnv-AgentEnv from this checkout and the recorded sweeps, and the
static replay Space that reads it; push either. Two kinds of tables share the dataset:

- v2's, the ops control center (the default): v2_tasks, the b6-v2 pack's dates per split with their references, the
  FAA's advisories as the desks get them, the desks' rules and each date's world as the env loads it; v2_runs, every
  attempt the control-center pilots recorded under --runs (the board, its labelled retry and the pilot), each replayed
  through the env's calls, with the attempt a retry superseded flagged; v2_desks, each desk of each attempt with its
  transcript; v2_board, each mode's means with the board's bootstrap CIs and the paired difference; the eval card
  results/eval-v2.json; and the bundle airops-occ, every date in both modes as the board ran them, which
  `agent-env hf run --bundle airops-occ` plays.
- AirOps's own, v1_*, per split of the pack: the days (what the planner is told, each watch's news, the reference
  costs and the task line itself), their references, and the scored runs (the grade, the final plan, the tool calls
  and the chat transcript); the model board (results) and its eval card, results/eval-v1.json, in FineEnvs' shape.
- agentenv-hf's for the bundle airops, as `agent-env hf publish` writes them: the eval days and the scored runs, each
  run's record and native trajectory, and the bundle itself, which `agent-env hf run` plays. Each sweep played a
  bundle of its own, so its runs are built from it and joined under the bundle name.

Beside them: viewer/, the runs (and any --fixture episodes) as the viewer reads them with what they point at (the
airport stages and each episode's traffic, the radar frames, the hub twins and the films' stills), with --occ-viewer's
control-center episodes merged in (scripts/occ_episodes.py writes that folder) and listed first; runs/, the sweeps and
the pilots as recorded; and the card, hub/dataset/README.md with the configs, both boards and the bundle's needs added.
Every file goes through agentenv-hf's check for keys and token shapes before anything is written. --repo pushes the
folder as one commit on top of the commit it read, removing what the build no longer writes; --space-out writes the
Space (hub/space/ and the viewer, web/) and --space-repo pushes it.

    python scripts/hub_dataset.py sol-v1 --occ-viewer cache/occ/viewer-release --out cache/hub/dataset \\
        --space-out cache/hub/space
    python scripts/hub_dataset.py sol-v1 --occ-viewer cache/occ/viewer-release --out cache/hub/dataset \\
        --repo earakely-scale/AirOpsEnv-AgentEnv --tag v0.3.1
"""

import argparse
import datetime
import gzip
import io
import json
import random
import re
import shutil
import statistics
import tomllib
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from agent_env.store.routing import namespace_routing
from agentenv_hf import dataset, records, runs, tables
from agentenv_hf.card import card
from agentenv_hf.messages import to_messages
from agentenv_hf.scan import known_values, scan
from huggingface_hub import CommitOperationAdd, CommitOperationDelete, DatasetCard, HfApi

from agentenv_airops import export, tasks
from agentenv_airops.core.model import Task
from agentenv_airops.core.reward import reward
from agentenv_airops.day import Day
from agentenv_airops.episodes import Runs
from agentenv_airops.occ import pages as occ_pages
from agentenv_airops.occ import tasks as occ_tasks
from agentenv_airops.occ.day import reward as occ_reward
from agentenv_airops.occ.pack import PACK as OCC_PACK
from agentenv_airops.occ.pack import Pack, World
from agentenv_airops.occ.replay import replay as occ_replay
from agentenv_airops.programs import occ
from agentenv_airops.schedule import ENV, TOOLS, max_turns, timetable
from agentenv_airops.sweep import POLICIES, RUNS, ci, load, priced, results

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "hub/dataset/README.md"
SPACE = ROOT / "hub/space"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
REPO = "earakely-scale/AirOpsEnv-AgentEnv"
DATASET_URL = f"https://huggingface.co/datasets/{REPO}"
SPACE_URL = f"https://huggingface.co/spaces/{REPO}"
CODE_URL = "https://github.com/earakely-scale/agentenv-airops-plugin"
BUNDLE = "airops"
TABLES = "v1"
SPLIT = "eval"
SETUP = "agent-env airops setup --agent"
AUTHORS = [{"name": "Edgar Arakelyan", "github": "earakely-scale", "affiliation": "Scale AI"}]
PROVIDERS = {"anthropic": "Anthropic", "openai": "Azure OpenAI", "fireworks_ai": "Fireworks", "groq": "Groq"}
MESSAGES = pa.field("messages", pa.list_(tables.MESSAGE))
BOARD = re.compile(r"(<!-- board -->\n).*?(\n<!-- /board -->)", re.S)
BASELINES = {"follower": "the follower: the time-line MIP re-solved at every watch on the day as known",
             "blind": "the blind follower: the same with no forecast of what isn't known yet",
             "naive": "naive: no plan, every flight as early as it can",
             "airline": "the airline: JetBlue's own cancellations and departure times as the plan"}
SOURCE = ("JetBlue's real storm days at its JFK and Boston hubs{months}: the flights, tails and delays from BTS "
          "on-time data, passengers from T-100, aircraft from the FAA registry and Form 41 B-43, hourly slot caps from "
          "what all carriers actually moved, and the FAA Command Center advisories, METARs, TAFs and forecast "
          "discussions as issued")
SELECTION = ("the pack's eval days: storm windows whose in-scope cancellations are at least 80% weather or air traffic "
             "control (BTS cause codes B and C), with 30 to 80 tails and every hub movement tail-numbered")
REFERENCE = ("the hindsight optimum: the time-line MIP (HiGHS, 1-minute arcs) on the day as it really happened, "
             "proven to 0.01%")
REWARD = ("0.2 + 0.8 exp(-g/0.5), g = max(0, cost - hindsight) / (hindsight - floor + 0.02 hindsight), the floor "
          "being the cost with no aircraft routing; cost in passenger-minutes: arrival delay, 523 per cancelled "
          "passenger and the end-of-day ferries; graded once, on the final plan flown on the day as it happened")
EXCUSED = "nothing: the env never strands a flight, so there are no rule breaks to excuse"
OCC = "v2"
OCC_BUNDLE = "airops-occ"
OCC_SETUP = "agent-env airops-occ setup"
OCC_CAPS = {"joint": 5.0, "one": 8.0}
OCC_DRAWN = json.loads((ROOT / "hub/dataset/b6-v2-draw.json").read_text())["eval"]
OCC_SPLITS = ("eval", "control", "train")
OCC_BOARD = "occ-board-sol"
OCC_LABELS = {"occ-board-sol-retry": OCC_BOARD}
OCC_MODES = {"joint": "two desks, talking", "one": "one agent at both desks"}
OCC_BOARD_MD = re.compile(r"(<!-- board-v2 -->\n).*?(\n<!-- /board-v2 -->)", re.S)
OCC_COSTS = ("delay", "cancel", "notice", "aircraft", "deadhead", "hotel", "end")
OCC_BOOT = 10000
OCC_SOURCE = ("JetBlue's whole network from 04:00 Eastern on real storm days at JFK and Boston from December 2022 to "
              "July 2026: the flights, tails and delays from BTS on-time data, aircraft from Form 41 B-43 and the FAA "
              "registry, the FAA Command Center's ground stops and ground delay programs as issued; pilots under FAR "
              "117 and flight attendants under 14 CFR 121.467 on trips generated from the published schedule, with "
              "reserves at a sourced industry band")
OCC_SELECTION = ("the pack's eval dates: per hub 8 summer and 4 winter storm dates, each from its own storm event, "
                 "where the FAA's programs controlled or held at least 5 JetBlue flights and the crew model passed its "
                 "filter (at most 1% of JetBlue's flown flights without a legal crew, crews adding at most 2% to the "
                 "calm day)")
OCC_REFERENCE = ("the best plan of aircraft and crews together a solver found knowing the whole day, replayed exactly "
                 "with a legal crew on every flight, published with its proven lower bound")
OCC_REWARD = ("0.2 + 0.8 exp(-g/0.5), g = max(0, cost - best) / max(B0 - best, 0.1 best), B0 being standard crew-desk "
              "practice with the aircraft doing nothing; cost in passenger-minutes: arrival delay, cancelled "
              "passengers, the FAA's aircraft rates, ferries, crew deadheads, hotels and crews out of place for "
              "tomorrow; reward_L is the same against the bound")
OCC_EXCUSED = ("nothing: a run is scored only if its audit passes (news, turns, causality, roles, crew legality, the "
               "bound, the desks' presence)")


def task_row(task: Task, raw: str) -> dict:
    """A day as the planner gets it, with each watch's news and the reference costs."""
    watches = timetable(task)
    news = {n.id: n for n in task.news}
    ref = task.reference
    row = {"task_id": task.task_id, "split": task.split, "carrier": task.carrier, "hub": task.hub, "date": task.date,
           "tz": task.tz, "window_start": task.window[0], "window_end": task.window[1], "start": task.start,
           "num_flights": len(task.flights), "num_tails": len(task.tails), "num_airports": len(task.airports),
           "num_watches": len(watches), "max_turns": max_turns(watches), "num_news": len(task.news),
           "airline_cancelled": sum(f.actual.cancelled for f in task.flights),
           "system_prompt": tasks.AIROPS_RULES, "situation": Day(task).briefing(),
           "watches": json.dumps([{"watch": w.index, "t": w.t, "news": [
               {"at": news[n].at, "kind": news[n].kind, "from": news[n].speaker, "text": news[n].text}
               for n in w.news]} for w in watches], ensure_ascii=False)}
    row |= {f"{key}_cost": ref.get(key) for key in ("floor", "hindsight", "anchor", *POLICIES)}
    return row | {"qualifies": ref.get("qualifies"), "task": raw}


def reference_row(task: Task) -> dict:
    ref = task.reference
    return {"task_id": task.task_id, "split": task.split, **{k: ref[k] for k in (
        "floor", "hindsight", "anchor", *POLICIES, "gap_pct", "qualifies")},
        **{f"{p}_reward": reward(ref[p], ref["anchor"], ref["floor"]) for p in POLICIES},
        "clauses": json.dumps(ref["clauses"]), "hindsight_plan": json.dumps(ref["hindsight_plan"]),
        "follower_plan": json.dumps(ref["follower_plan"])}


def episode_rows(sweep_dir: Path, days: dict[str, Task], episode_ids: dict[str, str],
                 graded: dict[str, dict]) -> list[dict]:
    """One row per scored run, with the day the verifier read from the env: its grade and final plan."""
    out = []
    for r in results(sweep_dir):
        if r["outcome"] != "scored":
            continue
        record = json.loads((sweep_dir / r["transcript"]).read_text())
        task = days[r["task_id"]]
        episode_id = episode_ids[r["instance"]]
        day = graded[episode_id]
        cost = day["grade"]["cost"]
        anchor = task.reference["anchor"]
        out.append({
            "episode_id": episode_id, "sweep": sweep_dir.name, "model": r["model"],
            "model_name": export.NAMES.get(r["model"], r["model"]), "task_id": r["task_id"], "split": task.split,
            "hub": task.hub, "rep": r["rep"], "attempt": r["attempt"], "reward": r["reward"],
            "finished": r["finished"], "cost": cost["total"], "pax_delay": cost["pax_delay"],
            "cancel_cost": cost["cancel"], "ferry_cost": cost["ferry"], "anchor": anchor,
            "gap_pct": round(100 * (cost["total"] - anchor) / anchor, 3), "cancelled": r["cancelled"],
            "auto_cancelled": r["auto_cancelled"], "num_watches": r["watches"], "planning_calls": r["planning_calls"],
            "refusals": r["refusals"], "turns": r["turns"], "tool_calls": r["tool_calls"], "checks": r["checks"],
            "confirms": r["confirms"], "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
            "cached_tokens": r["cached_tokens"], "cost_usd": priced(r), "seconds": record.get("seconds"),
            "wall_seconds": r["wall_seconds"], "end_reason": r["end_reason"],
            "final_plan": json.dumps(day["plan"]), "steps": json.dumps(record.get("steps"), ensure_ascii=False),
            "messages": to_messages(record)})
    return out


def table_bytes(rows: list[dict]) -> bytes:
    """The rows as parquet, with ``messages``, when there, typed as agentenv-hf types its chat transcripts."""
    keys = dict.fromkeys(k for row in rows for k in row if k != "messages")
    schema = pa.Table.from_pylist([{k: row.get(k) for k in keys} for row in rows]).schema
    if rows and "messages" in rows[0]:
        schema = schema.append(MESSAGES)
    return tables.parquet(rows, schema)


def by_split(name: str, rows: list[dict]) -> dict[str, bytes]:
    """A table's files, one per split it has rows in."""
    splits = dict.fromkeys(r["split"] for r in rows)
    return {f"{name}-{s}.parquet": table_bytes([r for r in rows if r["split"] == s]) for s in splits}


def board(episodes: list[dict], days: dict[str, Task]) -> list[dict]:
    """One row per model, best first: the mean over its days (a day played more than once counts as its runs' mean)
    with the bootstrap CI the sweep report draws in task id order, and the references' means on the same days."""
    rows = []
    for model in dict.fromkeys(e["model"] for e in episodes):
        eps = [e for e in episodes if e["model"] == model]
        played: dict[str, list[dict]] = {}
        for e in sorted(eps, key=lambda e: e["task_id"]):
            played.setdefault(e["task_id"], []).append(e)
        mean = {t: statistics.mean(e["reward"] for e in es) for t, es in played.items()}
        low, high = ci(list(mean.values()))
        costs = [e["cost_usd"] for e in eps if e["cost_usd"] is not None]
        seconds = [e["seconds"] for e in eps if e["seconds"] is not None]
        rows.append({
            "model": model, "model_name": export.NAMES.get(model, model),
            "provider": provider(model),
            "days": len(played), "runs": len(eps), "mean_reward": statistics.mean(mean.values()), "ci_low": low,
            "ci_high": high, **{f"{p}_reward": statistics.mean(
                reward(days[t].reference[p], days[t].reference["anchor"], days[t].reference["floor"]) for t in played)
                for p in POLICIES},
            "finished": sum(bool(e["finished"]) for e in eps),
            "mean_gap_pct": statistics.mean(e["gap_pct"] for e in eps),
            "median_turns": statistics.median(e["turns"] for e in eps if e["turns"] is not None),
            "input_tokens": sum(e["input_tokens"] or 0 for e in eps),
            "output_tokens": sum(e["output_tokens"] or 0 for e in eps), "cost_usd": sum(costs),
            "cost_per_episode": statistics.mean(costs) if costs else None,
            "median_seconds": statistics.median(seconds) if seconds else None})
    return sorted(rows, key=lambda r: -r["mean_reward"])


def provider(model: str) -> str:
    """The provider a LiteLLM model id names, or the Hugging Face router's (org/model:provider)."""
    if ":" in model:
        return f"Hugging Face ({model.rsplit(':', 1)[1]})"
    return PROVIDERS.get(model.split("/")[0], model.split("/")[0])


def board_markdown(rows: list[dict], days: int) -> str:
    lines = ["| Model | Provider | Days | Mean reward (95% CI) | Follower | Naive | Airline | Finished "
             "| Mean cost over hindsight | Median turns | Cost per episode |", "|---|---|---:|---|" + "---:|" * 7]
    for r in rows:
        lines.append(" | ".join([
            f"| {r['model_name']}", r["provider"], f"{r['days']} of {days}",
            f"**{r['mean_reward']:.3f}** ({r['ci_low']:.3f} to {r['ci_high']:.3f})",
            *(f"{r[f'{p}_reward']:.3f}" for p in ("follower", "naive", "airline")), f"{r['finished']} of {r['runs']}",
            f"{r['mean_gap_pct']:+.1f}%", f"{r['median_turns']:g}",
            f"${r['cost_per_episode']:.2f}" if r["cost_per_episode"] is not None else "–"]) + " |")
    return "\n".join(lines)


def eval_card(rows: list[dict], days: list[Task], months: list[str], date: str) -> dict:
    """The eval card in the shape FineEnvs' simulation catalog renders."""
    graded = [t for t in days if t.split == SPLIT and "anchor" in t.reference]
    when = f" in {', '.join(months)}" if months else ""
    return {
        "env": ENV, "version": "v1", "authors": AUTHORS, "framework": "agentenv",
        "tasks": {"split": SPLIT, "n": len(graded), "source": SOURCE.format(months=when), "selection": SELECTION},
        "harness": {"agent": "airops-llm", "tools": list(TOOLS),
                    "max_turns": max(max_turns(timetable(t)) for t in graded)},
        "grading": {"reference": REFERENCE, "reward": REWARD, "excused": EXCUSED, "judge": False},
        "baselines": [{"name": name, "mean": round(statistics.mean(
            reward(t.reference[p], t.reference["anchor"], t.reference["floor"]) for t in graded), 4)}
            for p, name in BASELINES.items()],
        "models": [{"model": r["model_name"], "provider": r["provider"], "runs": r["runs"],
                    "mean": round(r["mean_reward"], 4), "ci95": [round(r["ci_low"], 4), round(r["ci_high"], 4)],
                    "finished": r["finished"],
                    "usd_per_episode": None if r["cost_per_episode"] is None else round(r["cost_per_episode"], 4)}
                   for r in rows],
        "links": {"runs": f"{DATASET_URL}/viewer/{TABLES}_episodes", "replay": SPACE_URL, "code": CODE_URL},
        "date": date,
    }


def agentenv_files(bundle: Path, sweeps: list[tuple[Path, list[str]]]) -> tuple[dict[str, bytes], dict, dict]:
    """agentenv-hf's files for the bundle: its tasks from ``bundle``, and the runs ``sweeps`` list by instance id, each
    sweep built from its own bundle. Returns the files, each instance's episode id, and each episode's day as the
    verifier read it."""
    files = dataset.build(runs.locate(str(bundle)), name=BUNDLE, split=SPLIT, which="latest").files
    parts, raw, episode_ids, graded = [], {}, {}, {}
    for sweep_dir, ids in sweeps:
        played = runs.locate(str(sweep_dir / "bundle"))
        built = dataset.build(played, name=BUNDLE, split=SPLIT, which="latest", instance_ids=tuple(ids)).files
        parts.append(pq.read_table(io.BytesIO(built[f"episodes/{BUNDLE}.parquet"])))
        for line in built[f"raw/{BUNDLE}.jsonl"].decode().splitlines():
            run = json.loads(line)
            raw[run["episode_id"]] = line
            graded[run["episode_id"]] = run["record"]["metadata"]["verifications"]["airops"]["results"][0]["episode"]
        rewrite = records.Rewrite(played.id_root, BUNDLE)
        episode_ids |= {i: rewrite.text(i) for i in ids}
    empty = pq.read_table(io.BytesIO(tables.parquet([], tables.EPISODES)))
    episodes = pa.concat_tables(parts or [empty]).sort_by([("model", "ascending"), ("task", "ascending")])
    sink = io.BytesIO()
    pq.write_table(episodes, sink)
    files[f"episodes/{BUNDLE}.parquet"] = sink.getvalue()
    files[f"raw/{BUNDLE}.jsonl"] = "".join(raw[i] + "\n" for i in episodes.column("episode_id").to_pylist()).encode()
    return files, episode_ids, graded


def months(pack: Path) -> list[str]:
    """The months the pack was built from, as its manifest lists them; none for a bare tasks file."""
    return json.loads((pack / "manifest.json").read_text())["months"] if pack.is_dir() else []


def configs(files: dict[str, bytes]) -> list[dict]:
    """The dataset's own configs, v2's first and its days first of all: one per table, a split per file."""
    out = []
    for table, name in (("tasks", "tasks"), ("episodes", "runs"), ("desks", "desks")):
        found = sorted((p for p in files if p.startswith(f"{table}/{OCC}-")), key=lambda p: split_order(p))
        if found:
            out.append({"config_name": f"{OCC}_{name}", "data_files": [
                {"split": p.removesuffix(".parquet").rsplit("-", 1)[1], "path": p} for p in found]})
    if f"results/{OCC}-board.parquet" in files:
        out.insert(1, {"config_name": f"{OCC}_board", "data_files": [
            {"split": SPLIT, "path": f"results/{OCC}-board.parquet"}]})
    ours = len(out)
    for table in ("tasks", "references", "episodes"):
        found = sorted((p for p in files if p.startswith(f"{table}/{TABLES}-")), key=lambda p: split_order(p))
        if found:
            out.append({"config_name": f"{TABLES}_{table}", "data_files": [
                {"split": p.removesuffix(".parquet").rsplit("-", 1)[1], "path": p} for p in found]})
    if "results/board.parquet" in files:
        out.insert(ours + 1, {"config_name": "results", "data_files": [
            {"split": SPLIT, "path": "results/board.parquet"}]})
    return out


def split_order(path: str) -> int:
    split = path.removesuffix(".parquet").rsplit("-", 1)[1]
    return ("eval", "control", "train").index(split)


def card_text(plugin: str, files: dict[str, bytes], board_md: str = "", occ_md: str = "") -> str:
    """hub/dataset/README.md with the dataset's configs, each version's board between its markers, the bundle airops's
    two configs and needs as agentenv-hf's publish writes them, and airops-occ's needs when the build has it."""
    text = BOARD.sub(lambda m: m[1] + (f"\n{board_md}\n" if board_md else "") + m[2], CARD.read_text())
    text = OCC_BOARD_MD.sub(lambda m: m[1] + (f"\n{occ_md}\n" if occ_md else "") + m[2], text)
    ours = DatasetCard(text)
    ours.data.configs = configs(files)
    if ours.data.configs:
        ours.data.configs[0]["default"] = True
    text = card(str(ours), name=BUNDLE, split=SPLIT, repo=None, description=None, license=None,
                needs={"plugins": [plugin], "setup": SETUP})
    if not any(p.startswith(f"bundles/{OCC_BUNDLE}/") for p in files):
        return text
    both = DatasetCard(text)
    both.data.agentenv["bundles"][OCC_BUNDLE] = {"plugins": [plugin], "setup": OCC_SETUP}
    return str(both)


def build(sweeps: list[str], runs_dir: Path, out: Path, pack: Path, plugin: str, fixtures: list[Path], cache: Path,
          date: str, occ_pilots: list[str] = (), occ_pack: Path | None = None, occ_viewer: Path | None = None,
          replays: Path | None = None, workers: int = 1) -> dict[str, bytes]:
    days = tasks.pack_tasks(pack)
    raw = {json.loads(line)["task_id"]: line for line in tasks_file(pack).read_text().splitlines() if line.strip()}
    bundle = out / "bundles" / BUNDLE
    shutil.rmtree(bundle, ignore_errors=True)
    tasks.generate(bundle, split=SPLIT, pack=pack, noplay=True)
    played = [(runs_dir / name, [r["instance"] for r in results(runs_dir / name) if r["outcome"] == "scored"])
              for name in sweeps]
    files, episode_ids, graded = agentenv_files(bundle, [(d, ids) for d, ids in played if ids])
    files |= by_split(f"tasks/{TABLES}", [task_row(t, raw[t.task_id]) for t in days])
    files |= by_split(f"references/{TABLES}", [reference_row(t) for t in days if "anchor" in t.reference])
    sweep_days = {name: load(name, runs_dir).days() for name in sweeps}
    episodes = [row for d, _ in played for row in episode_rows(d, sweep_days[d.name], episode_ids, graded)]
    played_days = {t: day for found in sweep_days.values() for t, day in found.items()}
    files |= by_split(f"episodes/{TABLES}", sorted(episodes, key=lambda r: (r["model"], r["task_id"], r["rep"])))
    board_md = ""
    if episodes:
        rows = board(episodes, played_days)
        board_md = board_markdown(rows, len({e["task_id"] for e in episodes}))
        files["results/board.parquet"] = table_bytes(rows)
        card_json = eval_card(rows, days, months(pack), date)
        files["results/eval-v1.json"] = (json.dumps(card_json, indent=2, ensure_ascii=False) + "\n").encode()
    viewer = Runs(runs_dir, sweeps, cache)
    shown = viewer.episodes() + [json.loads(p.read_text()) for p in fixtures]
    for f in viewer.failed:
        print(f"not in the viewer: {f['run']} {f['model']} {f['task_id']}: {f['error']}")
    files |= {f"viewer/{path}": content for path, content in export.folder(shown, cache).items()}
    if occ_viewer:
        files = merge_viewer(files, occ_viewer)
    occ_tables, occ_md = occ_files(runs_dir, list(occ_pilots), Pack(occ_pack), replays or cache / "occ" / "replays",
                                   workers, date)
    files |= occ_tables | occ_bundle(out, occ_pack)
    for d in [d for d, _ in played] + [runs_dir / name for name in occ_pilots]:
        for path in sorted(d.rglob("*")):
            rel = path.relative_to(d)
            if path.is_file() and rel.parts[0] not in ("logs", "bundle"):
                files[f"runs/{d.name}/{rel.as_posix()}"] = path.read_bytes()
    return files | {"README.md": card_text(plugin, files, board_md, occ_md).encode()}


def occ_bundle(out: Path, pack: Path | None) -> dict[str, bytes]:
    """The bundle airops-occ as `agent-env airops-occ tasks` writes it, every date of the pack in both modes at the
    board's caps per desk, with wiring-noplay: the files the board ran, which `agent-env hf run --bundle airops-occ`
    plays."""
    folder = out / "bundles" / OCC_BUNDLE
    shutil.rmtree(folder, ignore_errors=True)
    for mode, cap in OCC_CAPS.items():
        occ_tasks.generate(folder, split="all", modes=(mode,), pack=pack, cap=cap, noplay=mode == "joint")
    return {f"bundles/{OCC_BUNDLE}/{p.relative_to(folder).as_posix()}": p.read_bytes()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def merge_viewer(files: dict[str, bytes], folder: Path) -> dict[str, bytes]:
    """The control-center viewer folder (scripts/occ_episodes.py --out) merged into the v1 build's viewer/: its index's
    entries first, then v1's; a file both write is the control-center build's, and each one that differs is named."""
    ours = {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}
    index = json.loads(ours.pop("episodes/index.json"))
    v1 = json.loads(files.get("viewer/episodes/index.json", b'{"version": 1, "episodes": []}'))
    for path, content in ours.items():
        if files.get(f"viewer/{path}", content) != content:
            print(f"viewer/{path}: the control-center build's copy replaces the v1 build's")
    entries = index["episodes"] + [e for e in v1["episodes"] if e["url"] not in {x["url"] for x in index["episodes"]}]
    return files | {f"viewer/{p}": c for p, c in ours.items()} | {"viewer/episodes/index.json": (json.dumps(
        {"version": 1, "episodes": entries}, indent=1, ensure_ascii=False) + "\n").encode()}


def occ_files(runs_dir: Path, pilots: list[str], pack: Pack, replays: Path, workers: int,
              date: str) -> tuple[dict[str, bytes], str]:
    """v2's tables and eval card, and its board as the card shows it: the pack's dates, and every attempt of
    ``pilots`` (folders under ``runs_dir`` with a pilot.json) with each desk's transcript."""
    days = sorted((occ_task_row(pack, t) for t in pack.tasks), key=lambda r: (OCC_SPLITS.index(r["split"]), r["date"]))
    files = by_split(f"tasks/{OCC}", days)
    attempts = occ_attempts(runs_dir, pilots, pack)
    if not attempts:
        return files, ""
    checked = occ_replays(attempts, runs_dir, pack, replays, workers)
    files |= by_split(f"episodes/{OCC}", [occ_run_row(a, checked[a["run_id"]], runs_dir) for a in attempts])
    files |= by_split(f"desks/{OCC}", [row for a in attempts for row in occ_desk_rows(a, runs_dir)])
    rows = occ_board(attempts)
    if not rows:
        return files, ""
    files[f"results/{OCC}-board.parquet"] = table_bytes(rows)
    files[f"results/eval-{OCC}.json"] = (json.dumps(occ_eval_card(rows, days, attempts, date), indent=2,
                                                    ensure_ascii=False) + "\n").encode()
    return files, occ_board_markdown(rows)


def occ_task_row(pack: Pack, task_id: str) -> dict:
    """A date of the pack with its references and their rewards, the FAA's advisories as the desks get them, the
    desks' rules, and the date's world as the env loads it. `drawn_for` is the storm hub an eval date was drawn for
    (hub/dataset/b6-v2-draw.json); `hub` is the task's, the airport the viewer shows."""
    entry = pack.tasks[task_id]
    raw = gzip.decompress((pack.path / "dates" / f"{entry['date']}.json.gz").read_bytes()).decode()
    world = World.from_dict(json.loads(raw))
    task, refs = world.task, world.refs
    units = Counter((u.kind, "reserve" if u.reserve else "trip") for u in world.units)
    news = sorted(occ_pages.news(task).values(), key=lambda n: (n["at"], n["id"]))
    scripted = refs.get("scripted") or {}
    row = {"task_id": task_id, "date": task.date, "split": entry["split"],
           "season": "summer" if task.date[5:7] in ("06", "07", "08") else "winter",
           "drawn_for": OCC_DRAWN.get(task.date) if entry["split"] == "eval" else None, "hub": task.hub,
           "storm_hubs": list(task.hubs), "num_flights": len(task.flights), "num_aircraft": len(task.tails),
           "num_airports": len(task.airports), "under_programs": task.clauses.get("controlled_or_held"),
           "airline_cancelled": sum(f.actual.cancelled for f in task.flights), "num_watches": len(occ.watches(task)),
           "num_advisories": len(news), "pilot_crews": units["pilot", "trip"], "cabin_crews": units["cabin", "trip"],
           "pilot_reserves": units["pilot", "reserve"], "cabin_reserves": units["cabin", "reserve"],
           "best": refs["best"], "bound": refs["bound"], "gap_pct": refs["gap_pct"], "best_from": refs["best_from"],
           "B0": refs["B0"]["notice"], "B0_flat": refs["B0"]["flat"], "idle": refs.get("idle"),
           "airline": refs.get("airline"), "scripted_joint": scripted.get("joint"), "scripted_one": scripted.get("one"),
           "calm": refs["calm"]["base"], "calm_crew_aware": refs["calm"]["crew_aware"],
           "calm_rise_pct": refs["calm"]["rise_pct"], "reserve_band_pilot": refs["bands"]["pilot"],
           "reserve_band_cabin": refs["bands"]["cabin"]}
    row |= {f"{k}_reward": None if row[k] is None else round(occ_reward(row[k], row["best"], row["B0"]), 6)
            for k in ("B0", "idle", "airline", "scripted_joint", "scripted_one")}
    return row | {"advisories": json.dumps(news, ensure_ascii=False),
                  **{f"system_prompt_{role}": occ_tasks.RULES[role] for role in ("ops", "crew", "both")},
                  "world": raw}


def occ_attempts(runs_dir: Path, pilots: list[str], pack: Pack) -> list[dict]:
    """Every attempt the pilots recorded, the board's folder and its labelled retries first. A board run's attempt is
    superseded by the last scored attempt of that run after it."""
    order = sorted(pilots, key=lambda f: (f != OCC_BOARD, OCC_LABELS.get(f) != OCC_BOARD, f))
    out = []
    for folder in order:
        pilot = json.loads((runs_dir / folder / "pilot.json").read_text())
        for row in results(runs_dir / folder):
            task_id, mode = row["run"].rsplit("-", 1)
            out.append({"run_id": f"{folder}/{row['run']}-a{row['attempt']}", "folder": folder, "pilot": pilot,
                        "row": row, "task_id": task_id, "mode": mode, "date": pack.tasks[task_id]["date"],
                        "split": pack.tasks[task_id]["split"],
                        "board": OCC_LABELS.get(folder, folder) == OCC_BOARD})
    latest = {a["row"]["run"]: a["run_id"] for a in out if a["board"] and a["row"]["outcome"] == "scored"}
    for a in out:
        last = latest.get(a["row"]["run"]) if a["board"] else None
        a["superseded_by"] = last if last and last != a["run_id"] else None
        a["on_board"] = a["board"] and last == a["run_id"]
    return out


def occ_replays(attempts: list[dict], runs_dir: Path, pack: Pack, cache: Path, workers: int) -> dict[str, dict]:
    """Each recorded day replayed through the env's calls, kept under ``cache`` by the day's digest (a replay takes
    minutes), ``workers`` at once."""
    out, todo = {}, []
    for a in attempts:
        day = a["row"].get("day")
        if not day:
            out[a["run_id"]] = {}
            continue
        path = cache / f"{a['run_id'].replace('/', '.')}.json"
        digest = json.loads((runs_dir / a["folder"] / day).read_text()).get("digest")
        kept = json.loads(path.read_text()) if path.is_file() else {}
        if kept.get("digest") == digest:
            out[a["run_id"]] = kept
        else:
            todo.append((a["run_id"], str(runs_dir / a["folder"] / day), str(pack.path), str(path)))
    if todo:
        print(f"replaying {len(todo)} control-center days, {workers} at once")
    if workers > 1 and len(todo) > 1:
        with ProcessPoolExecutor(workers) as pool:
            done = list(pool.map(replay_day, todo))
    else:
        done = [replay_day(job) for job in todo]
    for run_id, result in done:
        out[run_id] = result
        print(f"{run_id}: {'replays exactly' if result['exact'] else result['mismatches']}")
    return out


def replay_day(job: tuple[str, str, str, str]) -> tuple[str, dict]:
    run_id, day_path, pack_path, keep = job
    day = json.loads(Path(day_path).read_text())
    again = occ_replay(Pack(pack_path).world(day["task_id"]), day)
    result = {"digest": day.get("digest"), "exact": again.exact, "mismatches": len(again.mismatches),
              "first": again.mismatches[:3]}
    Path(keep).parent.mkdir(parents=True, exist_ok=True)
    Path(keep).write_text(json.dumps(result, default=str) + "\n")
    return run_id, result


def occ_run_row(a: dict, checked: dict, runs_dir: Path) -> dict:
    """An attempt: its grade against the best plan, the bound and B0, the day's costs and decisions, the requests
    between the desks, its spend and tokens, how each desk ended, and whether it replays and passes the audit."""
    r, mode = a["row"], a["mode"]
    day = json.loads((runs_dir / a["folder"] / r["day"]).read_text()) if r.get("day") else {}
    grade, desks = day.get("grade") or {}, r.get("desks") or {}
    counts, model = grade.get("counts") or {}, a["pilot"]["model"]

    def total(key: str) -> int:
        return sum(d.get(key) or 0 for d in desks.values())

    return {
        "run_id": a["run_id"], "pilot": a["folder"], "set": "board" if a["board"] else "pilot",
        "on_board": a["on_board"], "superseded_by": a["superseded_by"], "task_id": a["task_id"], "date": a["date"],
        "split": a["split"], "mode": mode, "mode_label": OCC_MODES[mode],
        "model": model, "model_name": export.NAMES.get(model, model),
        "effort": next((d["effort"] for d in desks.values() if d.get("effort")), None), "attempt": r["attempt"],
        "outcome": r["outcome"], "started_utc": r.get("started_utc"), "wall_seconds": r.get("wall_seconds"),
        **{k: r.get(k) for k in ("reward", "reward_L", "cost", "best", "bound", "gap_pct", "B0", "over_best_pct",
                                 "over_bound_pct", "avoidable_share", "above_script", "script", "below_best")},
        **{f"cost_{k}": (grade.get("costs") or {}).get(k) for k in OCC_COSTS},
        "cancelled": sum((counts.get("cancelled") or {}).values()),
        "cancelled_by": json.dumps(counts.get("cancelled") or {}), "auto_cancelled": counts.get("auto_cancelled"),
        "holds": counts.get("holds"), "claims": counts.get("claims"),
        "crew_moves": sum((counts.get("crew_moves") or {}).values()),
        "crew_moves_by": json.dumps(counts.get("crew_moves") or {}),
        "requests": sum((counts.get("requests") or {}).values()),
        "requests_by": json.dumps(counts.get("requests") or {}),
        "crew_system_alerts_by": json.dumps(counts.get("alerts") or {}),
        "spend_usd": r.get("cost_usd"), "cap_usd_per_desk": a["pilot"]["caps"][mode], "desks": sorted(desks),
        "input_tokens": total("input_tokens"), "output_tokens": total("output_tokens"),
        "cached_tokens": total("cached_tokens"), "turns": total("turns"), "tool_calls": total("tool_calls"),
        "context_max": max((d.get("context_max") or 0 for d in desks.values()), default=None),
        "failed_requests": total("failed_requests"),
        "end_reasons": json.dumps({d: v.get("end_reason") for d, v in sorted(desks.items())}),
        "errors": json.dumps({d: v["error"] for d, v in sorted(desks.items()) if v.get("error")}),
        "absent": json.dumps(r.get("absent") or {}), "replays_exactly": checked.get("exact"),
        "replay_mismatches": checked.get("mismatches"), "audit_ok": (day.get("audit") or {}).get("ok"),
        "audit": json.dumps(day.get("audit") or {}), "decisions": json.dumps(day.get("changes") or [], default=str),
        "request_log": json.dumps(day.get("requests") or [], default=str, ensure_ascii=False),
        "day_file": f"runs/{a['folder']}/{r['day']}" if r.get("day") else None}


def occ_desk_rows(a: dict, runs_dir: Path) -> list[dict]:
    """Each desk of an attempt with its transcript as chat messages."""
    out = []
    for desk, d in sorted((a["row"].get("desks") or {}).items()):
        record = json.loads((runs_dir / a["folder"] / d["transcript"]).read_text()) if d.get("transcript") else {}
        out.append({"run_id": a["run_id"], "desk": desk, "task_id": a["task_id"], "split": a["split"],
                    "mode": a["mode"], "model": a["pilot"]["model"], "on_board": a["on_board"],
                    "superseded_by": a["superseded_by"],
                    **{k: d.get(k) for k in ("end_reason", "effort", "desk_turns", "turns", "tool_calls",
                                             "input_tokens", "output_tokens", "cached_tokens", "context_max",
                                             "cost_usd", "failed_requests", "seconds", "error")},
                    "transcript_file": f"runs/{a['folder']}/{d['transcript']}" if d.get("transcript") else None,
                    "messages": to_messages(record) if record else None})
    return out


def occ_board(attempts: list[dict]) -> list[dict]:
    """Each mode's mean reward and reward_L over the board's dates, with the bootstrap CI its analysis drew (dates in
    the order the board recorded them), B0, and the two modes' paired difference."""
    final: dict[str, dict] = {}
    for a in attempts:
        if a["board"]:
            final[a["row"]["run"]] = a["row"]
    by: dict[str, dict] = {}
    for run, r in final.items():
        task_id, mode = run.rsplit("-", 1)
        by.setdefault(task_id, {})[mode] = r
    caps = next(a["pilot"]["caps"] for a in attempts if a["folder"] == OCC_BOARD) if by else {}
    rows = []
    for mode, label in OCC_MODES.items():
        played = [v[mode] for v in by.values() if mode in v and v[mode]["outcome"] == "scored"]
        if not played:
            continue
        mean, low, high = bootstrap([r["reward"] for r in played])
        mean_l, low_l, high_l = bootstrap([r["reward_L"] for r in played])
        desks = [d for r in played for d in (r.get("desks") or {}).values()]
        rows.append({"kind": "mode", "mode": mode, "label": label, "dates": len(played), "mean_reward": mean,
                     "ci_low": low, "ci_high": high, "mean_reward_L": mean_l, "ci_L_low": low_l, "ci_L_high": high_l,
                     "median_over_best_pct": round(statistics.median(r["over_best_pct"] for r in played), 2),
                     "median_avoidable_share": round(statistics.median(r["avoidable_share"] for r in played), 3),
                     "spend_usd": round(sum(r["cost_usd"] for r in played), 2),
                     "input_tokens": sum(d.get("input_tokens") or 0 for d in desks),
                     "output_tokens": sum(d.get("output_tokens") or 0 for d in desks),
                     "median_wall_seconds": statistics.median(r["wall_seconds"] for r in played),
                     "cap_usd_per_desk": caps.get(mode)})
    if not rows:
        return []
    b0 = [occ_reward(r["B0"], r["best"], r["B0"]) for v in by.values() for r in v.values() if r.get("best")]
    rows.append({"kind": "baseline", "mode": "B0", "label": "standard crew desk, aircraft doing nothing",
                 "dates": len(by), "mean_reward": round(statistics.mean(b0), 4)})
    pairs = [(v["joint"], v["one"]) for _, v in sorted(by.items())
             if {"joint", "one"} <= set(v) and v["joint"]["outcome"] == v["one"]["outcome"] == "scored"]
    if pairs:
        diff = [j["reward"] - o["reward"] for j, o in pairs]
        mean, low, high = bootstrap(diff)
        mean_l, low_l, high_l = bootstrap([j["reward_L"] - o["reward_L"] for j, o in pairs])
        cost, cost_low, cost_high = bootstrap([(j["cost"] - o["cost"]) / o["best"] * 100 for j, o in pairs])
        rows.append({"kind": "paired", "mode": "joint - one", "label": "two desks minus one agent, paired by date",
                     "dates": len(pairs), "mean_reward": mean, "ci_low": low, "ci_high": high, "mean_reward_L": mean_l,
                     "ci_L_low": low_l, "ci_L_high": high_l, "cost_diff_pct_of_best": cost,
                     "cost_diff_ci_low": cost_low, "cost_diff_ci_high": cost_high,
                     "higher_on": sum(x > 1e-9 for x in diff), "lower_on": sum(x < -1e-9 for x in diff)})
    return rows


def bootstrap(xs: list[float], n: int = OCC_BOOT, seed: int = 0) -> tuple[float, float, float]:
    """The mean and its 95% bootstrap interval, drawn as the board's analysis drew them."""
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(xs) for _ in range(len(xs))) for _ in range(n))
    return round(statistics.mean(xs), 4), round(means[int(0.025 * n)], 4), round(means[int(0.975 * n)], 4)


def occ_board_markdown(rows: list[dict]) -> str:
    modes = [r for r in rows if r["kind"] == "mode"]
    lines = ["| Mode | Dates | Mean reward (95% CI) | reward_L (95% CI) | Over the best plan, median "
             "| Avoidable cost saved, median | Spend |", "|---|---:|---|---|---:|---:|---:|"]
    for r in modes:
        lines.append(f"| GPT-6.1 Sol, {r['label']} | {r['dates']} | **{r['mean_reward']:.3f}** ({r['ci_low']:.3f}–"
                     f"{r['ci_high']:.3f}) | {r['mean_reward_L']:.3f} ({r['ci_L_low']:.3f}–{r['ci_L_high']:.3f}) | "
                     f"{r['median_over_best_pct']:.1f}% | {100 * r['median_avoidable_share']:.0f}% | "
                     f"${r['spend_usd']:.2f} |")
    for r in rows:
        if r["kind"] == "baseline":
            lines.append(f"| Standard crew desk (B0) | {r['dates']} | {r['mean_reward']:.3f} | | | 0% | |")
    for r in rows:
        if r["kind"] == "paired":
            lines += ["", f"Two desks minus one agent, paired by date: **{r['mean_reward']:+.3f}** reward (95% CI "
                      f"{r['ci_low']:.3f} to {r['ci_high']:.3f}), two desks higher on {r['higher_on']} of "
                      f"{r['dates']} dates; their days cost {-r['cost_diff_pct_of_best']:.1f}% of the best plan less "
                      f"(CI {-r['cost_diff_ci_high']:.1f} to {-r['cost_diff_ci_low']:.1f}%)."]
    return "\n".join(lines)


def occ_eval_card(rows: list[dict], days: list[dict], attempts: list[dict], date: str) -> dict:
    """v2's eval card in the shape FineEnvs' simulation catalog renders, one model entry per mode."""
    evals = [d for d in days if d["split"] == "eval"]
    model = next(a["pilot"]["model"] for a in attempts if a["board"])
    turns = max(occ_tasks.TURNS[m] * d["num_watches"] + occ_tasks.EXTRA_TURNS for d in evals for m in OCC_MODES)
    baselines = {"B0": "B0: standard crew-desk practice, the aircraft doing nothing",
                 "idle": "idle: the crew system alone, nobody at the desks",
                 "scripted_joint": "scripted desks: the crew desk forwarding its system's words, ops accepting its "
                                   "cancellations",
                 "scripted_one": "one scripted desk running both",
                 "airline": "the airline: JetBlue's own decisions, replayed with the generated crews"}
    return {
        "env": "airops-occ", "version": OCC, "authors": AUTHORS, "framework": "agentenv",
        "tasks": {"split": "eval", "n": len(evals), "source": OCC_SOURCE, "selection": OCC_SELECTION},
        "harness": {"agent": occ_tasks.AGENT, "tools": [*occ_tasks.OPS_TOOLS, *occ_tasks.CREW_TOOLS,
                                                        *occ_tasks.SHARED], "max_turns": turns},
        "grading": {"reference": OCC_REFERENCE, "reward": OCC_REWARD, "excused": OCC_EXCUSED, "judge": False},
        "baselines": [{"name": name, "mean": round(statistics.mean(d[f"{k}_reward"] for d in evals), 4)}
                      for k, name in baselines.items() if all(d[f"{k}_reward"] is not None for d in evals)],
        "models": [{"model": f"{export.NAMES.get(model, model)}, {r['label']}", "provider": provider(model),
                    "runs": r["dates"], "mean": r["mean_reward"], "ci95": [r["ci_low"], r["ci_high"]],
                    "finished": r["dates"], "usd_per_episode": round(r["spend_usd"] / r["dates"], 4)}
                   for r in rows if r["kind"] == "mode"],
        "links": {"runs": f"{DATASET_URL}/viewer/{OCC}_runs", "replay": SPACE_URL, "code": CODE_URL,
                  "fidelity": f"{CODE_URL}/blob/v{VERSION}/FIDELITY.md"},
        "date": date,
    }


def tasks_file(pack: Path) -> Path:
    return pack / "tasks.jsonl" if pack.is_dir() else pack


def space_files(revision: str) -> dict[str, bytes]:
    """The static Space: hub/space/ (its card and the page that opens the viewer on this dataset's episodes at
    ``revision``) and the viewer, web/, under viewer/."""
    files = {p.relative_to(SPACE).as_posix(): p.read_bytes() for p in sorted(SPACE.rglob("*")) if p.is_file()}
    files["index.html"] = files["index.html"].replace(b"/resolve/main", f"/resolve/{revision}".encode())
    return files | {f"viewer/{p.relative_to(export.WEB).as_posix()}": p.read_bytes()
                    for p in sorted(export.WEB.rglob("*")) if p.is_file()}


def write(files: dict[str, bytes], out: Path) -> None:
    for folder in ("tasks", "episodes", "desks", "references", "results", "raw", "runs", "bundles", "viewer"):
        shutil.rmtree(out / folder, ignore_errors=True)
    for path, content in files.items():
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_bytes(content)


def push(out: Path, repo: str, repo_type: str, tag: str | None, message: str) -> str:
    """The folder as one commit on top of the repo's current commit, deleting the files this build no longer writes;
    a concurrent push makes it fail rather than interleave."""
    api = HfApi()
    parent = api.repo_info(repo, repo_type=repo_type).sha
    paths = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    stale = [p for p in api.list_repo_files(repo, repo_type=repo_type, revision=parent)
             if p not in paths and p != ".gitattributes"]
    operations = [CommitOperationAdd(p, out / p) for p in paths] + [CommitOperationDelete(p) for p in stale]
    commit = api.create_commit(repo, operations, repo_type=repo_type, parent_commit=parent, commit_message=message)
    if tag:
        api.create_tag(repo, tag=tag, repo_type=repo_type, revision=commit.oid)
    return commit.commit_url


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sweeps", nargs="*", help="Default: every sweep under --runs.")
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=Path(str(tasks.PACK)), help="Default: the packaged b6-v1 pack.")
    ap.add_argument("--fixture", type=Path, action="append", default=[],
                    help="An episode JSON to show in the viewer too, e.g. scripts/fixture_episode.py's follower.")
    ap.add_argument("--cache", type=Path, default=ROOT / "cache",
                    help="Where the viewer's stages, traffic, radar frames, hub twins and stills are.")
    ap.add_argument("--plugin-ref", default=f"v{VERSION}", help="The plugin tag the card's bundle pins.")
    ap.add_argument("--repo", help="Push the folder to this dataset repo after writing it.")
    ap.add_argument("--tag", help="Tag the pushed commit, e.g. v0.3.1; the Space reads the dataset at it.")
    ap.add_argument("--message", default="Publish AirOpsEnv v2")
    ap.add_argument("--date", default=datetime.date.today().isoformat(), help="The eval card's date.")
    ap.add_argument("--space-out", type=Path, help="Write the static Space here.")
    ap.add_argument("--space-repo", help="Push the Space folder to this Space repo after writing it.")
    ap.add_argument("--occ", action="append", metavar="PILOT",
                    help="A control-center pilot folder under --runs for v2's tables (repeat); default: every folder "
                         "with a pilot.json.")
    ap.add_argument("--occ-pack", type=Path, default=Path(str(OCC_PACK)), help="Default: the packaged b6-v2 pack.")
    ap.add_argument("--occ-viewer", type=Path,
                    help="The control-center episodes as scripts/occ_episodes.py --out wrote them, merged into "
                         "viewer/.")
    ap.add_argument("--occ-replays", type=Path, help="Where each recorded day's replay is kept (default: --cache/occ/"
                                                     "replays); a day is replayed again only if it changed.")
    ap.add_argument("--workers", type=int, default=1, help="Replay this many control-center days at once.")
    args = ap.parse_args()
    plugin = f"agentenv-airops @ git+{CODE_URL}@{args.plugin_ref}"
    sweeps = args.sweeps or sorted(p.parent.name for p in args.runs.glob("*/sweep.json"))
    pilots = args.occ if args.occ is not None else sorted(p.parent.name for p in args.runs.glob("*/pilot.json"))
    print("sweeps:", " ".join(sweeps) or "none", "\ncontrol-center pilots:", " ".join(pilots) or "none")
    with namespace_routing():
        files = build(sweeps, args.runs, args.out, args.pack, plugin, args.fixture, args.cache, args.date,
                      occ_pilots=pilots, occ_pack=args.occ_pack, occ_viewer=args.occ_viewer, replays=args.occ_replays,
                      workers=args.workers)
    space = space_files(args.tag or "main") if args.space_out else {}
    scan(files | {f"space/{p}": c for p, c in space.items()}, known_values())
    write(files, args.out)
    counts = {p: pq.read_metadata(io.BytesIO(c)).num_rows for p, c in files.items() if p.endswith(".parquet")}
    print("\n".join(f"{n:6} {p}" for p, n in sorted(counts.items())), f"\n{len(files)} files into {args.out}")
    if space:
        shutil.rmtree(args.space_out, ignore_errors=True)
        write(space, args.space_out)
        print(f"{len(space)} files into {args.space_out}")
    if args.repo:
        print(push(args.out, args.repo, "dataset", args.tag, args.message))
    if args.space_repo:
        print(push(args.space_out, args.space_repo, "space", None, args.message))


if __name__ == "__main__":
    main()
