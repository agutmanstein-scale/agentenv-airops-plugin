"""Thumbnails for a viewer folder's control-center days: each day's airport in daylight with aircraft in it, picked by
score from candidate stills (an aircraft at its gate's jet bridge, a pushback, the tower's view over the apron), the
views varied down the list and about one card in five the control-center room on its desks; written to the folder's
thumbs/ as record.py --thumb writes them, with a contact sheet of all of them in the list's order.

A still scores for the aircraft in it (JetBlue's, in sight and not behind a building or a bridge, the nearest one's
size or how many there are), the light (the sun at the hub and the frame's brightness) and its detail (contrast and
edges, so that fog, a blank wall or the sky alone score low); one with no aircraft in sight is never picked.

usage: .venv/bin/python scripts/occ_thumbs.py cache/occ/viewer --sheet cache/occ/thumbs.jpg [--workers 3] [--only STEM]
"""

import argparse
import base64
import io
import json
import math
import sys
import tempfile
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record import DAY_SUN, FILM_CSS, FILM_JS, ROOT, THUMB, clock, find_chrome, serve, session  # noqa: E402

PATTERN = ("gate", "tower", "push", "gate", "room")
"""The views the list's cards take in turn after the featured ones, each falling back to the best airport still."""
EVERY = 50
"""Minutes between candidate times over the day's daylight."""
SETTLE = 3
"""Frames rendered at a candidate before its still, so that it shows what the replay would."""
NEAR, TOWER_NEAR = 0.12, 0.2
"""The least share of the frame's width the nearest JetBlue aircraft in sight spans in a still that may be picked, and
in a tower's view a card takes for its own (so that the apron reads, not the roofs)."""

PLAN_JS = r"""((T) => {
  const S = airops.stage, out = { gate: [], push: [] };
  for (const [key, tr] of S.plan.tracks) if (tr.jetblue && S.pose(T, key)?.phase === "parked") out.gate.push(key);
  for (let t = T; t <= T + 30 && !out.push.length; t += 1)
    for (const [key, tr] of S.plan.tracks)
      if (tr.jetblue && S.pose(t, key)?.phase === "pushback") { out.push.push([key, t]); break; }
  return out;
})"""

SEE_JS = r"""(async () => {
  const THREE = await import("three"), S = airops.stage, cam = S.camera;
  const w = S.container.clientWidth, h = S.container.clientHeight, ray = new THREE.Raycaster(), out = [];
  for (const f of S.fleet.live) {
    if ((f.pose.alt || 0) > 60) continue;
    const d = f.dims, pts = [[0, 0, -d.nose], [0, 0, d.length - d.nose], [-d.span / 2, 0, d.lz], [d.span / 2, 0, d.lz],
      [0, d.height, d.length - d.nose - 2]].map(([x, y, z]) => new THREE.Vector3(x, y, z).applyMatrix4(f.matrix));
    const centre = new THREE.Vector3(0, d.height * 0.35, d.length / 2 - d.nose).applyMatrix4(f.matrix);
    const ndc = pts.map((p) => p.clone().project(cam));
    if (ndc.some((p) => p.z > 1)) continue;
    const xs = ndc.map((p) => (p.x + 1) / 2 * w), ys = ndc.map((p) => (1 - p.y) / 2 * h);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const cx0 = Math.max(0, x0), cx1 = Math.min(w, x1), cy0 = Math.max(0, y0), cy1 = Math.min(h, y1);
    if (cx1 <= cx0 || cy1 <= cy0) continue;
    const dist = cam.position.distanceTo(centre);
    ray.set(cam.position, centre.clone().sub(cam.position).normalize());
    ray.far = Math.max(1, dist - d.length / 2);
    const hidden = ray.intersectObjects([S.buildings.group, S.bridges.group], true).length > 0;
    const shown = ((cx1 - cx0) * (cy1 - cy0)) / Math.max(1, (x1 - x0) * (y1 - y0));
    out.push({ jetblue: !!f.c.track.jetblue, width: (cx1 - cx0) / w, shown, hidden, phase: f.pose.phase });
  }
  const s = S.state();
  return { aircraft: out, sun: s.sun, snow: s.snow, weather: s.weather };
})()"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("folder", type=Path, help="A viewer folder: episodes/index.json and what it points at.")
    p.add_argument("--sheet", type=Path, help="Write a contact sheet of the thumbnails here, in the list's order.")
    p.add_argument("--only", action="append", default=[], metavar="STEM", help="Only these episodes.")
    p.add_argument("--keep", type=Path, help="Keep each day's best few candidate stills here, to review the picks.")
    p.add_argument("--workers", type=int, default=3, help="Days rendered at once, a Chrome each.")
    p.add_argument("--size", default="1280x720")
    p.add_argument("--chrome", help="Chrome or Chromium binary.")
    args = p.parse_args(argv)
    width, height = (int(v) for v in args.size.lower().split("x"))
    folder = args.folder.resolve()
    entries = listed(json.loads((folder / "episodes" / "index.json").read_text())["episodes"])
    wants = plan(entries)
    todo = [e for e in entries if not args.only or Path(e["url"]).stem in args.only]
    chrome = find_chrome(args.chrome)
    with tempfile.TemporaryDirectory() as tmp, serve(ROOT, folder) as port:
        jobs = [(port, folder, e, wants[e["url"]], Path(tmp), chrome, width, height) for e in todo]
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
            found = dict(zip((e["url"] for e in todo), pool.map(render, jobs), strict=True))
        last: dict[str, tuple] = {}
        picks: dict[str, str] = {}
        for e in todo:
            pick = choose(found[e["url"]], wants[e["url"]], last.get(e["date"]))
            stem = Path(e["url"]).stem
            if pick is None:
                raise SystemExit(f"{stem}: no daylight still with an aircraft in it")
            last[e["date"]] = (pick["kind"], pick["key"], pick["t"])
            picks[e["url"]] = f"{pick['kind']} {clock(pick['t'])}"
            if args.keep:
                args.keep.mkdir(parents=True, exist_ok=True)
                for i, c in enumerate(sorted(found[e["url"]], key=lambda c: -c["score"])[:6]):
                    name = f"{stem}-{i}-{c['kind']}-{clock(c['t']).replace(':', '')}-{c['score']:.2f}.jpg"
                    Image.open(c["path"]).resize(THUMB).save(args.keep / name, quality=80)
            thumb = folder / "thumbs" / f"{stem}.jpg"
            thumb.parent.mkdir(parents=True, exist_ok=True)
            Image.open(pick["path"]).resize(THUMB, Image.LANCZOS).save(thumb, quality=88, optimize=True,
                                                                         progressive=True)
            print(f"{stem}: {pick['kind']} {clock(pick['t'])} score {pick['score']:.2f}"
                  f"{' (wanted ' + wants[e['url']] + ')' if pick['kind'] != wants[e['url']] else ''}", flush=True)
    if args.sheet:
        sheet(folder, entries, picks, args.sheet)
    return 0


def listed(episodes: list[dict]) -> list[dict]:
    """The control-center days in the order the viewer's list shows them (web/app.js indexPage)."""
    v2 = [e for e in episodes if e.get("occ")]
    rank = lambda e: (0 if e.get("featured") else 1, e.get("featured") or 0,  # noqa: E731
                      0 if (e.get("run") or "").startswith("occ-board") else 1, e["date"],
                      0 if e["occ"] == "two desks" else 1)
    return sorted(v2, key=rank)


def plan(entries: list[dict]) -> dict[str, str]:
    """The view each card wants: the featured ones their best airport still, the rest PATTERN in turn."""
    out, k = {}, 0
    for e in entries:
        if e.get("featured"):
            out[e["url"]] = "best"
        else:
            out[e["url"]] = PATTERN[k % len(PATTERN)]
            k += 1
    return out


def render(job) -> list[dict]:
    """One day's candidate stills, scored, written under tmp; a day whose far-field photo didn't load is opened again
    once."""
    port, folder, entry, want, tmp, chrome, width, height = job
    ep = json.loads((folder / "episodes" / entry["url"]).read_text())
    stem = Path(entry["url"]).stem
    url = (f"http://127.0.0.1:{port}/web/index.html?record=1&t={ep['task']['start']}"
           f"&episode={quote('/data/episodes/' + entry['url'])}")
    for attempt in range(2):
        with session(url, chrome, width, height, "auto") as (page, ready, _):
            far = page.js("airops.stage.ground.ready")
            if far < 2 and not attempt:
                continue
            cands = candidates(page, ready, ep, want, tmp, stem)
        break
    return [c | {"far": far} for c in cands]


def candidates(page, ready: dict, ep: dict, want: str, tmp: Path, stem: str) -> list[dict]:
    """The day's candidate stills, every EVERY minutes of its daylight: the tower's view, two of JetBlue's aircraft
    parked at their gates, and the next pushback; and for a room card, the room at three requests or decisions."""
    if not page.js(FILM_JS.replace("__CSS__", json.dumps(FILM_CSS))):
        raise SystemExit("the film helper did not load")
    page.js("film.look([], '', '', '', {})")
    cands: list[dict] = []
    span = ready["span"]
    days = page.js(f"(() => {{ const e = airops.stage.env, out = []; for (let t = {span[0]}; t <= {span[1]}; "
                   f"t += {EVERY}) if (e.sunAt(t).el >= {DAY_SUN}) out.push(t); return out; }})()")
    for t in days:
        seen = page.js(f"{PLAN_JS}({t})")
        gates = sorted(seen["gate"], key=lambda k: zlib.crc32(f"{k}{t}".encode()))[:2]
        shots = [("tower", None, t), *(("gate", k, t) for k in gates), *(("push", k, tp) for k, tp in seen["push"])]
        for kind, key, at in shots:
            spec = page.js(f"film.camera({json.dumps({'camera': kind, 'key': key, 't0': at, 't1': at + 1})})")
            if spec is None:
                continue
            cands.append(still(page, spec, at, kind, key, tmp / f"{stem}-{len(cands)}.jpg"))
    if want == "room" and ready.get("room"):
        for t, spec in room_shots(ep):
            cands.append(room_still(page, spec, t, tmp / f"{stem}-{len(cands)}.jpg"))
    return cands


def still(page, spec: dict, t: float, kind: str, key: str | None, path: Path) -> dict:
    """A candidate airport still from camera spec at minute t, scored."""
    page.js(f"airops.shot({json.dumps(spec)})")
    page.js(f"airops.time({t}, {{dt: 0, speed: 0}})")
    for _ in range(SETTLE):
        page.js(f"airops.time({t}, {{dt: 1 / 30, speed: 0}})")
    seen = page.js(SEE_JS)
    image = capture(page, path)
    return {"kind": kind, "key": key, "t": t, "path": path, **score(seen, image)}


def room_still(page, spec: dict, t: float, path: Path) -> dict:
    """A candidate still of the control-center room at minute t, over a desk's shoulder with a request's cards (or
    the one agent's decisions) on show, scored for its light and detail."""
    page.js(f"airops.shot({json.dumps(spec)})")
    page.js(f"airops.time({t}, {{dt: 0, speed: 0}})")
    for _ in range(SETTLE):
        page.js(f"airops.time({t}, {{dt: 1 / 30, speed: 0}})")
    image = capture(page, path)
    light, detail, _ = looks(image)
    return {"kind": "room", "key": None, "t": t, "path": path, "score": light + detail, "aircraft": 0}


def room_shots(ep: dict) -> list[tuple[float, dict]]:
    """Up to three moments over the day for the room: a request between the desks answered, framed on the desk it
    went to with its cards; on a one-agent day, a decision at its desk."""
    occ = ep["occ"]
    asks = sorted((r for r in occ["requests"] if r["type"] != "TEXT" and r.get("answered") is not None),
                  key=lambda r: r["shown"])
    if asks:
        picks = [asks[i] for i in sorted({0, len(asks) // 2, len(asks) - 1})]
        return [(r["shown"] + 1.0, {"room": True, "name": "room-desks", "desk": r["receiver"], "ref": r["id"],
                                    "t0": r["shown"] + 1.0, "t1": r["shown"] + 1.0}) for r in picks]
    made = sorted(d["shown"] for d in occ["decisions"] if d.get("shown") is not None)
    picks = [made[i] for i in sorted({0, len(made) // 2, len(made) - 1})] if made else [ep["task"]["start"] + 60]
    return [(t + 0.5, {"room": True, "name": "room-desks", "desk": "one", "t0": t + 0.5, "t1": t + 0.5})
            for t in picks]


def capture(page, path: Path) -> Image.Image:
    data = base64.b64decode(page.call("Page.captureScreenshot", format="jpeg", quality=92)["data"])
    path.write_bytes(data)
    return Image.open(io.BytesIO(data)).convert("RGB")


def score(seen: dict, image: Image.Image) -> dict:
    """A still's score: 2 for the aircraft (the nearest JetBlue one's width up to half the frame, or five of them in
    sight), 1 for the light, 1 for the detail, less for a flat or blown-out frame; one whose nearest JetBlue aircraft
    in sight spans less than NEAR of it scores nothing."""
    ours = [a for a in seen["aircraft"] if a["jetblue"] and not a["hidden"] and a["shown"] > 0.6 and a["width"] > 0.05]
    near = max((a["width"] for a in ours), default=0.0)
    if near < NEAR:
        return {"score": 0.0, "aircraft": 0}
    plane = max(min(1.0, near / 0.5), 0.8 * min(1.0, len(ours) / 5))
    light, detail, flat = looks(image)
    sun = min(1.0, max(0.0, (seen["sun"] - 8) / 22))
    return {"score": round(2 * plane + 0.5 * sun + 0.5 * light + detail - flat, 3), "aircraft": len(ours),
            "near": round(near, 3), "sun": round(seen["sun"], 1)}


def looks(image: Image.Image) -> tuple[float, float, float]:
    """The frame's light (its brightness in a daylight range), detail (contrast and edges) and flatness (the share
    of it that is one even tone: fog, sky or a wall, beyond a third, and of it blown out to white, beyond 3 %)."""
    g = image.convert("L").resize((320, 180))
    mean, std = ImageStat.Stat(g).mean[0], ImageStat.Stat(g).stddev[0]
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    light = max(0.0, 1 - abs(mean - 135) / 75)
    detail = 0.5 * min(1.0, std / 50) + 0.5 * min(1.0, edges / 14)
    blocks = [ImageStat.Stat(g.crop((x, y, x + 16, y + 15))).stddev[0]
              for x in range(0, 320, 16) for y in range(0, 180, 15)]
    flat = max(0.0, sum(b < 3.5 for b in blocks) / len(blocks) - 1 / 3) * 1.5
    glare = sum(g.histogram()[250:]) / (320 * 180)
    return light, detail, flat + 3 * max(0.0, glare - 0.03)


def choose(cands: list[dict], want: str, other: tuple | None = None) -> dict | None:
    """The wanted view's best still if it scores within a fifth of the best airport still, else that one; the
    featured cards ("best") of the best airport stills the one with the biggest aircraft; never the still `other` (the
    same day's other card's: kind, aircraft, minute) or one of the same kind within the hour of it."""
    def apart(c: dict) -> bool:
        return other is None or not (c["kind"] == other[0] and (c["key"] == other[1] or abs(c["t"] - other[2]) < 60))

    air = sorted((c for c in cands if c["kind"] != "room" and c["aircraft"] and apart(c)), key=lambda c: -c["score"])
    if want == "room":
        rooms = sorted((c for c in cands if c["kind"] == "room"), key=lambda c: -c["score"])
        return rooms[0] if rooms else (air[0] if air else None)
    if not air:
        return None
    if want == "best":
        return max(air[:8], key=lambda c: c["score"] + 0.6 * min(1.0, c["near"] / 0.6))
    mine = [c for c in air if c["kind"] == want and (want != "tower" or c["near"] >= TOWER_NEAR)]
    return mine[0] if mine and mine[0]["score"] >= 0.8 * air[0]["score"] else air[0]


def sheet(folder: Path, entries: list[dict], picks: dict[str, str], out: Path, cols: int = 6) -> None:
    """The thumbnails in the list's order, numbered from 1, each with its day, setup and the view and minute picked
    (featured cards starred)."""
    w, h, cap = 320, 180, 28
    rows = math.ceil(len(entries) / cols)
    board = Image.new("RGB", (cols * w, rows * (h + cap)), "white")
    draw = ImageDraw.Draw(board)
    font = ImageFont.load_default()
    for i, e in enumerate(entries):
        x, y = (i % cols) * w, (i // cols) * (h + cap)
        path = folder / "thumbs" / f"{Path(e['url']).stem}.jpg"
        if path.is_file():
            board.paste(Image.open(path).convert("RGB").resize((w, h)), (x, y))
        draw.text((x + 4, y + h + 3), f"#{i + 1} {e['date']} {e['occ']}{' *' if e.get('featured') else ''}",
                  fill="black", font=font)
        draw.text((x + 4, y + h + 15), picks.get(e["url"], ""), fill="#555", font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    board.save(out, quality=85)
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
