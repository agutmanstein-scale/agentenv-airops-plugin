import importlib.util
import os
import sys
from pathlib import Path

import pytest
from agent_env.config import reset_config

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


@pytest.fixture(scope="session")
def hub():
    """scripts/hub_dataset.py as a module."""
    spec = importlib.util.spec_from_file_location("hub_dataset", HERE.parents[1] / "scripts" / "hub_dataset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def local_stores(monkeypatch, tmp_path):
    """agent-env on its local default stores under tmp_path, whatever config the machine has."""
    config = tmp_path / "config.toml"
    config.write_text("")
    for var in [v for v in os.environ if v.startswith("AGENT_ENV_")]:
        monkeypatch.delenv(var)
    monkeypatch.setenv("AGENT_ENV_CONFIG", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    reset_config()
    yield tmp_path / "state"
    reset_config()
