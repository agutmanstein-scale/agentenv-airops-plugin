"""Film an episode on the viewer: headless Chrome over the DevTools protocol drives window.airops frame by frame (hold
on each planner step at its watch, sweep the clock between watches), each frame a JPEG piped to ffmpeg for an MP4, and
optionally an animated WebP for a README. --shots takes one screenshot per view instead.

usage: uv run --no-project --with websockets --with pillow python scripts/record.py --episode EPISODE.json \
           --out cache/films/NAME.mp4 [--webp cache/films/NAME.webp] [--cut 1020:hub]
       ... python scripts/record.py --episode EPISODE.json --shots cache/shots
"""

import argparse
import base64
import functools
import itertools
import json
import math
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from PIL import Image
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable",
           "chromium", "chromium-browser"]
ATTRIBUTION = ("Basemap © OpenMapTiles © OpenStreetMap contributors via OpenFreeMap · hub twin © OpenStreetMap "
               "contributors (ODbL) · radar: NEXRAD, Iowa Environmental Mesonet · BTS, FAA ATCSCC, NWS")
ANSWER_SECONDS = 120
QUICK = {"advance"}
RENDERER = ("(() => { const g = document.createElement('canvas').getContext('webgl2'); "
            "const e = g && g.getExtension('WEBGL_debug_renderer_info'); "
            "return g ? (e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'webgl2') : null; })()")


class Frame(NamedTuple):
    step: int | None
    t: float
    view: str


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--episode", type=Path, required=True, help="Episode JSON under the repo (served with web/).")
    p.add_argument("--out", type=Path, help="MP4 to write.")
    p.add_argument("--webp", type=Path, help="Also write an animated WebP clip here.")
    p.add_argument("--webp-width", type=int, default=960)
    p.add_argument("--webp-seconds", type=float, default=12, help="Length of the WebP clip, from the MP4's middle.")
    p.add_argument("--shots", type=Path, help="Write one PNG per view (and the end) here instead of a film.")
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--view", default="network", help="Starting camera: network, region or hub.")
    p.add_argument("--cut", action="append", default=[], metavar="MINUTE:VIEW",
                   help="Switch the camera at a minute of the day, e.g. 1020:hub (repeatable).")
    p.add_argument("--intro-seconds", type=float, default=2.0)
    p.add_argument("--plan-seconds", type=float, default=1.6, help="Hold on each submitted plan.")
    p.add_argument("--look-seconds", type=float, default=0.5, help="Hold on each look at the situation.")
    p.add_argument("--minutes-per-second", type=float, default=45, help="Clock speed between watches.")
    p.add_argument("--after-minutes-per-second", type=float, default=150, help="Clock speed after the last watch.")
    p.add_argument("--outro-seconds", type=float, default=3.5)
    p.add_argument("--gl", choices=["auto", "gpu", "swiftshader"], default="auto")
    p.add_argument("--chrome", help="Chrome or Chromium binary.")
    args = p.parse_args(argv)
    if not args.out and not args.shots:
        p.error("pass --out (a film) or --shots (screenshots)")

    width, height = (int(v) for v in args.size.lower().split("x"))
    chrome = find_chrome(args.chrome)
    episode_path = args.episode.resolve()
    episode = json.loads(episode_path.read_text())
    with serve(ROOT) as port:
        url = (f"http://127.0.0.1:{port}/web/index.html?record=1&view={quote(args.view)}"
               f"&t={episode['task']['start']}&episode={quote('/' + str(episode_path.relative_to(ROOT)))}")
        with session(url, chrome, width, height, args.gl) as (page, ready, gl):
            print(f"viewer ready: {ready} · WebGL {gl}")
            if args.shots:
                shots(page, ready, episode, args.shots)
            else:
                frames = timeline(episode, ready, args)
                film(page, frames, args.out, args.fps, episode)
    if args.out and not args.shots:
        probe(args.out)
        if args.webp:
            webp(args.out, args.webp, args.webp_width, args.webp_seconds)
    return 0


def timeline(episode: dict, ready: dict, args) -> list[Frame]:
    """Hold on each planner step at its watch, sweep the clock to the next watch, then play out the rest of the day."""
    fps, steps, cuts = args.fps, episode["steps"], sorted(parse_cut(c) for c in args.cut)

    def view_at(t: float) -> str:
        return next((v for m, v in reversed(cuts) if t >= m), args.view)

    def hold(step, t, seconds):
        return [Frame(step, t, view_at(t))] * max(1, round(seconds * fps))

    def sweep(step, a, b, rate):
        n = max(1, math.ceil(abs(b - a) / rate * fps))
        return [Frame(step, a + (b - a) * (i + 1) / n, view_at(a + (b - a) * (i + 1) / n)) for i in range(n)]

    t = ready["start"]
    frames = hold(None, t, args.intro_seconds)
    for w in sorted(ready["watches"]):
        if w > t:
            frames += sweep(None, t, w, args.minutes_per_second)
            t = w
        for k, s in enumerate(steps):
            if s["t"] != w or s["tool"] in QUICK:
                continue
            frames += hold(k, t, args.plan_seconds if s["tool"] != "get_situation" else args.look_seconds)
    frames += sweep(None, t, ready["end"], args.after_minutes_per_second)
    return frames + hold(None, ready["end"], args.outro_seconds)


def parse_cut(cut: str) -> tuple[int, str]:
    minute, view = cut.split(":")
    return int(minute), view


def film(page, frames: list[Frame], out: Path, fps: int, episode: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    title = f"{episode['model']} · {episode['task']['task_id']} · {episode['run']}"
    encoder = subprocess.Popen(ffmpeg_args(find("ffmpeg"), out, fps, title), stdin=subprocess.PIPE)
    shown, view, start = "unset", None, time.monotonic()
    for i, frame in enumerate(frames):
        if frame.view != view:
            page.js(f"airops.view({json.dumps(frame.view)})")
            view = frame.view
        if frame.step != shown:
            page.js(f"airops.show({json.dumps(frame.step)})")
            shown = frame.step
        page.js(f"airops.time({frame.t:.3f})")
        encoder.stdin.write(base64.b64decode(page.call("Page.captureScreenshot", format="jpeg", quality=90)["data"]))
        if i % 150 == 0:
            print(f"frame {i}/{len(frames)} t={frame.t:.0f} ({time.monotonic() - start:.0f} s)", flush=True)
    encoder.stdin.close()
    if encoder.wait():
        raise SystemExit(f"ffmpeg exited {encoder.returncode}")
    print(f"wrote {out}: {len(frames)} frames, {len(frames) / fps:.1f} s, in {time.monotonic() - start:.0f} s")


def shots(page, ready: dict, episode: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    task = episode["task"]
    mid = next((w for w in ready["watches"] if w >= task["window"][0] + 120), ready["watches"][0])
    plan_step = next((k for k, s in enumerate(episode["steps"]) if s["t"] == mid and s["tool"] == "submit_plan"), None)
    plan = [("network", mid, plan_step), ("hub", mid, plan_step), ("region", mid, plan_step),
            ("network", task["start"], None), ("network", ready["end"], None)]
    for view, t, step in plan:
        page.js(f"airops.view({json.dumps(view)})")
        page.js(f"airops.show({json.dumps(step)})")
        page.js(f"airops.time({t})")
        end = "-end" if t == ready["end"] else ""
        name = f"{task['task_id']}-{view}-{int(t) // 60 % 24:02d}{int(t) % 60:02d}{end}.png"
        (out / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        print(f"wrote {out / name}")


def ffmpeg_args(ffmpeg: str, out: Path, fps: int, title: str) -> list[str]:
    return [ffmpeg, "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-vf", "scale=out_range=tv:out_color_matrix=bt709",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", "-metadata", f"title={title}", "-metadata", f"comment={ATTRIBUTION}", str(out)]


def probe(path: Path) -> None:
    info = json.loads(subprocess.run([find("ffprobe"), "-v", "error", "-show_entries",
                                      "stream=codec_name,width,height,nb_frames:format=duration,size", "-of", "json",
                                      str(path)], capture_output=True, text=True, check=True).stdout)
    s, f = info["streams"][0], info["format"]
    print(f"ffprobe {path.name}: {s['codec_name']} {s['width']}x{s['height']}, {s.get('nb_frames')} frames, "
          f"{float(f['duration']):.1f} s, {int(f['size']) / 1e6:.1f} MB")
    subprocess.run([find("ffmpeg"), "-v", "error", "-i", str(path), "-f", "null", "-"], check=True)


def webp(mp4: Path, out: Path, width: int, seconds: float) -> None:
    """An animated WebP of the film's middle, assembled with Pillow from frames ffmpeg extracts at 12 fps."""
    duration = float(subprocess.run([find("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                                     "csv=p=0", str(mp4)], capture_output=True, text=True, check=True).stdout)
    start = max(0.0, duration / 2 - seconds / 2)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([find("ffmpeg"), "-v", "error", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(mp4),
                        "-vf", f"fps=12,scale={width}:-2:flags=lanczos", f"{tmp}/f%04d.png"], check=True)
        images = [Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob("f*.png"))]
        out.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(out, save_all=True, append_images=images[1:], duration=round(1000 / 12), loop=0, quality=70,
                       method=4)
    print(f"wrote {out}: {len(images)} frames, {out.stat().st_size / 1e6:.1f} MB")


@contextmanager
def serve(root: Path):
    handler = functools.partial(Quiet, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


class Page:
    """One page target over the DevTools protocol."""

    def __init__(self, ws):
        self.ws = ws
        self.ids = itertools.count(1)

    def call(self, method: str, **params) -> dict:
        i = next(self.ids)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        try:
            while (message := json.loads(self.ws.recv(timeout=ANSWER_SECONDS))).get("id") != i:
                pass
        except TimeoutError as e:
            raise SystemExit(f"Chrome did not answer {method} in {ANSWER_SECONDS} s") from e
        if "error" in message:
            raise SystemExit(f"{method}: {message['error']}")
        return message["result"]

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise SystemExit(f"{expression}: {details.get('exception', {}).get('description', details['text'])}")
        return result["result"].get("value")


def chrome_args(chrome: str, profile: Path, width: int, height: int, swiftshader: bool) -> list[str]:
    gl = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if swiftshader else ["--enable-unsafe-swiftshader"]
    return [chrome, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
            f"--window-size={width},{height}", "--hide-scrollbars", "--mute-audio", "--no-first-run",
            "--disable-smooth-scrolling", "--force-device-scale-factor=1", *gl, "about:blank"]


@contextmanager
def session(url: str, chrome: str, width: int, height: int, gl: str):
    """The viewer at ``url`` in headless Chrome once ``airops.ready``: the GPU first, SwiftShader without WebGL2."""
    for swiftshader in ([gl == "swiftshader"] if gl != "auto" else [False, True]):
        with tempfile.TemporaryDirectory() as profile:
            browser = subprocess.Popen(chrome_args(chrome, Path(profile), width, height, swiftshader),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                with connect(devtools(Path(profile), browser), max_size=None) as ws:
                    page = Page(ws)
                    renderer = page.js(RENDERER)
                    if not renderer and gl == "auto" and not swiftshader:
                        continue
                    page.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                              mobile=False)
                    page.call("Page.navigate", url=url)
                    page.js("new Promise(r => { const f = () => window.airops ? r() : setTimeout(f, 50); f(); })")
                    ready = page.js("airops.ready")
                    yield page, ready, f"{renderer}{' (SwiftShader)' if swiftshader else ''}"
                    return
            finally:
                browser.terminate()
                browser.wait()
    raise SystemExit("no WebGL2 in headless Chrome, even on SwiftShader")


def devtools(profile: Path, chrome: subprocess.Popen) -> str:
    port_file = profile / "DevToolsActivePort"
    deadline = time.monotonic() + 30
    while not port_file.is_file():
        if chrome.poll() is not None or time.monotonic() > deadline:
            raise SystemExit("Chrome did not start its DevTools server")
        time.sleep(0.05)
    port = port_file.read_text().split()[0]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
        return next(t["webSocketDebuggerUrl"] for t in json.load(r) if t["type"] == "page")


def find_chrome(path: str | None) -> str:
    for candidate in [path] if path else CHROMES:
        if candidate and (found := shutil.which(candidate) or (Path(candidate).is_file() and candidate)):
            return found
    raise SystemExit(f"no Chrome found{f' at {path}' if path else ''}: pass --chrome PATH")


def find(tool: str) -> str:
    if found := shutil.which(tool):
        return found
    raise SystemExit(f"no {tool} on PATH")


if __name__ == "__main__":
    sys.exit(main())
