"""A fake live server for the viewer's live mode (web/index.html?live=BASE): recorded control-center days from a viewer
folder played back as if live, each truncated at the watches its desks have decided so far, one every --watch-seconds
until they stand down, then the day done with its grade, an intermission naming the next day, and the next day. It
serves what CONTRACT.md's live server does: /live/episode.json (the episode so far with its `live` object),
/live/state.json ({seq, live}) and /live/current.json (the runner's), the traffic sidecar under /live/ with a new URL
at each watch, the folder's stage/, radar/ and twin/ beside them, and the viewer itself under /web/.
/live/control?seek=SECONDS&rate=X&pause=0|1 moves its clock, for tests.

usage: .venv/bin/python scripts/live_fake.py FOLDER EPISODE.json [EPISODE.json ...] [--port 8791] [--watch-seconds 20]
       then open http://127.0.0.1:8791/web/index.html?live=http://127.0.0.1:8791&stream
"""

import argparse
import copy
import functools
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


class Fake:
    """The schedule of the fake days on a clock that --rate speeds up and /live/control moves."""

    def __init__(self, folder: Path, names: list[str], watch_seconds: float, start_seconds: float,
                 done_seconds: float, gap_seconds: float, spent0: float):
        self.folder, self.names = folder, names
        self.W, self.S, self.D, self.G = watch_seconds, start_seconds, done_seconds, gap_seconds
        self.spent0 = spent0
        self.lock = threading.Lock()
        self.cache: dict[str, dict] = {}
        self.t0, self.rate, self.paused, self.frozen = time.monotonic(), 1.0, False, 0.0
        self.epoch = datetime.now(UTC)
        self.history: list[dict] = []
        self.memo: tuple | None = None

    def ep(self, i: int) -> dict:
        name = self.names[i % len(self.names)]
        if name not in self.cache:
            self.cache[name] = json.loads((self.folder / "episodes" / name).read_text())
        return self.cache[name]

    def clock(self) -> float:
        return self.frozen if self.paused else self.frozen + (time.monotonic() - self.t0) * self.rate

    def control(self, q: dict) -> dict:
        with self.lock:
            now = self.clock()
            if "seek" in q:
                now = float(q["seek"][0])
            if "rate" in q:
                self.rate = float(q["rate"][0])
            if "pause" in q:
                self.paused = q["pause"][0] == "1"
            self.frozen, self.t0 = now, time.monotonic()
            self.memo = None
        return {"clock": now, "rate": self.rate, "paused": self.paused, **self.where(now)}

    def length(self, i: int) -> tuple[float, int]:
        """A day's wall seconds from its start to done, and the watch its desks stand down at."""
        ep = self.ep(i)
        stand = max((a.get("watch", 0) for a in (ep["occ"].get("absent") or {}).values()), default=len(ep["watches"]))
        return self.S + (stand + 1) * self.W, stand

    def where(self, now: float) -> dict:
        """Which day the clock is in and how far: {day, phase: starting|playing|done|intermission, k (watches
        decided), into (seconds into the day), frac (of the watch in progress)}."""
        i, at = 0, 0.0
        while True:
            n, stand = self.length(i)
            if now < at + n + self.D + self.G:
                into = now - at
                if into < self.S:
                    return {"day": i, "phase": "starting", "k": 0, "into": into, "frac": 0.0}
                if into < n:
                    k = int((into - self.S) // self.W)
                    return {"day": i, "phase": "playing", "k": k, "into": into,
                            "frac": (into - self.S - k * self.W) / self.W}
                return {"day": i, "phase": "done" if into < n + self.D else "intermission", "k": None, "into": into,
                        "frac": 1.0}
            at += n + self.D + self.G
            i += 1

    def day_start(self, i: int) -> float:
        return sum(self.length(j)[0] + self.D + self.G for j in range(i))

    def state(self) -> tuple[dict, dict]:
        """The episode so far and its `live` object, as CONTRACT.md's live server serves them."""
        with self.lock:
            now = self.clock()
            w = self.where(now)
            key = (w["day"], w["phase"], w["k"], round(w["frac"] * 20))
            if self.memo and self.memo[0] == key:
                return self.memo[1], self.memo[2]
            ep = self.ep(w["day"])
            ws = sorted(ep["watches"], key=lambda x: x["t"])
            done = w["phase"] in ("done", "intermission")
            k = len(ws) if done else w["k"]
            out = truncate(ep, k, w["frac"])
            stem = ep["stage"]["traffic"].removesuffix(".stage.json.gz")
            out["stage"] = {**ep["stage"], "traffic": ep["stage"]["traffic"] if done else f"{stem}.stage-{k}.json.gz"}
            seq = int(self.day_start(w["day"]) * 10) + (k + 1) * 100 + round(w["frac"] * 20)
            started = self.epoch + timedelta(seconds=self.day_start(w["day"]) / self.rate)
            run = lambda j: f"{self.ep(j)['task']['task_id']}-joint-a1-d{j}"  # noqa: E731
            history = [{"date": self.ep(j)["task"]["date"], "task_id": self.ep(j)["task"]["task_id"], "run_id": run(j),
                        "instance_id": f"fake-{j}", "outcome": "scored", "reward": self.ep(j)["reward"],
                        "reward_L": self.ep(j)["occ"]["grade"].get("reward_L"), "attempts": 1,
                        "cost_usd": self.ep(j)["record"]["cost_usd"],
                        "wall_seconds": self.ep(j)["record"]["wall_seconds"]}
                       for j in range(w["day"] + (1 if done else 0))]
            nxt = self.ep(w["day"] + 1)
            starts = self.epoch + timedelta(seconds=self.day_start(w["day"] + 1) / self.rate)
            span = max(1.0, self.length(w["day"])[0] - self.S)
            share = 1.0 if done else min(1.0, max(0.0, w["into"] - self.S) / span)
            t_open = ws[min(k, len(ws) - 1)]["t"]
            last = max(e[x] for e in ep["executed"] for x in ("dep", "arr") if e.get(x) is not None)
            cost = ws[k - 1]["est_cost"] if 0 < k <= len(ws) else ws[0]["est_cost"]
            g = ep["occ"]["grade"]
            live = {"status": w["phase"], "seq": seq, "run_id": run(w["day"]), "task_id": ep["task"]["task_id"],
                    "date": ep["task"]["date"], "instance_id": f"fake-{w['day']}", "model": ep["model"],
                    "model_label": ep.get("model_label"), "mode": "joint", "pilot": "fake-live",
                    "watch": min(k, len(ws) - 1), "watches": len(ws), "t": t_open,
                    "decided_until": last if done else t_open,
                    "turn": None if done else turn(ep, k, w["frac"]),
                    "desks": desks(ep, ws, k, w["frac"], datetime.now(UTC), done),
                    "feed": feed(ep, ws, k, w["frac"], datetime.now(UTC)),
                    "projected": {"total": cost},
                    "next_watch_at": None if done else (datetime.now(UTC) + timedelta(
                        seconds=(1 - w["frac"]) * self.W / self.rate)).isoformat(timespec="seconds"),
                    "watch_wall_s": self.W / self.rate,
                    "started_utc": started.isoformat(timespec="seconds"),
                    "updated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                    "wall_seconds": round(ep["record"]["wall_seconds"] if done else w["into"] / self.rate, 1),
                    "spent_usd": round(share * ep["record"]["cost_usd"], 2),
                    "result": {"reward": ep["reward"], "reward_L": g.get("reward_L"), "cost": g["cost"],
                               "best": g["best"], "bound": g["bound"], "B0": g["B0"],
                               "cost_usd": ep["record"]["cost_usd"],
                               "wall_seconds": ep["record"]["wall_seconds"]} if done else None,
                    "exact": True, "mismatches": 0,
                    "next": {"date": nxt["task"]["date"], "task_id": nxt["task"]["task_id"],
                             "starts_utc": starts.isoformat(timespec="seconds")}
                    if w["phase"] == "intermission" else None,
                    "history": history, "error": None}
            out["live"] = live
            self.memo = (key, out, {"seq": seq, "live": live})
            return out, self.memo[2]


def truncate(ep: dict, k: int, frac: float = 0.0) -> dict:
    """The episode as the live server builds it with watch ``k`` open and ``frac`` of it gone: the watches closed so
    far and the open one, the room's events so far (the open watch's held back by its later `shown`), the flights as
    the latest projection has them; the grade once every watch is closed."""
    ws = sorted(ep["watches"], key=lambda x: x["t"])
    if k >= len(ws):
        return copy.copy(ep)
    until = ws[k]["t"]
    out = copy.copy(ep)
    out["watches"] = ws[:k] + [{key: v for key, v in ws[k].items() if key != "est_cost"} | {"changes": []}]
    out["steps"] = so_far([s for s in ep["steps"] if s.get("watch", 0) <= k], lambda s: s.get("watch", 0) == k, frac)
    out["news"] = [n for n in ep.get("news") or [] if n["at"] <= until]
    proj = {r["id"]: r for r in ws[k - 1]["projected"]} if k else {}

    def final(e):
        return (e["status"] == "flown" and e.get("dep") is not None and e["dep"] < until) or \
            (e["status"] != "flown" and e.get("decided") is not None and e["decided"] < until)
    out["executed"] = [e if final(e) or e["id"] not in proj else {**e, **proj[e["id"]]} for e in ep["executed"]]
    occ = copy.copy(ep["occ"])
    occ["turns"] = so_far([u for u in occ["turns"] if u["watch"] <= k], lambda u: u["watch"] == k, frac)
    occ["pages"] = [p for p in occ["pages"] if p["watch"] < k]
    occ["decisions"] = so_far([d for d in occ["decisions"] if d["t"] <= until], lambda d: d["t"] == until, frac)
    occ["notes"] = [n for n in occ.get("notes") or [] if n["t"] < until]
    reqs = []
    for r in so_far([r for r in occ["requests"] if r["sent"] <= until], lambda r: r["sent"] == until, frac):
        if r.get("replied") is None or r["replied"] >= until:
            r = {**r, "status": "open", "reply": None, "replied": None, "reply_text": None, "answered": None,
                 "actions": []}
        reqs.append(r)
    occ["requests"] = reqs
    occ["grade"] = None
    occ["absent"] = {d: a for d, a in (occ.get("absent") or {}).items() if a["t"] < until}
    out["occ"] = occ
    out["reward"] = out["reward_L"] = None
    out["end_reason"] = None
    out["audit"] = None
    out["record"] = {**ep["record"], "cost_usd": None, "wall_seconds": None, "desks": None}
    out["cost"] = {**ep["cost"], "total": ws[k - 1]["est_cost"] if k else ws[0]["est_cost"]}
    return out


def so_far(items: list, open_: Callable[[dict], bool], frac: float) -> list:
    """``items`` with those of the open watch cut to the first ``frac`` of them."""
    opened = [x for x in items if open_(x)]
    keep = {id(x) for x in opened[: round(len(opened) * frac)]}
    return [x for x in items if not open_(x) or id(x) in keep]


def turn(ep: dict, k: int, frac: float) -> dict | None:
    turns = [u for u in ep["occ"]["turns"] if u["watch"] == k]
    if not turns:
        return None
    u = turns[min(len(turns) - 1, int(frac * len(turns)))]
    return {"n": u["n"], "desk": u["desk"], "kind": u["kind"], "calls": u["calls"]}


def events(ep: dict, t: float) -> list[tuple]:
    """The room's events of the watch at ``t``: (minute shown, desk, kind, text, request, flight)."""
    out = []
    for r in ep["occ"]["requests"]:
        if r["sent"] == t:
            fl = f" f{r['flight']}" if r["flight"] is not None else ""
            text = f"{r['id']} {r['type']}{fl} to {r['receiver']}: {(r.get('text') or '')[:80]}"
            out.append((r["shown"], r["sender"], "request", text, r["id"], r["flight"]))
        if r.get("replied") == t and r.get("answered") is not None:
            text = f"{r['id']} {r['reply']}: {(r.get('reply_text') or '')[:80]}"
            out.append((r["answered"], r["receiver"], "reply", text, r["id"], r["flight"]))
    for d in ep["occ"]["decisions"]:
        if d["t"] == t:
            text = f"{d['action']} f{d['flight']}{' ' + str(d['detail']) if d.get('detail') else ''}"
            out.append((d["shown"], d["desk"], "decision", text, d.get("ref"), d["flight"]))
    return sorted(out, key=lambda e: e[0])


def feed(ep: dict, ws: list[dict], k: int, frac: float, now: datetime) -> list[dict]:
    out = []
    for j in range(max(0, k - 3), min(k, len(ws) - 1) + 1):
        ev = events(ep, ws[j]["t"])
        if j == k:
            ev = ev[: round(len(ev) * frac)]
        for n, (_, desk, kind, text, req, flight) in enumerate(ev):
            at = now - timedelta(seconds=(k - j) * 60 + (len(ev) - n) * 4)
            out.append({"seq": j * 1000 + n, "at": at.isoformat(timespec="seconds"), "watch": j, "t": ws[j]["t"],
                        "desk": desk, "kind": kind, "text": text, "request": req, "flight": flight})
    return out[-60:]


def desks(ep: dict, ws: list[dict], k: int, frac: float, now: datetime, done: bool) -> list[dict]:
    """Each desk's state and latest call of the watch in progress, as far as ``frac`` of it has gone."""
    order = [d for d in ("crew", "customer", "ops") if d in ep["occ"]["desks"]]
    if done or k >= len(ws):
        return [{"role": d, "state": "off", "off": "stand_down", "last": None, "at": None} for d in order]
    steps = [s for s in ep["steps"] if s.get("watch") == k]
    steps = steps[: round(len(steps) * frac)]
    u = turn(ep, k, frac)
    out = []
    for d in order:
        mine = [s for s in steps if s.get("desk") == d]
        last = mine[-1] if mine else None
        out.append({"role": d, "state": "turn" if u and u["desk"] == d else "waiting", "off": None,
                    "last": {"seq": len(mine), "tool": last["tool"], "t": last["t"],
                             "text": f"{last['tool']} {json.dumps(last.get('args') or {})[:60]}"} if last else None,
                    "at": (now - timedelta(seconds=5)).isoformat(timespec="seconds") if last else None})
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, fake: Fake, web: Path, **kw):
        self.fake, self.web = fake, web
        super().__init__(*args, **kw)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        p = unquote(url.path)
        if p == "/live/episode.json":
            return self.send_json(self.fake.state()[0])
        if p in ("/live/state.json", "/live/state"):
            return self.send_json(self.fake.state()[1])
        if p == "/live/current.json":
            live = self.fake.state()[1]["live"]
            return self.send_json({k: live.get(k) for k in ("instance_id", "run_id", "date", "task_id", "model", "mode",
                                                            "started_utc", "status", "next", "history")})
        if p == "/live/control":
            return self.send_json(self.fake.control(parse_qs(url.query)))
        return super().do_GET()

    def send_json(self, doc: dict) -> None:
        body = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def translate_path(self, path: str) -> str:
        p = unquote(path.split("?", 1)[0].split("#", 1)[0])
        for prefix in ("/viewer/", "/web/"):
            if p.startswith(prefix):
                return str(self.web / p[len(prefix):])
        if m := re.match(r"^/live/([^/]+)\.stage(-\d+)?\.json\.gz$", p):
            return str(self.fake.folder / "episodes" / f"{m[1]}.stage.json.gz")
        for prefix in ("stage", "radar", "twin", "thumbs", "episodes"):
            if p.startswith(f"/{prefix}/"):
                return str(self.fake.folder / p[1:])
        return str(self.fake.folder / "__missing__")

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("folder", type=Path, help="A viewer folder: episodes/, stage/, radar/.")
    p.add_argument("episodes", nargs="+", help="Episode files under FOLDER/episodes, played one after another.")
    p.add_argument("--port", type=int, default=8791)
    p.add_argument("--viewer-port", type=int, help="Also serve on this port, another origin, for a cross-origin test.")
    p.add_argument("--web", type=Path, default=ROOT / "web")
    p.add_argument("--watch-seconds", type=float, default=20)
    p.add_argument("--start-seconds", type=float, default=10, help="Seconds a day starts with nothing decided.")
    p.add_argument("--done-seconds", type=float, default=60)
    p.add_argument("--gap-seconds", type=float, default=120)
    p.add_argument("--seek", type=float, default=0, help="Start the clock this many seconds in.")
    p.add_argument("--rate", type=float, default=1)
    p.add_argument("--spent", type=float, default=0, help="Model spend before the first day, in dollars.")
    args = p.parse_args(argv)
    fake = Fake(args.folder.expanduser().resolve(), args.episodes, args.watch_seconds, args.start_seconds,
                args.done_seconds, args.gap_seconds, args.spent)
    fake.control({"seek": [str(args.seek)], "rate": [str(args.rate)]})
    handler = functools.partial(Handler, fake=fake, web=args.web.resolve())
    servers = [ThreadingHTTPServer(("127.0.0.1", port), handler) for port in (args.port, args.viewer_port) if port]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    print(f"live at http://127.0.0.1:{args.port}/live/episode.json; open "
          f"http://127.0.0.1:{servers[-1].server_port}/web/index.html?live=http://127.0.0.1:{args.port}&stream",
          flush=True)
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
