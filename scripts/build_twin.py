"""Build the viewer's hub twin from OpenStreetMap through Overpass: runways and stopways as surfaces, taxiways with
their widths, aprons, terminals and hangars with heights, gates, stands and jet bridges, each gate and stand tagged with
its nearest terminal. ODbL 1.0 (© OpenStreetMap contributors): written under cache/twin/, never committed; index.json
records each file's sha256 and the attribution.

usage: python scripts/build_twin.py JFK BOS
"""

import argparse
import hashlib
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter"]
ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0), via Overpass"
KINDS = "runway|stopway|taxiway|taxilane|apron|terminal|gate|parking_position|hangar|jet_bridge|holding_position"
POLYGONS = {"apron", "terminal", "hangar"}
HEIGHTS = {"terminal": 20.0, "hangar": 16.0}
WIDTHS = {"runway": 45.0, "stopway": 45.0, "taxiway": 23.0, "taxilane": 15.0, "jet_bridge": 4.0}
CARRIER_TERMINAL = {"JFK": "Terminal 5", "BOS": "Terminal C"}
USER_AGENT = "agentenv-airops build_twin (github.com/earakely-scale/agentenv-airops-plugin)"
EARTH = 6378137.0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("hubs", nargs="+", help="IATA codes, e.g. JFK BOS.")
    p.add_argument("--out", type=Path, default=ROOT / "cache" / "twin")
    p.add_argument("--refresh", action="store_true", help="Query Overpass again instead of reusing cache/twin/raw/.")
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    index_path = args.out / "index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {}
    for hub in args.hubs:
        saved = args.out / "raw" / f"{hub}.overpass.json"
        if args.refresh or not saved.is_file():
            saved.parent.mkdir(exist_ok=True)
            raw, source = overpass(query(hub))
            raw["source"] = source
            saved.write_text(json.dumps(raw))
        raw = json.loads(saved.read_text())
        twin = build(hub, raw)
        body = json.dumps(twin, separators=(",", ":")).encode()
        (args.out / f"{hub}.geojson").write_bytes(body)
        counts = Counter(f["properties"]["kind"] for f in twin["features"])
        index[hub] = {"url": f"{hub}.geojson", "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                      "features": len(twin["features"]), "counts": dict(sorted(counts.items())),
                      "center": twin["center"], "osm_base": raw.get("osm3s", {}).get("timestamp_osm_base"),
                      "built": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"), "source": raw.get("source"),
                      "license": "ODbL-1.0", "attribution": ATTRIBUTION}
        print(f"{hub}: {len(twin['features'])} features {dict(counts)} -> {args.out / f'{hub}.geojson'}")
    index_path.write_text(json.dumps(index, indent=1) + "\n")
    return 0


def query(hub: str) -> str:
    return f"""[out:json][timeout:180];
nwr["aeroway"="aerodrome"]["iata"="{hub}"]->.a;
.a map_to_area->.ap;
(.a; nwr(area.ap)["aeroway"~"^({KINDS})$"]; way(area.ap)["building"~"^(terminal|hangar)$"];);
out geom;"""


def overpass(q: str) -> tuple[dict, str]:
    errors = []
    for attempt in range(2):
        for url in MIRRORS:
            request = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(),
                                             headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=100) as r:
                    doc = json.load(r)
                if doc.get("elements"):
                    return doc, url
                errors.append(f"{url}: empty ({doc.get('remark', '')})")
            except (OSError, ValueError) as e:
                errors.append(f"{url}: {e}")
            time.sleep(5 * (attempt + 1))
    raise SystemExit("Overpass failed:\n" + "\n".join(errors))


def build(hub: str, raw: dict) -> dict:
    features, terminals, aerodrome = [], [], None
    for e in raw["elements"]:
        tags = e.get("tags", {})
        kind = tags.get("aeroway") or tags.get("building")
        if kind == "aerodrome":
            aerodrome = e
            continue
        geom = geometry(e, kind)
        if geom is None:
            continue
        props = {"kind": kind, "ref": tags.get("ref", ""), "name": tags.get("name", ""),
                 "osm": f"{e['type']}/{e['id']}"}
        if kind in HEIGHTS:
            props["height"] = height(tags, kind)
        if kind in WIDTHS:
            props["width"] = number(tags.get("width")) or WIDTHS[kind]
        if kind in ("runway", "stopway") and geom["type"] == "LineString":
            geom = strip_polygon(geom["coordinates"], props["width"])
        feature = {"type": "Feature", "properties": props, "geometry": geom}
        if kind == "terminal" and geom["type"] != "Point" and props["name"]:
            terminals.append(feature)
        features.append(feature)
    for f in features:
        if f["properties"]["kind"] in ("gate", "parking_position", "jet_bridge"):
            f["properties"]["terminal"] = nearest(f, terminals)
    carrier = CARRIER_TERMINAL.get(hub)
    anchor = next((t for t in terminals if carrier and carrier.lower() in t["properties"]["name"].lower()), None)
    center = centroid(anchor) if anchor else center_of(aerodrome, features)
    features.sort(key=lambda f: ["apron", "runway", "stopway", "taxiway", "taxilane"].index(f["properties"]["kind"])
                  if f["properties"]["kind"] in ("apron", "runway", "stopway", "taxiway", "taxilane") else 9)
    return {"type": "FeatureCollection", "hub": hub, "carrier_terminal": carrier if anchor else None,
            "center": [round(center[0], 6), round(center[1], 6)], "attribution": ATTRIBUTION, "features": features}


def geometry(e: dict, kind: str) -> dict | None:
    if e["type"] == "node":
        return {"type": "Point", "coordinates": [round(e["lon"], 7), round(e["lat"], 7)]}
    if e["type"] == "way":
        coords = [[round(p["lon"], 7), round(p["lat"], 7)] for p in e.get("geometry") or [] if p]
        closed = len(coords) >= 4 and coords[0] == coords[-1]
        if kind in POLYGONS:
            return {"type": "Polygon", "coordinates": [coords]} if closed else None
        if len(coords) < 2:
            return None
        if kind in ("gate", "parking_position") and closed:
            return {"type": "Point", "coordinates": mean(coords[:-1])}
        return {"type": "LineString", "coordinates": coords}
    outers = [[[round(p["lon"], 7), round(p["lat"], 7)] for p in m["geometry"]]
              for m in e.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
    rings = [r for r in outers if len(r) >= 4 and r[0] == r[-1]]
    if kind in POLYGONS and rings:
        return {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
    return None


def height(tags: dict, kind: str) -> float:
    h = number(tags.get("height"))
    if h:
        return h
    levels = number(tags.get("building:levels"))
    return levels * 4.5 if levels else HEIGHTS[kind]


def number(value: str | None) -> float | None:
    try:
        return float(str(value).split()[0].rstrip("m")) if value else None
    except ValueError:
        return None


def strip_polygon(coords: list[list[float]], width: float) -> dict:
    """A runway centreline as one rectangle per segment, ``width`` metres wide, as the surface the view paints."""
    polys = []
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:], strict=False):
        k = math.cos(math.radians((lat1 + lat2) / 2))
        dx, dy = (lon2 - lon1) * k, lat2 - lat1
        n = math.hypot(dx, dy) or 1
        off = math.degrees(width / 2 / EARTH)
        ox, oy = -dy / n * off / k, dx / n * off
        ring = [[lon1 + ox, lat1 + oy], [lon2 + ox, lat2 + oy], [lon2 - ox, lat2 - oy], [lon1 - ox, lat1 - oy]]
        ring = [[round(x, 7), round(y, 7)] for x, y in ring]
        polys.append([ring + [ring[0]]])
    return {"type": "MultiPolygon", "coordinates": polys}


def nearest(f: dict, terminals: list[dict]) -> str:
    if not terminals:
        return ""
    x, y = point_of(f)
    k = math.cos(math.radians(y))
    best = min(terminals, key=lambda t: min(((px - x) * k) ** 2 + (py - y) ** 2 for px, py in ring_of(t)))
    return best["properties"]["name"]


def point_of(f: dict) -> list[float]:
    g = f["geometry"]
    return g["coordinates"] if g["type"] == "Point" else mean(g["coordinates"])


def ring_of(f: dict) -> list[list[float]]:
    g = f["geometry"]
    return g["coordinates"][0] if g["type"] == "Polygon" else [p for poly in g["coordinates"] for p in poly[0]]


def centroid(f: dict) -> list[float]:
    return mean(ring_of(f)[:-1])


def center_of(aerodrome: dict | None, features: list[dict]) -> list[float]:
    if aerodrome and "center" in aerodrome:
        return [aerodrome["center"]["lon"], aerodrome["center"]["lat"]]
    if aerodrome and "bounds" in aerodrome:
        b = aerodrome["bounds"]
        return [(b["minlon"] + b["maxlon"]) / 2, (b["minlat"] + b["maxlat"]) / 2]
    return mean([point_of(f) for f in features])


def mean(points: list[list[float]]) -> list[float]:
    return [sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)]


if __name__ == "__main__":
    sys.exit(main())
