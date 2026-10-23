"""The live stream's episode server: it follows the airops-occ3 day the runner plays (current.json: its instance, the
env's data/get base, the day file once graded), plays the env's calls again in its own day (agentenv_airops.occ.onair)
and serves the viewer the day so far as Episode JSON, with its traffic sidecars, the static assets and the viewer.
It only reads data/get, every --poll seconds; the env never sees it.

    prebuild TASK_ID... --live LIVE --inputs cache/inputs    each day's news and hub traffic into LIVE (seconds a day)
    serve --current current.json --live LIVE --assets A      HTTP on --port: /live/state.json, /live/episode.json, the
                                                              sidecars, /stage /radar /twin from A, /web the viewer
    feed DAY.json... --current current.json                  a fake runner and env: recorded days revealed a watch
                                                              every --watch-seconds behind a data/get of their own
    attach BUNDLE TASK --current current.json                 current.json for a local `agent-env run` of BUNDLE TASK

The viewer: http://HOST:PORT/web/index.html?live=http://HOST:PORT (CONTRACT.md, "Live server → viewer").

serve checkpoints the day's replay in LIVE/ckpt/<run_id>.pkl at each watch and at most every CKPT seconds between (a
few MB, a tenth of a second); started again on the same run (attempt, instance) and code, it goes on from there in
seconds instead of playing the day again from 04:00.

    .venv/bin/python scripts/live_server.py prebuild b6-20260715-0400 --live /tmp/live --inputs cache/inputs
    .venv/bin/python scripts/live_server.py serve --current /tmp/current.json --live /tmp/live --assets VIEWER_ASSETS
"""

import argparse
import collections
import functools
import gzip
import hashlib
import json
import mimetypes
import os
import pickle
import random
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from agentenv_airops import export
from agentenv_airops.occ import onair
from agentenv_airops.occ.pack import Pack3

ROOT = Path(__file__).resolve().parents[1]
RPC = "/agentenv"
STALE = 30.0
REBUILD = 15.0
CATCH_UP = 2
KEEP = 8
GET_TIMEOUT = 60.0
CKPT = 30.0
RADAR = (240, 1900, 20, 1600)


def utc(t: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(body)
    os.replace(part, path)


def data_get(base: str, timeout: float = GET_TIMEOUT) -> dict:
    """The env's data/get record (the verifier's call: JSON-RPC at the env's base)."""
    r = httpx.post(base.rstrip("/") + RPC, json={"jsonrpc": "2.0", "id": 1, "method": "data/get", "params": {}},
                   timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"data/get: {body['error'].get('message')}")
    return body["result"]["parts"][0]["data"]


# ---------------------------------------------------------------------------------------------------------- prebuild


def prebuild(args) -> int:
    """Each day's news and traffic (seconds), and its radar frames where neither LIVE nor --assets has them: IEM's
    NEXRAD composites every 20 minutes from 04:00 to 07:40 the next day, as the v3 episodes have them (minutes)."""
    pack = Pack3(args.pack)
    for task_id in args.tasks:
        started = time.monotonic()
        world = pack.world(task_id)
        out = onair.prebuild(world, args.live, args.inputs.expanduser())
        line = f"{task_id}: {out['news']} news, {out['aircraft']} other aircraft at the hub in " \
               f"{time.monotonic() - started:.1f}s"
        have = [r for r in (args.live, args.assets) if r and (r / "radar" / task_id / "index.json").is_file()]
        if have:
            line += f"; radar in {have[0]}"
        elif args.radar:
            started = time.monotonic()
            line += f"; radar {bake_radar(world, args.live / 'radar' / task_id)} in {time.monotonic() - started:.0f}s"
        else:
            line += "; NO RADAR"
        print(line, flush=True)
    return 0


def bake_radar(world, out: Path) -> str:
    sys.path.insert(0, str(ROOT / "scripts"))
    import prebake_radar

    task = world.task
    spec = out.parent / f"{task.task_id}.task.json"
    write_atomic(spec, json.dumps({"task_id": task.task_id, "date": task.date, "tz": task.tz,
                                   "window": list(task.window), "airports": {c: a.to_dict() for c, a in
                                                                             task.airports.items()}}).encode())
    code = prebake_radar.main(["--task", str(spec), "--from", str(RADAR[0]), "--to", str(RADAR[1]), "--step",
                               str(RADAR[2]), "--width", str(RADAR[3]), "--out", str(out)])
    spec.unlink()
    return "baked" if code == 0 else "FAILED"


# ---------------------------------------------------------------------------------------------------------- serve


@functools.cache
def code() -> str:
    """The replay's code and packs (the package's files): a checkpoint is good only on what wrote it."""
    root = Path(onair.__file__).resolve().parents[1]
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith("."):
            h.update(f"{p.relative_to(root)}\0{p.stat().st_size}\0".encode() + p.read_bytes())
    return h.hexdigest()


class Day:
    """One run of one day: its world, its replay and what the stream shows of it."""

    def __init__(self, cur: dict, live: Path, assets: Path, pack: Pack3, inputs: Path | None):
        self.run_id, self.instance_id = cur.get("run_id"), cur.get("instance_id")
        self.task_id, self.mode, self.attempt = cur["task_id"], cur.get("mode") or "joint", cur.get("attempt")
        self.model, self.pilot = cur.get("model") or "", cur.get("pilot") or "live"
        self.run_label = f"{self.pilot}-{self.mode}"
        self.world = pack.world(self.task_id)
        if not (live / "news" / f"{self.task_id}.json").is_file():
            if inputs is None:
                print(f"{self.task_id} isn't prebuilt in {live}: no news and no sidecar (prebuild it, or --inputs)",
                      flush=True)
            else:
                onair.prebuild(self.world, live, inputs)
        self.statics = onair.Statics(self.world, live, assets)
        self.follower = onair.Follower(self.world, self.mode)
        self.stem = onair.stem(self.run_label, self.model, self.task_id)
        self.view: dict | None = None
        self.viewed = 0.0
        self.feed: collections.deque = collections.deque(maxlen=onair.FEED)
        self.last: dict[str, dict] = {}
        self.opened: dict[int, float] = {}
        self.started = time.time()
        self.ep: dict | None = None
        self.sidecar: str | None = None
        self.final = False
        self.failing: float | None = None
        self.error: str | None = None
        self.data_grade: dict | None = None
        self.lag = 0
        self.ckpt = live / "ckpt" / f"{self.run_id}.pkl"
        self.saved: tuple[float, int] = (0.0, -1)
        self.resumed = self.resume()

    def resume(self) -> bool:
        """The replay from this run's checkpoint, when it is this run's (its instance's) on this code."""
        if not self.ckpt.is_file():
            return False
        started = time.monotonic()
        try:
            with self.ckpt.open("rb") as fh:
                head = pickle.load(fh)
                mine = {k: head.get(k) for k in self.key()} == self.key() and (
                    None in (self.instance_id, head.get("instance_id")) or self.instance_id == head["instance_id"])
                if not mine:
                    print(f"{self.run_id}: the checkpoint is another run's ({head}); playing the day from its start",
                          flush=True)
                    return False
                state = onair.unpickled(fh.read(), self.world)
        except Exception as e:
            print(f"{self.run_id}: the checkpoint doesn't load ({type(e).__name__}: {e}); playing the day from its "
                  "start", flush=True)
            return False
        self.follower, self.feed, self.last = state["follower"], state["feed"], state["last"]
        self.opened, self.started, self.data_grade = state["opened"], state["started"], state["data_grade"]
        day = self.follower.day
        self.saved = (time.monotonic(), day.k)
        print(f"{utc()} {self.run_id}: resumed at watch {day.k}, {self.follower.played} calls"
              f"{', done' if day.done else ''} in {time.monotonic() - started:.2f}s", flush=True)
        return True

    def save(self) -> None:
        """The replay so far into its checkpoint (tmp + rename), at a new watch, once done, and at most every CKPT
        seconds between."""
        day = self.follower.day
        if self.saved[1] == day.k and not day.done and time.monotonic() - self.saved[0] < CKPT:
            return
        started = time.monotonic()
        state = {"follower": self.follower, "feed": self.feed, "last": self.last, "opened": self.opened,
                 "started": self.started, "data_grade": self.data_grade}
        body = pickle.dumps(self.key() | {"instance_id": self.instance_id}) + onair.pickled(state, self.world)
        write_atomic(self.ckpt, body)
        self.saved = (time.monotonic(), day.k)
        print(f"{utc()} {self.run_id}: checkpoint at watch {day.k}, {self.follower.played} calls: "
              f"{len(body) / 1e6:.1f} MB in {self.saved[0] - started:.2f}s", flush=True)

    def key(self) -> dict:
        return {"run_id": self.run_id, "task_id": self.task_id, "mode": self.mode, "attempt": self.attempt,
                "code": code()}

    def restart(self) -> None:
        self.follower = onair.Follower(self.world, self.mode)
        self.view, self.feed, self.last, self.opened = None, collections.deque(maxlen=onair.FEED), {}, {}

    def follow(self, data: dict) -> bool:
        """The record's news played, at most CATCH_UP watches past the replay's at a time so that a server behind the
        day shows it watch by watch; whether anything changed."""
        if data.get("task_id") is None:
            return False
        self.lag = max(0, data.get("watch", 0) - self.follower.day.k)
        limit = self.follower.day.k + CATCH_UP
        if any(c["watch"] > limit for c in data["calls"]):
            data = {**data, "calls": [c for c in data["calls"] if c["watch"] <= limit], "done": False, "digest": None,
                    "notices": [n for n in data["notices"] if n["watch"] <= limit]}
        try:
            new = self.follower.follow(data)
        except onair.Diverged as e:
            print(f"{self.run_id}: {e}; playing the record again from its start", flush=True)
            self.restart()
            new = self.follower.follow(data)
        now = time.time()
        day = self.follower.day
        self.opened.setdefault(day.k, now)
        for x in new:
            if x["tool"] != "notice":
                self.last[x["desk"]] = {"last": {"seq": x["seq"], "tool": x["tool"], "t": x["t"],
                                                 "text": onair.summary(x, self.world.task)}, "at": utc(now)}
        by_id = {n["id"]: n for n in self.statics.news}
        self.feed.extend(row | {"at": utc(now)} for row in onair.feed(self.follower, new, by_id))
        if data.get("done") and data.get("grade") is not None:
            self.data_grade = data["grade"]
        return bool(new) or (day.done and not self.final)

    def build(self, live_dir: Path, cur: dict, force: bool = False) -> None:
        """The episode now: the final one once done, else the day so far, its projection and sidecar renewed at a new
        watch and at most every REBUILD seconds after a decision."""
        day = self.follower.day
        record = {"folder": self.pilot, "run": f"{self.task_id}-{self.mode}", "attempt": cur.get("attempt"),
                  "cost_usd": (cur.get("result") or {}).get("cost_usd"),
                  "wall_seconds": (cur.get("result") or {}).get("wall_seconds"),
                  "desks": (cur.get("result") or {}).get("desks")}
        if day.done:
            if not self.final:
                self.ep = onair.final(self.follower, self.statics, model=self.model, run_label=self.run_label,
                                      record=record)
                body = self.statics.sidecar(self.ep, self.ep["watches"][-1]["t"])
                self.sidecar = None if body is None else f"{self.stem}.stage.json.gz"
                if body is not None:
                    write_atomic(live_dir / self.sidecar, body)
                self.final = True
            else:
                self.ep["record"] = record
            return
        now = time.time()
        stale = self.view is None or self.view["watch"] != day.k or (
            self.view["version"] != day.version and now - self.viewed >= REBUILD)
        if stale or force:
            self.view, self.viewed = onair.projection(self.follower), now
        self.ep = onair.now(self.follower, self.statics, model=self.model, run_label=self.run_label, record=record,
                            view=self.view)
        if stale or force or self.sidecar is None:
            body = self.statics.sidecar(self.ep, day.t)
            if body is not None:
                self.sidecar = f"{self.stem}.stage-{int(now * 1000)}.json.gz"
                write_atomic(live_dir / self.sidecar, body)
                old = sorted(live_dir.glob(f"{self.stem}.stage-*.json.gz"), key=lambda p: p.stat().st_mtime)
                for p in old[:-KEEP]:
                    p.unlink(missing_ok=True)
        self.ep["stage"] = None if self.sidecar is None else {
            "hub": self.statics.hub, "url": f"../stage/{self.statics.hub}/stage.json.gz", "traffic": self.sidecar}

    def live(self, cur: dict, status: str, seq: int) -> dict:
        f, day = self.follower, self.follower.day
        task = self.world.task
        seen = sorted(self.opened)
        mean = (self.opened[seen[-1]] - self.opened[seen[0]]) / (seen[-1] - seen[0]) if len(seen) > 1 else None
        g = day.grade if day.done else None
        result = None
        if g is not None:
            res = cur.get("result") or {}
            result = {"reward": g["reward"], "reward_L": g["reward_L"], "cost": g["cost"], "best": g["best"],
                      "bound": g["bound"], "B0": g["B0"], "end_reason": g["end_reason"],
                      "audit_ok": ((self.ep or {}).get("audit") or {}).get("ok"),
                      "cost_usd": res.get("cost_usd"), "wall_seconds": res.get("wall_seconds")}
        exact = not f.mismatches and (self.data_grade is None or g is None or self.data_grade == onair.jsonable(g))
        return {
            "status": status, "seq": seq, "run_id": self.run_id, "task_id": self.task_id, "date": task.date,
            "hub": task.hub, "instance_id": self.instance_id, "model": self.model,
            "model_label": export.NAMES.get(self.model, self.model),
            "mode": self.mode, "pilot": self.pilot, "watch": day.k, "watches": len(day.times), "t": day.t,
            "decided_until": onair.decided_until(day, (self.ep or {}).get("executed") or []),
            "turn": None if day.turn is None else {k: day.turn[k] for k in ("n", "desk", "kind", "calls")},
            "desks": onair.desks(f, self.last), "feed": list(self.feed),
            "projected": None if self.view is None or day.done else self.view["costs"],
            "next_watch_at": utc(self.opened[day.k] + mean) if mean and day.k in self.opened and not day.done
            else None,
            "watch_wall_s": None if mean is None else round(mean, 1),
            "started_utc": cur.get("started_utc") or utc(self.started), "updated_utc": utc(),
            "wall_seconds": round(time.time() - self.started, 1),
            "spent_usd": cur.get("live_usd"),
            "total_usd": None if cur.get("spent_usd") is None and cur.get("live_usd") is None else
            round((cur.get("spent_usd") or 0) + (cur.get("live_usd") or 0), 2), "budget_usd": cur.get("budget_usd"),
            "result": result, "exact": exact, "mismatches": len(f.mismatches), "lag": self.lag,
            "next": cur.get("next"), "history": cur.get("history") or [], "final": bool(cur.get("final")),
            "error": self.error,
        }


class Server:
    def __init__(self, args):
        self.args = args
        self.live_dir = args.live / "live"
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.pack = Pack3(args.pack)
        self.seq = int(time.time())
        self.day: Day | None = None
        self.days: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.state = b"{}"
        self.episode = (b"null", gzip.compress(b"null"), "0")
        self.status = "intermission"
        self.last_cur: dict = {}

    def loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                self.step()
            except Exception as e:
                traceback.print_exc()
                if self.day is not None:
                    self.day.error = f"{type(e).__name__}: {e}"
                    self.publish()
            behind = self.day is not None and self.day.lag > CATCH_UP and not self.day.final
            time.sleep(0.05 if behind else max(0.2, self.args.poll - (time.monotonic() - started)))

    def current(self) -> dict:
        cur = read_json(self.args.current) if self.args.current else None
        if cur is None:
            cur = self.last_cur
        if self.args.env:
            cur = {**cur, "status": cur.get("status") or "playing", "env": {"base": self.args.env},
                   "task_id": cur.get("task_id") or self.args.task, "run_id": cur.get("run_id") or self.args.task,
                   "mode": cur.get("mode") or "joint"}
        self.last_cur = cur
        return cur

    def step(self) -> None:
        cur = self.current()
        status = cur.get("status") or "intermission"
        moved = self.day is not None and None not in (self.day.instance_id, cur.get("instance_id")) and \
            self.day.instance_id != cur["instance_id"]
        known = status in ("starting", "playing", "done") or bool(cur.get("day_file"))
        if cur.get("task_id") and cur.get("run_id") and known and (
                self.day is None or self.day.run_id != cur["run_id"] or moved):
            print(f"{utc()} day {cur['run_id']} ({cur['task_id']}, {status})", flush=True)
            for p in (self.args.live / "ckpt").glob("*"):
                if p.name != f"{cur['run_id']}.pkl":
                    p.unlink(missing_ok=True)
            self.day = Day(cur, self.args.live, self.args.assets, self.pack, self.args.inputs)
            self.day.instance_id = cur.get("instance_id")
            self.day.build(self.live_dir, cur, force=True)
            if self.day.final:
                self.archive(self.day, cur)
            self.status = self.standing(self.day, cur, status) if self.day.resumed else "starting"
            self.publish(cur)
        day = self.day
        if day is None:
            self.status = status
            self.publish(cur)
            return
        day.instance_id = cur.get("instance_id") or day.instance_id
        changed = False
        if not day.final and status in ("playing", "done", "intermission"):
            data, base = None, (cur.get("env") or {}).get("base") if status == "playing" else None
            if base:
                try:
                    data = data_get(base)
                    day.failing, day.error = None, None
                except Exception as e:
                    day.failing = day.failing or time.time()
                    day.error = f"data/get: {type(e).__name__}: {e}"[:300]
            if (data is None or not data.get("done")) and status != "playing" and cur.get("day_file"):
                data = read_json(Path(cur["day_file"]))
            if data is not None:
                changed = day.follow(data)
        if changed:
            day.build(self.live_dir, cur)
            if day.final:
                self.archive(day, cur)
            try:
                day.save()
            except Exception as e:
                print(f"{utc()} {day.run_id}: no checkpoint ({type(e).__name__}: {e})", flush=True)
        elif day.final:
            day.build(self.live_dir, cur)
        if day.final:
            day.error, day.lag = None, 0
        self.status = self.standing(day, cur, status)
        self.publish(cur, changed)

    @staticmethod
    def standing(day: Day, cur: dict, status: str) -> str:
        if day.final:
            return "intermission" if status == "intermission" else "done"
        if status == "starting":
            return "starting"
        if day.failing and time.time() - day.failing > STALE:
            return "stale"
        if status == "playing":
            return "playing"
        if status == "done" or cur.get("day_file"):
            return "ending"
        return status

    def archive(self, day: Day, cur: dict) -> None:
        """The done day's episode kept beside the live one (its sidecar is there already), in /live/index.json."""
        ep = {**day.ep, "live": day.live(cur, "done", self.seq)}
        name = f"{day.run_id}.json"
        write_atomic(self.live_dir / name, json.dumps(ep, ensure_ascii=False, separators=(",", ":")).encode())
        self.days[day.run_id] = export.entry(ep, name, self.args.assets) | {"live": True}
        index = {"version": 1, "episodes": list(self.days.values())}
        write_atomic(self.live_dir / "index.json", (json.dumps(index, indent=1) + "\n").encode())
        print(f"{utc()} {day.run_id} done: reward {ep['reward']}, exact {ep['live']['exact']}", flush=True)

    def publish(self, cur: dict | None = None, changed: bool = True) -> None:
        cur = cur if cur is not None else self.last_cur
        day = self.day
        with self.lock:
            if day is None:
                self.seq += 1
                state = {"seq": self.seq, "live": {"status": self.status, "seq": self.seq, "next": cur.get("next"),
                                                   "history": cur.get("history") or [], "final": bool(cur.get("final")),
                                                   "updated_utc": utc()}}
                self.state = json.dumps(state).encode()
                return
            previous = json.loads(self.state).get("live", {}) if self.state else {}
            live = day.live(cur, self.status, self.seq)
            keys = ("seq", "updated_utc", "wall_seconds")
            if not changed and {k: v for k, v in live.items() if k not in keys} == \
                    {k: v for k, v in previous.items() if k not in keys}:
                return
            self.seq += 1
            live["seq"] = self.seq
            ep = {**day.ep, "live": live}
            body = json.dumps(ep, ensure_ascii=False, separators=(",", ":")).encode()
            self.episode = (body, gzip.compress(body, compresslevel=5), str(self.seq))
            self.state = json.dumps({"seq": self.seq, "live": live}, ensure_ascii=False).encode()
        write_atomic(self.live_dir / "episode.json", body)

    def bind(self) -> ThreadingHTTPServer:
        server = self
        assets, web = self.args.assets, self.args.web

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Expose-Headers", "ETag")

            def do_OPTIONS(self):
                self.send_response(204)
                self.cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def send(self, code, body, kind="application/json", headers=None):
                self.send_response(code)
                self.cors()
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def file(self, root: Path, rel: str, cache: str):
                path = (root / rel).resolve()
                if not root or not path.is_relative_to(root.resolve()) or not path.is_file():
                    return self.send(404, b'{"error":"not found"}')
                kind = "application/octet-stream" if path.suffix == ".gz" else \
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
                    kind += "; charset=utf-8"
                self.send(200, path.read_bytes(), kind, {"Cache-Control": cache})

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                if path in ("/", "/index.html"):
                    host = self.headers.get("Host", f"localhost:{server.args.port}")
                    self.send_response(302)
                    self.cors()
                    self.send_header("Location", f"/web/index.html?live=http://{host}")
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                if path == "/live/state.json":
                    with server.lock:
                        body = server.state
                    return self.send(200, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})
                if path == "/live/episode.json":
                    with server.lock:
                        raw, packed, etag = server.episode
                    headers = {"Cache-Control": "no-store", "ETag": f'"{etag}"'}
                    if self.headers.get("If-None-Match") == f'"{etag}"':
                        self.send_response(304)
                        self.cors()
                        for k, v in headers.items():
                            self.send_header(k, v)
                        self.send_header("Content-Length", "0")
                        return self.end_headers()
                    if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                        return self.send(200, packed, "application/json; charset=utf-8",
                                         headers | {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
                    return self.send(200, raw, "application/json; charset=utf-8", headers)
                if path.startswith("/live/"):
                    cache = "no-store" if path.endswith("index.json") else "public, max-age=86400, immutable"
                    return self.file(server.live_dir, path[len("/live/"):], cache)
                if path.startswith("/radar/") and (server.args.live / path[1:]).is_file():
                    return self.file(server.args.live, path[1:], "public, max-age=3600")
                for prefix in ("stage", "radar", "twin"):
                    if path.startswith(f"/{prefix}/"):
                        return self.file(assets, path[1:], "public, max-age=3600")
                if path.startswith("/web/"):
                    return self.file(web, path[len("/web/"):], "no-cache")
                return self.send(404, b'{"error":"not found"}')

        httpd = ThreadingHTTPServer((self.args.host, self.args.port), Handler)
        httpd.daemon_threads = True
        print(f"{utc()} serving on http://{self.args.host}:{self.args.port}/web/index.html?live=http://"
              f"{'localhost' if self.args.host in ('0.0.0.0', '127.0.0.1') else self.args.host}:{self.args.port}",
              flush=True)
        return httpd


def serve(args) -> int:
    server = Server(args)
    threading.Thread(target=server.bind().serve_forever, daemon=True).start()
    server.loop()
    return 0


# ---------------------------------------------------------------------------------------------------------- feed


class Feed:
    """A fake runner and env: each recorded day (a days/<run>.json, the env's final data/get) played back as a run,
    starting, its calls and advisories released a watch every `watch` seconds behind a data/get of its own, done once
    the last is out (the full record), torn down after `linger`, then an intermission of `gap` before the next."""

    def __init__(self, args):
        self.args = args
        self.days = [json.loads(Path(p).read_text()) for p in args.days]
        self.files = [str(Path(p).resolve()) for p in args.days]
        self.lock = threading.Lock()
        self.instance: str | None = None
        self.data: dict | None = None
        self.history: list[dict] = []
        self.seq = 0
        self.cur: dict = {}

    def write(self, **cur) -> None:
        self.seq += 1
        self.cur = {"pilot": self.args.pilot, "model": self.args.model, "mode": "joint", "policy": "fake",
                    "budget_usd": None, "spent_usd": round(sum(h.get("cost_usd") or 0 for h in self.history), 4),
                    "history": self.history, "final": False, "seq": self.seq, "updated_utc": utc(), **cur}
        write_atomic(self.args.current, (json.dumps(self.cur, indent=1) + "\n").encode())

    @staticmethod
    def prefix(day: dict, s: int, watch: int) -> dict:
        """The record as data/get had it when seq s was the last event: what has a seq cut by it, the rest by watch."""
        turns = [{**u, "ended": u["ended"] if u["ended"] is not None and u["ended"] <= s else None}
                 for u in day["turns"] if u["granted"] is not None and u["granted"] <= s]
        last = max([c for c in day["calls"] if c["seq"] <= s], key=lambda c: c["seq"], default=None)
        k = watch if last is None else last["watch"]
        return {**day, "watch": k, "t": day["estimates"][k]["t"] if k < len(day["estimates"]) else day["t"],
                "done": False, "turns": turns, "calls": [c for c in day["calls"] if c["seq"] <= s],
                "refusals": [r for r in day["refusals"] if r["seq"] <= s],
                "changes": [c for c in day["changes"] if c["seq"] <= s],
                "notices": [n for n in day["notices"] if n["seq"] <= s],
                "absent": {d: a for d, a in day["absent"].items() if a["seq"] <= s},
                "requests": [r for r in day["requests"] if r["watch"] < k], "pages": [p for p in day["pages"]
                                                                                     if p["watch"] <= k],
                "flags": [f for f in day["flags"] if f["watch"] <= k],
                "estimates": [e for e in day["estimates"] if e["watch"] < k], "plan": {}, "committed": [],
                "acausal": [], "crews": None, "status": None, "audit": None, "grade": None, "reward": None,
                "digest": None, "passengers": None}

    def timeline(self, day: dict) -> list[tuple[float, int]]:
        """(seconds after the start, seq) for each event: a watch's spread evenly over its `watch` seconds."""
        events = sorted([(c["seq"], c["watch"]) for c in day["calls"]] + [(n["seq"], n["watch"]) for n in
                                                                          day["notices"]])
        by: dict[int, list[int]] = collections.defaultdict(list)
        for seq, k in events:
            by[k].append(seq)
        out = []
        for i, k in enumerate(sorted(by)):
            for j, seq in enumerate(by[k]):
                out.append(((i + (j + 1) / (len(by[k]) + 1)) * self.args.watch_seconds, seq))
        return out

    def play(self) -> None:
        a = self.args
        for n, (day, file) in enumerate(zip(self.days, self.files, strict=True)):
            task_id, mode = day["task_id"], day["mode"]
            date = f"{task_id[3:7]}-{task_id[7:9]}-{task_id[9:11]}"
            nxt = None if n + 1 >= len(self.days) else {"date": None, "task_id": self.days[n + 1]["task_id"],
                                                        "starts_utc": None}
            attempt, restart = 1, a.restart_at if n == 0 else None
            while True:
                run_id = f"{task_id}-{mode}-a{attempt}"
                self.instance = f"@local/fake/{task_id}-{mode}-{random.randrange(16 ** 8):08x}"
                base = f"http://127.0.0.1:{a.port}/{self.instance.rsplit('/', 1)[1]}"
                started = utc()
                common = {"instance_id": self.instance, "run_id": run_id, "date": date, "task_id": task_id,
                          "mode": mode, "started_utc": started, "next": nxt, "attempt": attempt, "day": n + 1}
                with self.lock:
                    self.data = {"task_id": None, "mode": None, "done": False, "calls": [], "turns": [],
                                 "requests": [], "audit": None, "grade": None, "reward": None}
                self.write(status="starting", env=None, day_file=None, result=None, **common)
                time.sleep(a.start_delay)
                self.write(status="playing", env={"base": base, "mcp_url": base + "/mcp"}, day_file=None, result=None,
                           **common)
                t0, broke = time.monotonic(), False
                for at, seq in self.timeline(day):
                    time.sleep(max(0.0, t0 + at - time.monotonic()))
                    k = next(c["watch"] for c in day["calls"] + day["notices"] if c["seq"] == seq)
                    if restart is not None and k >= restart:
                        broke = True
                        break
                    with self.lock:
                        self.data = self.prefix(day, seq, k)
                if broke:
                    print(f"{utc()} {run_id}: the instance dies at watch {restart}", flush=True)
                    with self.lock:
                        self.instance = None
                    attempt, restart = attempt + 1, None
                    continue
                with self.lock:
                    self.data = day
                time.sleep(a.linger)
                with self.lock:
                    self.instance = None
                result = {"date": date, "task_id": task_id, "run_id": run_id, "instance_id": common["instance_id"],
                          "outcome": "scored", "reward": day["grade"]["reward"], "reward_L": day["grade"]["reward_L"],
                          "cost_usd": a.cost_usd, "wall_seconds": round(time.monotonic() - t0, 1),
                          "attempts": attempt}
                self.history.append(result)
                self.write(status="done", env=None, day_file=file, result=result, **common)
                print(f"{utc()} {run_id}: done, reward {day['grade']['reward']}", flush=True)
                time.sleep(a.gap / 2)
                self.write(status="intermission", env=None, day_file=file, result=result, **common)
                time.sleep(a.gap / 2)
                break
        self.write(**{k: v for k, v in self.cur.items() if k not in ("seq", "updated_utc", "final", "history")}
                   | {"final": True, "reason": "dates", "next": None})

    def serve(self) -> None:
        feed = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with feed.lock:
                    instance, data = feed.instance, feed.data
                parts = self.path.strip("/").split("/")
                if instance is None or parts[0] != instance.rsplit("/", 1)[1] or "/".join(parts[1:]) != RPC[1:]:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                if random.random() < feed.args.hiccups:
                    if random.random() < 0.5:
                        time.sleep(feed.args.hang)
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                rid = json.loads(body or b"{}").get("id")
                out = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"parts": [{"kind": "data", "data": data}]}}
                                 ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        httpd = ThreadingHTTPServer(("127.0.0.1", self.args.port), Handler)
        httpd.daemon_threads = True
        httpd.serve_forever()


def feed(args) -> int:
    f = Feed(args)
    threading.Thread(target=f.serve, daemon=True).start()
    f.play()
    time.sleep(args.linger)
    return 0


# ---------------------------------------------------------------------------------------------------------- attach


def attach(args) -> int:
    """current.json for the newest run of a bundle task, rewritten until it ends: the instance, the env's base once
    deployed, done with the graded day."""
    from agent_env.store.ids import LOCAL_PREFIX
    from agent_env.store.routing import namespace_routing
    from agent_env.task.store import task_instances

    root = args.bundle.expanduser().resolve()
    home = Path.home()
    task = LOCAL_PREFIX + "/".join(["~", *root.relative_to(home).parts] if root.is_relative_to(home) else
                                   root.parts[1:]) + f"/{args.task}"
    task_id, mode = args.task.rsplit("-", 1)
    date = f"{task_id[3:7]}-{task_id[7:9]}-{task_id[9:11]}"
    seen, seq = None, 0
    while True:
        with namespace_routing():
            found = task_instances(task, limit=1)
        if found and (args.after is None or (found[0].created_at_utc or "") >= args.after):
            x = found[0]
            ctx = x.context or {}
            env = next((e for e in ctx.get("deployed_envs") or [] if e.get("env_id") == "airops-occ3"), None)
            v = ((ctx.get("metadata") or {}).get("verifications") or {}).get("airops-occ3") or {}
            day = (v.get("results") or [{}])[0].get("episode")
            file = None
            if day:
                file = args.current.with_name(f"{args.task}-{x.instance_id.rsplit('-', 1)[1]}.json")
                write_atomic(file, json.dumps(day).encode())
            status = "done" if day or x.status in ("completed", "failed", "cancelled") else \
                "playing" if env else "starting"
            cur = {"instance_id": x.instance_id, "run_id": f"{args.task}-a1", "date": date, "task_id": task_id,
                   "model": args.model, "mode": mode, "started_utc": x.created_at_utc, "status": status, "next": None,
                   "history": [], "attempt": 1, "day": 1, "pilot": args.pilot, "policy": "script",
                   "env": {"base": env["mcp_url"].removesuffix("/mcp"), "mcp_url": env["mcp_url"]} if env else None,
                   "day_file": str(file) if file else None, "result": None}
            if cur != seen:
                seq += 1
                write_atomic(args.current, (json.dumps(cur | {"seq": seq, "updated_utc": utc()}, indent=1) + "\n"
                                            ).encode())
                print(f"{utc()} {x.instance_id}: {status}", flush=True)
                seen = cur
            if status == "done":
                return 0
        time.sleep(2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("prebuild", help="Each day's news and hub traffic.")
    b.add_argument("tasks", nargs="+", metavar="TASK_ID")
    b.add_argument("--live", type=Path, required=True)
    b.add_argument("--inputs", type=Path, default=ROOT / "cache" / "inputs")
    b.add_argument("--assets", type=Path, help="A viewer folder whose radar frames count as there.")
    b.add_argument("--no-radar", dest="radar", action="store_false", help="Don't bake missing radar frames.")
    b.add_argument("--pack", type=Path)
    s = sub.add_parser("serve", help="Follow the runner's day and serve the viewer.")
    s.add_argument("--current", type=Path, help="The runner's current.json.")
    s.add_argument("--env", help="Follow this env's data/get instead (with --task).")
    s.add_argument("--task", help="The task id the env plays, with --env.")
    s.add_argument("--live", type=Path, required=True, help="Prebuilt days; the episodes are written under live/.")
    s.add_argument("--assets", type=Path, required=True, help="stage/, radar/ and twin/ (a viewer folder).")
    s.add_argument("--inputs", type=Path, help="Prebuild a day missing from --live from these raw inputs.")
    s.add_argument("--web", type=Path, default=ROOT / "web")
    s.add_argument("--pack", type=Path)
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--poll", type=float, default=3.0)
    f = sub.add_parser("feed", help="A fake runner and env playing recorded days back.")
    f.add_argument("days", nargs="+", type=Path, metavar="DAY_JSON")
    f.add_argument("--current", type=Path, required=True)
    f.add_argument("--port", type=int, default=8766)
    f.add_argument("--watch-seconds", type=float, default=75.0)
    f.add_argument("--start-delay", type=float, default=5.0)
    f.add_argument("--linger", type=float, default=10.0, help="Seconds the env stays up once done.")
    f.add_argument("--gap", type=float, default=30.0, help="Seconds between days.")
    f.add_argument("--hiccups", type=float, default=0.0, help="The share of data/get calls that fail.")
    f.add_argument("--hang", type=float, default=8.0, help="Seconds a failing data/get hangs, half the time.")
    f.add_argument("--restart-at", type=int, help="The first day's instance dies at this watch and starts again.")
    f.add_argument("--pilot", default="occ3-live-fake")
    f.add_argument("--model", default="openai/gpt-6-astra")
    f.add_argument("--cost-usd", type=float)
    a = sub.add_parser("attach", help="current.json for a local agent-env run of a bundle task.")
    a.add_argument("bundle", type=Path)
    a.add_argument("task", help="e.g. b6-20260715-0400-joint")
    a.add_argument("--current", type=Path, required=True)
    a.add_argument("--after", help="Only a run created at or after this (created_at_utc, 'YYYY-MM-DD HH:MM UTC').")
    a.add_argument("--model", default="script")
    a.add_argument("--pilot", default="occ3-live-local")
    args = p.parse_args(argv)
    if getattr(args, "live", None):
        args.live = args.live.expanduser().resolve()
    if args.command == "serve" and args.env and not args.task:
        p.error("--env needs --task")
    return {"prebuild": prebuild, "serve": serve, "feed": feed, "attach": attach}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
