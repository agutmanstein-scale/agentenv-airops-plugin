import errno
import importlib.util
import json
import sys
from dataclasses import replace

import pytest
from day_helpers import day, flight, tail
from program_helpers import CACHE, DERIVED, ROOT, needs_cache

from agentenv_airops.build import fetch
from agentenv_airops.programs import candidates, network, rbs, unseen
from agentenv_airops.programs.candidates import Built
from agentenv_airops.programs.layers import config
from agentenv_airops.programs.model import LAYERED, Task

SPEC = importlib.util.spec_from_file_location("v3_pack", ROOT / "scripts" / "v3_pack.py")
v3_pack = importlib.util.module_from_spec(SPEC)
sys.modules["v3_pack"] = v3_pack  # its build workers' function is pickled by module name
SPEC.loader.exec_module(v3_pack)
MONTHS = ("2023-12", "2026-01")


@needs_cache
def test_the_pack_splits_and_fits_on_its_train_dates_and_a_quick_run_writes_the_full_packs_tasks(tmp_path,
                                                                                                 monkeypatch):
    """Over the storm dates of two winter months (so the test builds 11, not 207): every split date is written with
    crew and layers at constants fitted on the train dates, rescaled to the unseen scale fitted on them; `--only` two
    of them, resumed, writes those same lines."""
    storms, counts = network.storms(CACHE, MONTHS)
    monkeypatch.setattr(network, "storms", lambda inputs: (storms, counts))
    v3_pack.main(["--out", str(tmp_path), "--workers", "4"])
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    lines = (tmp_path / "tasks.jsonl").read_text().splitlines()
    built = {d: tuple(json.loads((tmp_path / "built" / f"{d}.json").read_text())["task"]["hubs"]) for d in storms}
    split = network.split(built, candidates.events(storms), 0)
    train = sorted(d for d, v in split.items() if v == "train")
    assert manifest["split"] == split and manifest["constants"]["dates"] == train and train
    assert set(manifest["constants"]["reserves"]) == {"BOS", "FLL", "JFK", "MCO"}
    assert (manifest["constants"]["short"], manifest["constants"]["min_sched"]) == (0.9, 5)
    tasks = [Task.from_dict(json.loads(line)) for line in lines]
    assert [t.date for t in tasks] == sorted(split) and all(t.split == split[t.date] for t in tasks)
    assert all(json.loads(line)["version"] == LAYERED for line in lines)
    assert all(t.bases and all(f.crew for f in t.flights) and t.layers.reserves == manifest["constants"]["reserves"]
               for t in tasks)
    assert manifest["counts"]["total"]["flights"] == sum(len(t.flights) for t in tasks)
    fits = candidates.e_table(CACHE, DERIVED)
    plain = {d: Task.from_dict(json.loads((tmp_path / "built" / f"{d}.json").read_text())["task"]) for d in split}
    scale = unseen.fit_scale([plain[d] for d in train], fits)
    assert manifest["scale"] == scale and scale != dict.fromkeys(scale, 1.0)
    cfg, shipped = config.Config.from_dict(manifest["constants"]), unseen.table(fits, scale)
    assert [t.to_json() for t in tasks] == [
        network.layered(rbs.rescale(replace(plain[d], split=split[d]), scale, *shipped), cfg, CACHE, DERIVED).to_json()
        for d in sorted(split)]
    assert any(t.layers.dep_caps for t in tasks) and any(t.layers.arr_caps for t in tasks)
    for key in ("iem/speci/JFK_2026_01.csv", "iem/metar/BOS_2023_12.csv", "bts/ontime_2026_07.zip"):
        assert manifest["inputs"][key] == fetch.sha256(CACHE / key)

    chosen = [min(train), min(d for d, v in split.items() if v == "eval")]
    stamps = {d: (tmp_path / "built" / f"{d}.json").stat().st_mtime_ns for d in storms}
    v3_pack.main(["--out", str(tmp_path), "--workers", "4", "--resume", "--only", ",".join(chosen)])
    assert stamps == {d: (tmp_path / "built" / f"{d}.json").stat().st_mtime_ns for d in storms}
    quick = (tmp_path / "tasks.jsonl").read_text().splitlines()
    assert quick == [lines[sorted(split).index(d)] for d in sorted(chosen)]
    assert json.loads((tmp_path / "manifest.json").read_text())["constants"] == manifest["constants"]


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
