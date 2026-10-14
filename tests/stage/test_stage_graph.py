import gzip
import heapq
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAGES = ROOT / "cache" / "stage"
SIDECARS = sorted((ROOT / "cache" / "episodes").glob("*.stage.json.gz"))


def needs_stage(hub: str):
    return pytest.mark.skipif(not (STAGES / hub / "stage.json.gz").is_file(),
                              reason=f"needs cache/stage/{hub} from scripts/build_stage.py {hub}")


def load(path: Path) -> dict:
    return json.loads(gzip.decompress(path.read_bytes()))


def load_build_stage():
    spec = importlib.util.spec_from_file_location("build_stage", ROOT / "scripts" / "build_stage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reach(graph: dict, src: int) -> dict[int, float]:
    adj: dict[int, list[tuple[int, float]]] = {}
    for a, b, length, _ in graph["edges"]:
        adj.setdefault(a, []).append((b, length))
        adj.setdefault(b, []).append((a, length))
    dist, todo = {src: 0.0}, [(0.0, src)]
    while todo:
        d, u = heapq.heappop(todo)
        if d > dist[u]:
            continue
        for v, length in adj.get(u, ()):
            if d + length < dist.get(v, math.inf):
                dist[v] = d + length
                heapq.heappush(todo, (d + length, v))
    return dist


def assert_stands_reach_thresholds(graph: dict) -> None:
    for end, node in graph["thresholds"].items():
        dist = reach(graph, node)
        missing = sorted(ref for ref, n in graph["stand_nodes"].items() if n not in dist)
        assert not missing, f"{end}: {missing}"


def test_graph_nodes_crossings_snaps_gaps_and_strays():
    lines = [("runway", [(0, 0), (2000, 0)]), ("taxiway", [(0, 200), (2000, 200)]),
             ("taxiway", [(100, 200), (100, 0)]), ("taxiway", [(1900, 0), (1900, 198)]),
             ("taxilane", [(1000, 200), (1000, 260)]), ("taxilane", [(1600, 300), (1600, 100)]),
             ("taxilane", [(200, 350), (400, 350)])]
    stands = [("S1", [(1000, 280), (1000, 330)]), ("S2", [(1200, 150), (1200, 330)]),
              ("S3", [(300, 360), (300, 400)])]
    graph, leads = load_build_stage().taxi_graph(lines, stands, {"9": (0, 0), "27": (2000, 0)})
    nodes = graph["nodes"]
    assert nodes[graph["thresholds"]["9"]] == [0.0, 0.0] and nodes[graph["thresholds"]["27"]] == [2000.0, 0.0]
    assert len(set(graph["stand_nodes"].values())) == 3
    assert {k for *_, k in graph["edges"]} == {"runway", "taxiway", "taxilane", "lead"}
    assert [round(v, 1) for v in leads["S1"][0]] == [1000.0, 260.0]
    assert [round(v, 1) for v in leads["S2"][0]] == [1200.0, 200.0]
    assert [nodes[graph["stand_nodes"][r]] for r in ("S1", "S2", "S3")] == [[1000, 330], [1200, 330], [300, 400]]
    assert [1600.0, 200.0] in nodes
    assert len([p for p in nodes if math.dist(p, (1900, 200)) < 3]) == 1
    assert_stands_reach_thresholds(graph)


@pytest.mark.parametrize("hub", [pytest.param(h, marks=needs_stage(h)) for h in ("BOS", "JFK")])
def test_every_stand_reaches_every_threshold(hub):
    stage = load(STAGES / hub / "stage.json.gz")
    graph = stage["taxi_graph"]
    assert {s["ref"] for s in stage["stands"]} == set(graph["stand_nodes"])
    assert {e["id"] for r in stage["runways"] for e in r["ends"]} == set(graph["thresholds"])
    assert_stands_reach_thresholds(graph)


@needs_stage("JFK")
def test_jfk_has_its_four_runways_and_terminals_with_their_gates():
    stage = load(STAGES / "JFK" / "stage.json.gz")
    assert sorted(r["ref"] for r in stage["runways"]) == ["13L/31R", "13R/31L", "4L/22R", "4R/22L"]
    gates = {s["terminal"] for s in stage["stands"]}
    assert {"T1", "T4", "T5", "T7", "T8", "remote"} <= gates
    assert all(s["ref"].startswith(s["terminal"] + "-") for s in stage["stands"] if s["terminal"] != "remote")
    assert max(b["height"] for b in stage["buildings"]) > 300 and stage["tower"]["height"] > 90


@pytest.mark.skipif(not SIDECARS, reason="needs cache/episodes/*.stage.json.gz from export.write on a sweep with "
                                         "cache/stage and cache/traffic baked")
@pytest.mark.parametrize("path", SIDECARS, ids=[p.name.split(".")[-4] for p in SIDECARS])
def test_a_cached_sidecar_never_shares_a_stand(path):
    doc = load(path)
    stage = load(STAGES / doc["hub"] / "stage.json.gz")
    stands = {s["ref"] for s in stage["stands"]}
    lo, hi = doc["span"]
    by_stand: dict[str, list[tuple[float, float, str]]] = {}
    for a in doc["aircraft"]:
        for v in a["visits"]:
            assert v["stand"] in stands
            by_stand.setdefault(v["stand"], []).append((lo if v["from"] is None else v["from"],
                                                         hi if v["to"] is None else v["to"], a["key"]))
    for ref, spans in by_stand.items():
        spans.sort()
        for (_, end, a), (start, _, b) in zip(spans, spans[1:], strict=False):
            assert end <= start or a == b, f"{ref}: {a} and {b}"
