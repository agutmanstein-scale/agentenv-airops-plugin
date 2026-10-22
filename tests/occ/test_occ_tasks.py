"""The ops control center as a bundle: each day's steps (the desks' roles and tools, one trigger per watch with
advisories on the end_turn that opens it, the agents, the clock, the plays at once, end-day after both, the grade), the
rules each desk reads, the pack the env loads, and the verifier on a day's data/get; a pilot's margin, and the live
runner's order of the days, its spend guard and its pacing."""

import importlib.util
import json
import types
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest
from agent_env.task_step.task_steps.verifiers.scoring import ScoreAggregator, aggregate_score
from occ_helpers import play, scripted, storm, write_pack

from agentenv_airops.occ.pack import Pack, Pack3
from agentenv_airops.occ.sweep import Pilot, verdict
from agentenv_airops.occ.tasks import RULES, generate, steps

pytestmark = pytest.mark.anyio
SPEC = importlib.util.spec_from_file_location("live_runner", Path(__file__).parents[2] / "scripts" / "live_runner.py")
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


def test_a_joint_day_deploys_two_desks_in_their_roles_and_triggers_each_watch_with_news():
    task = storm().task
    s = steps(task, "joint", cap=3.0)
    assert [x["id"] for x in s] == ["deploy", "load-task", "crew-only", "crew-tools", "ops-only", "ops-tools",
                                    "watches", "deploy-crew-desk", "deploy-ops-desk", "clock", "play-crew-desk",
                                    "play-ops-desk", "end-day", "grade"]
    by = {x["id"]: x for x in s}
    assert by["load-task"]["directives"][0]["args"] == {"task_id": task.task_id, "mode": "joint"}
    assert by["crew-only"]["tools"] == ["*"] and "crew_confirm" in by["crew-tools"]["tools"] and \
        "ops_confirm" not in by["crew-tools"]["tools"] and "wait_turn" in by["ops-tools"]["tools"]
    assert by["watches"]["watch_roles"] == ["crew", "ops"] and by["watches"]["triggers"] == [{
        "id": "watch-2", "when": {"type": "action", "tool": "end_turn", "where": {"result.opened": {"equals": 2}}},
        "barrier": {"at": "provoking_call", "timeout_seconds": 60},
        "actions": [{"type": "tool", "tool": "airops_notice", "args": {"news_id": "adv-b"}}]}]
    assert by["deploy-ops-desk"] | {"env_vars": None} == {
        "id": "deploy-ops-desk", "type": "deploy_agent", "a2a_agent_id": "airops-desk", "agent_name": "ops-desk",
        "role": "ops", "env_ids": ["airops-occ"], "env_vars": None}
    assert by["deploy-ops-desk"]["env_vars"] == {"AIROPS_MAX_COST_USD": "3"}
    play = by["play-ops-desk"]
    assert (play["max_turns"], play["depends_on"], play["fail_task_on_error"], play["effort"]) == (
        6 * 25 + 10, ["clock"], False, "high")
    assert by["end-day"]["depends_on"] == ["play-crew-desk", "play-ops-desk"]


def test_one_desk_runs_both_without_the_channel_and_no_desk_is_the_crew_system_alone():
    task = storm().task
    by = {x["id"]: x for x in steps(task, "one")}
    tools = by["both-tools"]["tools"]
    assert "ops_confirm" in tools and "crew_confirm" in tools and not any(t.endswith(("request", "reply"))
                                                                         for t in tools)
    assert by["play-occ-desk"]["max_turns"] == 8 * 25 + 10 and by["deploy-occ-desk"]["role"] == "both"
    idle = steps(task, "idle")
    assert [x["id"] for x in idle] == ["deploy", "load-task", "watches", "clock", "end-day", "grade"]
    assert all(len(text) <= 5000 for text in RULES.values())


def test_the_bundle_and_the_pack(tmp_path):
    w = storm()
    pack = write_pack(tmp_path / "pack", w)
    names = generate(tmp_path / "bundle", split="eval", pack=pack, noplay=True)
    assert names == [f"{w.task.task_id}-joint", f"{w.task.task_id}-one", "wiring-noplay"]
    readme = (tmp_path / "bundle" / "README.md").read_text()
    assert "v2" not in readme and (tmp_path / "bundle" / "artifacts" / "airops-occ-verifier" / "verify.py").is_file()
    again = Pack(pack).world(w.task.task_id)
    assert again.task == w.task and again.units == w.units and again.trips == w.trips and again.partner == {0: 1, 1: 0}
    assert again.local("JFK", 300) == 300


def load_verifier() -> types.ModuleType:
    path = files("agentenv_airops.bundles").joinpath("airops-occ/artifacts/airops-occ-verifier/verify.py")
    module = types.ModuleType("occ_verifier")
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


async def test_the_verifier_scores_an_audited_day_and_refuses_one_that_fails(monkeypatch):
    verifier = load_verifier()
    data = json.loads(json.dumps(play(storm(), policies=scripted("crew", "ops")).data(), default=str))

    async def get_data(url):
        return types.SimpleNamespace(parts=[types.SimpleNamespace(data=data)])
    monkeypatch.setattr(verifier.client, "get_data", get_data)
    results = await verifier.verify("http://env/mcp")
    assert results[0]["score"] == data["reward"] and aggregate_score(results, ScoreAggregator.WEIGHTED_AVERAGE) == \
        data["reward"]
    data["audit"] = {"ok": False, "problems": {"A7": ["crew: crash"]}}
    with pytest.raises(RuntimeError, match="can't be scored") as crash:
        await verifier.verify("http://env/mcp")
    data["audit"] = {"ok": False, "problems": {"A5": ["flown without a legal crew: [3]"], "A7": ["crew: crash"]}}
    with pytest.raises(RuntimeError, match="can't be scored") as illegal:
        await verifier.verify("http://env/mcp")
    assert verdict(False, [{"step_id": "grade", "error": str(crash.value)}]) == "failed"
    assert verdict(False, [{"step_id": "grade", "error": str(illegal.value)}]) == "unscored"
    assert verdict(False, [{"step_id": "play-crew-desk", "error": "stopped answering"}]) == "failed"
    late = json.dumps({"done": True, "problems": {"A1": ["adv-b: watch 2's trigger never brought it"]}})
    assert verdict(False, [{"error": "the day can't be scored: " + late}]) == "failed"
    assert verdict(False, [{"error": str(illegal.value)[:60]}]) == "failed"


def test_a_pilot_keeps_a_worst_request_free_per_desk_of_a_run():
    p = Pilot("p", "m", ["b6-x-joint", "b6-x-one"], {"joint": 3.0, "one": 5.0}, 20.0, margin=1.5)
    assert (p.cap("b6-x-joint"), p.cap("b6-x-one")) == (9.0, 6.5)


def test_the_live_runner_plays_the_stormiest_training_days_first_a_storms_next_day_later_and_the_seasons_mixed(
        tmp_path):
    task = {"programs": {"JFK": {"segments": [{"since": 0, "until": 120}, {"since": 60, "until": 180}],
                                 "stops": [{"start": 30, "end": 90}]}},
            "flights": [{"actual": {"cancelled": i < 20}} for i in range(30)]}
    s = live.storm("b6-x", {"date": "2024-07-01", "split": "train"}, task)
    assert (s["hubs"], s["cancelled"], s["season"], s["score"]) == (
        {"JFK": {"gdp_h": 3.0, "gs": 1, "gs_h": 1.0}, "BOS": {"gdp_h": 0, "gs": 0, "gs_h": 0}}, 20, "summer", 7.0)

    def day(when, score, split="train"):
        return {"task_id": f"b6-{when}", "date": when, "split": split, "score": score,
                "season": "winter" if int(when[5:7]) in live.WINTER else "summer"}
    days = [day("2024-07-01", 90), day("2024-07-02", 80), day("2024-08-10", 70), day("2024-06-01", 60),
            day("2023-07-04", 50), day("2024-01-10", 20), day("2025-02-01", 10), day("2023-07-09", 99, "eval")]
    assert [d["date"] for d in live.order(days)] == ["2024-07-01", "2024-01-10", "2024-08-10", "2024-06-01",
                                                     "2023-07-04", "2025-02-01", "2024-07-02", "2023-07-09"]
    shipped = live.Settings.read(live.CONFIG)
    assert list(shipped.days) == [d["task_id"] for d in live.order(live.storms(Pack3()))]
    assert (shipped.model, shipped.policy, shipped.cap_joint_usd, shipped.request_margin_usd, shipped.budget_usd) == (
        "openai/gpt-6-astra", None, 23.5, 4.0, 430.0)
    for bad in ('days = ["b6-20240703-0400"]', 'days = ["b6-20240803-0400", "b6-20240803-0400"]', "budget = 1"):
        (tmp_path / "bad.toml").write_text(bad + "\n")
        with pytest.raises(ValueError):
            live.Settings.read(tmp_path / "bad.toml")


def test_the_live_runner_starts_a_day_only_while_the_spend_and_a_worst_day_fit_and_paces_the_days_on_slots(
        monkeypatch):
    logs = {"agent-local-a": "INFO request 7: 2 tool calls, stop tool_calls, $1.2000 spent\n"
                             "INFO request 8: 0 tool calls, stop stop, $1.5000 spent\n", "agent-local-b": "starting\n"}
    monkeypatch.setattr(live.subprocess, "run", lambda args, **_: types.SimpleNamespace(stdout=logs[args[-1]],
                                                                                        stderr=""))
    ctx = {"deployed_agents": [{"role": "crew", "sandbox_id": "local-a", "sandbox_type": "local"},
                               {"role": "ops", "sandbox_id": "local-b", "sandbox_type": "local"}]}
    assert live.desk_spend(ctx) == {"crew": 1.5}
    s = live.Settings(days=("b6-a", "b6-b", "b6-c"), budget_usd=100.0, worst_day_usd=40.0, typical_day_usd=25.0,
                      slot_minutes=75.0, min_intermission_minutes=5.0, window_hours=4.0)
    desks = {"crew": {"cost_usd": 10.0}, "customer": {"cost_usd": None}, "ops": {"cost_usd": None}}
    assert live.spend({"desks": desks, "live_usd": {"customer": 2.5}}, 4.0) == 16.5
    assert live.affordable(60.0, s) and not live.affordable(60.01, s)
    assert live.over(60.0, 40.0, s) and not live.over(60.0, 39.99, s)
    t0 = live.stamp("2026-10-23T16:00:00Z")

    def row(run, attempt, outcome, seconds, usd):
        return {"run": f"{run}-joint", "attempt": attempt, "outcome": outcome, "started_utc": live.utc(t0),
                "ended_utc": live.utc(t0 + seconds), "spend_usd": usd, "cost_usd": usd, "desks": {}, "instance": None,
                "reward": 0.7 if outcome == "scored" else None, "reward_L": None, "wall_seconds": seconds,
                "date": run}
    assert live.next_start(s, None, t0, t0 - 50) == t0
    assert live.next_start(s, row("b6-a", 1, "scored", 3000, 30.0), t0, t0 + 3000) == t0 + 4500
    assert live.next_start(s, row("b6-a", 1, "scored", 4800, 30.0), t0, t0 + 4800) == t0 + 5100
    assert live.next_start(s, row("b6-a", 1, "failed", 120, 0.0), t0, t0 + 120) == t0 + 420
    rows = [row("b6-a", 1, "failed", 120, 1.0), row("b6-a", 2, "failed", 120, 2.0), row("b6-b", 1, "unscored", 60, 3.0)]
    assert (live.settled(rows[:1]), live.settled(rows[:2]), live.settled(rows[2:])) == (None, "skipped", "unscored")
    assert live.pending(s, rows) == ["b6-c"] and live.spent(rows) == 6.0
    assert [(h["task_id"], h["outcome"], h["cost_usd"], h["attempts"]) for h in live.history(rows)] == [
        ("b6-a", "skipped", 3.0, 2), ("b6-b", "unscored", 3.0, 1)]
    plan, why = live.projection(s, [], t0, t0)
    assert [(p["task_id"], p["starts"] - t0, p["spent_before"]) for p in plan] == [
        ("b6-a", 0, 0), ("b6-b", 4500, 25.0), ("b6-c", 9000, 50.0)] and why.startswith("the order")
    plan, why = live.projection(replace(s, budget_usd=80.0), [], t0, t0)
    assert len(plan) == 2 and why.startswith("the budget: $50.00 spent")
    plan, why = live.projection(replace(s, window_hours=3.0), [], t0, t0)
    assert len(plan) == 2 and why.startswith("the window")


FAKE_RUN = """
import json, os, sys, time, uuid
from pathlib import Path
here = Path(os.environ["FAKE_AGENT_ENV"])
task = sys.argv[sys.argv.index("--task") + 1]
calls = here / "calls.jsonl"
n = sum(json.loads(line)["task"] == task for line in calls.read_text().splitlines()) if calls.exists() else 0
with calls.open("a") as f:
    f.write(json.dumps({"task": task, "start": time.time()}) + "\\n")
step = json.loads((here / "plan.json").read_text())[task][n]
time.sleep(0.2)
if step.get("touch"):
    Path(step["touch"]).touch()
instance = f"@local/fake/{task}-{uuid.uuid4().hex[:8]}"
(here / instance.replace("/", "_")).write_text(json.dumps(step["context"]))
print(f"  tasks/{task}.json v1: done, 0.2s, instance {instance}")
"""


def _desks(*usd):
    return [{"step_id": f"play-{role}-desk", "structured_output": {"cost_usd": c}}
            for role, c in zip(("crew", "customer", "ops"), usd, strict=True)]


SCORED = {"metadata": {"verifications": {"airops-occ3": {"score": 0.8, "results": [{"episode": {
    "absent": {}, "grade": {"reward_L": 0.4}}}]}}}, "prompt_responses": _desks(10.0, 3.0, 4.0)}
FAILED = {"metadata": {"failed_steps": [{"step_id": "play-ops-desk", "error": "provider_error"}]},
          "prompt_responses": _desks(1.0, 1.0, 0.5)}


@pytest.fixture
def live_runner(tmp_path, monkeypatch):
    """The live runner in tmp_path, each attempt a stand-in `agent-env run` that plays plan.json's next step for its
    task and leaves the step's context where the runner reads it."""
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "run.py").write_text(FAKE_RUN)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FAKE_AGENT_ENV", str(fake))
    monkeypatch.setattr(live.sweep, "AGENT_ENV", [live.sys.executable, str(fake / "run.py")])
    monkeypatch.setattr(live.sweep, "context", lambda i: json.loads((fake / i.replace("/", "_")).read_text()))
    monkeypatch.setattr(live, "instances", lambda task: [])
    monkeypatch.setattr(live, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(live, "IDLE_SECONDS", 0.02)

    def run(days, plan, **settings):
        (fake / "plan.json").write_text(json.dumps({f"{d}-joint": steps for d, steps in zip(days, plan, strict=True)}))
        body = {"name": "t", "policy": "script", "budget_usd": 70.0, "worst_day_usd": 40.0, "slot_minutes": 0.06,
                "min_intermission_minutes": 0.03, "card_seconds": 0.0, "days": days} | settings
        (tmp_path / "live.toml").write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in body.items()))
        runner = live.Runner(tmp_path / "live.toml", tmp_path / "live" / "current.json", {}, None)
        assert runner.run() == 0
        calls = [json.loads(line) for line in (fake / "calls.jsonl").read_text().splitlines()]
        return runner, calls, json.loads((tmp_path / "live" / "current.json").read_text())
    return run


def test_the_live_runner_tries_a_failed_day_once_more_then_skips_it_holds_each_slot_and_stops_at_the_budget(
        live_runner):
    days = ["b6-20240803-0400", "b6-20230627-0400", "b6-20250119-0400", "b6-20240818-0400"]
    runner, calls, current = live_runner(days, [[{"context": FAILED}, {"context": FAILED}], [{"context": SCORED}],
                                                [{"context": SCORED}], [{"context": SCORED}]])
    assert [c["task"] for c in calls] == [f"{d}-joint" for d in days[:1] + days[:3]]
    rows = live.sweep.results(runner.out)
    assert [(r["run"], r["attempt"], r["outcome"], r["spend_usd"]) for r in rows] == [
        (f"{days[0]}-joint", 1, "failed", 2.5), (f"{days[0]}-joint", 2, "failed", 2.5),
        (f"{days[1]}-joint", 1, "scored", 17.0), (f"{days[2]}-joint", 1, "scored", 17.0)]
    assert calls[1]["start"] - calls[0]["start"] >= 0.8 and calls[3]["start"] - calls[2]["start"] >= 2.6
    assert (current["final"], current["reason"].split(":")[0], current["next"], current["spent_usd"]) == (
        True, "budget", None, 39.0)
    assert [(h["date"], h["hub"], h["outcome"], h["attempts"], h["cost_usd"], h["reward"])
            for h in current["history"]] == [
        ("2024-08-03", "JFK", "skipped", 2, 5.0, None), ("2023-06-27", "BOS", "scored", 1, 17.0, 0.8),
        ("2025-01-19", "JFK", "scored", 1, 17.0, 0.8)]
    assert current["run_id"] == f"{days[2]}-joint-a1" and current["result"] == current["history"][-1]
    assert current["hub"] == "JFK" and current["result"]["desks"] == rows[-1]["desks"]
    assert {r: d["cost_usd"] for r, d in current["result"]["desks"].items()} == {
        "crew": 10.0, "customer": 3.0, "ops": 4.0}
    assert current["day_file"].endswith(f"days/{days[2]}-joint-a1.json") and Path(current["day_file"]).is_file()
    pilot = json.loads((runner.out / "pilot.json").read_text())
    assert pilot["runs"] == [f"{d}-joint" for d in days[:3]] and pilot["policy"] == "script"


def test_the_live_runner_finishes_the_day_in_progress_when_asked_to_stop(live_runner, tmp_path):
    days = ["b6-20240803-0400", "b6-20230627-0400"]
    flag = tmp_path / "live" / "stop.flag"
    runner, calls, current = live_runner(days, [[{"context": SCORED, "touch": str(flag)}], [{"context": SCORED}]])
    assert [c["task"] for c in calls] == [f"{days[0]}-joint"] and not flag.exists()
    assert (current["final"], current["reason"], current["history"][0]["outcome"]) == (True, "stopped", "scored")
