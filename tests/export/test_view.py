"""The scored runs of the sweeps under results/runs replayed into episodes, and `agent-env airops view` serving them on
the viewer: its routes, and the server over HTTP."""

import json
import shutil
import threading
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner
from recorder import DAY, FIXTURES

from agentenv_airops import export, record, view
from agentenv_airops.cli import airops
from agentenv_airops.episodes import Runs

ID = "b6-jfk-20240109-1800"
SONNET = "anthropic/claude-sonnet-5-5"


def sweep_dir(root: Path, name: str = "e2e", reward: float = 0.840588, k: int = 1) -> Path:
    """A sweep as `agent-env airops sweep run` leaves it, with the e2e run as its one scored attempt."""
    out = root / name
    (out / "transcripts" / "anthropic-claude-sonnet-5-5").mkdir(parents=True)
    transcript = f"transcripts/anthropic-claude-sonnet-5-5/{ID}-r1-a2.json"
    shutil.copyfile(FIXTURES / "e2e-transcript.json", out / transcript)
    (out / "sweep.json").write_text(json.dumps({"name": name, "models": [SONNET], "tasks": [ID], "k": k,
                                               "episode_cap_usd": 1.0, "pack": str(DAY)}))
    rows = [{"model": SONNET, "task_id": ID, "rep": 1, "attempt": 1, "outcome": "failed", "error_code": "x"},
            {"model": SONNET, "task_id": ID, "rep": 1, "attempt": 2, "outcome": "scored", "reward": reward,
             "instance": "@local/x/b6-jfk-20240109-1800-abcd1234", "end_reason": "done", "turns": 9,
             "tool_calls": 43, "cost_usd": 0.0, "transcript": transcript}]
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return out


def test_the_scored_runs_replay_into_episodes_and_one_that_does_not_is_reported(tmp_path):
    sweep_dir(tmp_path)
    sweep_dir(tmp_path, "drifted", reward=0.5, k=2)
    runs = Runs(tmp_path, cache=tmp_path / "cache")
    assert [r.key for r in runs.runs] == [("drifted.r1", SONNET, ID), ("e2e", SONNET, ID)]
    [ep] = runs.episodes()
    assert (ep["run"], ep["reward"], ep["record"]["attempt"], ep["record"]["sweep"]) == ("e2e", 0.840588, 2, "e2e")
    export.validate(ep)
    [failed] = runs.failed
    assert failed["run"] == "drifted.r1" and "replays to reward 0.840588, scored 0.5" in failed["error"]
    assert [r.id for r in Runs(tmp_path, ["e2e"]).runs] == ["e2e"]


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict[str, bytes]:
    root = tmp_path_factory.mktemp("runs")
    sweep_dir(root)
    runs = Runs(root, cache=root / "cache")
    return export.folder(runs.episodes(), root / "cache")


def test_the_routes_serve_the_viewer_the_episodes_and_nothing_else(files):
    name = f"e2e.anthropic-claude-sonnet-5-5.{ID}.json"
    assert view.route(files, "/") == (302, {"Location": "/web/index.html"}, b"")
    status, headers, body = view.route(files, "/web/index.html")
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"app.js" in body
    status, headers, body = view.route(files, "/cache/episodes/index.json")
    assert [e["url"] for e in json.loads(body)["episodes"]] == [name] and headers["Content-Type"] == "application/json"
    assert json.loads(view.route(files, f"/cache/episodes/{name}")[2])["reward"] == 0.840588
    assert view.route(files, "/web/app.js")[1]["Content-Type"].startswith("text/javascript")
    for path in ("/web/../pyproject.toml", "/web/%2e%2e/pyproject.toml", "/cache/episodes/other.json", "/api"):
        assert view.route(files, path)[0] == 404, path


def test_the_server_answers_over_http(files):
    server = view.serve(files, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(f"{base}/cache/episodes/index.json") as r:
            assert json.load(r)["episodes"][0]["reward"] == 0.840588 and r.headers["Cache-Control"] == "no-cache"
        with urllib.request.urlopen(f"{base}/") as r:
            assert r.url.endswith("/web/index.html") and b"AirOpsEnv viewer" in r.read()
    finally:
        server.shutdown()
        server.server_close()


def test_view_without_sweeps_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(airops, ["view"])
    assert result.exit_code == 2 and "agent-env airops sweep run" in result.output


def test_record_exports_the_run_beside_the_cache_and_hands_the_rest_to_scripts_record(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "record.py").write_text(
        "import json, sys; from pathlib import Path\n"
        "Path(sys.argv[0]).with_name('argv.json').write_text(json.dumps(sys.argv[1:]))\n")
    monkeypatch.setattr(record, "ROOT", root)
    monkeypatch.setattr(record, "SCRIPT", root / "scripts" / "record.py")
    monkeypatch.chdir(tmp_path)
    sweep_dir(tmp_path / "results" / "runs")
    result = CliRunner().invoke(airops, ["record", "e2e", "--task", ID, "--out", "film.mp4", "--cut", "1080:hub"])
    assert result.exit_code == 0, result.output
    name = f"e2e.anthropic-claude-sonnet-5-5.{ID}.json"
    episode = root / "cache" / "episodes" / name
    assert json.loads(episode.read_text())["reward"] == 0.840588
    assert json.loads((root / "scripts" / "argv.json").read_text()) == [
        "--episode", str(episode), "--out", "film.mp4", "--cut", "1080:hub"]
    missing = CliRunner().invoke(airops, ["record", "e2e", "--task", "b6-jfk-20990101-0600", "--out", "f.mp4"])
    assert missing.exit_code == 2 and "no scored run of b6-jfk-20990101-0600 rep 1 in e2e" in missing.output
