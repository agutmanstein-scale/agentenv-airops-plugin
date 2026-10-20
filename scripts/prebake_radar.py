"""Pre-bake the viewer's radar: IEM NEXRAD n0q composites (public domain) for a task's local time range, cropped to a
box around its airports, resampled to Web Mercator rows so the viewer can stretch each frame linearly between the box's
corners, with weak echoes cleared. Writes PNG frames and index.json under cache/radar/<task_id>/.

usage: uv run --no-project --with pillow --with numpy python scripts/prebake_radar.py --task EPISODE_OR_TASK_JSON
"""

import argparse
import hashlib
import http.client
import io
import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "https://mesonet.agron.iastate.edu/archive/data/{d:%Y/%m/%d}/GIS/uscomp/n0q_{d:%Y%m%d%H%M}.png"
ATTRIBUTION = "NEXRAD n0q composite: Iowa Environmental Mesonet (public domain)"
EXTENT = (-126.0, 23.0, -65.0, 50.0)
PIXEL = 0.005
REGIONS = {"northeast": (-82.0, 36.5, -66.0, 47.5), "conus": (-125.0, 24.0, -66.0, 50.0)}
MIN_DBZ = 10.0
USER_AGENT = "agentenv-airops prebake_radar (github.com/earakely-scale/agentenv-airops-plugin)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--task", type=Path, required=True, help="An episode JSON (its 'task') or a task JSON line.")
    p.add_argument("--region", default="airports", help="airports, northeast, conus, or w,s,e,n in degrees.")
    p.add_argument("--from", dest="t0", type=int, help="First local minute; default window start - 120.")
    p.add_argument("--to", dest="t1", type=int, help="Last local minute; default window end + 300.")
    p.add_argument("--step", type=int, default=10, help="Minutes between frames (a multiple of 5).")
    p.add_argument("--width", type=int, default=2400, help="Frame width in pixels.")
    p.add_argument("--raw-dir", type=Path, help="Look here for n0q_YYYYMMDDHHMM.png before downloading.")
    p.add_argument("--out", type=Path, help="Default: cache/radar/<task_id>/.")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args(argv)

    task = load_task(args.task)
    bounds = region(args.region, task)
    t0 = args.t0 if args.t0 is not None else task["window"][0] - 120
    t1 = args.t1 if args.t1 is not None else task["window"][1] + 300
    out = args.out or ROOT / "cache" / "radar" / task["task_id"]
    out.mkdir(parents=True, exist_ok=True)
    times = list(range(t0 - t0 % args.step, t1 + 1, args.step))
    stamps = [utc_at(task, t) for t in times]
    rows, cols, height = grid(bounds, args.width)

    def bake(i: int) -> dict | None:
        name = f"n0q_{stamps[i]:%Y%m%d%H%M}.png"
        body = raw(stamps[i], args.raw_dir)
        if body is None:
            print(f"missing {name}", file=sys.stderr)
            return None
        png = render(body, rows, cols)
        (out / name).write_bytes(png)
        return {"t": times[i], "utc": f"{stamps[i]:%Y-%m-%dT%H:%MZ}", "url": name,
                "sha256": hashlib.sha256(png).hexdigest()}

    with ThreadPoolExecutor(args.workers) as pool:
        frames = [f for f in pool.map(bake, range(len(times))) if f]
    index = {"task_id": task["task_id"], "bounds": list(bounds), "projection": "mercator", "size": [args.width, height],
             "step": args.step, "min_dbz": MIN_DBZ, "frames": frames, "source": ARCHIVE.split("{")[0],
             "attribution": ATTRIBUTION}
    (out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    total = sum((out / f["url"]).stat().st_size for f in frames)
    print(f"{len(frames)}/{len(times)} frames {args.width}x{height} -> {out} ({total / 1e6:.1f} MB)")
    return 0 if frames else 1


def load_task(path: Path) -> dict:
    doc = json.loads(path.read_text().splitlines()[0] if path.suffix == ".jsonl" else path.read_text())
    return doc.get("task", doc)


def region(name: str, task: dict) -> tuple[float, float, float, float]:
    if name in REGIONS:
        return REGIONS[name]
    if name != "airports":
        w, s, e, n = (float(v) for v in name.split(","))
        return clamp((w, s, e, n))
    lons = [a["lon"] for a in task["airports"].values()]
    lats = [a["lat"] for a in task["airports"].values()]
    return clamp((min(lons) - 2, min(lats) - 1.5, max(lons) + 2, max(lats) + 1.5))


def clamp(b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    w, s, e, n = b
    return max(w, EXTENT[0]), max(s, EXTENT[1]), min(e, EXTENT[2]), min(n, EXTENT[3])


def utc_at(task: dict, minute: int) -> datetime:
    day = datetime.fromisoformat(task["date"]).replace(tzinfo=ZoneInfo(task["tz"]))
    return (day + timedelta(minutes=minute)).astimezone(UTC)


def merc(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def grid(bounds, width: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Source rows and columns of the n0q mosaic for each output pixel: columns linear in longitude, rows linear in
    Mercator y, so the frame is a plain image between the corners on a Web Mercator map."""
    w, s, e, n = bounds
    height = round(width * (merc(n) - merc(s)) / math.radians(e - w))
    ys = merc(n) - (np.arange(height) + 0.5) / height * (merc(n) - merc(s))
    lats = np.degrees(2 * np.arctan(np.exp(ys)) - np.pi / 2)
    lons = w + (np.arange(width) + 0.5) / width * (e - w)
    rows = np.floor((EXTENT[3] - lats) / PIXEL).astype(int)
    cols = np.floor((lons - EXTENT[0]) / PIXEL).astype(int)
    return rows, cols, height


def raw(stamp: datetime, raw_dir: Path | None) -> bytes | None:
    name = f"n0q_{stamp:%Y%m%d%H%M}.png"
    if raw_dir and (raw_dir / name).is_file():
        return (raw_dir / name).read_bytes()
    request = urllib.request.Request(ARCHIVE.format(d=stamp), headers={"User-Agent": USER_AGENT})
    for _ in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as r:
                return r.read()
        except (OSError, http.client.HTTPException):
            continue
    return None


def render(body: bytes, rows: np.ndarray, cols: np.ndarray) -> bytes:
    """IEM's palette kept; index 0 (no data) and echoes under MIN_DBZ (dBZ = index * 0.5 - 32.5) transparent."""
    im = Image.open(io.BytesIO(body))
    if im.mode != "P":
        raise ValueError(f"expected a palette PNG, got {im.mode}")
    idx = np.asarray(im)
    sub = idx[np.clip(rows, 0, idx.shape[0] - 1)[:, None], np.clip(cols, 0, idx.shape[1] - 1)[None, :]]
    sub = np.where(sub * 0.5 - 32.5 >= MIN_DBZ, sub, 0).astype(np.uint8)
    frame = Image.fromarray(sub, "P")
    frame.putpalette(im.getpalette())
    buf = io.BytesIO()
    frame.save(buf, "PNG", optimize=True, transparency=0)
    return buf.getvalue()


if __name__ == "__main__":
    sys.exit(main())
