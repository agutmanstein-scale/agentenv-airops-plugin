"""`agent-env airops sweep report`: each model's mean reward over days with its bootstrap CI next to the references'
rewards on the same days, the day-by-day table, the unscored runs and the runs whose agent reward differs."""

import json
import statistics
from pathlib import Path

import pytest
from airops_contexts import FIXTURES
from click.testing import CliRunner

from agentenv_airops import sweep
from agentenv_airops.cli import airops
from agentenv_airops.core.model import load_pack
from agentenv_airops.core.reward import reward

SONNET, GPT, GLM = "anthropic/claude-sonnet-5-5", "openai/gpt-6.1-sol", "fireworks_ai/glm-5p3-flash"
A, B = "b6-jfk-20230626-1400-1700", "b6-bos-20230626-1400"
DAYS = {t.task_id: t for t in load_pack(FIXTURES)}


def row(name: str, model: str, task: str, rep: int, attempt: int = 1, *, score: float | None = None,
        cost: float | None = 0.1, code: str | None = None, outcome: str | None = None, agent_reward="score",
        day_cost: int | None = None, turns: int = 9, tokens: tuple[int, int] = (20000, 5000), finished: bool = True,
        cached: int = 10000) -> dict:
    """A results.jsonl row as the sweep writes one: scored when it has a score, else failed with ``code``."""
    scored = score is not None
    return {"sweep": name, "model": model, "task_id": task, "rep": rep, "attempt": attempt,
            "started_utc": "2026-10-13T12:00:00Z", "wall_seconds": 60.0, "exit": 0 if scored else 1,
            "instance": None, "outcome": outcome or ("scored" if scored else "failed"),
            "failed_step": None if scored else "play", "error_code": code, "error": None, "retryable": False,
            "reward": score, "finished": finished if scored else None,
            "day_cost": (day_cost or DAYS[task].reference["anchor"]) if scored else None, "watches": 13,
            "planning_calls": 13, "refusals": 0, "cancelled": 3, "auto_cancelled": 0,
            "end_reason": "done" if finished else "turn_limit", "turns": turns, "tool_calls": 30, "checks": 4,
            "confirms": 13, "input_tokens": tokens[0], "output_tokens": tokens[1], "cached_tokens": cached,
            "cache_write_tokens": 0, "cost_usd": cost, "priced_cost_usd": None, "failed_requests": 0,
            "agent_reward": (score if finished else None) if agent_reward == "score" else agent_reward,
            "transcript": None}


def write(s: sweep.Sweep, rows: list[dict]) -> None:
    s.out.mkdir(parents=True)
    (s.out / "sweep.json").write_text(json.dumps(sweep.asdict(s)))
    (s.out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def pilot(tmp_path, monkeypatch) -> list[sweep.Sweep]:
    """Two sweeps: a retry, a capped episode, an interrupted attempt, runs with no instance, a day the env ended, an
    agent reward that differs."""
    monkeypatch.chdir(tmp_path)
    first = sweep.Sweep("pilot", [SONNET, GPT], [A, B], 2, 0.5, str(FIXTURES))
    write(first, [
        row("pilot", SONNET, A, 1, score=0.9, cost=0.2, day_cost=2_000_000, tokens=(25000, 17000)),
        row("pilot", SONNET, A, 2, code="provider_error", cost=0.05),
        row("pilot", SONNET, A, 2, 2, score=0.7, cost=0.25, turns=12),
        row("pilot", SONNET, B, 1, score=0.3, cost=0.1, finished=False, cached=0),
        row("pilot", SONNET, B, 2, score=0.5, cost=0.3, turns=15, tokens=(500000, 80000), agent_reward=0.45),
        row("pilot", GPT, A, 1, score=1.0, tokens=(7000, 4000)),
        row("pilot", GPT, A, 2, outcome="interrupted", cost=None),
        row("pilot", GPT, A, 2, 2, code="cost_cap", cost=0.49),
        *(row("pilot", GPT, B, 1, n, code="no_instance", cost=None) for n in (1, 2, 3)),
    ])
    second = sweep.Sweep("extra", [GLM], [A], 1, 0.75, str(FIXTURES))
    write(second, [row("extra", GLM, A, 1, score=0.6, cost=0.01)])
    return [first, second]


def refs(task: str, policy: str) -> float:
    r = DAYS[task].reference
    return reward(r[policy], r["anchor"], r["floor"])


def cells(text: str, model: str) -> list[str]:
    [line] = [line for line in text.splitlines() if line.startswith(f"| {model} | ")]
    return line.strip("| ").split(" | ")


def test_each_model_is_its_mean_over_days_next_to_the_references_on_those_days(pilot):
    text = sweep.report(pilot)
    sonnet = cells(text, SONNET)
    days = [statistics.mean([0.9, 0.7]), statistics.mean([0.3, 0.5])]
    lo, hi = sweep.ci(sorted(days, key=lambda _: 0))
    assert sonnet[:4] == [SONNET, "2", "4/4", f"{statistics.mean(days):.3f} ({lo:.3f} to {hi:.3f})"]
    assert sonnet[4:8] == [f"{statistics.mean([refs(A, p), refs(B, p)]):.3f}"
                           for p in ("follower", "blind", "naive", "airline")]
    gap = 100 * (2_000_000 - DAYS[A].reference["anchor"]) / DAYS[A].reference["anchor"] / 4
    assert sonnet[8:] == ["1.5", f"{gap:+.1f}%", "10.5", "141.2k/26.8k", "7.5k", "$0.2125", "$0.90"]
    gpt = cells(text, GPT)
    assert gpt[:4] == [GPT, "1", "1/4", "1.000 (1.000 to 1.000)"]
    assert gpt[4] == f"{refs(A, 'follower'):.3f}" and gpt[-1] == "$0.59 + 4 unknown"
    assert cells(text, GLM)[:3] == [GLM, "1", "1/1"]
    assert ("Spend: $1.50 known; 4 attempts with no spend recorded, $2.00 at the cap. Attempts: 12, retries: 4. "
            "Unscored runs: 3 of 9.") in text


def test_the_day_table_unscored_runs_and_differing_rewards(pilot):
    text = sweep.report(pilot)
    section = text.split(f"### {SONNET}")[1].split("###")[0]
    [day_a] = [line for line in section.splitlines() if line.startswith(f"| {A} |")]
    anchor = DAYS[A].reference["anchor"]
    assert day_a.startswith(f"| {A} | eval | {len(DAYS[A].flights)} | 13 | {anchor:,} | ")
    assert day_a.endswith("| 0.900 | 0.700 | 0.800 |")
    assert f"| {DAYS[A].reference['follower']:,} ({refs(A, 'follower'):.3f}) |" in day_a
    unscored = text.split("## Unscored runs")[1].split("##")[0].strip().splitlines()[2:]
    assert unscored == [f"| pilot | {GPT} | {B} | 1 | 3 | no_instance | $1.50 |",
                        f"| pilot | {GPT} | {A} | 2 | 2 | cost_cap | $0.99 |",
                        f"| pilot | {GPT} | {B} | 2 | 0 | not run | $0.00 |"]
    differs = text.split("## Runs whose agent reward differs from the verifier's")[1]
    assert differs.strip().splitlines()[-1] == f"| pilot | {SONNET} | {B} | 2 | 0.450 | 0.500 |"
    assert f"| {B} | 1 |" not in differs


def test_the_command_writes_the_report(pilot):
    result = CliRunner().invoke(airops, ["sweep", "report", "pilot", "extra", "--out", "results/r.md"])
    assert result.exit_code == 0, result.output
    assert Path("results/r.md").read_text() == sweep.report(pilot)
    missing = CliRunner().invoke(airops, ["sweep", "report", "nope"])
    assert missing.exit_code == 2 and "no sweep 'nope'" in missing.output
