"""QA a viewer folder headlessly: open its episode list and each episode it lists in headless Chrome (record.py's
session), fail on an uncaught exception, a console error or a script or stylesheet from another host than the
viewer's (its third-party code is vendored under web/vendor/), check that the 3D airport, the control-center room and
the US map each come to the front and draw (window.airops.state() and a still that isn't blank) at a few minutes of
the day, with a day's passengers at the airport, inside its terminal (where it opens) and its customer desk in the room
when it has them, and no terminal without them, and write the stills, a contact sheet of them and a JSON report.

usage: .venv/bin/python scripts/occ_qa.py cache/occ/viewer --out cache/qa [--minutes 840,1075] [--no-room] \
           [--ignore REGEX]
"""

import argparse
import base64
import functools
import io
import json
import re
import sys
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from PIL import Image, ImageDraw, ImageFont, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record import clock, find_chrome, session  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VIEWS = {"terminal": "stage", "tower": "stage", "room": "room", "network": "map"}
BLANK = 6.0
"""The least spread of grey levels a still may have, below which it is blank."""
TILE = (480, 270)
QUIET = re.compile(r"favicon\.ico")
BLOCKED = re.compile(r"Access to \w+ at '([^']+)' from origin")
LOCAL = (None, "127.0.0.1", "localhost")
"""The hosts the QA serves the viewer and its data from (None: data: and blob: URLs)."""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("folder", type=Path, help="A viewer folder: episodes/index.json and what it points at.")
    p.add_argument("--out", type=Path, required=True, help="Where the stills, contact.jpg and qa.json go.")
    p.add_argument("--web", type=Path, default=ROOT / "web", help="The viewer's files.")
    p.add_argument("--minutes", help="Minutes of the day to check, comma-separated; by default the middle of the "
                                     "task's window and the first answer between the desks.")
    p.add_argument("--no-room", action="store_true", help="Don't require the 3D control-center room.")
    p.add_argument("--ignore", action="append", default=[], metavar="REGEX", help="Console errors to let pass.")
    p.add_argument("--size", default="1280x720")
    p.add_argument("--chrome", help="Chrome or Chromium binary.")
    p.add_argument("--cross-origin", action="store_true",
                   help="Serve the folder from another origin than the viewer, with CORS headers as huggingface.co "
                        "sends them, as the Space reads the dataset: an image loaded without crossOrigin then "
                        "taints its WebGL texture and fails the check.")
    args = p.parse_args(argv)
    width, height = (int(v) for v in args.size.lower().split("x"))
    args.out.mkdir(parents=True, exist_ok=True)
    ignore = [QUIET, *(re.compile(x) for x in args.ignore)]
    chrome = find_chrome(args.chrome)
    index = json.loads((args.folder / "episodes" / "index.json").read_text())
    report, stills = {"index": None, "episodes": []}, []
    with serve(args.web, args.folder, args.cross_origin) as (base, data):
        report["index"] = page_list(base, data, chrome, width, height, index, ignore, args.out, stills)
        for entry in index["episodes"]:
            ep = json.loads((args.folder / "episodes" / entry["url"]).read_text())
            minutes = [float(m) for m in args.minutes.split(",")] if args.minutes else default_minutes(ep)
            report["episodes"].append(episode(base, data, chrome, width, height, entry, ep, minutes,
                                              not args.no_room, ignore, args.out, stills))
    contact(stills, args.out / "contact.jpg")
    (args.out / "qa.json").write_text(json.dumps(report, indent=1))
    failed = [f"{r['name']}: {e}" for r in [report["index"], *report["episodes"]] for e in r["failures"]]
    for r in [report["index"], *report["episodes"]]:
        for note in r.get("notes", []):
            print(f"note {r['name']}: {note}")
    for line in failed:
        print(f"FAIL {line}")
    print(f"{len(report['episodes'])} episodes, {len(stills)} stills, {len(failed)} failures -> {args.out}")
    return 1 if failed else 0


def default_minutes(ep: dict) -> list[float]:
    lo, hi = ep["task"]["window"]
    answered = [r["answered"] for r in (ep.get("occ") or {}).get("requests", []) if r.get("answered") is not None
                and lo <= r["answered"] <= hi + 180]
    return sorted({round((lo + hi) / 2), *([round(min(answered)) + 1] if answered else [])})


def page_list(base: str, data: str, chrome: str, width: int, height: int, index: dict, ignore, out: Path,
              stills) -> dict:
    """The episode list: it loads without errors and has a card per episode, noting those without a thumbnail."""
    result = {"name": "index", "failures": []}
    try:
        with session(f"{base}/viewer/index.html?index={quote(data + '/episodes/index.json')}", chrome, width,
                     height, "auto", enable=("Runtime", "Log", "Network")) as (page, ready, _):
            cards = page.js("document.querySelectorAll('.ep-card').length")
            thumbs = page.js("document.querySelectorAll('.ep-card .thumb:not(.none)').length")
            inside = page.js("document.querySelectorAll('.ep-card[href*=\"view=terminal\"]').length")
            start = page.js("[document.querySelector('.start-card')?.href ?? '', "
                            "document.querySelector('.start .also a')?.href ?? '']")
            result |= {"cards": cards, "thumbs": thumbs, "terminal": inside, "start": start}
            if cards != len(index["episodes"]):
                result["failures"].append(f"{cards} cards for {len(index['episodes'])} episodes")
            v3 = sum(1 for e in index["episodes"] if e.get("passengers"))
            if inside != v3:
                result["failures"].append(f"{inside} cards open the terminal for {v3} episodes with passengers")
            if v3 and not ("view=terminal" in start[0] and "moment=passengers" in start[0] and "view=room" in start[1]):
                result["failures"].append(f"Start here opens {start}, not the terminal and the room")
            if thumbs < cards:
                result["notes"] = [f"{cards - thumbs} episodes without a thumbnail (record.py --thumb)"]
            capture(page, out / "index.jpg")
            stills.append(("episode list", out / "index.jpg"))
            result["failures"] += errors(page, ignore)
    except SystemExit as e:
        result["failures"].append(f"did not load: {e}")
    return result


def episode(base: str, data: str, chrome: str, width: int, height: int, entry: dict, ep: dict, minutes: list[float],
            room: bool, ignore, out: Path, stills) -> dict:
    """One episode: ready without errors, then each view in front and drawing at each minute."""
    name = Path(entry["url"]).stem
    result = {"name": name, "failures": [], "notes": [], "checks": []}
    url = (f"{base}/viewer/index.html?record=1&t={minutes[0]}"
           f"&episode={quote(data + '/episodes/' + entry['url'])}")
    try:
        with session(url, chrome, width, height, "auto", enable=("Runtime", "Log", "Network")) as (page, ready, gl):
            inside = bool(ep.get("passengers") and ep.get("stage"))
            opened = page.js("airops.state().view")
            result |= {"gl": gl, "views": ready["views"], "stage": ready["stage"], "room": ready["room"],
                       "opened": opened}
            wanted = ["terminal" if inside else None, "tower" if ep.get("stage") else None,
                      "room" if ep.get("occ") and room else None, "network"]
            for missing in [v for v in wanted if v and v != "network" and v not in ready["views"]]:
                result["failures"].append(f"no {missing if missing == 'terminal' else VIEWS[missing]} view")
            if not inside and "terminal" in ready["views"]:
                result["failures"].append("a terminal view without passengers")
            if ep.get("stage") and opened != ("terminal" if inside else "tower"):
                result["failures"].append(f"opens on {opened}")
            for t in minutes:
                for view in [v for v in wanted if v and v in ready["views"]]:
                    page.js(f"airops.view({json.dumps(view)})")
                    page.js(f"airops.time({t}, {{dt: 0, speed: 0}}).then(() => airops.time({t}, {{dt: 1 / 30}}))")
                    state = page.js("airops.state()")
                    path = out / f"{name}-{view}-{clock(t).replace(':', '')}.jpg"
                    spread = capture(page, path)
                    check = {"view": view, "t": t, "main": state["main"], "spread": round(spread, 1),
                             "aircraft": (state.get("stage") or {}).get("aircraft"), "airborne": state["airborne"],
                             "passengers": (state.get("stage") or {}).get("passengers"),
                             "gate": ((state.get("stage") or {}).get("terminal") or {}).get("gate")}
                    result["checks"].append(check)
                    stills.append((f"{ep['task']['date']} · {ep['occ']['label'] if ep.get('occ') else 'v1'} · "
                                   f"{view} {clock(t)}", path))
                    if state["main"] != VIEWS[view]:
                        result["failures"].append(f"{view} at {clock(t)}: {state['main']} in front")
                    if spread < BLANK:
                        result["failures"].append(f"{view} at {clock(t)}: blank (spread {spread:.1f})")
                    if view == "tower" and not check["aircraft"]:
                        result["failures"].append(f"tower at {clock(t)}: no aircraft drawn")
                    if view == "tower" and ep.get("passengers") and check["passengers"] is None:
                        result["failures"].append(f"tower at {clock(t)}: no passengers at the airport")
                    if view == "terminal" and (state["view"] != "terminal" or check["gate"] is None):
                        result["failures"].append(f"terminal at {clock(t)}: no gate in view")
                    desks = (state.get("room") or {}).get("desks") or []
                    if view == "room" and "customer" in ep["occ"].get("desks", []) and "customer" not in desks:
                        result["failures"].append(f"room at {clock(t)}: no customer desk")
            result["failures"] += errors(page, ignore, result["notes"])
    except SystemExit as e:
        result["failures"].append(f"did not load: {e}")
    print(f"{name}: {len(result['checks'])} checks, {len(result['failures'])} failures", flush=True)
    return result


def errors(page, ignore, notes: list | None = None) -> list[str]:
    """The page's uncaught exceptions and console and log errors, but those ``ignore`` lets pass, and any script or
    stylesheet it loaded from another host; another host's resource that failed to load or came without CORS headers
    (map tiles, imagery) goes to ``notes``, counted by host."""
    out, away = [], {}
    for e in page.events:
        m, p = e["method"], e.get("params", {})
        if m == "Network.requestWillBeSent":
            if p.get("type") in ("Script", "Stylesheet") and urlparse(p["request"]["url"]).hostname not in LOCAL:
                out.append(f"third-party {p['type'].lower()}: {p['request']['url']}")
            continue
        if m == "Runtime.exceptionThrown":
            d = p["exceptionDetails"]
            text = d.get("exception", {}).get("description") or d.get("text", "")
        elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))
        elif m == "Log.entryAdded" and p["entry"]["level"] == "error":
            text = f"{p['entry'].get('text', '')} {p['entry'].get('url', '')}".strip()
            blocked = BLOCKED.search(p["entry"].get("text", ""))
            host = urlparse(blocked.group(1) if blocked else p["entry"].get("url", "")).hostname
            if (p["entry"].get("source") == "network" or blocked) and host not in LOCAL:
                away[host] = away.get(host, 0) + 1
                continue
        else:
            continue
        if not any(x.search(text) for x in ignore):
            out.append(f"console: {text.splitlines()[0][:300]}")
    page.events.clear()
    if notes is not None:
        notes += [f"{n} resources from {host} failed to load" for host, n in away.items()]
    return list(dict.fromkeys(out))


def capture(page, path: Path) -> float:
    """Write the page as a JPEG; the spread of its grey levels."""
    image = Image.open(io.BytesIO(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"])))
    image = image.convert("RGB")
    image.save(path, quality=85)
    return ImageStat.Stat(image.convert("L")).stddev[0]


def contact(stills: list[tuple[str, Path]], out: Path, cols: int = 4) -> None:
    """The stills on one sheet, each under its label."""
    font = ImageFont.load_default(size=15)
    rows = -(-len(stills) // cols)
    sheet = Image.new("RGB", (cols * TILE[0], rows * (TILE[1] + 24)), (16, 22, 28))
    draw = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(stills):
        x, y = i % cols * TILE[0], i // cols * (TILE[1] + 24)
        sheet.paste(Image.open(path).convert("RGB").resize(TILE, Image.LANCZOS), (x, y + 24))
        draw.text((x + 6, y + 4), label, fill=(225, 235, 240), font=font)
    sheet.save(out, quality=85)
    print(f"wrote {out}: {len(stills)} stills")


@contextmanager
def serve(web: Path, folder: Path, cross: bool = False):
    """The viewer at /viewer/ and the folder at /data/, as the Space lays them out; with ``cross`` the folder from a
    second server, another origin, sending Access-Control-Allow-Origin as huggingface.co does. Yields the viewer's
    base URL and the folder's."""
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, web=web.resolve(),
                                                                         data=folder.resolve()))]
    if cross:
        servers.append(ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, web=None,
                                                                             data=folder.resolve(), cors=True)))
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{servers[0].server_port}"
        yield base, f"http://127.0.0.1:{servers[-1].server_port}/data" if cross else f"{base}/data"
    finally:
        for server in servers:
            server.shutdown()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, web: Path | None, data: Path, cors: bool = False, **kw):
        self.web, self.data, self.cors = web, data, cors
        super().__init__(*args, **kw)

    def translate_path(self, path: str) -> str:
        p = unquote(path.split("?", 1)[0].split("#", 1)[0])
        for prefix, root in (("/viewer/", self.web), ("/data/", self.data)):
            if root is not None and p.startswith(prefix):
                return str(root / p[len(prefix):])
        return str(self.data / "__missing__")

    def end_headers(self) -> None:
        if self.cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    sys.exit(main())
