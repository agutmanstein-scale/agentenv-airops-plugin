"""scripts/hub_dataset.py: its rows, the board and the eval card, the card it writes for agent-env hf run, the pins, a
dry build without a sweep and with one (its run in both kinds of table, on the board and in the viewer with the assets
and traffic its episode points at), the secret scan, and the static Space it assembles."""

import gzip
import io
import json
import re
import sys
import tomllib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from agent_env.bundle.parse import parse_bundle
from agent_env.config import get_config
from agent_env.store.routing import namespace_routing
from agent_env.task.store import TASK_INSTANCES_COLLECTION
from huggingface_hub import DatasetCard

from agentenv_airops import export, stage, tasks
from agentenv_airops.core.model import load_pack
from agentenv_airops.core.reward import reward
from agentenv_airops.day import Day
from agentenv_airops.episodes import replay
from agentenv_airops.schedule import TOOLS, max_turns, timetable

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tasks.jsonl"
E2E = ROOT / "tests" / "export" / "fixtures"
DAY = E2E / "e2e-day.jsonl"
ID = "b6-jfk-20240109-1800"
SONNET, GPT = "anthropic/claude-sonnet-5-5", "openai/gpt-6.1-sol"
TAG = f"v{tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']}"
PLUGIN = f"agentenv-airops @ git+https://github.com/earakely-scale/agentenv-airops-plugin@{TAG}"
EVAL_CARD = {"env", "version", "authors", "framework", "tasks", "harness", "grading", "baselines", "models", "links",
             "date"}


def test_a_day_row_is_what_the_planner_gets_with_each_watchs_news_and_the_reference_costs(hub):
    [task] = load_pack(DAY)
    row = hub.task_row(task, "{}")
    watches = timetable(task)
    assert (row["task_id"], row["split"], row["hub"], row["num_flights"], row["num_watches"]) == (
        ID, "eval", "JFK", 45, len(watches))
    assert row["situation"] == Day(task).briefing() and row["system_prompt"] == tasks.AIROPS_RULES
    assert row["max_turns"] == max_turns(watches) and row["task"] == "{}"
    assert [[n["at"] for n in w["news"]] for w in json.loads(row["watches"])] == [
        [next(n.at for n in task.news if n.id == i) for i in w.news] for w in watches]
    assert (row["anchor_cost"], row["follower_cost"], row["qualifies"]) == (667266, 691780, False)


def test_a_reference_row_carries_each_policys_cost_and_reward_and_the_plans(hub):
    [task] = load_pack(DAY)
    row = hub.reference_row(task)
    ref = task.reference
    assert row["follower_reward"] == reward(ref["follower"], ref["anchor"], ref["floor"])
    assert round(row["follower_reward"], 6) == 0.840588
    assert json.loads(row["follower_plan"]) == ref["follower_plan"] and set(json.loads(row["clauses"])) >= {"tails"}


def _run(model: str, task: str, score: float, cost_usd: float | None = 0.5, finished: bool = True,
         gap: float = 5.0) -> dict:
    return {"model": model, "task_id": task, "reward": score, "finished": finished, "gap_pct": gap, "turns": 9,
            "input_tokens": 1000, "output_tokens": 100, "cost_usd": cost_usd, "seconds": 60.0}


def test_the_board_ranks_models_by_their_mean_over_days_next_to_the_references_on_those_days(hub):
    days = {t.task_id: t for t in load_pack(FIXTURES)}
    a, b = "b6-jfk-20230626-1400-1700", "b6-bos-20230626-1400"
    episodes = [_run(GPT, a, 0.2, finished=False, cost_usd=None), _run(GPT, b, 0.4),
                _run(SONNET, a, 1.0), _run(SONNET, a, 0.8), _run(SONNET, b, 0.6, gap=15.0)]
    sonnet, gpt = hub.board(episodes, days)
    assert (sonnet["model_name"], sonnet["provider"], sonnet["days"], sonnet["runs"]) == (
        "Claude Sonnet 5.5", "Anthropic", 2, 3)
    assert sonnet["mean_reward"] == 0.75 and sonnet["ci_low"] <= 0.75 <= sonnet["ci_high"]
    ref = {t: days[t].reference for t in (a, b)}
    assert sonnet["follower_reward"] == (reward(ref[a]["follower"], ref[a]["anchor"], ref[a]["floor"])
                                         + reward(ref[b]["follower"], ref[b]["anchor"], ref[b]["floor"])) / 2
    assert (gpt["finished"], gpt["cost_usd"], gpt["cost_per_episode"], sonnet["mean_gap_pct"]) == (1, 0.5, 0.5,
                                                                                                    25 / 3)
    table = hub.board_markdown([sonnet, gpt], 2).splitlines()
    assert table[2].startswith("| Claude Sonnet 5.5 | Anthropic | 2 of 2 | **0.750**")
    assert table[2].endswith("| 3 of 3 | +8.3% | 9 | $0.50 |")
    assert hub.board([_run("Qwen/Qwen3.8-27B:ovhcloud", a, 0.3)], days)[0]["provider"] == "Hugging Face (ovhcloud)"


def test_the_eval_card_has_fineenvs_shape(hub):
    days = load_pack(FIXTURES)
    rows = hub.board([_run(SONNET, days[0].task_id, 0.9)], {t.task_id: t for t in days})
    card = hub.eval_card(rows, days, ["2023-06"], "2026-10-13")
    assert set(card) == EVAL_CARD and card["env"] == "airops-day" and card["version"] == "v1"
    assert card["tasks"]["n"] == len(days) and "2023-06" in card["tasks"]["source"]
    assert card["harness"] == {"agent": "airops-llm", "tools": list(TOOLS),
                               "max_turns": max(max_turns(timetable(t)) for t in days)}
    assert card["grading"]["judge"] is False and [b["name"].split(":")[0] for b in card["baselines"]] == [
        "the follower", "the blind follower", "naive", "the airline"]
    assert card["models"] == [{"model": "Claude Sonnet 5.5", "provider": "Anthropic", "runs": 1, "mean": 0.9,
                               "ci95": [0.9, 0.9], "finished": 1, "usd_per_episode": 0.5}]
    assert card["links"]["runs"].endswith("/viewer/v1_episodes")


def test_the_card_lists_the_tables_with_the_days_first_and_the_bundle_for_agent_env_hf_run(hub):
    files = {"tasks/v1-train.parquet": b"", "tasks/v1-eval.parquet": b"", "results/board.parquet": b"",
             "episodes/v1-eval.parquet": b""}
    text = hub.card_text(PLUGIN, files, "| Claude |")
    data = DatasetCard(text).data.to_dict()
    assert [c["config_name"] for c in data["configs"]] == [
        "v1_tasks", "results", "v1_episodes", "airops_tasks", "airops_episodes"]
    assert data["configs"][0]["default"] is True and [c for c in data["configs"] if c.get("default")] == [
        data["configs"][0]]
    assert data["configs"][0]["data_files"] == [{"split": "eval", "path": "tasks/v1-eval.parquet"},
                                                {"split": "train", "path": "tasks/v1-train.parquet"}]
    assert data["agentenv"] == {"default": "airops", "bundles": {"airops": {
        "plugins": [PLUGIN], "setup": "agent-env airops setup --agent"}}}
    assert {"rl-environment", "agentenv"} <= set(data["tags"]) and data["license"] == "other"
    assert data["pretty_name"] == "AirOpsEnv v1" and "\n<h1>AirOpsEnv v1</h1>\n" in text
    assert "<!-- board -->\n\n| Claude |\n\n<!-- /board -->" in text
    assert "<!-- board -->\n\n<!-- /board -->" in hub.card_text(PLUGIN, files)


def test_the_readme_and_the_hub_sources_pin_this_version_of_the_plugin_and_the_dataset():
    pin = re.compile(r"(?:agentenv-airops-plugin(?:@|/blob/|/releases/tag/|/)"
                     r"|AirOpsEnv-AgentEnv(?:@|/resolve/|%2Fresolve%2F))(v\d+\.\d+\.\d+)")
    for path in ("README.md", "hub/dataset/README.md", "hub/space/README.md"):
        pins = pin.findall((ROOT / path).read_text())
        assert pins and set(pins) == {TAG}, path


def _dry_build(hub, tmp_path, monkeypatch, *extra: str) -> Path:
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["hub_dataset.py", "--runs", str(tmp_path / "runs"), "--out", str(out),
                                      "--cache", str(tmp_path / "cache"), *extra])
    hub.main()
    return out


def _rows(out: Path, path: str) -> list[dict]:
    return pq.read_table(out / path).to_pylist()


def test_a_dry_build_without_a_sweep_has_the_days_the_bundle_and_no_runs(hub, local_stores, tmp_path, monkeypatch):
    (tmp_path / "runs").mkdir()
    out = _dry_build(hub, tmp_path, monkeypatch)
    pack = tasks.pack_tasks()
    for split in ("eval", "control", "train"):
        assert [r["task_id"] for r in _rows(out, f"tasks/v1-{split}.parquet")] == [
            t.task_id for t in pack if t.split == split]
    eval_days = [t.task_id for t in pack if t.split == "eval"]
    bundled = sorted(p.stem for p in (out / "bundles/airops/tasks").glob("*.json"))
    assert bundled == sorted([*eval_days, "wiring-noplay"])
    assert len(_rows(out, "tasks/airops.parquet")) == len(eval_days) + 1 and _rows(out, "episodes/airops.parquet") == []
    assert not (out / "results").exists() and not list(out.glob("episodes/v1-*"))
    assert json.loads((out / "viewer/episodes/index.json").read_text()) == {"version": 1, "episodes": []}
    data = DatasetCard((out / "README.md").read_text()).data.to_dict()
    assert data["configs"][0]["config_name"] == "v1_tasks" and data["agentenv"]["default"] == "airops"


def _sweep(tmp_path: Path, transcript: bytes) -> tuple[Path, str]:
    """A sweep as `agent-env airops sweep run` leaves it, the e2e run its one scored attempt, and the run's instance in
    the store as `agent-env run` stores it."""
    sweep = tmp_path / "runs" / "e2e-sonnet"
    tasks.generate(sweep / "bundle", task_ids=[ID], pack=DAY, episode_cap_usd=1.0)
    (sweep / "logs").mkdir()
    (sweep / "logs" / "preflight.log").write_text("dry run")
    (sweep / "sweep.json").write_text(json.dumps({"name": "e2e-sonnet", "models": [SONNET], "tasks": [ID], "k": 1,
                                                  "episode_cap_usd": 1.0, "pack": str(DAY)}))
    transcript_path = f"transcripts/anthropic-claude-sonnet-5-5/{ID}-r1-a1.json"
    (sweep / transcript_path).parent.mkdir(parents=True)
    (sweep / transcript_path).write_bytes(transcript)
    task_id = f"{parse_bundle(sweep / 'bundle').id_root}/{ID}"
    instance = f"{task_id}-abcd1234"
    [task] = load_pack(DAY)
    day = replay(task, json.loads(transcript)["messages"]).day.data()
    with namespace_routing():
        get_config().get_document_store().insert(TASK_INSTANCES_COLLECTION, {
            "instance_id": instance, "task_id": task_id, "task_version": 1, "status": "completed", "current_step": 9,
            "total_steps": 9, "created_at_utc": "2026-10-13 09:00 UTC", "completed_at_utc": "2026-10-13 09:02 UTC",
            "context": {"agent_model": SONNET, "prompt_responses": [{"step_id": "play", "model": SONNET,
                                                                   "response": "done", "tool_call_count": 43}],
                        "metadata": {"verifications": {"airops": {"score": day["reward"], "results": [
                            {"criterion": "the final plan's reward", "result": False, "score": day["reward"],
                             "episode": day}]}}}}})
    row = {"sweep": "e2e-sonnet", "model": SONNET, "task_id": ID, "rep": 1, "attempt": 1, "instance": instance,
           "started_utc": "2026-10-13T09:00:00Z", "wall_seconds": 51.5, "outcome": "scored", "error_code": None,
           "reward": day["reward"], "finished": True, "day_cost": 691780, "watches": 9, "planning_calls": 9,
           "refusals": 0, "cancelled": 7, "auto_cancelled": 0, "end_reason": "done", "turns": 9, "tool_calls": 43,
           "checks": 0, "confirms": 9, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0,
           "priced_cost_usd": 0.0, "agent_reward": day["reward"], "transcript": transcript_path}
    (sweep / "results.jsonl").write_text(json.dumps(row) + "\n")
    return sweep, instance


def test_a_dry_build_with_a_sweep_publishes_its_run_in_both_kinds_of_table_on_the_board_and_in_the_viewer(
        hub, local_stores, tmp_path, monkeypatch):
    _sweep(tmp_path, (E2E / "e2e-transcript.json").read_bytes())
    (tmp_path / "cache").mkdir()
    out = _dry_build(hub, tmp_path, monkeypatch, "--pack", str(DAY), "--date", "2026-10-13")
    [run] = _rows(out, "episodes/v1-eval.parquet")
    assert run["episode_id"].startswith("airops/") and (run["sweep"], run["reward"], run["cost"]) == (
        "e2e-sonnet", 0.840588, 691780)
    assert run["gap_pct"] == round(100 * (691780 - 667266) / 667266, 3) and run["model_name"] == "Claude Sonnet 5.5"
    assert json.loads(run["final_plan"])[0] == {"flight": 0, "cancel": False, "not_before": 1129}
    assert pq.read_schema(out / "episodes/v1-eval.parquet").field("messages").type == pa.list_(
        hub.tables.MESSAGE)
    tool = next(m for m in run["messages"] if m["tool_calls"])
    assert json.loads(tool["tool_calls"][0])["function"]["arguments"] == {}
    [published] = _rows(out, "episodes/airops.parquet")
    assert (published["episode_id"], published["reward"]) == (run["episode_id"], 0.840588)
    assert len((out / "raw/airops.jsonl").read_text().splitlines()) == 1
    [ref] = _rows(out, "references/v1-eval.parquet")
    assert ref["anchor"] == 667266
    [board] = _rows(out, "results/board.parquet")
    assert (board["model"], board["days"], board["mean_reward"]) == (SONNET, 1, 0.840588)
    card = json.loads((out / "results/eval-v1.json").read_text())
    assert set(card) == EVAL_CARD and card["models"][0]["mean"] == 0.8406 and card["date"] == "2026-10-13"
    index = json.loads((out / "viewer/episodes/index.json").read_text())
    [entry] = index["episodes"]
    episode = json.loads((out / "viewer/episodes" / entry["url"]).read_text())
    export.validate(episode)
    assert (entry["run"], entry["reward"], episode["record"]["sweep"]) == ("e2e-sonnet", 0.840588, "e2e-sonnet")
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert {"runs/e2e-sonnet/sweep.json", "runs/e2e-sonnet/results.jsonl",
            f"runs/e2e-sonnet/transcripts/anthropic-claude-sonnet-5-5/{ID}-r1-a1.json"} <= files
    assert not {f for f in files if f.startswith(("runs/e2e-sonnet/bundle", "runs/e2e-sonnet/logs"))}
    text = (out / "README.md").read_text()
    assert "| Claude Sonnet 5.5 | Anthropic | 1 of 1 | **0.841** (0.841 to 0.841) |" in text
    names = [c["config_name"] for c in DatasetCard(text).data.to_dict()["configs"]]
    assert names == ["v1_tasks", "results", "v1_references", "v1_episodes", "airops_tasks", "airops_episodes"]


def test_a_key_in_a_transcript_stops_the_build_before_anything_is_written(hub, local_stores, tmp_path, monkeypatch):
    leaked = (E2E / "e2e-transcript.json").read_text().replace('"errors":[]', '"errors":["sk-' + "a" * 32 + '"]')
    _sweep(tmp_path, leaked.encode())
    with pytest.raises(ValueError, match="looks like it holds a API key; nothing was published"):
        _dry_build(hub, tmp_path, monkeypatch, "--pack", str(DAY))
    assert not (tmp_path / "out" / "README.md").exists()


def test_the_space_is_the_viewer_opened_on_the_datasets_episodes_at_a_revision(hub):
    files = hub.space_files(TAG)
    card = DatasetCard(files["README.md"].decode()).data.to_dict()
    assert (card["sdk"], card["app_file"], card["datasets"]) == ("static", "index.html", [hub.REPO])
    assert card["title"] == "AirOpsEnv v1" and len(card["short_description"]) <= 60
    assert (f"https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv/resolve/{TAG}".encode()
            in files["index.html"]) and b"viewer/index.html" in files["index.html"]
    web = {f"viewer/{p.relative_to(export.WEB).as_posix()}": p.read_bytes() for p in export.WEB.rglob("*")
           if p.is_file()}
    assert {k: v for k, v in files.items() if k.startswith("viewer/")} == web
    assert b"/resolve/main" in hub.space_files("main")["index.html"]


def test_messages_are_typed_as_agentenv_hf_types_a_transcript(hub):
    record = {"messages": [{"role": "user", "content": "hi"},
                           {"role": "assistant", "content": "", "tool_calls": [
                               {"id": "c1", "name": "check_plan", "arguments": '{"changes": []}'}]},
                           {"role": "tool", "tool_call_id": "c1", "name": "check_plan", "content": "{}"}]}
    table = pq.read_table(io.BytesIO(hub.table_bytes([{"task_id": "t", "split": "eval",
                                                       "messages": hub.to_messages(record)}])))
    assert table.schema.field("messages").type == pa.list_(hub.tables.MESSAGE)
    [message] = [m for m in table.column("messages").to_pylist()[0] if m["tool_calls"]]
    assert json.loads(message["tool_calls"][0])["function"]["arguments"] == {"changes": []}


def test_the_dry_build_copies_the_twin_and_stage_the_viewer_episodes_point_at_with_their_traffic(
        hub, local_stores, tmp_path, monkeypatch):
    _sweep(tmp_path, (E2E / "e2e-transcript.json").read_bytes())
    cache = tmp_path / "cache"
    twin, stage_dir = cache / "twin", cache / "stage" / "JFK"
    for folder in (twin, stage_dir, cache / "traffic"):
        folder.mkdir(parents=True)
    (twin / "JFK.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    (twin / "index.json").write_text(json.dumps({"JFK": {"url": "JFK.geojson", "sha256": "0" * 64,
                                                         "attribution": "© OpenStreetMap contributors"}}))
    (stage_dir / "stage.json.gz").write_bytes(gzip.compress(json.dumps({"hub": "JFK"}).encode()))
    (stage_dir / "ortho.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (cache / "traffic" / f"{ID}.json.gz").write_bytes(gzip.compress(json.dumps({"task_id": ID}).encode()))
    monkeypatch.setattr(stage, "sidecar", lambda task, executed, traffic, hub_stage, crew: {
        "hub": hub_stage["hub"], "task_id": traffic["task_id"], "legs": len(executed)})
    out = _dry_build(hub, tmp_path, monkeypatch, "--pack", str(DAY))
    [entry] = json.loads((out / "viewer/episodes/index.json").read_text())["episodes"]
    episode = json.loads((out / "viewer/episodes" / entry["url"]).read_text())
    assert episode["twin"]["url"] == "../twin/JFK.geojson"
    assert (out / "viewer/twin/JFK.geojson").read_bytes() == (twin / "JFK.geojson").read_bytes()
    assert episode["stage"] == {"hub": "JFK", "url": "../stage/JFK/stage.json.gz",
                                "traffic": f"{entry['url'].removesuffix('.json')}.stage.json.gz"}
    for name in ("stage.json.gz", "ortho.jpg"):
        assert (out / "viewer/stage/JFK" / name).read_bytes() == (stage_dir / name).read_bytes()
    traffic = json.loads(gzip.decompress((out / "viewer/episodes" / episode["stage"]["traffic"]).read_bytes()))
    assert traffic == {"hub": "JFK", "task_id": ID, "legs": len(episode["executed"])}
