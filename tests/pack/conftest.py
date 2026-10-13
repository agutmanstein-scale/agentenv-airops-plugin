import importlib.util
import json
from pathlib import Path

import pytest

from agentenv_airops.build import pack

ROOT = Path(__file__).resolve().parents[2]
INPUTS = Path(__file__).resolve().parent / "fixtures" / "inputs"


def load_build_pack():
    spec = importlib.util.spec_from_file_location("build_pack", ROOT / "scripts" / "build_pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(out: Path, derived: Path, inputs: Path = INPUTS, *extra: str) -> int:
    """The build on the fixture slice: one month, JFK, eight tails, so the size and disruption floors come down."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pack, "MIN_TAILS", 3)
        mp.setitem(pack.MIN_DISRUPTION, "eval", 1)
        return load_build_pack().main(["--offline", "--months", "2023-06", "--hubs", "JFK", "--inputs", str(inputs),
                                       "--derived", str(derived), "--out", str(out), *extra])


@pytest.fixture(scope="session")
def run_build():
    return run


@pytest.fixture(scope="session")
def built(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("pack")
    out = root / "a" / "b6-v1"
    assert run(out, root / "derived") == 0
    return out


@pytest.fixture(scope="session")
def task(built) -> dict:
    (line,) = (built / "tasks.jsonl").read_text().splitlines()
    return json.loads(line)
