"""`agent-env airops sweep run` with a stand-in `agent-env run`: the rows it records, its spend cap, retries, stops and
resume, the bundle it plays and the days it refuses, and the reader of a run the stores hold."""

import json
import os
import re
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_env.config import get_config
from agent_env.store.routing import namespace_routing
from agent_env.task.store import get_task_instance_store, seed_task_instance_context
from agent_env.task_step.context import TaskStepContext
from airops_contexts import FIXTURES, agent_failed, crashed, scored, step_failed, summary, timed_out
from click.testing import CliRunner

from agentenv_airops import sweep, tasks
from agentenv_airops.cli import airops
from agentenv_airops.core.model import load_pack

SONNET, GPT = "anthropic/claude-sonnet-5-5", "openai/gpt-6.1-sol"
T1, T2, T3 = "b6-jfk-20230626-1400-1700", "b6-jfk-20230626-1400", "b6-bos-20230626-1400"
ROW_KEYS = ["sweep", "model", "task_id", "rep", "attempt", "started_utc", "wall_seconds", "exit", "instance", "outcome",
            "failed_step", "error_code", "error", "retryable", "reward", "finished", "day_cost", "watches",
            "planning_calls", "refusals", "cancelled", "auto_cancelled", "end_reason", "turns", "tool_calls", "checks",
            "confirms", "input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens", "cost_usd",
            "priced_cost_usd", "failed_requests", "agent_reward", "transcript"]


def sweep_run(*args: str, name: str = "s", pack: Path | None = FIXTURES):
    return CliRunner().invoke(airops, ["sweep", "run", "--name", name, *args, *(["--pack", str(pack)] if pack else [])])


def rows(name: str = "s") -> list[dict]:
    return sweep.results(Path("results/runs") / name)


def brief(row: dict) -> tuple:
    return row["task_id"], row["attempt"], row["outcome"], row["error_code"], row["cost_usd"], row["retryable"]


def test_the_sweep_runs_agent_env_as_a_module_of_this_python():
    assert sweep.AGENT_ENV == [sys.executable, "-m", "agent_env.cli"]


def test_an_attempt_is_one_row_with_the_score_the_day_and_the_agents_summary(fake):
    fake.script({"*": [{"context": scored(T1, SONNET)}]})
    result = sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "10")
    assert result.exit_code == 0, result.output
    [row] = rows()
    assert list(row) == ROW_KEYS
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", row.pop("started_utc"))
    assert row.pop("instance").startswith(f"@local/fake/tasks/{T1}-")
    assert row == {
        "sweep": "s", "model": SONNET, "task_id": T1, "rep": 1, "attempt": 1, "wall_seconds": 12.5, "exit": 0,
        "outcome": "scored", "failed_step": None, "error_code": None, "error": None, "retryable": False,
        "reward": 0.840588, "finished": True, "day_cost": 691780, "watches": 9, "planning_calls": 9, "refusals": 1,
        "cancelled": 4, "auto_cancelled": 1, "end_reason": "done", "turns": 9, "tool_calls": 43, "checks": 2,
        "confirms": 9, "input_tokens": 251050, "output_tokens": 17392, "cached_tokens": 200000,
        "cache_write_tokens": 30000, "cost_usd": 0.224018, "priced_cost_usd": 0.224018, "failed_requests": 0,
        "agent_reward": 0.840588, "transcript": f"transcripts/anthropic-claude-sonnet-5-5/{T1}-r1-a1.json"}
    assert sweep.priced(row | {"cost_usd": 2.7, "priced_cost_usd": 0.23, "failed_requests": 1}) == 0.23
    assert sweep.priced(row | {"priced_cost_usd": None}) == 0.224018
    out = Path("results/runs/s")
    assert json.loads((out / row["transcript"]).read_text())["uri"] == (
        "file:///state/agent-env/object_store/trajectories/play.json")
    log = out / f"logs/anthropic-claude-sonnet-5-5/{T1}-r1-a1.log"
    assert "12.5s, instance @local/fake/tasks/" in log.read_text()
    assert json.loads((out / "sweep.json").read_text()) == {
        "name": "s", "models": [SONNET], "tasks": [T1], "k": 1, "episode_cap_usd": 5.0,
        "pack": f"~/{FIXTURES.relative_to(Path.home())}"}
    assert [p.name for p in (out / "bundle/tasks").iterdir()] == [f"{T1}.json"]
    assert result.output.splitlines() == [
        f"start {SONNET} {T1} r1 a1 (spent $0.00 of $10.00)",
        f"done {SONNET} {T1} r1 a1: reward 0.840588 (done), $0.2240 (spent $0.22)",
        f"{SONNET}: 1 scored, 0 unscored, 0 not final, of 1 runs; $0.22 spent"]


def test_the_bundle_plays_the_days_of_the_pack_with_the_episode_cap_and_the_billing(fake):
    fake.script({"*": [{"context": scored(T1, SONNET)}]})
    assert sweep_run("--models", SONNET, "--tasks", f"{T2},{T1}", "--cap-usd", "10", "--episode-cap-usd", "0.5",
                     "--hf-bill-to", "ScaleAI").exit_code == 0
    bundle = Path("results/runs/s/bundle")
    days = {t.task_id: t for t in load_pack(FIXTURES)}
    for task_id in (T1, T2):
        played = json.loads((bundle / f"tasks/{task_id}.json").read_text())
        assert played == json.loads(json.dumps(tasks.steps(days[task_id], 0.5, "ScaleAI")))
        assert next(s for s in played if s["id"] == "deploy-agent")["env_vars"] == {
            "AIROPS_MAX_COST_USD": "0.5", "HF_BILL_TO": "ScaleAI"}
    assert (bundle / "artifacts/airops-verifier/verify.py").is_file()
    assert sweep.load("s") == sweep.Sweep("s", [SONNET], [T2, T1], 1, 0.5, sweep.home_relative(FIXTURES), "ScaleAI")
    assert sweep.load("s").days().keys() == {t.task_id for t in load_pack(FIXTURES)}
    assert sweep.home_relative(Path("/opt/pack.jsonl")) == "/opt/pack.jsonl"


def test_each_attempt_is_a_fresh_agent_env_run_that_inherits_the_environment(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "patches"))
    fake.script({"*": [{"context": scored(T1, SONNET)}]})
    assert sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "10").exit_code == 0
    bundle = str(Path.cwd() / "results/runs/s/bundle")
    started = [c for c in fake.calls() if "start" in c]
    assert [c["argv"] for c in started] == [["run", bundle, "--task", T1, "--dry-run"],
                                            ["run", bundle, "--task", T1, "--model", SONNET]]
    assert {c["pythonpath"] for c in started} == {str(tmp_path / "patches")}


def test_a_failed_dry_run_stops_the_sweep_before_any_attempt(fake, monkeypatch):
    monkeypatch.setenv("FAKE_DRY_RUN_EXIT", "1")
    fake.script({"*": [{"context": scored(T1, SONNET)}]})
    result = sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "10")
    assert result.exit_code == 1
    assert f"stopped: the dry run of {T1} exited 1" in result.output
    assert fake.runs() == [] and rows() == []


def test_a_sweep_resumes_its_runs_that_are_not_final_and_refuses_other_settings(fake):
    fake.script({"*": [{"context": scored(T1, SONNET, cost=0.3)}]})
    args = ["--models", SONNET, "--tasks", f"{T1},{T2}", "--episode-cap-usd", "0.5"]
    first = sweep_run(*args, "--cap-usd", "0.6")
    assert first.exit_code == 1
    assert first.output.splitlines()[-1] == ("stopped: another attempt could take the spend past $0.60 ($0.30 spent, "
                                             "$0.50 an attempt at most); 1 runs not final")
    second = sweep_run(*args, "--cap-usd", "5", "--parallel", "2")
    assert second.exit_code == 0, second.output
    third = sweep_run(*args, "--cap-usd", "5")
    assert third.exit_code == 0 and not [line for line in third.output.splitlines() if line.startswith("start")]
    assert [r["task_id"] for r in rows()] == [T1, T2]
    assert [a[a.index("--task") + 1] for a in fake.runs()] == [T1, T2]
    for other in (["--models", GPT, "--tasks", f"{T1},{T2}", "--episode-cap-usd", "0.5"],
                  ["--models", SONNET, "--tasks", T1, "--episode-cap-usd", "0.5"],
                  [*args, "--k", "2"], ["--models", SONNET, "--tasks", f"{T1},{T2}"],
                  [*args, "--hf-bill-to", "ScaleAI"]):
        refused = sweep_run(*other, "--cap-usd", "5")
        assert refused.exit_code == 2 and "is another sweep" in refused.output
    other_pack = sweep_run(*args, "--cap-usd", "5", pack=Path(__file__).resolve().parents[1] / "export/fixtures/"
                           "e2e-day.jsonl")
    assert other_pack.exit_code == 2 and "not task ids of the pack" in other_pack.output


def test_failures_are_retried_twice_a_capped_episode_is_final_and_a_deploy_failure_costs_nothing(fake):
    fake.script({
        f"{SONNET}|{T1}": [{"context": agent_failed(T1, SONNET, "provider_error"), "exit": 1}],
        f"{SONNET}|{T2}": [{"context": agent_failed(T2, SONNET, "cost_cap", cost=0.49), "exit": 1}],
        f"{SONNET}|{T3}": [{"context": step_failed("deploy", "deploy_env"), "exit": 1},
                           {"context": scored(T3, SONNET)}],
    })
    result = sweep_run("--models", SONNET, "--tasks", f"{T1},{T2},{T3}", "--cap-usd", "10", "--episode-cap-usd",
                       "0.5", "--parallel", "1")
    assert result.exit_code == 0, result.output
    assert [brief(r) for r in rows()] == [
        (T1, 1, "failed", "provider_error", 0.05, True), (T2, 1, "failed", "cost_cap", 0.49, False),
        (T3, 1, "failed", "RuntimeError", 0.0, True), (T1, 2, "failed", "provider_error", 0.05, True),
        (T3, 2, "scored", None, 0.224018, False), (T1, 3, "failed", "provider_error", 0.05, False)]
    first, _, deploy, *_ = rows()
    assert (first["failed_step"], first["error"], first["end_reason"], first["reward"]) == (
        "play", "provider_error: the episode stopped", "provider_error", None)
    assert (deploy["failed_step"], deploy["error"], deploy["transcript"]) == (
        "deploy", "deploy failed: the sandbox did not start", None)
    assert f"failed {SONNET} {T1} r1 a1: provider_error retry ($0.0500)" in result.output
    assert f"failed {SONNET} {T2} r1 a1: cost_cap final ($0.4900)" in result.output
    assert f"{SONNET}: 1 scored, 2 unscored, 0 not final, of 3 runs; $0.86 spent" in result.output


def test_a_run_the_provider_refused_is_final(fake):
    fake.script({"*": [{"context": agent_failed(T1, SONNET, "provider_refused", cost=0), "exit": 1}]})
    result = sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "1", "--episode-cap-usd", "0.5")
    assert result.exit_code == 0, result.output
    assert [brief(r) for r in rows()] == [(T1, 1, "failed", "provider_refused", 0.0, False)]


@pytest.mark.parametrize(("context", "code"), [(None, "no_instance"), (timed_out(), "TimeoutError"),
                                               (crashed(T1, SONNET), "RuntimeError")])
def test_a_run_without_a_recorded_spend_is_retried_and_counted_at_the_cap(fake, context, code):
    fake.script({"*": [{"context": context, "exit": 1}]})
    result = sweep_run("--models", SONNET, "--tasks", f"{T1},{T2},{T3}", "--cap-usd", "1.4", "--episode-cap-usd",
                       "0.5", "--parallel", "1")
    assert result.exit_code == 1
    assert [brief(r) for r in rows()] == [(T1, 1, "failed", code, None, True), (T2, 1, "failed", code, None, True)]
    assert f"failed {SONNET} {T1} r1 a1: {code} retry (no spend recorded, counted as $0.50)" in result.output
    assert result.output.splitlines()[-1].startswith("stopped: another attempt could take the spend past $1.40 "
                                                     "($1.00 spent")


@pytest.mark.parametrize("code", ["model_endpoint", "unpriced_model"])
def test_a_missing_endpoint_or_price_starts_nothing_new_and_lets_running_attempts_finish(fake, code):
    fake.script({f"{SONNET}|{T1}": [{"context": agent_failed(T1, SONNET, code, cost=0.0), "exit": 1}],
                 "*": [{"context": scored(T2, SONNET), "sleep": 0.5}]})
    result = sweep_run("--models", SONNET, "--tasks", f"{T1},{T2},{T3}", "--cap-usd", "10", "--parallel", "2")
    assert result.exit_code == 1
    assert [brief(r) for r in rows()] == [(T1, 1, "failed", code, 0.0, True), (T2, 1, "scored", None, 0.224018, False)]
    assert result.output.splitlines()[-1] == (f"stopped: {code} from {SONNET} {T1} r1 a1: {code}: the episode "
                                              "stopped")


def test_a_day_the_env_ended_is_scored_and_not_finished(fake):
    fake.script({"*": [{"context": scored(T1, SONNET, reward=0.4, end_reason="turn_limit", day_cost=900000)}]})
    assert sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "10").exit_code == 0
    [row] = rows()
    assert (row["outcome"], row["reward"], row["finished"], row["agent_reward"], row["day_cost"]) == (
        "scored", 0.4, False, None, 900000)


def test_a_signal_tears_the_attempts_down_and_their_rows_dont_count(fake, monkeypatch):
    fake.script({"*": [{"wait": True, "context": scored(T1, SONNET, cost=0.1) | {"metadata": {}}}]})
    sleep, sent = time.sleep, []

    def interrupt(seconds):
        if fake.runs() and not sent:
            sent.append(os.kill(os.getpid(), signal.SIGINT))
        sleep(seconds)

    monkeypatch.setattr(sweep.time, "sleep", interrupt)
    result = sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "10")
    assert result.exit_code == 130
    [row] = rows()
    assert (row["outcome"], row["exit"], row["error_code"], row["cost_usd"], row["retryable"]) == (
        "interrupted", 143, None, 0.1, True)
    assert f"interrupted {SONNET} {T1} r1 a1 ($0.1000)" in result.output
    monkeypatch.setattr(sweep.time, "sleep", sleep)
    fake.script({"*": [{"context": agent_failed(T1, SONNET, "env_error"), "exit": 1}]})
    assert sweep_run("--models", SONNET, "--tasks", T1, "--cap-usd", "10").exit_code == 0
    assert [(r["attempt"], r["outcome"], r["retryable"]) for r in rows()] == [
        (1, "interrupted", True), (2, "failed", True), (3, "failed", True), (4, "failed", False)]


def test_a_sweep_that_fails_tears_its_attempts_down_and_counts_them_at_the_cap(fake, monkeypatch):
    fake.script({f"{SONNET}|{T1}": [{"context": scored(T1, SONNET), "sleep": 0.5}],
                 "*": [{"wait": True, "context": scored(T2, SONNET)}]})

    def unreadable(instance):
        raise RuntimeError("the store is down")

    monkeypatch.setattr(sweep, "context", unreadable)
    result = sweep_run("--models", SONNET, "--tasks", f"{T1},{T2}", "--cap-usd", "10", "--parallel", "2")
    assert str(result.exception) == "the store is down"
    assert [(r["task_id"], r["outcome"], r["exit"], r["cost_usd"], r["retryable"]) for r in rows()] == [
        (T1, "interrupted", 0, None, True), (T2, "interrupted", 143, None, True)]


def test_an_interrupted_attempt_keeps_a_score_its_run_recorded(fake, tmp_path):
    log = tmp_path / "attempt.log"
    (fake.root / "instances").mkdir()
    (fake.root / "instances/@local_fake_t-1").write_text(json.dumps(scored(T1, SONNET)))
    log.write_text(f"  tasks/{T1}.json v1: cancelled, 80.0s, instance @local/fake/t-1\n")
    attempt = sweep.Attempt((SONNET, T1, 1), 1, "2026-10-13T12:00:00Z", log, SimpleNamespace(returncode=143))
    row = sweep.outcome(sweep.Sweep("s", [SONNET], [T1], 1, 0.5), attempt, [], interrupted=True)
    assert (row["outcome"], row["cost_usd"], row["error_code"], row["retryable"]) == ("scored", 0.224018, None, False)


def test_attempts_go_model_major_and_never_more_than_parallel_at_once(fake):
    fake.script({"*": [{"context": scored(T1, SONNET, cost=0.01), "sleep": 0.2}]})
    result = sweep_run("--models", f"{SONNET},{GPT}", "--tasks", f"{T1},{T2},{T3}", "--k", "2", "--cap-usd", "10",
                       "--parallel", "2")
    assert result.exit_code == 0, result.output
    starts = [line.split()[1:4] for line in result.output.splitlines() if line.startswith("start ")]
    assert starts == [[m, t, f"r{rep}"] for m in (SONNET, GPT) for rep in (1, 2) for t in (T1, T2, T3)]
    assert len(rows()) == 12
    events = sorted((c.get("start", c.get("end")), 1 if "start" in c else -1) for c in fake.calls()
                    if "--dry-run" not in c["argv"])
    level = peak = 0
    for _, step in events:
        level += step
        peak = max(peak, level)
    assert peak == 2


def test_task_ids_are_a_split_fewest_watches_first_or_ids_of_the_pack(fake):
    pack = {t.task_id: t for t in load_pack(FIXTURES)}
    ids = sweep.task_ids("eval", FIXTURES)
    assert sorted(ids) == sorted(pack) and ids[:2] == ["b6-bos-20230626-1400", "b6-jfk-20230610-1400"]
    assert ids[-2:] == ["b6-jfk-20230626-1200", "b6-bos-20240107-0600"]
    assert sweep.task_ids(f"{T2},{T1}", FIXTURES) == [T2, T1]
    assert len(sweep.task_ids("eval")) == 20 and len(sweep.task_ids("all")) == 57
    result = sweep_run("--models", SONNET, "--tasks", f"{T1},b6-jfk-20990101-0600", "--cap-usd", "1")
    assert result.exit_code == 2 and "not task ids of the pack: b6-jfk-20990101-0600" in result.output
    assert not Path("results").exists() and not (fake.root / "calls.jsonl").exists()


def test_days_without_a_reference_are_refused_before_any_spend(fake, tmp_path):
    lines = FIXTURES.read_text().splitlines()
    bare = json.loads(lines[0]) | {"reference": {}}
    pack = tmp_path / "bare.jsonl"
    pack.write_text("\n".join([json.dumps(bare), *lines[1:]]) + "\n")
    result = sweep_run("--models", SONNET, "--tasks", f"{T2},{T1}", "--cap-usd", "1", pack=pack)
    assert result.exit_code == 2 and f"no reference yet for {T1}: the verifier can't score" in result.output
    assert not Path("results/runs/s/sweep.json").exists() and not (fake.root / "calls.jsonl").exists()


def test_the_reader_finds_the_instance_and_the_trajectory_a_run_stored(local_stores):
    context = TaskStepContext.from_dict(scored(T1, SONNET))
    instance = f"@local/~/results/runs/s/bundle/{T1}-abcd1234"
    with namespace_routing():
        get_task_instance_store().upsert_instance(instance, f"@local/~/results/runs/s/bundle/{T1}", 1, 9)
        seed_task_instance_context(instance, context)
        url = get_config().get_object_store().put("trajectories/t.json", b'{"format": "airops-rollout"}')
    found = sweep.context(instance)
    assert found["prompt_responses"][0]["structured_output"] == summary(SONNET)
    assert found["metadata"]["verifications"]["airops"]["score"] == 0.840588
    assert sweep.trajectory(url) == b'{"format": "airops-rollout"}'
