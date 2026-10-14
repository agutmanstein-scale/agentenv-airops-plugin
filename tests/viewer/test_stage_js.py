import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
TEST = ROOT / "tests" / "viewer" / "stage.test.mjs"
MINI = ROOT / "tests" / "viewer" / "fixtures" / "stage-mini"
SIDECARS = sorted((ROOT / "cache" / "episodes").glob("*.stage.json.gz"))

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node (Node.js 18+) on PATH to run web/stage/motion.js")


def plan(stage: Path, traffic: Path, at: list[str] = ()) -> tuple[str, dict, list]:
    r = subprocess.run([NODE, str(TEST), str(stage), str(traffic), ",".join(at)], capture_output=True, text=True,
                       timeout=900, cwd=ROOT)
    assert r.returncode == 0 and r.stdout.rstrip().endswith("PASS"), r.stdout[-4000:] + r.stderr[-2000:]
    lines = dict(ln.split(" ", 1) for ln in r.stdout.splitlines() if ln.startswith(("POSES ", "EVENTS ")))
    return r.stdout, json.loads(lines.get("POSES", "{}")), json.loads(lines.get("EVENTS", "[]"))


def test_the_stage_flies_a_whole_day_on_the_mini_airport():
    at = ["N1A@654.6", "N1A@700", "N1A@750", "N1A@790", "N1A@1450", "N3C@930", "N3C@965", "N2B@1466", "N6F@974.6"]
    _, poses, events = plan(MINI / "stage.json", MINI / "traffic.json", at)
    assert poses["N1A@654.6"]["phase"] == "roll" and round(poses["N1A@654.6"]["heading"]) == 270
    assert poses["N6F@974.6"]["phase"] == "roll" and round(poses["N6F@974.6"]["heading"]) == 90
    assert poses["N1A@700"]["phase"] == "absent"
    assert [poses[f"N1A@{t}"]["stand"] for t in (750, 790, 1450)] == ["S1", "S1", "S1"]
    assert poses["N3C@930"] | {"x": 0, "y": 0} == {"phase": "parked", "x": 0, "y": 0, "heading": 0, "gear": 1,
                                                  "stand": "S4"}
    assert poses["N3C@965"]["phase"] == "absent"
    assert poses["N2B@1466"]["phase"] in {"taxi", "hold", "lineup"}
    kinds = {(e["key"], e["flight"], e["kind"]) for e in events}
    assert ("N1A", "B6 3", "pushback") not in kinds
    assert {("N2B", "B6 7", "pushback"), ("N2B", "B6 7", "takeoff"), ("N4D", "DL 9", "gate_in")} <= kinds
    assert {e["t"] for e in events if e["kind"] == "pushback"} == {620, 640, 700, 830, 960, 1460}
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)


NONE = pytest.param(None, marks=pytest.mark.skip(reason="no traffic sidecars in cache/episodes"))


@pytest.mark.parametrize("sidecar", [pytest.param(p, id=p.name.split(".")[-4]) for p in SIDECARS] or [NONE])
def test_the_stage_flies_each_cached_episode(sidecar):
    hub = json.loads(gzip.decompress(sidecar.read_bytes()))["hub"]
    stage = ROOT / "cache" / "stage" / hub / "stage.json.gz"
    if not stage.is_file():
        pytest.skip(f"needs cache/stage/{hub} from scripts/build_stage.py {hub}")
    out, _, events = plan(stage, sidecar)
    assert f"{hub} " in out.splitlines()[0]
    assert {"pushback", "takeoff", "landing", "gate_in"} <= {e["kind"] for e in events}
