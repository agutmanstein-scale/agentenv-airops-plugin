"""Film an episode on the viewer: headless Chrome over the DevTools protocol drives window.airops frame by frame, each
frame a JPEG piped to ffmpeg for an H.264 MP4, optionally an animated WebP for a README. --director cuts the day into
shots on the 3D airport (direct()); without it the clock sweeps from watch to watch. --shots writes stills, --thumb a
640x360 list thumbnail, --still one frame without cards or tags.

usage: .venv/bin/python scripts/record.py --episode cache/episodes/NAME.json --director --out cache/films/NAME.mp4 \
           [--webp assets/NAME.webp] [--seconds 66]
       ... --episode E.json --shots cache/shots [--director]
       ... --episode E.json --thumb cache/thumbs/STEM.jpg
       ... --episode E.json --size 1920x640 --scale 1.5 --still assets/airops-banner.jpg --pick push:0.6
"""

import argparse
import base64
import functools
import gzip
import io
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from PIL import Image
from websockets.sync.client import connect

from agentenv_airops.stage import cancelled_at

ROOT = Path(__file__).resolve().parents[1]
CHROMES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable",
           "chromium", "chromium-browser"]
ATTRIBUTION = ("Airport © OpenStreetMap contributors (ODbL) · imagery: USGS The National Map · basemap © OpenMapTiles "
               "© OpenStreetMap contributors via OpenFreeMap · radar: NEXRAD, Iowa Environmental Mesonet · "
               "BTS, FAA ATCSCC, NWS")
ANSWER_SECONDS = 180
QUICK = {"advance"}
RENDERER = ("(() => { const g = document.createElement('canvas').getContext('webgl2'); "
            "const e = g && g.getExtension('WEBGL_debug_renderer_info'); "
            "return g ? (e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'webgl2') : null; })()")
HUBS = {"BOS": "Boston Logan", "JFK": "New York JFK"}
FAST = {"harbour": 10, "tower": 10, "gate": 6, "push": 6, "taxi": 20, "takeoff": 4, "landing": 4}
"""The fastest clock each camera films at, in seconds of the day per second of film, so that nothing in frame zips."""
SECONDS = {"establish": 5.5, "faa": 6, "decide": 6, "flip": 5, "push": 4.5, "taxi": 4.5, "takeoff": 8, "landing": 7,
           "inbound": 7, "end": 7}
STRETCH = ("establish", "faa", "decide", "flip")
ROLLOUT = {"acted": 4.0, "quiet": 2.2}
"""Seconds of the rollout a watch gets: where the model acted or the FAA's news arrived, and where neither did."""
PLANNING = ("check_plan", "confirm_changes")
VERBS = {"cancel": "cancels", "hold": "holds", "restore": "restores", "release": "releases"}
LEAD = 1.6
SHOWN = 45
ROLL = 0.8
THUMB = (640, 360)
SUFFIX = re.compile(r" (International|Intl|Regional|Municipal|Metropolitan|County)?\s*"
                    r"(Airport|Field|Jetport|Airfield)$")


@dataclass(frozen=True)
class Shot:
    """One shot: a camera from minute ``t0`` for ``seconds`` at a clock of ``speed`` (seconds of the day per second,
    ramped from the first to the second), the cards it shows and its caption; ``at`` is the moment it is about and
    ``reach`` how near the weather brings a wide camera, in metres."""
    name: str
    camera: str
    t0: float
    seconds: float
    speed: tuple[float, float]
    key: str | None = None
    at: float | None = None
    cards: tuple[str, ...] = ("head", "tags")
    kicker: str = ""
    caption: str = ""
    reach: float | None = None

    def clock(self, fps: int) -> list[tuple[float, float]]:
        """(minute, rate) of each frame."""
        n = max(1, round(self.seconds * fps))
        s0, s1 = self.speed
        rates = [s0 + (s1 - s0) * i / max(1, n - 1) for i in range(n)]
        times = itertools.accumulate(rates[:-1], lambda t, r: t + r / fps / 60, initial=self.t0)
        return list(zip(times, rates, strict=True))

    @property
    def t1(self) -> float:
        return self.t0 + self.seconds * (self.speed[0] + self.speed[1]) / 2 / 60


def lead_in(at: float, seconds: float, speed: tuple[float, float], total: float) -> float:
    """The first minute of a shot ``total`` seconds long that reaches minute ``at`` after ``seconds``."""
    s0, s1 = speed
    return at - seconds * (s0 + (s1 - s0) * min(1.0, seconds / total) / 2) / 60


def direct(ep: Mapping, traffic: Mapping, events: Sequence[Mapping] | None = None,
           poses: Callable[[str, float, float], list] | None = None, seconds: float = 40) -> list[Shot]:
    """The film of a day about ``seconds`` long, in the order it happened: the hub across the water as the desk opens,
    the FAA's program arriving over a quiet field, a cancellation landing at its gate and on the board, a held flight
    finally pushing back, taxiing and taking off, then the day's score. ``events`` are the stage's movements
    (AirportStage.events(), else the sidecar's times) and ``poses(key, a, b)`` the stage's [[minute, phase, speed]]."""
    return Day(ep, traffic, events, poses).film(seconds)


class Day:
    """A day as the director reads it: its watches and decisions, the FAA's programs and the movements at the hub."""

    def __init__(self, ep: Mapping, traffic: Mapping, events: Sequence[Mapping] | None, poses):
        self.ep, self.traffic, self.task = ep, traffic, ep["task"]
        self.hub, self.span, self.window = self.task["hub"], traffic["span"], self.task["window"]
        self.where = HUBS.get(self.hub, self.hub)
        self.model = ep.get("model_label") or ep["model"]
        self.watches = sorted(ep["watches"], key=lambda w: w["t"])
        flights = {s["id"]: s["flight"] for s in ep["schedule"]}
        self.held = {flights[e["id"]]: e["not_before"] for e in ep["executed"] if e.get("held")}
        self.executed = {e["id"]: e for e in ep["executed"]}
        self.legs = {(g["flight"], g["kind"]): (a, v, g) for a in traffic["aircraft"] if a["task"]
                     for v in a["visits"] for g in (v["arr"], v["dep"]) if g}
        self.cities = {code: SUFFIX.sub("", a.get("name") or code) for code, a in self.task["airports"].items()}
        self.cities |= {g["other"]: g["city"] for a in traffic["aircraft"] for v in a["visits"]
                        for g in (v["arr"], v["dep"]) if g and g.get("city")}
        self.events = list(events) if events is not None else sidecar_events(traffic)
        self.poses = poses
        self.advisories = sorted((n for n in ep["news"] if n["kind"] == "advisory"
                                  and (n.get("data") or {}).get("element") == self.hub), key=lambda n: n["at"])

    def film(self, seconds: float) -> list[Shot]:
        """The beats that fit, most needed first, each the best of its options clear of the others, in time order."""
        first = self.establish()
        leaver = self.leaver()
        beats: list[list[Shot]] = []
        for options, extra in ((self.leave(leaver), False), (self.faa(first[-1].t1), False),
                               (self.decide(leaver), False), (self.inbound(leaver), True), (self.flip(), True),
                               (self.landing(), True)):
            beat = next((b for b in options if b[0].t0 >= first[-1].t1 and not any(overlap(b, c) for c in beats)),
                        None)
            if beat and not (extra and length([first, *beats, beat]) + SECONDS["end"] > seconds + 2):
                beats.append(beat)
        shots = [*first, *(s for b in sorted(beats, key=lambda b: b[0].t0) for s in b)]
        return stretch([*shots, self.end(max(s.t1 for s in shots))], seconds)

    def establish(self) -> list[Shot]:
        t = self.span[0]
        wx = next((w for w in reversed(self.traffic.get("weather") or []) if w["t"] <= t), None)
        reach = max(1600, wx["vis_sm"] * 1300) if wx and wx.get("vis_sm") is not None and wx["vis_sm"] < 3 else None
        return [Shot("establish", "harbour", t, SECONDS["establish"], (6, 6), reach=reach, cards=("head",),
                     kicker=f"{self.where} · {long_date(self.task['date'])} · {clock(t)}",
                     caption=f"{self.model} takes JetBlue's operations desk for a storm day")]

    def faa(self, after: float) -> list[list[Shot]]:
        """The watches at which the desk hears of the ground stop (else delay program) in force, after the desk
        opens first."""
        found = []
        for k, w in enumerate(self.watches):
            before = self.watches[k - 1]["t"] if k else -math.inf
            for rank, n in enumerate(self.programs(w["t"])):
                if n and before < n["at"] <= w["t"]:
                    found.append((rank, w["t"] <= self.span[0], w["t"], n))
        out = []
        for _, _, t, n in sorted(found, key=lambda f: f[:3]):
            speed, (what, text) = (6, 6), self.announce(n)
            out.append([Shot("faa", "tower", max(after, lead_in(t, LEAD, speed, SECONDS["faa"])), SECONDS["faa"], speed,
                             at=t, cards=("head", "desk", "map"), kicker=f"{clock(t)} · {what}", caption=text)])
        return out

    def announce(self, n: Mapping) -> tuple[str, str]:
        """The program an advisory puts in force, and what it does, in plain English."""
        d = n["data"]
        if d["kind"] == "gs":
            who = "JetBlue flights" if "JBU" in (d.get("flt_incl") or "") else "flights"
            return "Ground stop", f"The FAA stops {who} to {self.where} at their departure airports until " \
                                  f"{until(d['gs_period'][1])}"
        rate = d.get("program_rate") or []
        cap = (f"{min(rate)}–{max(rate)}" if min(rate) != max(rate) else str(rate[0])) if rate else "fewer"
        return "Ground delay program", f"The FAA holds arrivals at {self.where} to {cap} an hour until " \
                                       f"{until(d['program_period'][1])}"

    def rollout(self, seconds: float = 55) -> list[Shot]:
        """The day watch by watch from the tower, as PortSim's clip runs its week: each watch opens still on the
        desk, the model's planning calls, what it did or the FAA's news, then the field moves on; then the score,
        the watches shortened alike to fit about ``seconds``."""
        shots, n = self.establish(), len(self.watches)
        for k, w in enumerate(self.watches):
            calls = Counter(s["tool"] for s in self.ep.get("steps", [])
                            if s.get("watch") == k and s["tool"] in PLANNING)
            tools = " · ".join(f"{tool} ×{c}" if c > 1 else tool for tool, c in calls.items())
            changes = [c for c in w["changes"] if c["action"] in VERBS]
            before = self.watches[k - 1]["t"] if k else -math.inf
            news = next((x for x in self.programs(w["t"]) if x and before < x["at"] <= w["t"]), None)
            caption = self.said(changes) if changes else self.announce(news)[1] if news else \
                f"{self.model} keeps its plan"
            shots.append(Shot("watch", "tower", w["t"], ROLLOUT["acted" if changes or news else "quiet"],
                              (0, FAST["tower"]), at=w["t"], cards=("head", "desk", "board", "tags"),
                              kicker=" · ".join(x for x in (clock(w["t"]), f"watch {k + 1} of {n}", tools) if x),
                              caption=caption))
        fit = min(1.0, (seconds - SECONDS["establish"] - SECONDS["end"]) / sum(s.seconds for s in shots[1:]))
        shots[1:] = [replace(s, seconds=round(s.seconds * fit, 2)) for s in shots[1:]]
        return [*shots, self.end(shots[-1].t1)]

    def programs(self, t: float) -> tuple[dict | None, dict | None]:
        """The hub's ground stop and delay program in force at t as issued by then (web/model.js programsAt)."""
        known = [n for n in self.advisories if n["at"] <= t]

        def live(kind: str, period: str, cnx: str) -> dict | None:
            n = next((x for x in reversed(known) if x["data"]["kind"] == kind and x["data"].get(period)), None)
            if n is None or any(x["data"]["kind"] == cnx and x["at"] >= n["at"] for x in known):
                return None
            a, b = n["data"][period]
            return n if a <= t + 1 and t < b else None

        return live("gs", "gs_period", "gs_cnx"), live("gdp", "program_period", "gdp_cnx")

    def decide(self, leaver: tuple | None) -> list[list[Shot]]:
        """A watch's decisions landing, the desk and the board flipping as it opens over a changed flight's row: at
        the gate of a cancelled flight whose tag already shows it, else of the flight the film follows out, else of a
        changed flight its tag shows, else of any, else at JetBlue's terminal; tags only where they show the flight."""
        speed, found = (4, 4), []
        follow = leaver[2]["flight"] if leaver else None
        for k, w in enumerate(self.watches):
            changes = [c for c in w["changes"] if c["action"] in ("cancel", "hold")]
            if w["t"] <= self.span[0] or not changes:
                continue
            base = Shot("decide", "gate", lead_in(w["t"], LEAD, speed, SECONDS["decide"]), SECONDS["decide"], speed,
                        at=w["t"], cards=("head", "desk", "board"),
                        kicker=f"{clock(w['t'])} · watch {k + 1} of {len(self.watches)}")
            listed, story = self.board(base.t1), self.window[0] <= w["t"] <= self.window[1] + 180
            for c in changes:
                if c["flight"] not in listed:
                    continue
                a, _, g = self.legs.get((c["flight"], self.kind(c)), (None, None, None))
                a = a if a is not None and parked(a, base.t0, base.t1) is not None else None
                shown = a is not None and tag_at(a, base.t1) is g
                shot = replace(base, key=a and a["key"], caption=self.said(changes, c),
                               cards=(*base.cards, "tags") if shown else base.cards)
                found.append(((shown and c["action"] == "cancel", c["flight"] == follow, shown, a is not None, story,
                               len(changes), -w["t"]), shot))
        return [[s] for _, s in sorted(found, key=lambda f: f[0], reverse=True)]

    def board(self, t: float, rows: int = 8) -> set[str]:
        """The flights the departures and arrivals board lists at t (web/model.js boardAt), as far as its rows go."""
        k = max((i for i, w in enumerate(self.watches) if w["t"] <= t), default=-1)
        last = self.watches[-1]["t"] if self.watches else -math.inf
        plan = {r["id"]: r for r in self.watches[k].get("projected") or []} if 0 <= k and t < last else {}
        listed: dict[str, list] = {"dep": [], "arr": []}
        for s in self.ep["schedule"]:
            kind = "dep" if s["orig"] == self.hub else "arr" if s["dest"] == self.hub else None
            if kind is None or (ex := self.executed.get(s["id"])) is None:
                continue
            sched, at = (s["sched_dep"], ex["dep"]) if kind == "dep" else (s["sched_arr"], ex["arr"])
            done = ex["status"] == "flown" and at <= t
            decided = ex["status"] != "flown" and ex.get("decided") is not None and ex["decided"] <= t
            r = ex if done or decided or t >= last or k < 0 else plan.get(s["id"], ex)
            if (done and t - at > 12) or ((decided or r["status"] == "cancelled") and sched < t - 30):
                continue
            listed[kind].append((sched, s["flight"]))
        return {f for found in listed.values() for _, f in sorted(found)[:rows]}

    def flip(self) -> list[list[Shot]]:
        """A cancelled flight's tag turning red at its gate after the watch that cancelled it, in the story's hours
        and departures first."""
        speed, found = (4, 4), []
        for w in self.watches:
            for c in w["changes"]:
                if c["action"] != "cancel" or (entry := self.legs.get((c["flight"], self.kind(c)))) is None:
                    continue
                a, _, g = entry
                at = cancelled_at(g) - SHOWN
                shot = Shot("flip", "gate", lead_in(at, LEAD, speed, SECONDS["flip"]), SECONDS["flip"], speed,
                            key=a["key"], at=at, cards=("head", "board", "tags"), kicker=clock(at),
                            caption=f"{c['flight']} {'to' if self.kind(c) == 'dep' else 'from'} {g['city']} stays "
                                    f"at the gate: {self.model} cancelled it at {clock(w['t'])}")
                if (w["t"] < shot.t0 and self.span[0] < shot.t0 and parked(a, shot.t0, shot.t1) is not None
                        and tag_at(a, shot.t1) is g and tag_at(a, shot.t0) is not g):
                    found.append(((self.window[0] <= at <= self.window[1] + 180, self.kind(c) == "dep", -at), shot))
        return [[s] for _, s in sorted(found, key=lambda f: f[0], reverse=True)]

    def said(self, changes: Sequence[Mapping], first: Mapping | None = None) -> str:
        first = first or changes[0]
        rest = [c for c in changes if c is not first]
        more = f" and {self.change(rest[0], verb=rest[0]['action'] != first['action'])}" if len(rest) == 1 else \
            f" and changes {len(rest)} more flights" if rest else ""
        return f"{self.model} {self.change(first)}{more}"

    def change(self, c: Mapping, verb: bool = True) -> str:
        to = "to" if self.kind(c) == "dep" else "from"
        code = c["dest"] if to == "to" else c["orig"]
        what = f"{VERBS[c['action']]} " if verb else ""
        return f"{what}{c['flight']} {to} {self.cities.get(code, code)}" + \
            (f" until {clock(c['not_before'])}" if c["action"] == "hold" else "")

    def kind(self, c: Mapping) -> str | None:
        return "dep" if c["orig"] == self.hub else "arr" if c["dest"] == self.hub else None

    def leaver(self) -> tuple | None:
        """The departure to watch leave, with its pushback and liftoff: the best featured held one, else the first
        takeoff after a stop, else the longest delayed, else the task's held and late ones in the story's hours."""
        featured = [(f["reason"], f["key"], f["flight"]) for f in self.traffic.get("featured", [])]
        deps = sorted(((a, g) for (_, kind), (a, _, g) in self.legs.items() if kind == "dep" and g["out"] is not None),
                      key=lambda e: (e[1]["tone"] != "hold", not self.window[0] <= e[1]["out"] <= self.window[1] + 180,
                                     e[1]["sched"] - e[1]["out"]))
        ranked = [f for r in ("held", "first_after_stop", "delayed") for f in featured if f[0] == r]
        ranked += [("held" if g["tone"] == "hold" else "delayed", a["key"], g["flight"]) for a, g in deps]
        for reason, key, flight in ranked:
            entry = self.legs.get((flight, "dep"))
            if not entry or entry[0]["key"] != key:
                continue
            push, off = (self.event(key, k, flight) for k in ("pushback", "takeoff"))
            if push is not None and off is not None and self.span[0] <= push < off <= self.span[1]:
                return (*entry, reason, push, off)
        return None

    def leave(self, leaver: tuple | None) -> list[list[Shot]]:
        """The departure pushing back at its gate, taxiing past and taking off, as in the prototype's clip."""
        if leaver is None:
            return []
        a, _, g, reason, push, off = leaver
        if reason == "first_after_stop":
            text = f"{g['flight']} to {g['city']}, the first JetBlue departure after the stop"
        elif reason == "held":
            text = f"{g['flight']} to {g['city']}, held until {clock(self.held.get(g['flight'], push))}, pushes back"
        else:
            text = f"{g['flight']} to {g['city']} pushes back {late(g['out'] - g['sched'])} late"
        speed = (FAST["push"],) * 2
        p = Shot("push", "push", lead_in(push, 1.0, speed, SECONDS["push"]), SECONDS["push"], speed, key=a["key"],
                 at=push, kicker=clock(push), caption=text)
        span = SECONDS["taxi"] * FAST["taxi"] / 60
        t0 = self.taxiing(a["key"], max(p.t1 + 0.5, push + 1.5), off - ROLL - 0.3, span)
        taxi = Shot("taxi", "taxi", t0, SECONDS["taxi"], (FAST["taxi"],) * 2, key=a["key"], at=t0 + span / 2)
        ramp = (FAST["takeoff"], 1.5)
        up = Shot("takeoff", "takeoff", lead_in(off, 5.5, ramp, SECONDS["takeoff"]), SECONDS["takeoff"], ramp,
                  key=a["key"], at=off)
        return [[p, taxi, up]]

    def taxiing(self, key: str, a: float, b: float, span: float) -> float:
        """The start of the ``span`` minutes in [a, b] in which the aircraft taxis furthest, the latest of equals."""
        if b - a <= span or self.poses is None:
            return max(a, b - span)
        moved = [(t, (v or 0) * 5 if phase in ("taxi", "lineup") else 0) for t, phase, v in self.poses(key, a, b)]
        n = max(1, round(span * 12))
        return max((sum(m for _, m in moved[i:i + n]), moved[i][0]) for i in range(max(1, len(moved) - n)))[1]

    def inbound(self, leaver: tuple | None) -> list[list[Shot]]:
        """The departure's aircraft landing on its way in, from beside the runway."""
        if leaver is None:
            return []
        a, _, g, _, push, _ = leaver
        arrs = [v["arr"] for v in a["visits"] if v["arr"] and v["arr"]["status"] == "flown" and v["arr"]["tag"]]
        for arr in sorted(arrs, key=lambda x: -(x["in"] or 0)):
            on = self.event(a["key"], "landing", arr["flight"])
            if on is not None and on < push - 10:
                text = f"{arr['flight']} from {arr['city']} lands {late(arr['in'] - arr['sched'])} late with the " \
                       f"aircraft for {g['flight']}"
                return [self.touchdown("inbound", a["key"], on, text)]
        return []

    def landing(self) -> list[list[Shot]]:
        """The first JetBlue landings after a stop, then the featured late arrivals, from beside the runway."""
        out = []
        for reason in ("first_after_stop", "delayed"):
            for f in self.traffic.get("featured", []):
                entry = self.legs.get((f["flight"], "arr"))
                if f["reason"] != reason or not entry or entry[0]["key"] != f["key"]:
                    continue
                on = self.event(f["key"], "landing", f["flight"])
                if on is None or not self.span[0] < on - 2 <= self.span[1]:
                    continue
                g = entry[2]
                text = (f"{g['flight']} from {g['city']}, the first JetBlue landing after the stop"
                        if reason == "first_after_stop" else
                        f"{g['flight']} from {g['city']} lands {late(g['in'] - g['sched'])} late")
                out.append(self.touchdown("landing", f["key"], on, text))
        return out

    def touchdown(self, name: str, key: str, on: float, text: str) -> list[Shot]:
        ramp = (FAST["landing"], 1.5)
        return [Shot(name, "landing", lead_in(on, 4.5, ramp, SECONDS["landing"]), SECONDS["landing"], ramp, key=key,
                     at=on, kicker=clock(on), caption=text)]

    def end(self, after: float) -> Shot:
        return Shot("end", "harbour", after + 5, SECONDS["end"], (4, 4), cards=("end",))

    def event(self, key: str, kind: str, flight: str) -> float | None:
        return next((e["t"] for e in self.events if e["key"] == key and e["kind"] == kind and e["flight"] == flight),
                    None)


def sidecar_events(traffic: Mapping) -> list[dict]:
    """The movements as the sidecar times them, for a director without the stage."""
    out = []
    for a in traffic["aircraft"]:
        for v in a["visits"]:
            for g, moves in ((v["arr"], (("landing", "on"), ("gate_in", "in"))),
                             (v["dep"], (("pushback", "out"), ("takeoff", "off")))):
                for kind, field in moves if g and g["status"] == "flown" else ():
                    if g[field] is not None:
                        out.append({"t": g[field], "key": a["key"], "kind": kind, "flight": g["flight"]})
    return sorted(out, key=lambda e: e["t"])


def parked(a: Mapping, t0: float, t1: float) -> dict | None:
    """The aircraft's visit at a stand over the whole of [t0, t1], if it has one."""
    return next((v for v in a["visits"] if (v["from"] is None or v["from"] <= t0)
                 and (v["to"] is None or v["to"] >= t1)), None)


def tag_at(a: Mapping, t: float) -> dict | None:
    """The leg a task aircraft's tag shows at t (web/stage/stage.js legAt)."""
    legs = [g for v in a["visits"] for g in (v["arr"], v["dep"]) if g and g.get("tag")]

    def key(g):
        if g["status"] == "cancelled":
            return cancelled_at(g)
        return g["out"] if g["kind"] == "dep" else (g["in"] or g["on"])

    for g in legs:
        if g["status"] == "flown" and (g["out"] - 20 <= t <= (g["off"] or g["out"]) + 3 if g["kind"] == "dep" else
                                       (g["on"] or g["in"]) - 6 <= t <= (g["in"] or g["on"]) + 20):
            return g
    if found := next((g for g in legs if g["status"] == "cancelled" and t >= (g.get("decided") or -math.inf)
                      and abs(t - cancelled_at(g)) <= SHOWN), None):
        return found
    return min((g for g in legs if g["status"] == "flown" and key(g) >= t), key=key, default=None)


def overlap(a: Sequence[Shot], b: Sequence[Shot]) -> bool:
    return a[0].t0 < max(s.t1 for s in b) and b[0].t0 < max(s.t1 for s in a)


def length(beats: Sequence[Sequence[Shot]]) -> float:
    return sum(s.seconds for b in beats for s in b)


def stretch(shots: list[Shot], seconds: float) -> list[Shot]:
    """Longer still shots to bring the film to ``seconds``, by at most a third each."""
    still = sum(s.seconds for s in shots if s.name in STRETCH)
    k = min(4 / 3, max(1.0, 1 + (seconds - sum(s.seconds for s in shots)) / still)) if still else 1.0
    return [replace(s, seconds=round(s.seconds * k, 2)) if s.name in STRETCH else s for s in shots]


def until(m: float) -> str:
    """The end of an FAA program, whose periods close a minute before the hour."""
    return clock(m + 1 if (m + 1) % 60 == 0 else m)


def clock(m: float) -> str:
    m = math.floor(m + 1e-6)
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def late(minutes: float) -> str:
    m = round(minutes)
    return f"{m} min" if m < 60 else f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%a} {d.day} {d:%b %Y}"


FILM_CSS = """
html.film .card { display: none !important; }
html.film[data-cards~="head"] .card.head, html.film[data-cards~="desk"] .card.desk { display: block !important; }
html.film[data-cards~="board"] .card.board { display: flex !important; }
html.film .timeline, html.film .credit, html.film .follow-chip, html.film .radar-key,
  html.film .inset-caption { display: none !important; }
html.film:not([data-cards~="map"]) .view.corner { visibility: hidden; }
html.film:not([data-cards~="tags"]) .ao-tags { visibility: hidden; }
html.film .view.corner { bottom: 14px; }
html.film .cards.right { bottom: auto; }
#film-cap { position: absolute; z-index: 9; left: 50%; bottom: 46px; transform: translateX(-50%); max-width: 1180px;
  width: max-content; padding: 11px 26px 13px; border-radius: 12px; text-align: center;
  background: rgba(10, 22, 32, 0.8); border: 1px solid rgba(190, 214, 224, 0.18);
  box-shadow: 0 8px 30px rgba(4, 16, 26, 0.4); backdrop-filter: blur(8px); }
#film-cap small { display: block; margin-bottom: 3px; font-size: 13px; font-weight: 650; letter-spacing: 0.08em;
  text-transform: uppercase; color: #9fc2d2; }
#film-cap b { display: block; font-size: 27px; font-weight: 600; line-height: 1.25; color: #fff; }
#film-end { position: absolute; inset: 0; z-index: 9; display: none; flex-direction: column; align-items: center;
  justify-content: center;
  background: radial-gradient(ellipse at center, rgba(8, 18, 28, 0.45), rgba(8, 18, 28, 0.8)); }
html.film[data-cards~="end"] #film-end { display: flex; }
html.film[data-cards~="end"] .view-stage canvas { filter: blur(3px) brightness(0.85); }
#film-end h1 { margin: 0; font-size: 60px; font-weight: 750; letter-spacing: -0.01em; }
#film-end h2 { margin: 2px 0 6px; font-size: 25px; font-weight: 450; color: #d6e4ea; }
#film-end > p { margin: 0 0 24px; font-size: 18px; color: #b0c2cb; }
#film-end .card.score { display: block !important; width: 560px; zoom: 1.3; }
#film-end .credit-line { margin: 26px 0 0; max-width: 1300px; font-size: 12px; text-align: center;
  color: rgba(238, 243, 246, 0.72); }
"""

FILM_JS = """
(async () => {
  const S = window.airops.stage, root = document.querySelector(".ep"), html = document.documentElement;
  const cams = await import(new URL("./stage/cameras.js", location.href));
  const style = document.createElement("style");
  style.textContent = __CSS__;
  document.head.appendChild(style);
  html.classList.add("film");
  const cap = Object.assign(document.createElement("div"), { id: "film-cap" });
  cap.innerHTML = "<small></small><b></b>";
  const end = Object.assign(document.createElement("div"), { id: "film-end" });
  end.innerHTML = "<h1>AirOpsEnv v1</h1><h2>Recover an airline's storm day at JFK or Boston</h2><p></p><div></div>"
    + "<div class='credit-line'></div>";
  root.append(cap, end);
  const D = Math.PI / 180, dir = (h) => [Math.sin(h * D), Math.cos(h * D)];
  const add = (a, b, k = 1) => [a[0] + b[0] * k, a[1] + b[1] * k];
  const mix = (a, b, k) => a.map((v, i) => v + (b[i] - v) * k);
  const standOf = (key, t) => {
    const stays = S.plan.tracks.get(key)?.stays || [];
    return (stays.find((x) => x.a <= t * 60 && t * 60 < x.b) || stays[0])?.stand ?? null;
  };
  const away = (p, h, home) => {
    const l = dir(h - 90);
    return (p[0] - home[0]) * l[0] + (p[1] - home[1]) * l[1] >= 0 ? l : [-l[0], -l[1]];
  };
  window.film = {
    look(cards, kicker, caption, who) {
      html.dataset.cards = cards.join(" ");
      cap.querySelector("small").textContent = kicker;
      cap.querySelector("b").textContent = caption;
      cap.hidden = !caption;
      if (!cards.includes("end")) return;
      end.querySelector("p").textContent = who;
      end.querySelector("div").append(root.querySelector(".card.score"));
      end.querySelector(".credit-line").textContent = root.querySelector(".credit").textContent;
    },
    poses(key, a, b, step) {
      const out = [];
      for (let t = a; t <= b + 1e-9; t += step) {
        const p = S.pose(t, key);
        out.push([t, p?.phase ?? "absent", p?.speed ?? 0]);
      }
      return out;
    },
    camera(c) {
      const T = c.at ?? c.t0, cfg = S.plan.config(T), home = cams.homeOf(S.data);
      const length = S.plan.tracks.get(c.key)?.spec.length || 40, ref = c.key ? standOf(c.key, c.t0) : null;
      const P = cams.cameraPresets(S.data, { stand: ref, departures: cfg.departures, length,
        roll: S.plan.rolls.get(cfg.departures) });
      const span = { t0: c.t0, t1: c.t1 };
      if (c.camera === "harbour") {
        const h = P.harbour, d = Math.hypot(h.position[0] - h.target[0], h.position[1] - h.target[1]);
        const from = mix(h.target, h.position, Math.min(1, (c.reach ?? d) / d));
        return { from, to: mix(from, h.target, 0.06), at: h.target, fov: h.fov, ...span };
      }
      if (c.camera === "tower") return { from: P.tower.position, at: P.tower.target, fov: P.tower.fov,
        fov1: P.tower.fov - 6, ...span };
      if (c.camera === "gate" || c.camera === "push") {
        const near = (s) => Math.hypot(s.xy[0] - home.centre[0], s.xy[1] - home.centre[1]);
        const st = S.data.stands.find((s) => s.ref === ref)
          || home.stands.reduce((a, s) => (near(s) < near(a) ? s : a));
        const hd = dir(st.heading), sd = dir(st.heading + (c.camera === "push" ? -90 : 90));
        const from = add(add(st.xy, hd, -(length + 26)), sd, 30), to = add(from, hd, 5);
        const at = c.key ? { at: c.key, offset: [0, 0, 3] } : { at: [...add(st.xy, hd, -6), 3.5] };
        return { from: [...from, 11], to: [...to, 10], ...at, fov: c.camera === "push" ? 40 : 44, ...span };
      }
      const p = S.pose(T, c.key);
      if (!p || p.phase === "absent") return null;
      const fw = dir(p.heading), sd = away([p.x, p.y], p.heading, home.centre);
      if (c.camera === "taxi") {
        const at = add(add([p.x, p.y], fw, 25), sd, 85);
        return { from: [at[0], at[1], 6], at: c.key, fov: 36, frame: 55, ...span };
      }
      const at = add(add([p.x, p.y], fw, c.camera === "takeoff" ? 260 : 380), sd, 165);
      return { from: [at[0], at[1], 5], at: c.key, fov: 40, frame: 65, ...span };
    },
  };
  return true;
})()
"""


class Frame(NamedTuple):
    step: int | None
    t: float
    view: str


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--episode", type=Path, required=True, help="Episode JSON under the repo (served with web/).")
    p.add_argument("--out", type=Path, help="MP4 to write.")
    p.add_argument("--director", action="store_true", help="Cut the day into shots on the 3D airport.")
    p.add_argument("--rollout", action="store_true",
                   help="Film every watch from the tower instead, then the score; the WebP is the whole film.")
    p.add_argument("--seconds", type=float, help="The film's length: 40 by default, 55 with --rollout.")
    p.add_argument("--webp", type=Path, help="Also write an animated WebP clip here.")
    p.add_argument("--webp-width", type=int, default=960)
    p.add_argument("--webp-fps", type=int, default=15)
    p.add_argument("--webp-quality", type=int, default=72)
    p.add_argument("--camera", type=json.loads, metavar="JSON",
                   help='With --rollout, film the watches from this camera instead of the tower, e.g. '
                        '{"from": [x, y, up], "at": [x, y, up], "fov": 50} in ENU metres.')
    p.add_argument("--webp-seconds", type=float, default=12, help="Length of the WebP clip without --director.")
    p.add_argument("--shots", type=Path, help="Write stills (PNG) here: one per shot with --director, else per view.")
    p.add_argument("--thumb", type=Path, help="Write a 640x360 JPEG of the poster frame here.")
    p.add_argument("--still", type=Path, help="Write the poster frame without cards or tags here (JPEG or PNG).")
    p.add_argument("--pick", default="taxi:0.5", metavar="SHOT:FRACTION",
                   help="The poster frame: a shot of the director's and how far through it.")
    p.add_argument("--size", default="1920x1080")
    p.add_argument("--scale", type=float, default=1, help="Device pixels per CSS pixel.")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--view", default="network", help="Starting camera without --director.")
    p.add_argument("--cut", action="append", default=[], metavar="MINUTE:VIEW",
                   help="Without --director, switch the camera at a minute of the day, e.g. 1020:tower (repeatable).")
    p.add_argument("--intro-seconds", type=float, default=2.0)
    p.add_argument("--plan-seconds", type=float, default=1.6, help="Hold on each submitted plan.")
    p.add_argument("--look-seconds", type=float, default=0.5, help="Hold on each look at the situation.")
    p.add_argument("--minutes-per-second", type=float, default=45, help="Clock speed between watches.")
    p.add_argument("--after-minutes-per-second", type=float, default=150, help="Clock speed after the last watch.")
    p.add_argument("--outro-seconds", type=float, default=3.5)
    p.add_argument("--gl", choices=["auto", "gpu", "swiftshader"], default="auto")
    p.add_argument("--chrome", help="Chrome or Chromium binary.")
    args = p.parse_args(argv)
    if not (args.out or args.shots or args.thumb or args.still):
        p.error("pass --out (a film), --shots (stills), --thumb or --still")

    width, height = (int(v) for v in args.size.lower().split("x"))
    path = args.episode.resolve()
    episode = json.loads(path.read_text())
    directed = args.director or args.rollout or bool(args.thumb or args.still)
    if directed and not episode.get("stage"):
        raise SystemExit(f"{path.name} has no 3D airport to direct")
    best = None
    with serve(ROOT) as port:
        query = "record=1" if directed else f"record=1&view={quote(args.view)}"
        url = (f"http://127.0.0.1:{port}/web/index.html?{query}&t={episode['task']['start']}"
               f"&episode={quote('/' + str(path.relative_to(ROOT)))}")
        with session(url, find_chrome(args.chrome), width, height, args.gl, args.scale) as (page, ready, gl):
            print(f"viewer ready: {len(ready['watches'])} watches · {', '.join(ready['views'])} · WebGL {gl}")
            if directed:
                shots = director(page, episode, path, args.seconds, args.rollout)
                if args.shots:
                    stills(page, episode, shots, args.shots)
                if args.thumb or args.still:
                    poster(page, episode, shots, args.pick, args.thumb, args.still)
                if args.out:
                    film(page, episode, shots, args.out, args.fps, args.camera)
                    best = [(0.0, sum(s.seconds for s in shots))] if args.rollout else highlight(shots)
            elif args.shots:
                views(page, ready, episode, args.shots)
            if args.out and not directed:
                sweep(page, timeline(episode, ready, args), args.out, args.fps, episode)
    if args.out:
        probe(args.out)
        if args.webp:
            webp(args.out, args.webp, args.webp_width, best or middle(args.out, args.webp_seconds), args.webp_fps,
                 args.webp_quality)
    return 0


def director(page, ep: Mapping, path: Path, seconds: float | None, rollout: bool = False) -> list[Shot]:
    """The director's shots for the episode on the page, or its rollout, with the film helper loaded into it."""
    if not page.js(FILM_JS.replace("__CSS__", json.dumps(FILM_CSS))):
        raise SystemExit("the film helper did not load")
    traffic = json.loads(gzip.decompress((path.parent / ep["stage"]["traffic"]).read_bytes()))

    def poses(key: str, a: float, b: float) -> list:
        return page.js(f"film.poses({json.dumps(key)}, {a}, {b}, {5 / 60})")

    events = page.js("airops.events()")
    day = Day(ep, traffic, events, poses)
    shots = day.rollout(seconds or 55) if rollout else direct(ep, traffic, events, poses, seconds or 40)
    for s in shots:
        print(f"  {s.name:<9} {s.camera:<8} {clock(s.t0)}–{clock(s.t1)} {s.seconds:4.1f} s "
              f"×{s.speed[0]:g}–{s.speed[1]:g} {s.key or ''} {s.caption}")
    print(f"{len(shots)} shots, {sum(s.seconds for s in shots):.1f} s")
    return shots


def setup(page, ep: Mapping, shot: Shot, bare: bool = False, camera: Mapping | None = None) -> None:
    """The shot's cards, caption and camera, a rollout watch's from ``camera`` if given; the end card shows the day's
    final score."""
    t = ep["task"]
    who = f"{ep.get('model_label') or ep['model']} · {HUBS.get(t['hub'], t['hub'])} · {long_date(t['date'])}"
    cards, kicker, caption = ((), "", "") if bare else (shot.cards, shot.kicker, shot.caption)
    page.js(f"film.look({json.dumps(list(cards))}, {json.dumps(kicker)}, {json.dumps(caption)}, {json.dumps(who)})")
    if shot.name == "end":
        page.js("airops.ready.then((r) => airops.time(r.end, {dt: 0, speed: 0}))")
    if camera and shot.name == "watch":
        page.js(f"airops.shot({json.dumps(camera)})")
        return
    cam = {"camera": shot.camera, "key": shot.key, "t0": shot.t0, "t1": shot.t1, "at": shot.at, "reach": shot.reach}
    if (spec := page.js(f"film.camera({json.dumps(cam)})")) is None:
        raise SystemExit(f"no camera for the {shot.name} shot at {clock(shot.t0)}")
    page.js(f"airops.shot({json.dumps(spec)})")


def frame_at(page, shot: Shot, t: float, rate: float, dt: float) -> None:
    target = "airops.stage" if shot.name == "end" else "airops"
    page.js(f"{target}.time({t:.5f}, {{dt: {dt}, speed: {0 if shot.name == 'end' else round(rate, 3)}}})")


def film(page, ep: Mapping, shots: Sequence[Shot], out: Path, fps: int, camera: Mapping | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    encoder = subprocess.Popen(ffmpeg_args(find("ffmpeg"), out, fps, title(ep)), stdin=subprocess.PIPE)
    start, n = time.monotonic(), 0
    for shot in shots:
        setup(page, ep, shot, camera=camera)
        for k, (t, rate) in enumerate(shot.clock(fps)):
            frame_at(page, shot, t, rate, 1 / fps if k else 0)
            jpeg = page.call("Page.captureScreenshot", format="jpeg", quality=92)["data"]
            encoder.stdin.write(base64.b64decode(jpeg))
            n += 1
        print(f"  {shot.name} {clock(shot.t0)}: {n} frames, {time.monotonic() - start:.0f} s", flush=True)
    encoder.stdin.close()
    if encoder.wait():
        raise SystemExit(f"ffmpeg exited {encoder.returncode}")
    print(f"wrote {out}: {n} frames, {n / fps:.1f} s")


def warm(page, shot: Shot, fraction: float) -> float:
    """Render the frames of the shot leading to ``fraction`` of it, so that a still shows what the film would."""
    frames = shot.clock(30)
    k = min(len(frames) - 1, round(len(frames) * fraction))
    for i in range(max(0, k - 3), k + 1):
        frame_at(page, shot, *frames[i], 1 / 30 if i > max(0, k - 3) else 0)
    return frames[k][0]


def stills(page, ep: Mapping, shots: Sequence[Shot], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for i, shot in enumerate(shots):
        setup(page, ep, shot)
        t = warm(page, shot, 0.6)
        path = out / f"{ep['task']['task_id']}-{i:02d}-{shot.name}-{clock(t).replace(':', '')}.png"
        path.write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        print(f"wrote {path}")


def poster(page, ep: Mapping, shots: Sequence[Shot], pick: str, thumb: Path | None, still: Path | None) -> None:
    """The picked frame of the film without cards or tags: full size to ``still``, 640x360 to ``thumb``."""
    name, _, fraction = pick.partition(":")
    shot = next((s for s in shots if s.name == name), None) or next(s for s in shots if s.name != "end")
    setup(page, ep, shot, bare=True)
    warm(page, shot, float(fraction or 0.5))
    image = Image.open(io.BytesIO(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"])))
    image = image.convert("RGB")
    for path, size in ((still, None), (thumb, THUMB)):
        if path is None:
            continue
        size = size or (min(image.width, 1920), round(image.height * min(image.width, 1920) / image.width))
        shown = image if size == image.size else image.resize(size, Image.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() in (".jpg", ".jpeg"):
            shown.save(path, quality=88, optimize=True, progressive=True)
        else:
            shown.save(path)
        print(f"wrote {path}: {shown.width}x{shown.height}, {path.stat().st_size / 1e3:.0f} kB")


def highlight(shots: Sequence[Shot]) -> list[tuple[float, float]] | None:
    """The film's best 12 seconds or so for the WebP: the pushback's last seconds, the taxi and the takeoff."""
    start = dict(zip((s.name for s in shots), itertools.accumulate((s.seconds for s in shots), initial=0),
                     strict=False))
    if not {"push", "taxi", "takeoff"} <= start.keys():
        return None
    push, taxi, up = (next(s for s in shots if s.name == n) for n in ("push", "taxi", "takeoff"))
    return [(start["push"] + push.seconds - 3, 3), (start["taxi"] + 1, taxi.seconds - 1.5),
            (start["takeoff"] + 1.5, up.seconds - 1.5)]


def title(ep: Mapping) -> str:
    t = ep["task"]
    return f"AirOpsEnv v1 · {ep.get('model_label') or ep['model']} · {HUBS.get(t['hub'], t['hub'])} {t['date']}"


def timeline(episode: dict, ready: dict, args) -> list[Frame]:
    """Hold on each planner step at its watch, sweep the clock to the next watch, then play out the rest of the day."""
    fps, steps, cuts = args.fps, episode["steps"], sorted(parse_cut(c) for c in args.cut)

    def view_at(t: float) -> str:
        return next((v for m, v in reversed(cuts) if t >= m), args.view)

    def hold(step, t, seconds):
        return [Frame(step, t, view_at(t))] * max(1, round(seconds * fps))

    def sweep_to(step, a, b, rate):
        n = max(1, math.ceil(abs(b - a) / rate * fps))
        return [Frame(step, a + (b - a) * (i + 1) / n, view_at(a + (b - a) * (i + 1) / n)) for i in range(n)]

    t = ready["start"]
    frames = hold(None, t, args.intro_seconds)
    for w in sorted(ready["watches"]):
        if w > t:
            frames += sweep_to(None, t, w, args.minutes_per_second)
            t = w
        for k, s in enumerate(steps):
            if s["t"] != w or s["tool"] in QUICK:
                continue
            frames += hold(k, t, args.plan_seconds if s["tool"] != "get_situation" else args.look_seconds)
    frames += sweep_to(None, t, ready["end"], args.after_minutes_per_second)
    return frames + hold(None, ready["end"], args.outro_seconds)


def parse_cut(cut: str) -> tuple[int, str]:
    minute, view = cut.split(":")
    return int(minute), view


def sweep(page, frames: list[Frame], out: Path, fps: int, episode: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    encoder = subprocess.Popen(ffmpeg_args(find("ffmpeg"), out, fps, title(episode)), stdin=subprocess.PIPE)
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


def views(page, ready: dict, episode: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    task = episode["task"]
    mid = next((w for w in ready["watches"] if w >= task["window"][0] + 120), ready["watches"][0])
    plan_step = next((k for k, s in enumerate(episode["steps"]) if s["t"] == mid and s["tool"] != "advance"), None)
    plan = [(v, mid, plan_step) for v in ready["views"]] + [("network", task["start"], None),
                                                             (ready["views"][0], ready["end"], None)]
    for view, t, step in dict.fromkeys(plan):
        page.js(f"airops.view({json.dumps(view)})")
        page.js(f"airops.show({json.dumps(step)})")
        page.js(f"airops.time({t})")
        end = "-end" if t == ready["end"] else ""
        name = f"{task['task_id']}-{view}-{clock(t).replace(':', '')}{end}.png"
        (out / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        print(f"wrote {out / name}")


def ffmpeg_args(ffmpeg: str, out: Path, fps: int, name: str) -> list[str]:
    return [ffmpeg, "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-vf", "scale=out_range=tv:out_color_matrix=bt709",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", "-metadata", f"title={name}", "-metadata", f"comment={ATTRIBUTION}", str(out)]


def probe(path: Path) -> None:
    info = json.loads(subprocess.run([find("ffprobe"), "-v", "error", "-show_entries",
                                      "stream=codec_name,width,height,nb_frames:format=duration,size", "-of", "json",
                                      str(path)], capture_output=True, text=True, check=True).stdout)
    s, f = info["streams"][0], info["format"]
    print(f"ffprobe {path.name}: {s['codec_name']} {s['width']}x{s['height']}, {s.get('nb_frames')} frames, "
          f"{float(f['duration']):.1f} s, {int(f['size']) / 1e6:.1f} MB")
    subprocess.run([find("ffmpeg"), "-v", "error", "-i", str(path), "-f", "null", "-"], check=True)


def middle(mp4: Path, seconds: float) -> list[tuple[float, float]]:
    duration = float(subprocess.run([find("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of",
                                     "csv=p=0", str(mp4)], capture_output=True, text=True, check=True).stdout)
    return [(max(0.0, duration / 2 - seconds / 2), seconds)]


def webp(mp4: Path, out: Path, width: int, parts: Sequence[tuple[float, float]], fps: int = 15,
         quality: int = 72) -> None:
    """An animated WebP of stretches of the film, assembled with Pillow from frames ffmpeg extracts."""
    with tempfile.TemporaryDirectory() as tmp:
        images = []
        for i, (start, seconds) in enumerate(parts):
            subprocess.run([find("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i",
                            str(mp4), "-vf", f"fps={fps},scale={width}:-2:flags=lanczos", f"{tmp}/p{i}-%04d.png"],
                           check=True)
            images += [Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob(f"p{i}-*.png"))]
        out.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(out, save_all=True, append_images=images[1:], duration=round(1000 / fps), loop=0,
                       quality=quality, method=6)
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
            raise SystemExit(f"{expression[:160]}: {details.get('exception', {}).get('description', details['text'])}")
        return result["result"].get("value")


def chrome_args(chrome: str, profile: Path, width: int, height: int, swiftshader: bool) -> list[str]:
    gl = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if swiftshader else ["--enable-unsafe-swiftshader"]
    return [chrome, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
            f"--window-size={width},{height}", "--hide-scrollbars", "--mute-audio", "--no-first-run",
            "--disable-smooth-scrolling", "--force-device-scale-factor=1", *gl, "about:blank"]


@contextmanager
def session(url: str, chrome: str, width: int, height: int, gl: str, scale: float = 1):
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
                    page.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                              deviceScaleFactor=scale, mobile=False)
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
