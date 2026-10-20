"""The bundle airops-v3: what `agent-env airops-v3 tasks` writes from a pack, the packaged bundle and data/b6-v3 it
ships, and scripts/ship_v3_pack.py, which turns a v3_pack.py --refs folder into data/b6-v3."""

import gzip
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentenv_airops.programs import references
from agentenv_airops.programs.model import load_tasks
from agentenv_airops.schedule import VERIFIER
from agentenv_airops.v3day import tasks as v3_tasks
from agentenv_airops.v3day.cli import airops_v3

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ship_v3_pack", ROOT / "scripts" / "ship_v3_pack.py")
ship_v3_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ship_v3_pack)


def pack(folder: Path, tasks: list, refs: dict) -> Path:
    """A v3_pack.py --refs folder: tasks.jsonl, refs.json and a manifest."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tasks.jsonl").write_text("".join(t.to_json() + "\n" for t in tasks))
    (folder / "refs.json").write_text(json.dumps(refs))
    (folder / "manifest.json").write_text(json.dumps({"code": "abc123", "scale": {"JFK": 0.35}, "seed": 0}))
    return folder


@pytest.fixture
def two(task):
    """The slice's day as an eval and a train task, the eval one with stored costs run on it, the train one with stale
    ones (run on another task)."""
    ev, tr = replace(task, task_id="b6-eval", split="eval"), replace(task, task_id="b6-train", split="train")
    return ev, tr, {ev.task_id: {"anchor": 100, "follower": 120, "task": references.digest(ev)},
                    tr.task_id: {"anchor": 90, "follower": 95, "task": references.digest(ev)}}


def test_a_bundle_has_each_days_steps_with_the_costs_stored_for_that_very_task_and_a_noplay(tmp_path, two):
    ev, tr, refs = two
    folder = pack(tmp_path / "pack", [ev, tr], refs)
    out = tmp_path / "bundle"
    assert v3_tasks.generate(out, split="all", pack=folder, noplay=True) == [ev.task_id, tr.task_id, v3_tasks.NOPLAY]
    read = {p.stem: json.loads(p.read_text()) for p in (out / "tasks").iterdir()}
    assert read[ev.task_id] == v3_tasks.steps(ev, 100, 120)
    assert read[tr.task_id] == v3_tasks.steps(tr)
    load = next(s for s in read[tr.task_id] if s["id"] == "load-task")
    assert load["directives"][0]["args"] == {"task_id": tr.task_id} and load["timeout_seconds"] > 1800
    assert [s["id"] for s in read[v3_tasks.NOPLAY]] == ["deploy", "load-task", "clock", "end-day", "grade"]
    assert read[v3_tasks.NOPLAY] == [s for s in read[ev.task_id] if s["id"] not in ("deploy-agent", "play")]
    verifier = v3_tasks.V1_BUNDLE / "artifacts" / VERIFIER / "verify.py"
    assert (out / "artifacts" / VERIFIER / "verify.py").read_bytes() == verifier.read_bytes()
    assert (out / "README.md").read_text() == v3_tasks.README
    assert v3_tasks.generate(tmp_path / "eval", split="eval", pack=folder) == [ev.task_id]


def test_the_packaged_bundle_is_the_shipped_packs_eval_days_as_generate_writes_them(tmp_path):
    out = tmp_path / "bundle"
    names = v3_tasks.generate(out, split="eval", noplay=True)
    written = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    packaged = Path(str(v3_tasks.BUNDLE))
    assert written == {p.relative_to(packaged).as_posix(): p.read_bytes() for p in packaged.rglob("*") if p.is_file()}
    assert len(names) == 25 and names[-1] == v3_tasks.NOPLAY


def test_the_shipped_pack_stores_an_anchor_run_on_every_one_of_its_tasks():
    tasks, refs = v3_tasks.pack_refs()
    manifest = json.loads((Path(str(v3_tasks.PACK)) / "manifest.json").read_text())
    assert manifest["pack"] == "b6-v3" and manifest["bases"] == []
    assert manifest["tasks"] == {t.task_id: {"date": t.date, "split": t.split} for t in tasks}
    assert all(t.layered and not t.bases for t in tasks)
    assert all(refs[t.task_id]["task"] == references.digest(t) and "anchor" in refs[t.task_id] for t in tasks)


def test_tasks_load_from_a_gzipped_file_or_a_folder_with_one(tmp_path, task):
    other = replace(task, task_id="b6-other")
    line = "".join(t.to_json() + "\n" for t in (task, other)).encode()
    (tmp_path / "tasks.jsonl.gz").write_bytes(gzip.compress(line))
    assert load_tasks(tmp_path) == load_tasks(tmp_path / "tasks.jsonl.gz") == [task, other]
    (tmp_path / "tasks.jsonl").write_bytes(line[:len(task.to_json()) + 1])
    assert load_tasks(tmp_path) == [task]


def test_shipping_a_pack_writes_its_lines_gzipped_its_refs_and_what_it_is_and_refuses_stale_refs(tmp_path, two):
    ev, tr, refs = two
    folder = pack(tmp_path / "pack", [ev], refs)
    out = tmp_path / "b6-v3"
    manifest = ship_v3_pack.ship(folder, out)
    assert gzip.decompress((out / "tasks.jsonl.gz").read_bytes()) == (folder / "tasks.jsonl").read_bytes()
    assert json.loads((out / "refs.json").read_text()) == {ev.task_id: refs[ev.task_id]}
    assert manifest == json.loads((out / "manifest.json").read_text()) == {
        "pack": "b6-v3", "code": "abc123", "seed": 0, "scale": {"JFK": 0.35},
        "tasks": {ev.task_id: {"date": ev.date, "split": "eval"}}}
    first = (out / "tasks.jsonl.gz").read_bytes()
    ship_v3_pack.ship(folder, out)
    assert (out / "tasks.jsonl.gz").read_bytes() == first
    with pytest.raises(SystemExit, match="b6-train"):
        ship_v3_pack.ship(pack(tmp_path / "stale", [ev, tr], refs), tmp_path / "never")
    assert not (tmp_path / "never").exists()


def test_the_cli_writes_the_bundle_and_names_unknown_days(tmp_path, two):
    ev, tr, refs = two
    folder = pack(tmp_path / "pack", [ev, tr], refs)
    run = CliRunner().invoke(airops_v3, ["tasks", "--pack", str(folder), "--out", str(tmp_path / "b"), "--noplay"])
    assert run.exit_code == 0 and sorted(p.stem for p in (tmp_path / "b" / "tasks").iterdir()) == [
        ev.task_id, v3_tasks.NOPLAY]
    run = CliRunner().invoke(airops_v3, ["tasks", "--pack", str(folder), "--task", "b6-nope"])
    assert run.exit_code == 2 and "unknown task ids: b6-nope" in run.output
