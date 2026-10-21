"""The viewer's plain-English decoders, its board and score and how a gate's Terminal view reads (plain.test.mjs) under
Node's test runner."""

import shutil
import subprocess
from pathlib import Path

import pytest

TEST = Path(__file__).resolve().parent / "plain.test.mjs"


def test_the_viewer_reads_the_news_the_changes_and_the_score_in_plain_english():
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH")
    run = subprocess.run([node, "--test", str(TEST)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
