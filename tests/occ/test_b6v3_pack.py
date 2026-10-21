"""b6-v3 is written from b6-v2 and a stage-2 reference run; b6-v2 stays byte-identical: its manifest is the one
shipped at 6bc7139 and every file matches the manifest, before and after a v3 build reads it."""

import gzip
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

from agentenv_airops.occ.pack import Pack
from agentenv_airops.programs import pax

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_occ_pack", ROOT / "scripts" / "build_occ_pack.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
B6_V2 = ROOT / "data" / "b6-v2"
MANIFEST_SHA256 = "5576545931b62dd6bd9274a966016648a51f014b54aa452367983ad401bf13b8"


def intact(pack: Path) -> bool:
    manifest = json.loads((pack / "manifest.json").read_text())
    return all(hashlib.sha256((pack / f).read_bytes()).hexdigest() == h for f, h in manifest["files"].items())


def test_b6_v2_is_byte_identical_and_a_v3_build_leaves_it_so(tmp_path):
    assert hashlib.sha256((B6_V2 / "manifest.json").read_bytes()).hexdigest() == MANIFEST_SHA256 and intact(B6_V2)
    base = Pack(B6_V2)
    tid, t = sorted(base.tasks.items())[0]
    w = base.world(tid)
    passengers = pax.book(w.task, pax.Flows.load(t["date"]), pax.Dwell.load())
    run = tmp_path / "run" / "dates"
    run.mkdir(parents=True)
    plan = {"0": {"not_before": 300}}
    (run / f"{t['date']}.json").write_text(json.dumps({
        "date": t["date"], "passengers": passengers.to_dict(),
        "reference": {"P": 100.0, "bound": 90.0, "gap_pct": 10.0, "from": "B0", "mode": "desk", "plan": plan},
        "candidates": {"B0": {"P": 100.0}, "v2_best": {"P": 120.0}}, "controls": {"naive": 130.0},
        "airline": {"P": 140.0}}))
    out = builder.assemble_v3(tmp_path / "run", tmp_path / "b6-v3", B6_V2)
    assert out["dates"] == 1 and len(out["skipped"]) == len(base.manifest["skipped"]) + len(base.tasks) - 1
    d = json.loads(gzip.decompress((tmp_path / "b6-v3" / "dates" / f"{t['date']}.json.gz").read_bytes()))
    v2 = json.loads(gzip.decompress((B6_V2 / "dates" / f"{t['date']}.json.gz").read_bytes()))
    assert {k: d[k] for k in v2} == v2 and d["best_plan_p"] == plan and d["refs_p"]["best_from"] == "B0"
    assert pax.Passengers.from_dict(d["passengers"]) == passengers
    manifest = json.loads((tmp_path / "b6-v3" / "manifest.json").read_text())
    assert manifest["base_manifest_sha256"] == MANIFEST_SHA256 and intact(tmp_path / "b6-v3")
    assert Pack(tmp_path / "b6-v3").world(tid).task == w.task
    assert hashlib.sha256((B6_V2 / "manifest.json").read_bytes()).hexdigest() == MANIFEST_SHA256 and intact(B6_V2)
    shutil.rmtree(tmp_path / "b6-v3")
