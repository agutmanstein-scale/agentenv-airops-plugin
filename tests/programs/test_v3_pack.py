import importlib.util
import json
import sys

from program_helpers import CACHE, ROOT, needs_cache

from agentenv_airops.build import fetch
from agentenv_airops.programs import candidates, network
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
    crew and layers at constants fitted on the train dates; `--only` two of them, resumed, writes those same lines."""
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
    for key in ("iem/speci/JFK_2026_01.csv", "iem/metar/BOS_2023_12.csv", "bts/ontime_2026_07.zip"):
        assert manifest["inputs"][key] == fetch.sha256(CACHE / key)

    chosen = [min(train), min(d for d, v in split.items() if v == "eval")]
    stamps = {d: (tmp_path / "built" / f"{d}.json").stat().st_mtime_ns for d in storms}
    v3_pack.main(["--out", str(tmp_path), "--workers", "4", "--resume", "--only", ",".join(chosen)])
    assert stamps == {d: (tmp_path / "built" / f"{d}.json").stat().st_mtime_ns for d in storms}
    quick = (tmp_path / "tasks.jsonl").read_text().splitlines()
    assert quick == [lines[sorted(split).index(d)] for d in sorted(chosen)]
    assert json.loads((tmp_path / "manifest.json").read_text())["constants"] == manifest["constants"]
