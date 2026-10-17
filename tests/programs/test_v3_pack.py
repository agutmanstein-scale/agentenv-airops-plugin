import errno
import importlib.util
import json
import sys
from dataclasses import replace

import pytest
from day_helpers import day, flight, tail
from program_helpers import CACHE, DERIVED, ROOT, needs_cache

from agentenv_airops.build import fetch
from agentenv_airops.programs import candidates, network, rbs, references, unseen
from agentenv_airops.programs.candidates import Built
from agentenv_airops.programs.layers import calibrate, config, crew
from agentenv_airops.programs.model import LAYERED, Task

sys.path.insert(0, str(ROOT / "scripts"))  # v3_pack imports world_check beside it, as a script run does
SPEC = importlib.util.spec_from_file_location("v3_pack", ROOT / "scripts" / "v3_pack.py")
v3_pack = importlib.util.module_from_spec(SPEC)
sys.modules["v3_pack"] = v3_pack  # its build workers' function is pickled by module name
SPEC.loader.exec_module(v3_pack)
MONTHS = ("2023-12", "2026-01")
CONTROL = ("2025-02-04", {"BOS": (420, 660)})


@needs_cache
def test_the_pack_splits_and_fits_on_its_train_dates_and_a_quick_run_writes_the_full_packs_tasks(tmp_path,
                                                                                                 monkeypatch):
    """Over the storm dates of two winter months (so the test builds 11, not 207) and one control date: every split
    date is written with layers and no crews (v3's default) at constants fitted on the train dates, rescaled to the
    unseen scale fitted on them, the control after them alike; `--only` two of them, resumed, writes those same
    lines."""
    storms, counts = network.storms(CACHE, MONTHS)
    monkeypatch.setattr(network, "storms", lambda inputs: (storms, counts))
    monkeypatch.setattr(network, "control_dates", lambda inputs, derived: dict([CONTROL]))
    v3_pack.main(["--out", str(tmp_path), "--workers", "4"])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    lines = (tmp_path / "tasks.jsonl").read_text().splitlines()
    control = Task.from_dict(json.loads(lines.pop()))
    assert control.split == "control" and control.date == CONTROL[0] and not control.bases and control.layered
    assert manifest["controls"] == {CONTROL[0]: {"hubs": ["BOS"], "windows": {"BOS": [420, 660]},
                                                 "task_id": control.task_id}}
    built = {d: tuple(json.loads((tmp_path / "built" / f"{d}.json").read_text())["task"]["hubs"]) for d in storms}
    split = network.split(built, candidates.events(storms), 0)
    train = sorted(d for d, v in split.items() if v == "train")
    assert manifest["split"] == split and manifest["constants"]["dates"] == train and train
    assert manifest["constants"]["reserves"] == {} and manifest["bases"] == []
    assert (manifest["constants"]["short"], manifest["constants"]["min_sched"]) == (0.9, 5)
    tasks = [Task.from_dict(json.loads(line)) for line in lines]
    assert [t.date for t in tasks] == sorted(split) and all(t.split == split[t.date] for t in tasks)
    assert all(json.loads(line)["version"] == LAYERED for line in lines)
    assert all(not t.bases and not any(f.crew for f in t.flights) and not t.layers.reserves for t in tasks)
    assert manifest["counts"]["total"]["flights"] == sum(len(t.flights) for t in [*tasks, control])
    fits = candidates.e_table(CACHE, DERIVED)
    plain = {d: Task.from_dict(json.loads((tmp_path / "built" / f"{d}.json").read_text())["task"]) for d in split}
    scale = unseen.fit_scale([plain[d] for d in train], fits)
    assert manifest["scale"] == scale and scale != dict.fromkeys(scale, 1.0)
    cfg, shipped = config.Config.from_dict(manifest["constants"]), unseen.table(fits, scale)
    assert [t.to_json() for t in tasks] == [
        network.layered(rbs.rescale(replace(plain[d], split=split[d]), scale, *shipped), cfg, CACHE, DERIVED).to_json()
        for d in sorted(split)]
    held = Task.from_dict(json.loads((tmp_path / "built" / f"control-{CONTROL[0]}.json").read_text())["task"])
    assert control.to_json() == network.layered(rbs.rescale(replace(held, split="control"), scale, *shipped), cfg,
                                                CACHE, DERIVED).to_json()
    assert any(t.layers.dep_caps for t in tasks) and any(t.layers.arr_caps for t in tasks)
    assert all(t.layers.rates == manifest["constants"]["rate"] and t.layers.weather for t in tasks)
    tafs = sum(k == "taf" for t in [*tasks, control] for _, k, _, _ in t.layers.weather)
    assert manifest["counts"]["total"]["weather"]["taf"] == tafs > 0
    for key in ("iem/speci/JFK_2026_01.csv", "iem/metar/BOS_2023_12.csv", "bts/ontime_2026_07.zip",
                "iem/afos/TAFBOS_2023_12.txt"):
        assert manifest["inputs"][key] == fetch.sha256(CACHE / key)

    chosen = [min(train), min(d for d, v in split.items() if v == "eval")]
    stamps = {d: (tmp_path / "built" / f"{d}.json").stat().st_mtime_ns for d in [*storms, f"control-{CONTROL[0]}"]}
    v3_pack.main(["--out", str(tmp_path), "--workers", "4", "--resume", "--only", ",".join(chosen)])
    assert stamps == {d: (tmp_path / "built" / f"{d}.json").stat().st_mtime_ns for d in stamps}
    quick = (tmp_path / "tasks.jsonl").read_text().splitlines()
    assert quick == [lines[sorted(split).index(d)] for d in sorted(chosen)]
    assert json.loads((tmp_path / "manifest.json").read_text())["constants"] == manifest["constants"]


@needs_cache
def test_a_v4_crewed_pack_fits_its_cut_on_a_train_sample_and_builds_every_date_at_it(tmp_path, monkeypatch):
    """With `--crew v4 --max-segments 4` the buffer is fitted (the fit stubbed to pick 30): each buffer of the grid
    builds the train sample at its cut, layered at the fitted rates with unlimited reserves; every date is then built
    at the pick, its pools fitted there, and the manifest records the crew, its fit and its sample."""
    storms, counts = network.storms(CACHE, MONTHS)
    monkeypatch.setattr(network, "storms", lambda inputs: (storms, counts))
    monkeypatch.setattr(network, "control_dates", lambda inputs, derived: dict([CONTROL]))
    seen = {}

    def fit(grid, workers):
        seen.update(grid)
        return crew.Rule("v4", 30, 4), [{"stub": True}]

    monkeypatch.setattr(calibrate, "crew_fit", fit)
    with pytest.raises(SystemExit):
        v3_pack.main(["--out", str(tmp_path), "--crew", "v4"])
    v3_pack.main(["--out", str(tmp_path), "--workers", "4", "--bases", "JFK,BOS,FLL,MCO", "--crew", "v4",
                  "--max-segments", "4"])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    train = manifest["constants"]["dates"]
    sample = list(calibrate.sample(train))
    assert manifest["crew"] == {"crew": "v4", "buffer": 30, "max_segments": 4, "fitted": True,
                                "fit": {"sample": sample, "seed": 0, "time_limit_s": 600.0, "rules": [{"stub": True}],
                                        "chosen": {"crew": "v4", "buffer": 30, "max_segments": 4}}}
    assert list(seen) == calibrate.grid(calibrate.BUFFERS, (4,))
    for rule, tasks in seen.items():
        assert [t.date for t in tasks] == sample and all(t.layers and not t.layers.reserves for t in tasks)
        x = Task.from_dict(json.loads((tmp_path / v3_pack.folder_of(tmp_path, rule).name / f"{sample[0]}.json")
                                      .read_text())["task"])
        assert [f.crew for f in tasks[0].flights] == [f.crew for f in x.flights]
        assert all(f.sched_dep + f.sched_block + rule.buffer <= f.crew.report + f.crew.fdp for f in x.flights)
    assert seen[crew.Rule("v4", 0, 4)][0].flights != seen[crew.Rule("v4", 120, 4)][0].flights
    tasks = [Task.from_dict(json.loads(line)) for line in (tmp_path / "tasks.jsonl").read_text().splitlines()]
    at = {d: Task.from_dict(json.loads((tmp_path / "built-v4-30-4" / f"{d}.json").read_text())["task"])
          for d in manifest["split"]}
    for t in tasks:
        if t.split != "control":
            assert [f.crew for f in t.flights] == [f.crew for f in at[t.date].flights]
    assert all(t.bases == ("JFK", "BOS", "FLL", "MCO") for t in tasks)
    assert {t.layers.reserves.keys() <= set(t.bases) for t in tasks} == {True}


def test_a_resumed_pack_keeps_each_dates_build_and_rebuilds_a_date_left_unbuilt_or_cut_short(tmp_path):
    """A date an earlier run couldn't build (its input since fetched) or whose file a killed run cut short is built
    again; a built date is read back as it was written."""
    task = replace(day([flight(0, "JFK", "BUF", 100, 60)], [tail("N1", "JFK")]), date="2023-12-03")
    v3_pack.save(tmp_path / "2023-12-03.json", Built(task, {"date": "2023-12-03"}))
    v3_pack.save(tmp_path / "2023-12-04.json", Built(None, {"date": "2023-12-04", "reason": "error: registry"}))
    (tmp_path / "2023-12-05.json").write_text('{"row": {"date": "2023-12-05", "hu')
    kept = v3_pack.kept(tmp_path, ["2023-12-03", "2023-12-04", "2023-12-05", "2023-12-06"])
    assert list(kept) == ["2023-12-03"] and kept["2023-12-03"].task.to_json() == task.to_json()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023-12-03.json", "2023-12-04.json", "2023-12-05.json"]


def test_a_date_missing_an_input_is_unbuilt_with_its_reason_and_any_other_error_stops_the_pack(monkeypatch):
    def missing(job):
        raise FileNotFoundError("cache/inputs/faa/ReleasableAircraft.zip")

    monkeypatch.setattr(network, "build_one", missing)
    b = v3_pack.build_one(("2026-07-02", ("JFK",)))
    assert b.task is None and b.row == {"date": "2026-07-02", "hubs": ["JFK"], "task_id": None,
                                        "reason": "error: FileNotFoundError: cache/inputs/faa/ReleasableAircraft.zip"}
    monkeypatch.setattr(network, "build_one", lambda job: {}["N3291J"])
    with pytest.raises(KeyError):
        v3_pack.build_one(("2026-07-02", ("JFK",)))

    def too_many(job):
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(network, "build_one", too_many)
    with pytest.raises(OSError):
        v3_pack.build_one(("2026-07-02", ("JFK",)))


def test_only_names_each_date_once():
    assert v3_pack.dates_of(" 2024-08-06,2024-07-15,,2024-08-06 ") == ["2024-07-15", "2024-08-06"]
    assert v3_pack.dates_of("") == []


def test_the_refs_stage_stores_world_checks_references_the_probe_and_the_costs_a_load_reads(tmp_path, crewed):
    """On the fixture slice's crewed evening and the same day as a control, at small time limits: one JSON per task
    with world_check's references (the flat anchor on the control alone), the replay's fidelity and the probe; refs.json
    the anchor's and the follower's costs with the task's digest; a rerun keeps every result and runs nothing."""
    control = replace(crewed, task_id=crewed.task_id + "-c", split="control")
    got = v3_pack.refs(tmp_path, [crewed, control], 2, (60.0, 30.0))
    r, c = got[crewed.task_id], got[control.task_id]
    for k in (*v3_pack.REFS, "fidelity", "probe", "jetblue"):
        assert "error" not in r[k], (k, r[k])
    assert "flat" in c and "flat" not in r and not v3_pack.failed(got)
    assert r["anchor"]["proven"] and r["anchor"]["exact"] and r["follower"]["total"] >= r["anchor"]["objective"]
    assert r["jetblue"]["cancelled"] == r["probe"]["cancelled"] == sum(f.actual.dep is None for f in crewed.flights)
    assert r["fidelity"]["flown"] <= r["fidelity"]["jetblue_flown"] == r["jetblue"]["flown"]
    assert json.loads((tmp_path / "refs" / f"{crewed.task_id}.json").read_text()) == r
    assert json.loads((tmp_path / "refs.json").read_text())[crewed.task_id] == {
        "anchor": r["anchor"]["total"], "follower": r["follower"]["total"], "task": references.digest(crewed)}

    def rerun(job):
        raise AssertionError(f"ran {job[0]} again")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(v3_pack, "refs_job", rerun)
    try:
        assert v3_pack.refs(tmp_path, [crewed, control], 2, (60.0, 30.0)) == got
    finally:
        monkey.undo()


def test_a_follower_stopped_by_an_unproven_watch_is_captured_and_flagged(monkeypatch, crewed):
    def unproven(task, *a, **k):
        raise RuntimeError(f"{task.task_id}: watch 60 solve Time limit reached at gap 3.1%")

    monkeypatch.setattr(references, "follow", unproven)
    names = ("naive", "anchor", "follower")
    r = v3_pack.refs_job((crewed.task_id, crewed.to_json(), "sig", names, (60.0, 30.0)))
    assert r["follower"]["unproven_watch"] and "at gap 3.1%" in r["follower"]["error"]
    assert "total" in r["naive"] and r["anchor"]["proven"]
    assert v3_pack.failed({crewed.task_id: r}) == {crewed.task_id: ["follower"]}
    assert v3_pack.stored([crewed], {crewed.task_id: r}) == {
        crewed.task_id: {"anchor": r["anchor"]["total"], "task": references.digest(crewed)}}
