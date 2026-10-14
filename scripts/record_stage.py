"""Film the 3D airport stage (web/stage/proto.html) with record.py's headless Chrome: stills at chosen times and
cameras, and a three-shot clip of the featured departure (pushback at the gate, taxi, takeoff), frame by frame.

usage: uv run --no-project --with websockets --with pillow python scripts/record_stage.py --shots cache/stage/proto
       ... python scripts/record_stage.py --out cache/stage/proto/b6-2519.mp4 --webp cache/stage/proto/b6-2519.webp
"""

import argparse
import base64
import json
import subprocess
import time
from pathlib import Path

from record import ATTRIBUTION, ROOT, ffmpeg_args, find, find_chrome, probe, serve, session, webp

CREDIT = ("Boston Logan twin © OpenStreetMap contributors (ODbL) · imagery: USGS The National Map (public domain) · "
          "flights: BTS · weather: Iowa Environmental Mesonet")
STILLS = [
    ("gate-1712", 1032.25, {"from": [-770, 570, 14], "at": "featured", "offset": [0, 0, 3], "fov": 40}),
    ("tower-1715", 1035.3, {"from": [-986.8, 328.4, 67.9], "at": [-790, 800, 0], "fov": 30}),
    ("harbour-1719", 1039.7, {"from": [2000, -2200, 120], "at": [427, -257, -90], "fov": 40}),
    ("takeoff-1737", 1057.8, {"from": [-545, -863, 4], "at": "featured", "fov": 30, "frame": 75}),
    ("tower-1800", 1080.3, {"from": [-988.2, 328.7, 67.9], "at": [-870, 760, 0], "fov": 30}),
]
"""(name, minute of the day, camera): ENU metres [x, y, up] about the hub; "featured" aims at the featured aircraft."""
CLIP = [
    (3.0, 6, 1032.2, {"from": [-770, 570, 14], "to": [-775, 566, 13], "at": "featured", "offset": [0, 0, 3],
                       "fov": 40}),
    (4.5, 20, 1052.4, {"from": [860, -420, 8], "to": [850, -425, 7], "at": "featured", "fov": 34, "frame": 80}),
    (7.5, (3.5, 1.5), 1057.5375, {"from": [-545, -863, 4], "to": [-548, -858, 4.5], "at": "featured", "fov": 40,
                                  "frame": 90}),
]
"""(seconds, clock speed or a ramp from one speed to another, first minute, camera) per shot, hard cuts between."""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--shots", type=Path, help="Write the stills here (PNG).")
    p.add_argument("--out", type=Path, help="MP4 of the clip.")
    p.add_argument("--webp", type=Path, help="Also write the clip as an animated WebP here.")
    p.add_argument("--webp-width", type=int, default=960)
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--only", help="Comma-separated still names to render.")
    p.add_argument("--gl", choices=["auto", "gpu", "swiftshader"], default="auto")
    p.add_argument("--chrome", help="Chrome or Chromium binary.")
    args = p.parse_args(argv)
    if not args.out and not args.shots:
        p.error("pass --shots (stills) or --out (the clip)")
    width, height = (int(v) for v in args.size.lower().split("x"))
    with serve(ROOT) as port:
        url = f"http://127.0.0.1:{port}/web/stage/proto.html?record=1"
        with session(url, find_chrome(args.chrome), width, height, args.gl) as (page, ready, gl):
            print(f"stage ready: featured {ready['featured']['flight']} · far field {ready['far']} · WebGL {gl}")
            if args.shots:
                stills(page, args.shots, args.only)
            if args.out:
                clip(page, args.out, args.fps)
    if args.out:
        probe(args.out)
        if args.webp:
            webp(args.out, args.webp, args.webp_width, sum(s for s, *_ in CLIP))
    return 0


def stills(page, out: Path, only: str | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, t, cam in STILLS:
        if only and name not in only.split(","):
            continue
        page.js(f"airops.shot({json.dumps(cam)})")
        for _ in range(3):
            page.js(f"airops.time({t})")
        (out / f"{name}.png").write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        print(f"wrote {out / name}.png · {json.dumps(page.js('airops.perf(20)'))}")


def clip(page, out: Path, fps: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ffmpeg_args(find("ffmpeg"), out, fps, "AirOpsEnv v1 · B6 2519 at Boston Logan")
    encoder = subprocess.Popen([a.replace(ATTRIBUTION, CREDIT) for a in args], stdin=subprocess.PIPE)
    start, n = time.monotonic(), 0
    for seconds, speed, t0, cam in CLIP:
        frames = round(seconds * fps)
        s0, s1 = speed if isinstance(speed, tuple) else (speed, speed)
        rates = [s0 + (s1 - s0) * i / max(1, frames - 1) for i in range(frames)]
        times = [t0 + sum(rates[:i]) / fps / 60 for i in range(frames)]
        page.js(f"airops.shot({json.dumps({**cam, 't0': t0, 't1': times[-1]})})")
        page.js(f"airops.time({t0}, 0)")
        for t, rate in zip(times, rates, strict=True):
            page.js(f"airops.time({t:.5f}, {1 / fps}, {rate:.2f})")
            shot = page.call("Page.captureScreenshot", format="jpeg", quality=92)["data"]
            encoder.stdin.write(base64.b64decode(shot))
            n += 1
        print(f"shot {t0:.2f}-{times[-1]:.2f} ×{s0}-{s1}: {frames} frames, {time.monotonic() - start:.0f} s")
    encoder.stdin.close()
    if encoder.wait():
        raise SystemExit(f"ffmpeg exited {encoder.returncode}")
    print(f"wrote {out}: {n} frames, {n / fps:.1f} s")


if __name__ == "__main__":
    raise SystemExit(main())
