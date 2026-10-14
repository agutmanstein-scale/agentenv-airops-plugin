import json
import sys
from pathlib import Path

import pytest

from agentenv_airops.core.model import load_pack

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recorder import DAY, FIXTURES  # noqa: E402


@pytest.fixture(scope="session")
def task():
    """b6-jfk-20240109-1800 from b6-v1 with its reference, 45 flights in 9 watches."""
    [day] = load_pack(DAY)
    return day


@pytest.fixture(scope="session")
def e2e() -> dict:
    """airops-llm's record of the zero-spend e2e run on local Docker: the follower's decisions through the real DAG,
    one turn per watch, with get_time calls."""
    return json.loads((FIXTURES / "e2e-transcript.json").read_text())
