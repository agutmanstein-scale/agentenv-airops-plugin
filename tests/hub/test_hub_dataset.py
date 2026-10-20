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
from agentenv_airops.occ import tasks as occ_tasks
from agentenv_airops.occ.day import reward as occ_reward
from agentenv_airops.occ.pack import Pack
from agentenv_airops.schedule import TOOLS, max_turns, timetable

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tasks.jsonl"
E2E = ROOT / "tests" / "export" / "fixtures"
DAY = E2E / "e2e-day.jsonl"
ID = "b6-jfk-20240109-1800"
OCC_EVAL, OCC_CONTROL, OCC_OTHER = "b6-20240713-0400", "b6-20240703-0400", "b6-20250111-0400"
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
    assert data["pretty_name"] == "AirOpsEnv v2: the ops control center"
    assert "\n<h1>AirOpsEnv v2: the ops control center</h1>\n" in text
    assert "\n# AirOpsEnv v1: one desk at one hub\n" in text
    assert "<!-- board -->\n\n| Claude |\n\n<!-- /board -->" in text
    assert "<!-- board -->\n\n<!-- /board -->" in hub.card_text(PLUGIN, files)


def test_the_readme_and_the_hub_sources_pin_this_version_of_the_plugin_and_the_dataset():
    pin = re.compile(r"(?:agentenv-airops-plugin(?:@|/blob/|/)"
                     r"|AirOpsEnv-AgentEnv(?:@|/resolve/|%2Fresolve%2F))(v\d+\.\d+\.\d+)")
    for path in ("README.md", "hub/dataset/README.md", "hub/space/README.md"):
        pins = pin.findall((ROOT / path).read_text())
        assert pins and set(pins) == {TAG}, path
    films = re.findall(r"agentenv-airops-plugin/releases/tag/(v\d+\.\d+\.\d+)", (ROOT / "README.md").read_text())
    assert TAG in films and set(films) <= {TAG, "v0.2.0"}


def _dry_build(hub, tmp_path, monkeypatch, *extra: str) -> Path:
    out = tmp_path / "out"
    occ = [] if "--occ-pack" in extra else ["--occ-pack", str(_occ_pack(tmp_path))]
    monkeypatch.setattr(sys, "argv", ["hub_dataset.py", "--runs", str(tmp_path / "runs"), "--out", str(out),
                                      "--cache", str(tmp_path / "cache"), *occ, *extra])
    hub.main()
    return out


def _occ_pack(tmp_path: Path, ids: tuple[str, ...] = (OCC_EVAL, OCC_CONTROL)) -> Path:
    """The packaged b6-v2 pack cut down to ``ids``."""
    pack, out = Pack(), tmp_path / "b6-v2"
    (out / "dates").mkdir(parents=True, exist_ok=True)
    for i in ids:
        name = f"{pack.tasks[i]['date']}.json.gz"
        (out / "dates" / name).write_bytes((pack.path / "dates" / name).read_bytes())
    (out / "manifest.json").write_text(json.dumps(pack.manifest | {"tasks": {i: pack.tasks[i] for i in ids}}))
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
    assert data["configs"][0]["config_name"] == "v2_tasks" and data["configs"][0]["default"] is True
    assert data["agentenv"]["default"] == "airops"
    assert [r["task_id"] for r in _rows(out, "tasks/v2-eval.parquet")] == [OCC_EVAL]
    assert not list(out.glob("episodes/v2-*")) and not list(out.glob("desks/*"))


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
    assert names == ["v2_tasks", "v1_tasks", "results", "v1_references", "v1_episodes", "airops_tasks",
                     "airops_episodes"]


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


def test_a_v2_day_row_carries_its_references_their_rewards_the_advisories_the_rules_and_the_world(hub):
    pack = Pack()
    world = pack.world(OCC_EVAL)
    row = hub.occ_task_row(pack, OCC_EVAL)
    assert (row["date"], row["split"], row["season"], row["storm_hubs"], row["num_flights"]) == (
        "2024-07-13", "eval", "summer", ["BOS"], len(world.task.flights))
    assert (row["best"], row["bound"], row["B0"]) == (world.refs["best"], world.refs["bound"],
                                                      world.refs["B0"]["notice"])
    assert row["B0_reward"] == 0.308268 and row["idle_reward"] == round(occ_reward(row["idle"], row["best"],
                                                                                   row["B0"]), 6)
    assert row["pilot_crews"] + row["pilot_reserves"] == sum(u.kind == "pilot" for u in world.units)
    news = json.loads(row["advisories"])
    assert len(news) == row["num_advisories"] > 0 and [n["at"] for n in news] == sorted(n["at"] for n in news)
    assert row["system_prompt_crew"] == occ_tasks.RULES["crew"] and json.loads(row["world"])["task_id"] == OCC_EVAL


def _occ_row(run: str, attempt: int, reward_: float, cost: float, desks: tuple[str, ...], folder: Path) -> dict:
    """A pilot's results row as `agent-env airops-occ pilot` writes it, with its day and its desks' transcripts."""
    day = {"task_id": run.rsplit("-", 1)[0], "mode": run.rsplit("-", 1)[1], "digest": f"{run}-a{attempt}",
           "audit": {"ok": True, "problems": {}}, "changes": [{"desk": "ops", "action": "cancel", "target": 3}],
           "requests": [{"id": 1, "type": "HOLD", "status": "accepted"}],
           "grade": {"costs": {"delay": cost - 10, "end": 10.0}, "counts": {
               "cancelled": {"confirm": 2, "accept": 1}, "auto_cancelled": 0, "holds": 1, "claims": 0,
               "crew_moves": {"assign": 3}, "requests": {"HOLD accepted": 1}, "alerts": {"HOLD": 4}}}}
    (folder / "days").mkdir(parents=True, exist_ok=True)
    (folder / "days" / f"{run}-a{attempt}.json").write_text(json.dumps(day))
    stats = {}
    for desk in desks:
        path = f"transcripts/{run}-a{attempt}-{desk}.json"
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(json.dumps({"messages": [{"role": "user", "content": "page"}, {
            "role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "end_turn", "arguments": "{}"}]}]}))
        stats[desk] = {"end_reason": "stand_down", "effort": "high", "desk_turns": 2, "turns": 3, "tool_calls": 4,
                       "input_tokens": 100, "output_tokens": 10, "cached_tokens": 50, "context_max": 900,
                       "cost_usd": 0.5, "failed_requests": 0, "seconds": 60.0, "error": None, "transcript": path}
    return {"pilot": folder.name, "run": run, "attempt": attempt, "started_utc": "2026-10-19T18:00:00Z",
            "wall_seconds": 600.0, "exit": 0, "outcome": "scored", "cost_usd": 0.5 * len(desks), "desks": stats,
            "absent": {}, "day": f"days/{run}-a{attempt}.json", "reward": reward_, "reward_L": reward_ - 0.1,
            "cost": cost, "best": 1000.0, "bound": 900.0, "gap_pct": 10.0, "B0": 2000.0,
            "over_best_pct": cost / 10 - 100, "over_bound_pct": 0.0, "avoidable_share": (2000 - cost) / 1000,
            "above_script": None, "script": None, "below_best": False}


def _occ_runs(tmp_path: Path) -> tuple[Path, Path]:
    """The board on two dates, a retry folder replacing one of its runs, and a pilot run on the control day, with
    each recorded day's replay kept as the build keeps it."""
    runs, replays = tmp_path / "runs", tmp_path / "replays"
    plan = {"occ-board-sol": [(f"{OCC_EVAL}-joint", 0.7, 1300.0), (f"{OCC_EVAL}-one", 0.5, 1500.0),
                              (f"{OCC_OTHER}-joint", 0.9, 1100.0), (f"{OCC_OTHER}-one", 0.6, 1400.0)],
            "occ-board-sol-retry": [(f"{OCC_OTHER}-joint", 0.4, 1600.0)],
            "occ-pilot-canary": [(f"{OCC_CONTROL}-joint", 0.8, 1200.0)]}
    for name, played in plan.items():
        folder = runs / name
        rows = [_occ_row(run, 1, score, cost, ("crew", "ops") if run.endswith("joint") else ("both",), folder)
                for run, score, cost in played]
        (folder / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (folder / "pilot.json").write_text(json.dumps({"name": name, "model": GPT, "runs": [r["run"] for r in rows],
                                                       "caps": {"joint": 5.0, "one": 8.0}, "budget": 93.0}))
        replays.mkdir(exist_ok=True)
        for r in rows:
            run_id = f"{name}/{r['run']}-a1"
            (replays / f"{run_id.replace('/', '.')}.json").write_text(json.dumps(
                {"digest": run_id.split("/")[1], "exact": True, "mismatches": 0, "first": []}))
    return runs, replays


def test_v2s_runs_mark_the_superseded_attempt_and_the_board_reads_the_retry(hub, tmp_path):
    runs, replays = _occ_runs(tmp_path)
    pack = Pack(_occ_pack(tmp_path, (OCC_EVAL, OCC_CONTROL, OCC_OTHER)))
    files, board_md = hub.occ_files(runs, ["occ-pilot-canary", "occ-board-sol-retry", "occ-board-sol"], pack,
                                    replays, 1, "2026-10-19")
    rows = {r["run_id"]: r for r in pq.read_table(io.BytesIO(files["episodes/v2-eval.parquet"])).to_pylist()}
    masked, retry = rows[f"occ-board-sol/{OCC_OTHER}-joint-a1"], rows[f"occ-board-sol-retry/{OCC_OTHER}-joint-a1"]
    assert (masked["on_board"], masked["superseded_by"]) == (False, retry["run_id"])
    assert (retry["on_board"], retry["superseded_by"], retry["set"]) == (True, None, "board")
    assert sum(r["on_board"] for r in rows.values()) == 4 and retry["date"] == "2025-01-11"
    assert (retry["replays_exactly"], retry["audit_ok"], retry["cancelled"], retry["crew_moves"]) == (True, True, 3, 3)
    assert (retry["desks"], retry["input_tokens"], retry["cap_usd_per_desk"]) == (["crew", "ops"], 200, 5.0)
    [canary] = pq.read_table(io.BytesIO(files["episodes/v2-control.parquet"])).to_pylist()
    assert (canary["set"], canary["on_board"], canary["split"]) == ("pilot", False, "control")
    desks = pq.read_table(io.BytesIO(files["desks/v2-eval.parquet"]))
    assert desks.num_rows == 2 + 1 + 2 + 1 + 2 and desks.schema.field("messages").type == pa.list_(hub.tables.MESSAGE)
    joint, one, b0, paired = pq.read_table(io.BytesIO(files["results/v2-board.parquet"])).to_pylist()
    assert (joint["mode"], joint["dates"], joint["mean_reward"]) == ("joint", 2, round((0.7 + 0.4) / 2, 4))
    assert (one["mean_reward"], b0["mean_reward"]) == (0.55, 0.3083)
    assert (paired["mean_reward"], paired["higher_on"], paired["lower_on"]) == (0.0, 1, 1)
    assert joint["ci_low"] <= joint["mean_reward"] <= joint["ci_high"] and joint["spend_usd"] == 2.0
    card = json.loads(files["results/eval-v2.json"])
    assert set(card) == EVAL_CARD and (card["env"], card["version"], card["tasks"]["n"]) == ("airops-occ", "v2", 2)
    assert [m["model"] for m in card["models"]] == ["GPT-6.1 Sol, two desks, talking",
                                                    "GPT-6.1 Sol, one agent at both desks"]
    assert "| GPT-6.1 Sol, two desks, talking | 2 | **0.550**" in board_md


def test_a_recorded_day_is_replayed_again_only_when_it_changed(hub, tmp_path, monkeypatch):
    runs, replays = _occ_runs(tmp_path)
    stale = replays / f"occ-board-sol.{OCC_EVAL}-one-a1.json"
    stale.write_text(json.dumps({"digest": "before", "exact": False, "mismatches": 2, "first": []}))
    done = []
    monkeypatch.setattr(hub, "replay_day", lambda job: done.append(job[0]) or (job[0], {"exact": True,
                                                                                         "mismatches": 0}))
    pack = Pack(_occ_pack(tmp_path, (OCC_EVAL, OCC_CONTROL, OCC_OTHER)))
    attempts = hub.occ_attempts(runs, ["occ-board-sol"], pack)
    checked = hub.occ_replays(attempts, runs, pack, replays, 1)
    assert done == [f"occ-board-sol/{OCC_EVAL}-one-a1"] and all(c["exact"] for c in checked.values())


def test_the_control_center_viewer_is_merged_in_first_and_its_copy_of_a_shared_file_wins(hub, tmp_path, capsys):
    v1 = {"viewer/episodes/index.json": json.dumps({"version": 1, "episodes": [{"url": "v1.json"}]}).encode(),
          "viewer/episodes/v1.json": b"{}", "viewer/stage/JFK/stage.json.gz": b"old"}
    occ_viewer = tmp_path / "occ"
    for path, content in {"episodes/index.json": json.dumps({"version": 1, "episodes": [
            {"url": "occ.json", "occ": "two desks", "featured": 1}]}), "episodes/occ.json": "{}",
            "stage/JFK/stage.json.gz": "new"}.items():
        (occ_viewer / path).parent.mkdir(parents=True, exist_ok=True)
        (occ_viewer / path).write_text(content)
    files = hub.merge_viewer(v1, occ_viewer)
    assert json.loads(files["viewer/episodes/index.json"])["episodes"] == [
        {"url": "occ.json", "occ": "two desks", "featured": 1}, {"url": "v1.json"}]
    assert files["viewer/stage/JFK/stage.json.gz"] == b"new" and files["viewer/episodes/v1.json"] == b"{}"
    assert "viewer/stage/JFK/stage.json.gz" in capsys.readouterr().out


def test_the_card_leads_with_v2s_tables_and_board_and_keeps_v1s(hub):
    files = {p: b"" for p in ("tasks/v2-eval.parquet", "tasks/v2-train.parquet", "tasks/v2-control.parquet",
                              "episodes/v2-eval.parquet", "desks/v2-eval.parquet", "results/v2-board.parquet",
                              "tasks/v1-eval.parquet", "results/board.parquet", "episodes/v1-eval.parquet")}
    text = hub.card_text(PLUGIN, files, "| Claude |", "| GPT-6.1 Sol, two desks |")
    data = DatasetCard(text).data.to_dict()
    assert [c["config_name"] for c in data["configs"]] == [
        "v2_tasks", "v2_board", "v2_runs", "v2_desks", "v1_tasks", "results", "v1_episodes", "airops_tasks",
        "airops_episodes"]
    assert [c for c in data["configs"] if c.get("default")] == [data["configs"][0]]
    assert [f["split"] for f in data["configs"][0]["data_files"]] == ["eval", "control", "train"]
    assert "<!-- board-v2 -->\n\n| GPT-6.1 Sol, two desks |\n\n<!-- /board-v2 -->" in text
    assert text.index("<!-- board-v2 -->") < text.index("<!-- board -->")
