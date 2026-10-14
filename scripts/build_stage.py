"""Build the 3D viewer's stage for a hub: the airfield modelled from OpenStreetMap (runways with their thresholds,
taxiways, aprons, stands with lead-in lines, jet bridges, terminals, hangars, the tower), the land and the skyline
around it, a taxi graph joining every stand to every threshold, and a USGS aerial mosaic of the land, all in local ENU
metres about the hub's reference point. ODbL 1.0 (© OpenStreetMap contributors) and USGS public domain imagery,
written under cache/stage/<HUB>/, never committed.

usage: python scripts/build_stage.py BOS JFK
"""

import argparse
import gzip
import io
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import shapely
from PIL import Image
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, nearest_points, polygonize, substring, unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_twin import ATTRIBUTION, overpass, query  # noqa: E402

HUBS = {
    "BOS": {"origin": (42.3629, -71.0064), "bounds": (-4900.0, -3500.0, 2600.0, 4000.0), "declination": -14.4,
            "terminals": (42.3656, -71.0189), "tower_height": 77.0, "near": 2500, "skyline": ((8000, 25, 8),),
            "ortho_px": 4096, "gates": re.compile(r"^([ABCE])\d")},
    "JFK": {"origin": (40.6399, -73.7787), "bounds": (-7600.0, -5200.0, 4400.0, 4800.0), "declination": -13.0,
            "terminals": (40.6430, -73.7830), "tower_height": 98.0, "near": 3000,
            "skyline": ((9000, 25, 8), (27000, 100, 30)), "ortho_px": 5632, "gates": None,
            "closed": {"New Terminal 1"}},
}
"""Per hub: the reference point (the task's airport), the stage's bounds in metres, magnetic declination to orient the
runway ends, the point the nearby buildings are taken around and how far, the skyline bands (radius from the terminals,
minimum height in metres or levels), the ortho's longer side in pixels, how a stand's terminal is read (the letter its
ref starts with, else the nearest terminal building's number), and terminal buildings not yet open."""
USGS = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
USER_AGENT = "agentenv-airops build_stage (github.com/earakely-scale/agentenv-airops-plugin)"
STAGE_ATTRIBUTION = f"{ATTRIBUTION}; imagery: USGS The National Map, USGSImageryOnly (public domain)"
WGS84_A, WGS84_E2 = 6378137.0, 6.69437999014e-3
ZOOM = 16
LEVEL_M, HALL_LEVEL_M, DEFAULT_HEIGHT = 3.5, 4.5, 10.0
WIDTHS = {"taxiway": 23.0, "taxilane": 15.0}
RANK = {"runway": 0, "taxiway": 1, "taxilane": 2, "lead": 3}
SNAP, GAP, BRIDGE, NOSE_CLEAR = 3.0, 15.0, 400.0, 9.0
GATE_REACH, DUPLICATE = 130.0, 15.0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("hubs", nargs="+", choices=sorted(HUBS))
    p.add_argument("--out", type=Path, default=ROOT / "cache" / "stage")
    p.add_argument("--twin", type=Path, default=ROOT / "cache" / "twin" / "raw")
    p.add_argument("--refresh", action="store_true", help="Query Overpass and USGS again instead of the cache.")
    args = p.parse_args(argv)
    for hub in args.hubs:
        stage_hub(hub, args.out, args.twin, args.refresh)
    return 0


def stage_hub(hub: str, root: Path, twin: Path, refresh: bool) -> None:
    cfg = HUBS[hub]
    out, raw_dir = root / hub, root / "raw"
    out.mkdir(parents=True, exist_ok=True)
    enu = Enu(*cfg["origin"])
    airfield = cached(twin / f"{hub}.overpass.json", lambda: query(hub), refresh)
    bbox = enu.bbox(cfg["bounds"])
    extras = cached(raw_dir / f"{hub}.extras.overpass.json", lambda: extras_query(bbox), refresh)
    skyline = cached(raw_dir / f"{hub}.buildings.overpass.json", lambda: buildings_query(cfg), refresh)
    stage = build(hub, cfg, enu, airfield["elements"], extras["elements"], skyline["elements"])
    ortho(enu, cfg["bounds"], cfg["ortho_px"], raw_dir / "usgs", out / "ortho.jpg", refresh)
    body = json.dumps(stage, separators=(",", ":")).encode()
    with gzip.GzipFile(out / "stage.json.gz", "wb", mtime=0) as f:
        f.write(body)
    g = stage["taxi_graph"]
    print(f"{hub}: {len(stage['runways'])} runways, {len(stage['taxiways'])} taxiways, {len(stage['aprons'])} "
          f"aprons, {len(stage['stands'])} stands, {len(stage['jet_bridges'])} jet bridges, {len(stage['terminals'])} "
          f"terminals, {len(stage['hangars'])} hangars, {len(stage['land'])} land, {len(stage['buildings'])} "
          f"buildings, taxi graph {len(g['nodes'])} nodes / {len(g['edges'])} edges -> {out / 'stage.json.gz'} "
          f"({(out / 'stage.json.gz').stat().st_size} B), ortho {(out / 'ortho.jpg').stat().st_size} B")


class Enu:
    """Equirectangular local metres on WGS84 about (lat0, lon0): x east, y north."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        s = math.sin(math.radians(lat0))
        w = 1 - WGS84_E2 * s * s
        self.my = math.radians(1) * WGS84_A * (1 - WGS84_E2) / w**1.5
        self.mx = math.radians(1) * WGS84_A / math.sqrt(w) * math.cos(math.radians(lat0))

    def xy(self, lon, lat):
        return (lon - self.lon0) * self.mx, (lat - self.lat0) * self.my

    def lonlat(self, x, y):
        return self.lon0 + x / self.mx, self.lat0 + y / self.my

    def bbox(self, bounds: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        w, s, e, n = bounds
        (lon_w, lat_s), (lon_e, lat_n) = self.lonlat(w, s), self.lonlat(e, n)
        return lat_s, lon_w, lat_n, lon_e


def cached(path: Path, q, refresh: bool) -> dict:
    if refresh or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        raw, source = overpass(q())
        raw["source"] = source
        path.write_text(json.dumps(raw))
    return json.loads(path.read_text())


def extras_query(bbox: tuple[float, float, float, float]) -> str:
    b = ",".join(f"{v:.5f}" for v in bbox)
    return f"""[out:json][timeout:180];
(way["natural"="coastline"]({b}); way["natural"="water"]({b}); relation["natural"="water"]({b});
 way["waterway"="riverbank"]({b}); nwr["aeroway"~"^(control_tower|tower)$"]({b}); nwr["man_made"="tower"]({b}););
out geom;"""


def buildings_query(cfg: dict) -> str:
    lat, lon = cfg["origin"]
    tlat, tlon = cfg["terminals"]
    near = f"around:{cfg['near']},{tlat},{tlon}"
    bands = []
    for radius, tall, levels in cfg["skyline"]:
        far = f"around:{radius},{lat},{lon}"
        h, lv = f'["height"~"{at_least(tall)}"]', f'["building:levels"~"{at_least(levels)}$"]'
        bands += [f'way["building"]{h}({far});', f'way["building"]{lv}({far});', f'relation["building"]{h}({far});',
                  f'relation["building"]{lv}({far});']
    return f"""[out:json][timeout:600];
({" ".join(bands)} way["building"]({near}); relation["building"]({near}););
out geom;"""


def at_least(n: int) -> str:
    """A regex prefix matching the decimal integers from n to 999."""
    s, alts = str(n), []
    for i, ch in enumerate(s):
        if i == len(s) - 1:
            alts.append(s[:i] + f"[{ch}-9]")
        elif ch != "9":
            alts.append(s[:i] + f"[{int(ch) + 1}-9]" + "[0-9]" * (len(s) - i - 1))
    alts += ["[1-9]" + "[0-9]" * (k - 1) for k in range(len(s) + 1, 4)]
    return f"^({'|'.join(alts)})"


def build(hub: str, cfg: dict, enu: Enu, airfield: list[dict], extras: list[dict], skyline: list[dict]) -> dict:
    frame = shapely.box(*cfg["bounds"])
    proj = Projector(enu)
    runways = runways_of(airfield, proj, cfg["declination"])
    taxiways = [{"ref": t.get("ref", ""), "kind": t["aeroway"], "width": number(t.get("width")) or WIDTHS[t["aeroway"]],
                 "line": pts(proj.line(e))} for e, t in tagged(airfield) if t.get("aeroway") in WIDTHS
                and len(proj.line(e)) >= 2]
    terminal_polys = [(e, t, proj.polygon(e)) for e, t in tagged(airfield) if t.get("aeroway") == "terminal"]
    used = {(e["type"], e["id"]) for e, _, _ in terminal_polys}
    terminal_polys = [(e, t, poly) for e, t, poly in terminal_polys
                      if not poly.is_empty and t.get("name") not in cfg.get("closed", ())]
    terminals = distinct([dict(name=t.get("name", ""), **r, height=height(t, 20.0, HALL_LEVEL_M))
                          for _, t, poly in terminal_polys for r in rings(poly, 0.2)])
    hangars = []
    for e, t in tagged(airfield):
        if t.get("aeroway") == "hangar" or t.get("building") == "hangar":
            used.add((e["type"], e["id"]))
            hangars += [dict(**r, height=height(t, 16.0, HALL_LEVEL_M)) for r in rings(proj.polygon(e), 0.2)]
    aprons = [r for e, t in tagged(airfield) if t.get("aeroway") == "apron" for r in rings(proj.polygon(e), 0.2)]
    built = unary_union([poly for _, _, poly in terminal_polys])
    network = [LineString(t["line"]) for t in taxiways]
    stands = stands_of(airfield, proj, unary_union(network), built,
                       gate_namer(cfg["gates"], [(t.get("name", ""), poly) for _, t, poly in terminal_polys]))
    lines = [("runway", r["centreline"]) for r in runways] + [(t["kind"], t["line"]) for t in taxiways]
    thresholds = {end["id"]: end["xy"] for r in runways for end in r["ends"]}
    graph, leads = taxi_graph(lines, [(s["ref"], s["lead"]) for s in stands], thresholds)
    for s in stands:
        s["lead"] = pts(leads[s["ref"]])
    stand_xy = {s["ref"]: s["xy"] for s in stands if s["ref"] in graph["stand_nodes"]}
    stands = [s for s in stands if s["ref"] in stand_xy]
    bridges = []
    for e, t in tagged(airfield):
        if t.get("aeroway") == "jet_bridge" and len(line := proj.line(e)) >= 2:
            tip = max((line[0], line[-1]), key=lambda q: built.distance(Point(q)))
            ref, d = min(((r, math.dist(tip, xy)) for r, xy in stand_xy.items()), key=lambda v: v[1])
            bridges.append({"line": pts(line), "stand": ref if d <= 25 else None})
    land = land_of(extras, proj, frame)
    tower, key = tower_of(extras + airfield, proj, built, cfg["tower_height"])
    buildings = buildings_of(skyline, proj, used | {key}, built, cfg["near"], cfg["skyline"], Point(tower["xy"]))
    for r in runways:
        r.pop("centreline")
    return {"hub": hub, "origin": list(cfg["origin"]), "bounds": list(cfg["bounds"]), "runways": runways,
            "taxiways": taxiways, "aprons": aprons, "stands": stands, "jet_bridges": bridges, "terminals": terminals,
            "hangars": hangars, "land": land, "buildings": buildings, "tower": tower, "taxi_graph": graph,
            "ortho": {"url": "ortho.jpg", "bounds": list(cfg["bounds"])}, "attribution": STAGE_ATTRIBUTION}


def distinct(parts: list[dict], share: float = 0.9) -> list[dict]:
    """The rings in order, less any lying mostly on one before it (a building drawn both as a way and as a
    multipolygon)."""
    kept: list[tuple[dict, Polygon]] = []
    for r in parts:
        poly = Polygon(r["outer"], r["holes"])
        if not any(poly.intersection(q).area >= share * min(poly.area, q.area) for _, q in kept):
            kept.append((r, poly))
    return [r for r, _ in kept]


class Projector:
    def __init__(self, enu: Enu):
        self.enu = enu

    def point(self, p: dict) -> tuple[float, float]:
        return self.enu.xy(p["lon"], p["lat"])

    def line(self, e: dict) -> list[tuple[float, float]]:
        if e["type"] == "node":
            return [self.point(e)]
        return [self.point(p) for p in e.get("geometry") or [] if p]

    def polygon(self, e: dict):
        if e["type"] == "way":
            c = self.line(e)
            return Polygon(c).buffer(0) if len(c) >= 4 and c[0] == c[-1] else Polygon()
        if e["type"] != "relation":
            return Polygon()
        parts = {"outer": [], "inner": []}
        for m in e.get("members", []):
            if m.get("type") == "way" and m.get("geometry") and m.get("role") in ("outer", "inner", ""):
                c = [self.point(p) for p in m["geometry"] if p]
                if len(c) >= 2:
                    parts["inner" if m["role"] == "inner" else "outer"].append(LineString(c))
        outer = faces(parts["outer"])
        return (outer.difference(faces(parts["inner"])) if parts["inner"] else outer).buffer(0)


def faces(lines: list[LineString]):
    noded = unary_union(lines)
    return unary_union(list(polygonize(getattr(noded, "geoms", [noded])))) if lines else Polygon()


def tagged(elements: list[dict]):
    for e in elements:
        yield e, e.get("tags", {})


def runways_of(elements: list[dict], proj: Projector, declination: float) -> list[dict]:
    ways = [(e, t, proj.line(e)) for e, t in tagged(elements) if t.get("aeroway") == "runway" and e["type"] == "way"]
    mains = [(t, c) for _, t, c in ways if t.get("runway") != "displaced_threshold" and t.get("ref")]
    named = {t["ref"] for t, _ in mains}
    for ref in sorted({t["ref"] for _, t, _ in ways if t.get("ref")} - named):
        mains.append(max(((t, c) for _, t, c in ways if t.get("ref") == ref), key=lambda v: LineString(v[1]).length))
    out = []
    for t, c in mains:
        parts, ends = [c], {c[0], c[-1]}
        for _, t2, c2 in ways:
            if c2 is not c and t2.get("runway") == "displaced_threshold" and (t2.get("ref") == t["ref"] or
                                                              (not t2.get("ref") and ends & {c2[0], c2[-1]})):
                parts.append(c2)
        centre = linemerge(MultiLineString(parts))
        if centre.geom_type != "LineString":
            centre = LineString(c)
        width = number(t.get("width")) or 45.0
        ids = [re.sub(r"^0+(?=\d)", "", v) for v in t["ref"].split("/")]
        a, b = c[0], c[-1]
        ha, hb = bearing(a, b), bearing(b, a)
        expect = (int(re.match(r"\d+", ids[0]).group()) * 10 + declination) % 360
        if angle(ha, expect) > angle(hb, expect):
            a, b, ha, hb = b, a, hb, ha
        poly = centre.buffer(width / 2, cap_style="flat", join_style="mitre")
        out.append({"ref": "/".join(ids), "width": width,
                    "ends": [{"id": ids[0], "xy": pt(a), "heading": round(ha, 1)},
                             {"id": ids[1], "xy": pt(b), "heading": round(hb, 1)}],
                    "polygon": rings(poly, 0.2)[0]["outer"], "centreline": pts(centre.coords)})
    return sorted(out, key=lambda r: r["ref"])


def stands_of(elements: list[dict], proj: Projector, network, built, name) -> list[dict]:
    """Each stand once: its lead-in oriented to end at the nose-wheel stop, the end away from the taxi network,
    pulled back to clear the terminal; a stand drawn as a point leads in square from the nearest taxi line, unless a
    stand drawn as a line stops within DUPLICATE metres of it. ``name`` gives a raw ref and its point the stand's ref
    and terminal."""
    best: dict[str, list] = {}
    drawn = [Point(proj.line(e)[-1]) for e, t in tagged(elements) if t.get("aeroway") == "parking_position"
             and e["type"] == "way" and len(proj.line(e)) >= 2]
    lines = shapely.STRtree(drawn)
    for e, t in tagged(elements):
        if t.get("aeroway") != "parking_position" or not (raw := stand_ref(t.get("ref") or t.get("name") or "")):
            continue
        line = dedupe(proj.line(e))
        if len(line) < 2 and len(lines.query(Point(line[0]), predicate="dwithin", distance=DUPLICATE)):
            continue
        if not (named := name(raw, line[-1])):
            continue
        ref, terminal = named
        if len(line) >= 2:
            d0, d1 = network.distance(Point(line[0])), network.distance(Point(line[-1]))
            if d1 < d0 - 1 or (abs(d0 - d1) <= 1 and built.distance(Point(line[0])) < built.distance(Point(line[-1]))):
                line = line[::-1]
            line = clear_of(line, built)
        else:
            q = nearest_points(network, Point(line[0]))[0]
            line = [(q.x, q.y), line[0]]
        length = LineString(line).length
        if ref not in best or length > best[ref][0]:
            best[ref] = [length, line, terminal]
    out = []
    for ref, (_, line, terminal) in sorted(best.items(), key=lambda kv: natural(kv[0])):
        out.append({"ref": ref, "terminal": terminal, "xy": pt(line[-1]),
                    "heading": round(bearing(line[-2], line[-1]), 1), "lead": line})
    return out


def gate_namer(gates: re.Pattern | None, terminals: list[tuple[str, Polygon]]):
    """A stand's terminal is the letter its ref starts with when ``gates`` matches, else the number of the terminal
    building within GATE_REACH metres, its ref then prefixed (T5-12; JFK's Terminal 5 gates are 501..530 in OSM)."""

    def lettered(raw: str, xy) -> tuple[str, str]:
        m = gates.match(raw)
        return raw, m.group(1) if m else "remote"

    def numbered(raw: str, xy) -> tuple[str, str]:
        p = Point(xy)
        found = [(poly.distance(p), m.group(1)) for n, poly in terminals if (m := re.search(r"(\d+)$", n))]
        d, number = min(found, default=(math.inf, ""))
        if d > GATE_REACH:
            return raw, "remote"
        if number == "5" and (m := re.fullmatch(r"5(\d\d)([A-Z]*)", raw)):
            raw = f"{int(m.group(1))}{m.group(2)}"
        return f"T{number}-{raw}", f"T{number}"

    return lettered if gates else numbered


def stand_ref(raw: str) -> str:
    raw = re.sub(r"^Hardstand\s*", "H", raw.strip())
    return raw.split()[0].split("/")[0] if raw else ""


def clear_of(line: list, built) -> list:
    ls = LineString(line)
    stop = ls.length
    while stop > 0 and built.distance(ls.interpolate(stop)) < NOSE_CLEAR:
        stop -= 0.5
    if stop >= ls.length or stop < ls.length / 3:
        return line
    cut = substring(ls, 0, stop)
    return list(cut.coords)


def taxi_graph(lines: list[tuple[str, list]], stands: list[tuple[str, list]], thresholds: dict[str, list],
               snap: float = SNAP) -> tuple[dict, dict[str, list]]:
    """Runway centrelines and taxi lines noded at every crossing, vertices within `snap` merged; each lead-in joins
    where it first meets a taxi line (else at the nearest one) and stray components are bridged to the runways' or
    dropped."""
    g = Graph(snap)
    sources = [(kind, LineString(c)) for kind, c in lines if len(c) >= 2]
    tree = shapely.STRtree([s for _, s in sources])
    noded = unary_union([s for _, s in sources])
    for piece in getattr(noded, "geoms", [noded]):
        c = list(piece.coords)
        probe = LineString(c[:2]).interpolate(0.5, normalized=True)
        kind = min((sources[i][0] for i in tree.query(probe, predicate="dwithin", distance=0.05)),
                   key=RANK.get, default="taxilane")
        g.polyline(c, kind)
    for n in [i for i, d in enumerate(g.degree()) if d == 1]:
        kinds = set(g.kinds_at(n))
        if kinds <= {"taxiway", "taxilane"}:
            g.attach(g.nodes[n], {"taxiway", "taxilane"}, GAP, exclude=g.near(n, 3 * GAP), join=n,
                     kind=min(kinds, key=RANK.get))
    thr = {k: g.attach(xy, {"runway"}, snap * 4) for k, xy in thresholds.items()}
    taxi = unary_union([s for k, s in sources if k != "runway"])
    stand_nodes, leads = {}, {}
    for ref, lead in stands:
        ls = LineString(lead)
        cross = ls.intersection(taxi)
        hits = [Point(q) for part in getattr(cross, "geoms", [cross]) if not part.is_empty for q in part.coords]
        if hits:
            at = max(ls.project(p) for p in hits)
            lead = list(substring(ls, at, ls.length).coords) if at < ls.length - 1 else lead
        start = g.attach(lead[0], {"taxiway", "taxilane"}, BRIDGE)
        path = [g.nodes[start]] + [q for q in lead if math.dist(q, g.nodes[start]) > 0.5]
        prev = start
        for q in path[1:]:
            cur = g.add(q)
            g.edge(prev, cur, "lead")
            prev = cur
        stand_nodes[ref], leads[ref] = prev, path
    main = g.connect(set(thr.values()), set(stand_nodes.values()))
    remap = g.compact(main)
    return {"nodes": [pt(p) for p in g.nodes], "edges": [[a, b, round(length, 1), kind] for (a, b), (length, kind)
                                                         in sorted(g.edges.items())],
            "stand_nodes": {r: remap[n] for r, n in stand_nodes.items() if n in remap},
            "thresholds": {k: remap[n] for k, n in thr.items() if n in remap}}, leads


class Graph:
    def __init__(self, snap: float):
        self.snap = snap
        self.nodes: list[tuple[float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}
        self.edges: dict[tuple[int, int], tuple[float, str]] = {}

    def node(self, p) -> int:
        cx, cy = int(p[0] // self.snap), int(p[1] // self.snap)
        near = [(math.dist(self.nodes[i], p), i) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for i in self.grid.get((cx + dx, cy + dy), ())]
        near = [v for v in near if v[0] <= self.snap]
        return min(near)[1] if near else self.add(p)

    def add(self, p) -> int:
        self.nodes.append((float(p[0]), float(p[1])))
        self.grid.setdefault((int(p[0] // self.snap), int(p[1] // self.snap)), []).append(len(self.nodes) - 1)
        return len(self.nodes) - 1

    def edge(self, a: int, b: int, kind: str) -> None:
        if a == b:
            return
        key = (min(a, b), max(a, b))
        old = self.edges.get(key)
        if old is None or RANK[kind] < RANK[old[1]]:
            self.edges[key] = (math.dist(self.nodes[a], self.nodes[b]), kind)

    def polyline(self, coords: list, kind: str) -> None:
        ids = [self.node(p) for p in coords]
        for a, b in zip(ids, ids[1:], strict=False):
            self.edge(a, b, kind)

    def degree(self) -> list[int]:
        deg = [0] * len(self.nodes)
        for a, b in self.edges:
            deg[a] += 1
            deg[b] += 1
        return deg

    def kinds_at(self, n: int) -> list[str]:
        return [k for (a, b), (_, k) in self.edges.items() if n in (a, b)]

    def near(self, n: int, reach: float) -> set[int]:
        """The nodes within `reach` metres of `n` along the graph."""
        adj: dict[int, list[tuple[int, float]]] = {}
        for (a, b), (length, _) in self.edges.items():
            adj.setdefault(a, []).append((b, length))
            adj.setdefault(b, []).append((a, length))
        dist, todo = {n: 0.0}, [n]
        while todo:
            u = todo.pop()
            for v, length in adj.get(u, ()):
                if dist[u] + length <= reach and dist[u] + length < dist.get(v, math.inf):
                    dist[v] = dist[u] + length
                    todo.append(v)
        return set(dist)

    def attach(self, p, kinds: set[str], reach: float, exclude: set[int] = frozenset(), join: int | None = None,
               kind: str = "lead") -> int:
        """The node at `p` on an edge of `kinds` within `reach` metres, splitting the edge there; with `join`, an edge
        of `kind` from that node to the existing node `join`, or `join` itself as the split when within `snap`."""
        keys = [k for k, (_, kd) in self.edges.items() if kd in kinds and not (exclude & set(k))]
        if not keys:
            return self.node(p) if join is None else join
        a = np.array([self.nodes[k[0]] for k in keys])
        b = np.array([self.nodes[k[1]] for k in keys])
        q = np.asarray(p, dtype=float)
        ab = b - a
        t = np.clip(((q - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
        foot = a + ab * t[:, None]
        d = np.hypot(*(foot - q).T)
        i = int(d.argmin())
        if d[i] > reach:
            return self.node(p) if join is None else join
        (u, v), (_, kd) = keys[i], self.edges[keys[i]]
        f = tuple(foot[i])
        if math.dist(f, self.nodes[u]) <= self.snap:
            n = u
        elif math.dist(f, self.nodes[v]) <= self.snap:
            n = v
        else:
            n = join if join is not None and d[i] <= self.snap else self.add(f)
            del self.edges[keys[i]]
            self.edge(u, n, kd)
            self.edge(n, v, kd)
        if join is not None:
            self.edge(join, n, kind)
        return n

    def components(self) -> list[int]:
        parent = list(range(len(self.nodes)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a, b in self.edges:
            parent[find(a)] = find(b)
        return [find(i) for i in range(len(self.nodes))]

    def connect(self, thresholds: set[int], stands: set[int]) -> set[int]:
        """Bridges every component holding a stand or threshold to the one holding the most thresholds at their closest
        taxi-line nodes, and returns the nodes of the joined component."""
        while True:
            comp = self.components()
            groups: dict[int, list[int]] = {}
            for i, c in enumerate(comp):
                groups.setdefault(c, []).append(i)
            main = max(groups, key=lambda c: (sum(comp[n] == c for n in thresholds), sum(comp[n] == c for n in stands),
                                              len(groups[c])))
            loose = [c for c in groups if c != main and any(comp[n] == c for n in thresholds | stands)]
            if not loose:
                return set(groups[main])
            taxi = {n for (a, b), (_, kind) in self.edges.items() if kind != "lead" for n in (a, b)}
            ends = {c: [n for n in groups[c] if n in taxi] or groups[c] for c in [main, *loose]}
            m = np.array([self.nodes[i] for i in ends[main]])
            joined = False
            for c in loose:
                pts_c = np.array([self.nodes[i] for i in ends[c]])
                d = np.hypot(pts_c[:, None, 0] - m[None, :, 0], pts_c[:, None, 1] - m[None, :, 1])
                i, j = np.unravel_index(int(d.argmin()), d.shape)
                if d[i, j] <= BRIDGE:
                    self.edge(ends[c][i], ends[main][j], "taxilane")
                    joined = True
            if not joined:
                return set(groups[main])

    def compact(self, keep: set[int]) -> dict[int, int]:
        order = sorted(keep)
        remap = {old: new for new, old in enumerate(order)}
        self.nodes = [self.nodes[i] for i in order]
        self.edges = {(remap[a], remap[b]): v for (a, b), v in self.edges.items() if a in remap and b in remap}
        return remap


def land_of(elements: list[dict], proj: Projector, frame) -> list[dict]:
    """Faces of the coastline and the frame, each land or water by which side of its coastline edges it lies on (OSM
    draws land on the left), minus inland water."""
    coast = [LineString(c) for e, t in tagged(elements) if t.get("natural") == "coastline" and e["type"] == "way"
             and len(c := proj.line(e)) >= 2]
    merged = unary_union([*coast, frame.exterior])
    faces = [f for f in polygonize(getattr(merged, "geoms", [merged])) if frame.contains(f.representative_point())]
    a = np.array([q for ln in coast for q in ln.coords[:-1]])
    b = np.array([q for ln in coast for q in ln.coords[1:]])
    d = b - a
    length = np.hypot(*d.T)
    a, d, length = a[length > 1], d[length > 1], length[length > 1]
    mid, left = a + d / 2, np.stack([-d[:, 1], d[:, 0]], 1) / length[:, None] * 0.3
    tree = shapely.STRtree(faces)
    votes = np.zeros(len(faces))
    for side, sign in ((mid + left, 1), (mid - left, -1)):
        _, hit = tree.query(shapely.points(side), predicate="within")
        np.add.at(votes, hit, sign)
    shape = unary_union([f for f, v in zip(faces, votes, strict=True) if v > 0])
    water = [poly for e, t in tagged(elements) if t.get("natural") == "water" or t.get("waterway") == "riverbank"
             for poly in [proj.polygon(e)] if not poly.is_empty and poly.area > 2000]
    if water:
        shape = shape.difference(unary_union(water))
    return rings(shape.intersection(frame), 1.0, min_area=500)


def tower_of(elements: list[dict], proj: Projector, built, default_height: float) -> tuple[dict, tuple]:
    found = []
    for e, t in tagged(elements):
        kind = t.get("aeroway") in ("control_tower", "tower") or (t.get("man_made") == "tower" and (
            re.search(r"control|airport|atc", t.get("tower:type", "") + t.get("name", ""), re.I)))
        if kind:
            c = proj.line(e)
            p = Polygon(c).centroid if len(c) >= 4 else Point(np.mean(c, axis=0))
            found.append((built.distance(p), p, height(t, default_height), (e["type"], e["id"])))
    _, p, h, key = min(found, key=lambda v: v[0])
    return {"xy": pt((p.x, p.y)), "height": round(h, 1)}, key


def buildings_of(elements: list[dict], proj: Projector, used: set, built, near: float, bands, tower) -> list[dict]:
    """Every building within ``near`` metres of the terminals, and beyond it those as tall as the first skyline band
    reaching them asks, in metres or levels; not the tower's own footprint, which the stage models."""
    out, seen = [], set()
    centre = built.centroid
    for e, t in tagged(elements):
        if (e["type"], e["id"]) in used or (e["type"], e["id"]) in seen or "building" not in t:
            continue
        seen.add((e["type"], e["id"]))
        poly = proj.polygon(e)
        if poly.is_empty or poly.area < 20 or poly.contains(tower):
            continue
        h = height(t, DEFAULT_HEIGHT)
        d = centre.distance(poly)
        tall, levels = next(((h0, lv) for r, h0, lv in bands if d <= r), (math.inf, math.inf))
        if d > near and h < tall and (number(t.get("building:levels")) or 0) < levels:
            continue
        for r in rings(poly, 0.5):
            out.append({"outer": r["outer"], "height": round(h, 1)})
    return out


def height(t: dict, default: float, level: float = LEVEL_M) -> float:
    h = number(t.get("height"))
    if h:
        return h
    levels = number(t.get("building:levels"))
    return levels * level + 2 if levels else default


def number(value: str | None) -> float | None:
    try:
        return float(str(value).split()[0].rstrip("m").replace(",", ".")) if value else None
    except ValueError:
        return None


def rings(shape, tol: float, min_area: float = 1.0) -> list[dict]:
    if shape.is_empty:
        return []
    out = []
    for poly in getattr(shape, "geoms", [shape]):
        if poly.geom_type != "Polygon" or poly.area < min_area:
            continue
        poly = orient(poly.simplify(tol, preserve_topology=True), 1.0)
        if poly.is_empty:
            continue
        holes = [pts(h.coords[:-1]) for h in poly.interiors if Polygon(h).area >= min_area]
        out.append({"outer": pts(poly.exterior.coords[:-1]), "holes": holes})
    return out


def ortho(enu: Enu, bounds: tuple, px: int, tiles: Path, path: Path, refresh: bool) -> None:
    """The USGS mosaic at ZOOM resampled bilinearly onto the ENU grid of `bounds`, north up, `px` pixels along its
    longer side."""
    w, s, e, n = bounds
    width, rows_px = (px, round(px * (n - s) / (e - w))) if e - w >= n - s else (round(px * (e - w) / (n - s)), px)
    xs = w + (np.arange(width) + 0.5) * (e - w) / width
    ys = n - (np.arange(rows_px) + 0.5) * (n - s) / rows_px
    lon, _ = enu.lonlat(xs, 0)
    _, lat = enu.lonlat(0, ys)
    scale = 256 * 2**ZOOM
    px = (lon + 180) / 360 * scale
    lat_r = np.radians(lat)
    py = (1 - np.log(np.tan(lat_r) + 1 / np.cos(lat_r)) / math.pi) / 2 * scale
    tx0, tx1 = int(px.min() // 256), int(px.max() // 256)
    ty0, ty1 = int(py.min() // 256), int(py.max() // 256)
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256, 3), dtype=np.uint8)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            img = Image.open(io.BytesIO(tile(tiles, ZOOM, tx, ty, refresh))).convert("RGB")
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = np.asarray(img)
    fx, fy = px - tx0 * 256 - 0.5, py - ty0 * 256 - 0.5
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    ax, ay = (fx - x0)[None, :, None].astype(np.float32), (fy - y0)[:, None, None].astype(np.float32)
    x0, y0 = np.clip(x0, 0, mosaic.shape[1] - 2), np.clip(y0, 0, mosaic.shape[0] - 2)
    out = np.empty((rows_px, width, 3), dtype=np.uint8)
    for r in range(0, rows_px, 256):
        rows = slice(r, r + 256)
        m0, m1 = mosaic[y0[rows]].astype(np.float32), mosaic[y0[rows] + 1].astype(np.float32)
        top = m0[:, x0] * (1 - ax) + m0[:, x0 + 1] * ax
        bottom = m1[:, x0] * (1 - ax) + m1[:, x0 + 1] * ax
        out[rows] = (top * (1 - ay[rows]) + bottom * ay[rows]).round().clip(0, 255).astype(np.uint8)
    Image.fromarray(out).save(path, quality=85, optimize=True)


def tile(root: Path, z: int, x: int, y: int, refresh: bool) -> bytes:
    path = root / str(z) / str(x) / f"{y}.jpg"
    if refresh or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(4):
            try:
                req = urllib.request.Request(USGS.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r:
                    path.write_bytes(r.read())
                break
            except OSError:
                time.sleep(2 * (attempt + 1))
        else:
            raise SystemExit(f"USGS tile {z}/{x}/{y} failed")
        time.sleep(0.05)
    return path.read_bytes()


def bearing(a, b) -> float:
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def angle(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def dedupe(c: list) -> list:
    return [p for i, p in enumerate(c) if i == 0 or math.dist(p, c[i - 1]) > 0.05]


def natural(ref: str) -> list:
    return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", ref)]


def pt(p) -> list[float]:
    return [round(float(p[0]), 1), round(float(p[1]), 1)]


def pts(c) -> list[list[float]]:
    return [pt(p) for p in c]


if __name__ == "__main__":
    sys.exit(main())
