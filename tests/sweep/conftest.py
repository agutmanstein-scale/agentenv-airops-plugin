"""A stand-in `agent-env run` for the sweep: it records how it was called and leaves the instance context it is
scripted with where the sweep's instance reader finds it."""

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
from agent_env.config import reset_config

from agentenv_airops import sweep

FAKE = textwrap.dedent("""
    import fcntl, json, os, signal, sys, time, uuid
    from pathlib import Path

    here = Path(os.environ["FAKE_AGENT_ENV"])
    args = sys.argv[1:]
    stopped = []
    signal.signal(signal.SIGTERM, lambda *_: stopped.append(1))
    with open(here / "calls.jsonl", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        earlier = [c["argv"] for c in map(json.loads, f.read().splitlines()) if "start" in c]
        f.write(json.dumps({"argv": args, "pythonpath": os.environ.get("PYTHONPATH"), "pid": os.getpid(),
                            "start": time.time()}) + "\\n")
    if "--dry-run" in args:
        sys.exit(int(os.environ.get("FAKE_DRY_RUN_EXIT", "0")))
    task, model = args[args.index("--task") + 1], args[args.index("--model") + 1]
    script = json.loads((here / "script.json").read_text())
    plan = script.get(f"{model}|{task}") or script["*"]
    step = plan[min(earlier.count(args), len(plan) - 1)]
    while step.get("wait") and not stopped:
        time.sleep(0.02)
    time.sleep(step.get("sleep", 0))
    if step.get("context") is not None:
        instance = f"@local/fake/tasks/{task}-{uuid.uuid4().hex[:8]}"
        (here / "instances").mkdir(exist_ok=True)
        (here / "instances" / instance.replace("/", "_")).write_text(json.dumps(step["context"]))
        print(f"  tasks/{task}.json v1: {step.get('says', 'passed')}, 12.5s, instance {instance}")
    with open(here / "calls.jsonl", "a") as f:
        f.write(json.dumps({"argv": args, "pid": os.getpid(), "end": time.time()}) + "\\n")
    sys.exit(143 if stopped else step.get("exit", 0))
""")


class Fake:
    """The stand-in's folder: script.json says what each (model, task) run does on its first, second, ... call."""

    def __init__(self, root: Path):
        self.root = root

    def script(self, plans: dict[str, list[dict]]) -> None:
        (self.root / "script.json").write_text(json.dumps(plans))

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def runs(self) -> list[list[str]]:
        return [c["argv"] for c in self.calls() if "start" in c and "--dry-run" not in c["argv"]]


@pytest.fixture
def fake(tmp_path, monkeypatch) -> Fake:
    """The sweep in tmp_path, its attempts played by the stand-in, which leaves the contexts it is scripted with
    where the sweep's instance reader finds them."""
    root = tmp_path / "fake"
    root.mkdir()
    script = root / "agent_env_run.py"
    script.write_text(FAKE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FAKE_AGENT_ENV", str(root))
    monkeypatch.setattr(sweep, "AGENT_ENV", [sys.executable, str(script)])
    monkeypatch.setattr(sweep, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(sweep, "context",
                        lambda instance: json.loads((root / "instances" / instance.replace("/", "_")).read_text()))
    monkeypatch.setattr(sweep, "trajectory", lambda uri: json.dumps({"format": "airops-rollout", "uri": uri}).encode())
    return Fake(root)




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

