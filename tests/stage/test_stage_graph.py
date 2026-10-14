import gzip
import heapq
import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "cache" / "stage" / "BOS" / "stage.json.gz"
MOMENT = ROOT / "cache" / "stage" / "BOS" / "moment-bos-20250216.json"
needs_stage = pytest.mark.skipif(not STAGE.is_file(), reason="needs cache/stage/BOS from scripts/build_stage.py BOS")
needs_moment = pytest.mark.skipif(not (STAGE.is_file() and MOMENT.is_file()),
                                  reason="needs cache/stage/BOS from scripts/build_stage.py and stage_moment.py")


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


@needs_stage
def test_bos_every_stand_reaches_every_threshold():
    stage = json.loads(gzip.decompress(STAGE.read_bytes()))
    graph = stage["taxi_graph"]
    assert {s["ref"] for s in stage["stands"]} == set(graph["stand_nodes"])
    assert {e["id"] for r in stage["runways"] for e in r["ends"]} == set(graph["thresholds"])
    assert_stands_reach_thresholds(graph)


@needs_moment
def test_bos_moment_never_shares_a_stand():
    stage = json.loads(gzip.decompress(STAGE.read_bytes()))
    moment = json.loads(MOMENT.read_text())
    stands = {s["ref"] for s in stage["stands"]}
    w0, w1 = moment["window"]
    by_stand: dict[str, list[tuple[float, float, str]]] = {}
    for a in moment["aircraft"]:
        assert a["stand"] in stands
        for start, end in parked(a["legs"]):
            if start < w1 and end > w0:
                by_stand.setdefault(a["stand"], []).append((max(start, w0), min(end, w1), a["key"]))
    for ref, spans in by_stand.items():
        spans.sort()
        for (_, end, a), (start, _, b) in zip(spans, spans[1:], strict=False):
            assert end <= start, f"{ref}: {a} and {b}"


def parked(legs: list[dict]) -> list[tuple[float, float]]:
    """At the stand before the first pushback, from each gate arrival to the next pushback, and after the last."""
    spans, since = [], -math.inf
    for leg in (g for g in legs if g["status"] == "flown"):
        if leg["kind"] == "dep":
            if since is not None:
                spans.append((since, leg["out"]))
            since = None
        else:
            since = leg["in"]
    if since is not None:
        spans.append((since, math.inf))
    return spans
