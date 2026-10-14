"""The live tasks: the exact steps of a day, its prompts and turn limit, the installed airops bundle as `agent-env run`
checks it, and `tasks generate`."""

import json
from importlib.resources import files
from pathlib import Path

import pytest
from agent_env.bundle.installed import checked, find_bundle
from agent_env.bundle.parse import BundleKind
from agent_env.cli import cli
from agent_env.task_step.registry import get_task_step_registry
from click.testing import CliRunner

from agentenv_airops import tasks
from agentenv_airops.cli import airops
from agentenv_airops.day import Day
from agentenv_airops.schedule import timetable, triggers

STORM = "b6-jfk-20230714-1400"
BUNDLE = files("agentenv_airops.bundles") / "airops"
PACK = {t.task_id: t for t in tasks.pack_tasks()}
EVAL = [t.task_id for t in PACK.values() if t.split == "eval"]


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def test_a_day_loads_hides_airops_notice_registers_its_watches_arms_the_clock_plays_ends_and_grades():
    task = PACK[STORM]
    assert tasks.steps(task, 5.0, "ScaleAI") == [
        {"id": "deploy", "type": "deploy_env", "env_id": "airops-day"},
        {"id": "load-task", "type": "apply_server_config", "env_id": "airops-day",
         "directives": [{"service": "airops-day", "uri": "urn:airops:live-load/v1", "args": {"task_id": STORM}}]},
        {"id": "hide-notice", "type": "modify_env_tool_access", "env_id": "airops-day", "action": "disable",
         "role": "default", "tools": ["airops_notice"]},
        {"id": "watches", "type": "register_env_triggers", "env_id": "airops-day", "watch_roles": ["default"],
         "triggers": triggers(timetable(task))},
        {"id": "deploy-agent", "type": "deploy_agent", "a2a_agent_id": "airops-llm", "env_ids": ["airops-day"],
         "env_vars": {"AIROPS_MAX_COST_USD": "5", "HF_BILL_TO": "ScaleAI"}},
        {"id": "clock", "type": "sync_env_clock", "env_id": "airops-day", "virtual_time": "2023-07-14T17:00:00Z",
         "virtual_seconds_per_real_second": 0, "tolerate_missing_sync_time": False},
        {"id": "play", "type": "prompt_agent", "prompt_id": STORM, "system_prompt": tasks.AIROPS_RULES,
         "prompt": Day(task).briefing(), "max_turns": 5 * 13 + 4, "model_params": {"max_tokens": 32000},
         "timeout_seconds": 7200},
        {"id": "end-day", "type": "apply_server_config", "env_id": "airops-day",
         "directives": [{"service": "airops-day", "uri": "urn:airops:end-day/v1", "args": {}}]},
        {"id": "grade", "type": "env_outcome_verifier", "env_id": "airops-day", "file_artifact_id": "airops-verifier",
         "verifier_id": "airops", "score_aggregator": "weighted_average"},
    ]
    assert "env_vars" not in tasks.steps(task)[4]
    assert [s["id"] for s in tasks.noplay_steps(task)] == ["deploy", "load-task", "hide-notice", "watches", "clock",
                                                            "end-day", "grade"]


def test_every_watch_with_news_gets_its_trigger_and_every_day_five_turns_a_watch(local_stores):
    registry = get_task_step_registry()
    for task in PACK.values():
        steps = tasks.steps(task)
        assert [registry[s["type"]].from_dict(s).id for s in steps] == [s["id"] for s in steps]
        watches = timetable(task)
        assert steps[6]["max_turns"] == 5 * len(watches) + 4
        delivered = [a["args"]["news_id"] for t in steps[3]["triggers"] for a in t["actions"]]
        assert delivered == [n for w in watches[1:] for n in w.news]


def test_the_rules_say_the_job_the_information_the_tools_and_the_cost():
    rules = tasks.AIROPS_RULES
    for words in ["decide which flights to cancel and which to hold", "becomes known 2 hours before its scheduled "
                  "departure", "becomes known 4 hours ahead", "nothing is frozen", "the freeze line", "3 planning "
                  "calls", "get_situation()", "check_plan(changes)", "confirm_changes(changes)", "advance()",
                  "523 minutes for each passenger", "best plan possible in hindsight"]:
        assert words in rules
    assert "\n\n\n" not in rules and "airops_notice" not in rules


def test_the_opening_is_the_first_watch_with_only_the_news_issued_by_then():
    task = PACK[STORM]
    opening = tasks.steps(task)[6]["prompt"]
    first = timetable(task)[0]
    assert opening == Day(task).briefing()
    assert all(n.text in opening for n in task.news if n.id in first.news)
    assert not [n for n in task.news if n.id not in first.news and n.text in opening]
    assert "Now 13:00, watch 0. First watch: nothing is frozen" in opening


def test_the_bundle_installs_every_eval_day_wiring_noplay_and_the_verifier(local_stores):
    bundle = checked(find_bundle("airops"))
    entries = {(e.kind, e.name): e for e in bundle.entries}
    assert bundle.description.startswith("AirOps: a storm day at a JetBlue hub played live in hourly watches")
    assert sorted(name for kind, name in entries if kind is BundleKind.TASK) == sorted([*EVAL, "wiring-noplay"])
    assert entries[BundleKind.ARTIFACT, "airops-verifier"].type == "file"


def test_the_installed_bundle_is_what_generate_writes(tmp_path):
    assert tasks.generate(tmp_path, noplay=True) == EVAL
    assert snapshot(tmp_path) == snapshot(Path(str(BUNDLE)))
    assert json.loads((tmp_path / "tasks/wiring-noplay.json").read_text()) == tasks.noplay_steps(PACK[EVAL[0]])


def test_tasks_generate_writes_a_split_or_the_days_asked_for(tmp_path):
    out = CliRunner().invoke(airops, ["tasks", "generate", "--split", "control", "--out", str(tmp_path / "c")])
    control = [t.task_id for t in PACK.values() if t.split == "control"]
    assert out.exit_code == 0, out.output
    assert out.output == (f"Wrote 3 tasks into {tmp_path / 'c'}; play one with: agent-env run {tmp_path / 'c'} "
                          f"--task {control[0]} --model <litellm model id>\n")
    assert sorted(p.stem for p in (tmp_path / "c/tasks").iterdir()) == sorted(control)
    out = CliRunner().invoke(airops, ["tasks", "generate", "--task", STORM, "--out", str(tmp_path / "one")])
    assert [p.name for p in (tmp_path / "one/tasks").iterdir()] == [f"{STORM}.json"]
    out = CliRunner().invoke(airops, ["tasks", "generate", "--task", "nope", "--out", str(tmp_path / "x")])
    assert out.exit_code == 2 and "unknown task ids: nope" in out.output


@pytest.mark.parametrize("name", ["b6-jfk-20230714-1400", "wiring-noplay"])
def test_each_bundled_task_builds_through_the_step_registry(local_stores, name):
    steps = json.loads((BUNDLE / "tasks" / f"{name}.json").read_text())
    registry = get_task_step_registry()
    built = [registry[s["type"]].from_dict(s) for s in steps]
    assert {getattr(s, "env_id", "airops-day") for s in built} == {"airops-day"}
    assert (built[-1].type, built[-1].file_artifact_id, built[-1].score_aggregator.value) == (
        "env_outcome_verifier", "airops-verifier", "weighted_average")


def test_plugin_check_passes(local_stores):
    result = CliRunner().invoke(cli, ["plugin", "check"])
    assert result.exit_code == 0, result.output
