"""The zero-spend end-to-end run on local Docker: scripts/live_e2e.py plays the follower's decisions through the real
DAG and checks the day ends done with the follower's reward. Needs `agent-env airops setup --agent` with an env image
whose pack holds the day's reference; AIROPS_E2E_TASK and AIROPS_E2E_PACK pick the day and that pack."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.docker
def test_a_live_day_plays_through_the_real_dag_to_the_followers_reward(tmp_path):
    args = [arg for name, flag in (("AIROPS_E2E_TASK", "--task"), ("AIROPS_E2E_PACK", "--pack"))
            if (value := os.environ.get(name)) for arg in (flag, value)]
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "live_e2e.py"), *args, "--out",
                          str(tmp_path / "day.json")], capture_output=True, text=True, cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": str(ROOT / "tests")}, timeout=1800)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-4000:]
    report = json.loads(run.stdout[run.stdout.index('{\n "instance"'):])
    assert report["problems"] == [] and report["score"] == report["expected"]["reward"]
    day = json.loads((tmp_path / "day.json").read_text())
    assert day["done"] and day["end_reason"] == "done" and day["audit"]["ok"]
