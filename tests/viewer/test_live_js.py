"""The live viewer's clock (live.test.mjs) under Node's test runner: never past what is decided, paced to the
watches."""

import shutil
import subprocess
from pathlib import Path

import pytest

TEST = Path(__file__).resolve().parent / "live.test.mjs"


def test_the_live_clock_keeps_to_what_is_decided_and_to_the_desks_pace():
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    run = subprocess.run([node, "--test", str(TEST)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
