"""Build the Hugging Face dataset earakely-scale/AirOpsEnv-AgentEnv from this checkout and the recorded sweeps, and the
static replay Space that reads it; push either. Two kinds of tables share the dataset:

- AirOps's own, per split of the pack: the days (what the planner is told, each watch's news, the reference costs and
  the task line itself), their references, and the scored runs (the grade, the final plan, the tool calls and the
  chat transcript); the model board (results) and its eval card, results/eval-v1.json, in FineEnvs' shape.
- agentenv-hf's for the bundle airops, as `agent-env hf publish` writes them: the eval days and the scored runs, each
  run's record and native trajectory, and the bundle itself, which `agent-env hf run` plays. Each sweep played a
  bundle of its own, so its runs are built from it and joined under the bundle name.

Beside them: viewer/, the runs (and any --fixture episodes) as the viewer reads them with the radar frames and hub
twins they point at; runs/, the sweeps as recorded; and the card, hub/dataset/README.md with the configs, the board and
the bundle's needs added. Every file goes through agentenv-hf's check for keys and token shapes before anything is
written. --repo pushes the folder as one commit on top of the commit it read, removing what the build no longer
writes; --space-out writes the Space (hub/space/ and the viewer, web/) and --space-repo pushes it.

    python scripts/hub_dataset.py --out cache/hub/dataset --space-out cache/hub/space
    python scripts/hub_dataset.py --out cache/hub/dataset --repo earakely-scale/AirOpsEnv-AgentEnv --tag v0.1.0
"""

import argparse
import datetime
import io
import json
import re
import shutil
import statistics
import tomllib
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


def eval_card(rows: list[dict], days: list[Task], name: str, months: list[str], date: str) -> dict:
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
        "links": {"runs": f"{DATASET_URL}/viewer/{name}_episodes", "replay": SPACE_URL, "code": CODE_URL},
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


def manifest(pack: Path) -> dict:
    """The pack's manifest; a bare tasks file is named by its stem."""
    return json.loads((pack / "manifest.json").read_text()) if pack.is_dir() else {"name": pack.stem, "months": []}


def configs(files: dict[str, bytes], name: str) -> list[dict]:
    """The dataset's own configs, tasks first: one per table, a split per file."""
    out = []
    for table in ("tasks", "references", "episodes"):
        found = sorted((p for p in files if p.startswith(f"{table}/{name}-")), key=lambda p: split_order(p))
        if found:
            out.append({"config_name": f"{name}_{table}", "data_files": [
                {"split": p.removesuffix(".parquet").rsplit("-", 1)[1], "path": p} for p in found]})
    if "results/board.parquet" in files:
        out.insert(1, {"config_name": "results", "data_files": [{"split": SPLIT, "path": "results/board.parquet"}]})
    return out


def split_order(path: str) -> int:
    split = path.removesuffix(".parquet").rsplit("-", 1)[1]
    return ("eval", "control", "train").index(split)


def card_text(plugin: str, files: dict[str, bytes], name: str, board_md: str = "") -> str:
    """hub/dataset/README.md with the dataset's configs, the board between its markers, and the bundle's two configs
    and needs, as agentenv-hf's publish writes them."""
    text = BOARD.sub(lambda m: m[1] + (f"\n{board_md}\n" if board_md else "") + m[2], CARD.read_text())
    ours = DatasetCard(text)
    ours.data.configs = configs(files, name)
    if ours.data.configs:
        ours.data.configs[0]["default"] = True
    return card(str(ours), name=BUNDLE, split=SPLIT, repo=None, description=None, license=None,
                needs={"plugins": [plugin], "setup": SETUP})


def build(sweeps: list[str], runs_dir: Path, out: Path, pack: Path, plugin: str, fixtures: list[Path], cache: Path,
          date: str) -> dict[str, bytes]:
    days = tasks.pack_tasks(pack)
    meta = manifest(pack)
    name = meta["name"]
    raw = {json.loads(line)["task_id"]: line for line in tasks_file(pack).read_text().splitlines() if line.strip()}
    bundle = out / "bundles" / BUNDLE
    shutil.rmtree(bundle, ignore_errors=True)
    tasks.generate(bundle, split=SPLIT, pack=pack, noplay=True)
    played = [(runs_dir / name, [r["instance"] for r in results(runs_dir / name) if r["outcome"] == "scored"])
              for name in sweeps]
    files, episode_ids, graded = agentenv_files(bundle, [(d, ids) for d, ids in played if ids])
    files |= by_split(f"tasks/{name}", [task_row(t, raw[t.task_id]) for t in days])
    files |= by_split(f"references/{name}", [reference_row(t) for t in days if "anchor" in t.reference])
    sweep_days = {name: load(name, runs_dir).days() for name in sweeps}
    episodes = [row for d, _ in played for row in episode_rows(d, sweep_days[d.name], episode_ids, graded)]
    played_days = {t: day for found in sweep_days.values() for t, day in found.items()}
    files |= by_split(f"episodes/{name}", sorted(episodes, key=lambda r: (r["model"], r["task_id"], r["rep"])))
    board_md = ""
    if episodes:
        rows = board(episodes, played_days)
        board_md = board_markdown(rows, len({e["task_id"] for e in episodes}))
        files["results/board.parquet"] = table_bytes(rows)
        card_json = eval_card(rows, days, name, meta["months"], date)
        files["results/eval-v1.json"] = (json.dumps(card_json, indent=2, ensure_ascii=False) + "\n").encode()
    viewer = Runs(runs_dir, sweeps, cache)
    shown = viewer.episodes() + [json.loads(p.read_text()) for p in fixtures]
    for f in viewer.failed:
        print(f"not in the viewer: {f['run']} {f['model']} {f['task_id']}: {f['error']}")
    files |= {f"viewer/{path}": content for path, content in export.folder(shown, cache).items()}
    for d, _ in played:
        for path in sorted(d.rglob("*")):
            rel = path.relative_to(d)
            if path.is_file() and rel.parts[0] not in ("logs", "bundle"):
                files[f"runs/{d.name}/{rel.as_posix()}"] = path.read_bytes()
    return files | {"README.md": card_text(plugin, files, name, board_md).encode()}


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
    for folder in ("tasks", "episodes", "references", "results", "raw", "runs", "bundles", "viewer"):
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
    ap.add_argument("--cache", type=Path, default=ROOT / "cache", help="Where the radar frames and hub twins are.")
    ap.add_argument("--plugin-ref", default=f"v{VERSION}", help="The plugin tag the card's bundle pins.")
    ap.add_argument("--repo", help="Push the folder to this dataset repo after writing it.")
    ap.add_argument("--tag", help="Tag the pushed commit, e.g. v0.1.0; the Space reads the dataset at it.")
    ap.add_argument("--message", default="Publish AirOpsEnv on AgentEnv")
    ap.add_argument("--date", default=datetime.date.today().isoformat(), help="The eval card's date.")
    ap.add_argument("--space-out", type=Path, help="Write the static Space here.")
    ap.add_argument("--space-repo", help="Push the Space folder to this Space repo after writing it.")
    args = ap.parse_args()
    plugin = f"agentenv-airops @ git+{CODE_URL}@{args.plugin_ref}"
    sweeps = args.sweeps or sorted(p.parent.name for p in args.runs.glob("*/sweep.json"))
    print("sweeps:", " ".join(sweeps) or "none")
    with namespace_routing():
        files = build(sweeps, args.runs, args.out, args.pack, plugin, args.fixture, args.cache, args.date)
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
