"""A day of the ops control center followed as it is played, for a live stream: the env's calls so far (its data/get
record) played again in order through a local day on the same pack, each result checked against the env's digest,
and the viewer's Episode JSON of the day so far. Nothing here reaches the env: it reads data/get and plays its own
copy.

While the day runs the episode is the recorded episode's shape (episode.episode) on the day so far: the room as it
stands, the closed watches as they were left, the flights, crews and passengers on the projection at the open watch
(the truth before its minute, the day as known after), the projected cost, and no grade. Once the day is done it is
the recorded episode itself, built from the replay and its watches' projections. `live` says where the day stands.
"""

import copy
import dataclasses
import gzip
import io
import json
import pickle
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from .. import export
from .. import stage as hub_stage
from ..core.model import hhmm
from ..core.sim import FLOWN
from ..programs import crewplan, crewsim, pax
from ..programs import occ as programs
from . import episode, run
from .day import OccDay, costs, digest
from .day3 import OccDay3
from .pack import World
from .replay import ROLE

FEED = 60
ATOMS = (str, int, float, bool, type(None), bytes)
LOOKUPS = {"wait_turn": "reads its page", "ops_flights": "looks up flights", "crew_flights": "looks up flights",
           "cust_flights": "looks up flights", "cust_flight": "looks up the passengers of",
           "ops_program": "reads the FAA programs", "crew_program": "reads the FAA programs",
           "ops_aircraft": "looks up aircraft", "crew_crews": "looks up crews", "crew_reserves": "looks up reserves",
           "crew_options": "prices crew options for", "ops_check": "tries changes", "crew_check": "tries crew moves"}


class Diverged(ValueError):
    """The record isn't a continuation of what was played: another day, or one started again."""


def rows(day: OccDay, proj: run.Projection) -> list[dict]:
    """A projection's flights as episode.projections draws a watch."""
    return [{"id": i, "status": r.status, "tail": r.tail, "dep": r.dep, "arr": r.arr,
             "arr_delay": r.arr - day.task.flight(i).sched_arr if r.status == FLOWN else None, "held": False,
             "decided": r.decided} for i, r in sorted(proj.day.run.flights.items())]


def jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


class Played(OccDay3):
    """The day played again, keeping each watch's projection as the watch left it (`left`)."""

    def __init__(self, world: World, mode: str = "joint"):
        self.left: list[list[dict]] = []
        super().__init__(world, mode)

    def _close(self) -> None:
        super()._close()
        self.left.append(rows(self, self.project(self.plan)))


class Follower:
    """A day played again from the env's record as it grows: `follow` takes each newer data/get and plays the calls
    and the triggers' advisories past those already played, in the env's order, checking each result and its place."""

    def __init__(self, world: World, mode: str = "joint", make: type[OccDay] = Played):
        self.world, self.day = world, make(world, mode)
        self.seq = self.played = 0
        self.mismatches: list[dict] = []

    def follow(self, data: Mapping) -> list[dict]:
        """The record's new calls played (each with its `out`) and advisories delivered, in seq order; the day ended
        as end-day ends it once the record is done."""
        day = self.day
        if data.get("task_id") != self.world.task.task_id or data.get("mode") != day.mode:
            raise Diverged(f"the record is {data.get('task_id')} {data.get('mode')}, not "
                           f"{self.world.task.task_id} {day.mode}")
        theirs = sum(c["seq"] <= self.seq for c in data["calls"])
        if theirs != self.played:
            raise Diverged(f"the record has {theirs} calls up to seq {self.seq}, {self.played} were played")
        events = sorted([(c["seq"], 0, c) for c in data["calls"] if c["seq"] > self.seq] +
                        [(n["seq"], 1, n) for n in data["notices"] if n["seq"] > self.seq
                         and n["via"] in ("trigger", "late")], key=lambda e: e[:2])
        out = []
        for seq, kind, x in events:
            if kind:
                day.notice(x["news_id"], x["via"])
                out.append({**x, "tool": "notice"})
            else:
                result = day.call(ROLE[x["desk"]], x["tool"], x["args"])
                logged = day.calls[-1]["seq"] if day.calls else None
                if digest(result) != x["result"] or logged != seq:
                    self.mismatches.append({"seq": seq, "desk": x["desk"], "tool": x["tool"], "watch": x["watch"],
                                            "logged": logged})
                out.append({**x, "out": result})
                self.played += 1
            self.seq = seq
        if data.get("done") and not day.done:
            day.end()
        if data.get("digest") and day.done and day.digest() != data["digest"]:
            self.mismatches.append({"digest": day.digest(), "recorded": data["digest"]})
        return out


def pickled(obj, world: World) -> bytes:
    """A replay's state on ``world`` pickled with the world's objects named by their place in it, not copied (the
    world holds closures, and is the pack's): `unpickled` puts it on the same pack's world in another process."""
    out = io.BytesIO()
    _Pickler(out, world).dump(obj)
    return out.getvalue()


def unpickled(body: bytes, world: World):
    return _Unpickler(io.BytesIO(body), world).load()


class _Pickler(pickle.Pickler):
    def __init__(self, file, world: World):
        super().__init__(file, pickle.HIGHEST_PROTOCOL)
        self.ids = {id(x): i for i, x in enumerate(anchors(world))}

    def persistent_id(self, obj):
        return self.ids.get(id(obj))


class _Unpickler(pickle.Unpickler):
    def __init__(self, file, world: World):
        super().__init__(file)
        self.objs = anchors(world)

    def persistent_load(self, pid):
        return self.objs[pid]


def anchors(world: World) -> list:
    """The world's objects in a fixed order, depth first through its dataclasses' fields, tuples, lists and dicts: the
    same objects in the same places in any process that loads the same pack."""
    out, seen, stack = [], set(), [world]
    while stack:
        x = stack.pop()
        if isinstance(x, ATOMS) or id(x) in seen:
            continue
        seen.add(id(x))
        out.append(x)
        if dataclasses.is_dataclass(x) and not isinstance(x, type):
            stack.extend(getattr(x, f.name) for f in reversed(dataclasses.fields(x)))
        elif isinstance(x, list | tuple):
            stack.extend(reversed(x))
        elif isinstance(x, dict):
            stack.extend(v for kv in reversed(x.items()) for v in reversed(kv))
    return out


def traffic_at(doc: Mapping, task, hub: str, news: Sequence[Mapping]) -> dict:
    """The task's baked traffic (stage.traffic, on any news) with its METARs and ground stops as ``news`` gives them,
    as stage.traffic would bake it on that news."""
    view = episode.view_task(task, hub, news)
    return {**doc, "weather": [hub_stage.metar(n.at, n.text) for n in sorted(view.news, key=lambda n: n.at)
                               if n.kind == "metar"], "stops": hub_stage.stops(view)}


class Statics:
    """What a day's episodes share that no play changes: the hub's news through its last watch and its baked traffic
    (`prebuild`, under ``root``), the hub's stage, the radar and twin (the viewer's ``assets``); each None or empty
    where there is none, and then no sidecar."""

    def __init__(self, world: World, root: Path, assets: Path):
        task = world.task
        self.world, self.hub, self.assets = world, task.hub, assets
        self.news = read(root / "news" / f"{task.task_id}.json") or []
        self.traffic = read(root / "traffic" / f"{task.task_id}.json.gz")
        self.stage = read(assets / "stage" / self.hub / "stage.json.gz")
        self.radar = export.radar(task, root if (root / "radar" / task.task_id / "index.json").is_file() else assets)
        self.twin = export.twin(SimpleNamespace(hub=self.hub), assets)

    def through(self, t: int) -> list[dict]:
        return [n for n in self.news if n["at"] <= t]

    def sidecar(self, ep: Mapping, t: int) -> bytes | None:
        if self.traffic is None or self.stage is None:
            return None
        traffic = traffic_at(self.traffic, self.world.task, self.hub, self.through(t))
        return export.sidecar(ep, self.assets, {self.hub: self.stage}, traffic)


def read(path: Path):
    if not path.is_file():
        return None
    body = path.read_bytes()
    return json.loads(gzip.decompress(body) if path.suffix == ".gz" else body)


def prebuild(world: World, root: Path, inputs: Path) -> dict:
    """A day's news through its last watch and its traffic at the hub (stage.traffic on that news) into ``root``."""
    task = world.task
    news = sorted(episode.day_news(task, task.hub, inputs, programs.watches(task)[-1]),
                  key=lambda n: (n["at"], n["id"]))
    doc = hub_stage.traffic(episode.view_task(task, task.hub, news), inputs)
    for path, body in ((root / "news" / f"{task.task_id}.json", json.dumps(news).encode()),
                       (root / "traffic" / f"{task.task_id}.json.gz",
                        gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), mtime=0))):
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(body)
        part.replace(path)
    return {"news": len(news), "aircraft": len(doc["aircraft"])}


def stem(run_label: str, model: str, task_id: str) -> str:
    return f"{export.slug(run_label)}.{export.slug(model)}.{task_id}"


def final(f: Follower, statics: Statics, *, model: str, run_label: str, record: Mapping | None = None) -> dict:
    """The done day's episode as occ_viewer.py builds it from the env's record, from the replay."""
    day = f.day
    data = jsonable(day.data())
    times = programs.watches(day.task)[: data["watch"] + 1]
    ep = episode.episode(f.world, data, model=model, run=run_label, inputs=Path("/nonexistent"),
                         cache=statics.assets, record=record, projected=day.left, news=statics.through(times[-1]))
    ep["radar"], ep["twin"] = statics.radar, statics.twin
    ep["stage"] = None if statics.traffic is None or statics.stage is None else {
        "hub": statics.hub, "url": f"../stage/{statics.hub}/stage.json.gz",
        "traffic": f"{stem(run_label, model, day.task.task_id)}.stage.json.gz"}
    return ep


def now(f: Follower, statics: Statics, *, model: str, run_label: str, record: Mapping | None = None,
        view: dict | None = None) -> dict:
    """The day so far as an episode: the room and the closed watches as they stand, the rest on `view` (`projection`,
    the open watch's)."""
    world, day = f.world, f.day
    task = world.task
    data = jsonable(day.data())
    view = view or projection(f)
    times = programs.watches(task)[: day.k + 1]
    news = statics.through(day.t)
    by_id = {n["id"]: n for n in news}
    estimates = {e["watch"]: e["total"] for e in data["estimates"]}
    refs = world.refs
    best, b0 = refs["best"], refs["B0"]["notice"]
    c = view["costs"]
    fake = {**data, "grade": {"best": best, "B0": b0}, "crews": view["units"], "status": view["status"]}
    room = episode.room(world, fake, view["executed"])
    room["grade"] = None
    ep = {
        "version": 1,
        "task": {"task_id": task.task_id, "carrier": task.carrier, "hub": task.hub, "date": task.date, "tz": task.tz,
                 "window": list(task.window), "start": task.start, "turn": dict(task.turn), "dmax": task.dmax,
                 "airports": {k: a.to_dict() for k, a in sorted(task.airports.items())},
                 "tails": [{"tail": t.tail, "family": t.family, "type": t.type, "seats": t.seats, "pos": t.pos,
                            "ready": t.ready, "end_req": t.end_req, "actual_end": t.actual_end} for t in task.tails]},
        "model": model, "model_label": export.NAMES.get(model, model), "run": run_label,
        "reward": None,
        "cost": {"total": c["total"], "pax_delay": c["delay"], "cancel": c["cancel"] + c["notice"], **c},
        "reference": {"anchor": best, "best": best, "bound": refs["bound"], "B0": b0, "flat": view["flat"],
                      "gap_pct": refs["gap_pct"]},
        "schedule": [episode.schedule_row(x) for x in task.flights],
        "executed": view["executed"],
        "ferries": [],
        "frozen": [],
        "watches": [{"t": t, "news": [by_id[n["news_id"]] for n in data["notices"]
                                      if n["watch"] == k and n["news_id"] in by_id],
                     "changes": episode.changes(task, data["changes"], k),
                     **({"est_cost": estimates[k]} if k in estimates else {}),
                     "projected": day.left[k] if k < len(day.left) else view["projected"]}
                    for k, t in enumerate(times)],
        "steps": [{"t": x["t"], "tool": x["tool"], "args": x["args"], "desk": x["desk"], "watch": x["watch"],
                   "turn": x["turn"]} for x in data["calls"]],
        "news": sorted(news, key=lambda n: (n["at"], n["id"])),
        "caps": {}, "nominal": {},
        "radar": statics.radar, "twin": statics.twin,
        "stage": None,
        "end_reason": None, "audit": None, "record": dict(record or {}),
        "sources": episode.SOURCES,
        "occ": room,
    }
    if world.passengers is not None:
        ep["passengers"] = view["passengers"]
    return ep


def projection(f: Follower) -> dict:
    """What the open watch's projection shows: the flights (executed rows), its cost and flat cost, the crews' log and
    gate tags, and the passengers on it, a flight's hold against the projection without the customer desk's holds."""
    world, day = f.world, f.day
    task, t = world.task, day.t
    proj = day.project(day.plan)
    plan = jsonable(day.plan)
    executed = [episode.outcome(task, x, plan.get(str(x["id"]), {})) for x in jsonable(proj.day.run.rows())]
    out = {"watch": day.k, "version": day.version, "t": t, "executed": executed, "costs": costs(proj.day),
           "flat": round(crewplan.worth(proj.day, True), 1), "units": jsonable(proj.day.units),
           "status": jsonable(crewsim.statuses(proj.day)), "projected": rows(day, proj), "passengers": None}
    if world.passengers is not None:
        units = {u.id: u for u in world.units}
        seated = {i: sum(units[u].size for u in us) for i, us in proj.day.riders.items() if us}
        done, led = pax.ledger(task, world.passengers, proj.day.run.flights, seated)
        data = {"requests": day.requests, "changes": day.changes}
        held = episode.held_minutes(world, jsonable(data), plan, proj.day, None,
                                    lambda p: run.project(world, p, t, day.world, day.known_at(t)).day)
        out["passengers"] = episode.ledger_rows(world, done, led, held)
    return out


def feed(f: Follower, new: Sequence[Mapping], news: Mapping[str, Mapping]) -> list[dict]:
    """The room's events among newly played calls, one line each: requests, replies, decisions, notes, advisories,
    desks leaving."""
    task, day = f.world.task, f.day
    out = []
    for x in new:
        tool, r = x["tool"], x.get("out") or {}
        row = {"seq": x["seq"], "watch": x["watch"], "t": x["t"], "desk": x.get("desk"), "request": None,
               "flight": None}
        if tool == "notice":
            n = news.get(x["news_id"])
            out.append(row | {"kind": "notice", "desk": None,
                              "text": (n or {}).get("text", x["news_id"]).splitlines()[0][:200]})
        elif tool.endswith("_request") and "request" in r:
            q = next(q for q in day.requests if q["id"] == r["request"])
            i = q["params"].get("flight")
            what = " ".join(p for p in (q["type"], task.flight(i).flight if i is not None else None,
                                        hhmm(q["params"]["time"]) if q["params"].get("time") else None) if p)
            text = episode.prose(q["text"], task)
            out.append(row | {"kind": "request", "request": q["id"], "flight": i,
                              "text": f"{q['sender']} to {q['receiver']}: {what}" + (f": {text}" if text else "")})
        elif tool.endswith("_reply") and "answer" in r and "error" not in r:
            q = next(q for q in day.requests if q["id"] == r["request"])
            i = q["params"].get("flight")
            text = r["lapsed"] if r.get("lapsed") else episode.prose(x["args"].get("text"), task)
            out.append(row | {"kind": "reply", "request": q["id"], "flight": i,
                              "text": f"{x['desk']} {'finds lapsed' if r.get('lapsed') else r['answer'] + 's'} "
                                      f"{q['id']} ({q['type']}{' ' + task.flight(i).flight if i is not None else ''})"
                                      + (f": {text}" if text else "")})
        elif tool.endswith("_confirm") and r.get("confirmed"):
            delta = (r.get("delta") or {}).get("total")
            out.append(row | {"kind": "decision", "text": f"{x['desk']} confirms "
                                                          f"{episode.prose(', '.join(r['confirmed']), task)}"
                                                          + (f" ({delta:+,.0f})" if delta else "")})
        elif tool == "end_turn" and x["args"].get("note"):
            out.append(row | {"kind": "note", "text": f"{x['desk']}: {episode.prose(x['args']['note'], task)}"})
        elif tool == "resign":
            out.append(row | {"kind": "off", "text": f"{x['desk']} desk off ({x['args'].get('reason')})"})
    return out


def summary(x: Mapping, task) -> str:
    """A call in a few words, for a desk's last action."""
    tool, args, r = x["tool"], x.get("args") or {}, x.get("out") or {}
    if tool in LOOKUPS:
        flight = args.get("flight") or (args.get("ids") or [None])[0]
        return f"{LOOKUPS[tool]}{' ' + episode.prose(str(flight), task) if flight else ''}"
    if tool.endswith("_confirm"):
        return episode.prose(f"confirms {', '.join(r.get('confirmed') or []) or 'nothing'}", task)
    if tool.endswith("_request"):
        return episode.prose(f"asks {args.get('to') or ('ops' if not tool.startswith('ops') else 'crew')} "
                             f"{args.get('type')}{' ' + args['flight'] if args.get('flight') else ''}", task)
    if tool.endswith("_reply"):
        return f"{args.get('answer')}s {args.get('request')}"
    if tool == "end_turn":
        return "stands down" if args.get("stand_down") else "ends its turn"
    if tool == "resign":
        return f"leaves ({args.get('reason')})"
    return tool


def desks(f: Follower, last: Mapping[str, dict]) -> list[dict]:
    day = f.day
    out = []
    for d in day.desks:
        state = "off" if d in day.absent else "turn" if day.turn and day.turn["desk"] == d else "waiting"
        out.append({"role": d, "state": state, "off": (day.absent.get(d) or {}).get("reason"),
                    "last": (last.get(d) or {}).get("last"), "at": (last.get(d) or {}).get("at")})
    return out


def decided_until(day: OccDay, executed: Sequence[Mapping]) -> int:
    """The minute before which the day is final: the open watch's while it runs; once done, its last movement."""
    if not day.done:
        return day.t
    return max([day.t] + [x[k] for x in executed for k in ("dep", "arr") if x.get(k) is not None])


def cut(ep: Mapping, n: int = 1) -> dict:
    """The episode with each long list cut to its first n rows, for showing its shape."""
    def short(v):
        if isinstance(v, list):
            return [short(x) for x in v[:n]]
        if isinstance(v, dict):
            return {k: short(x) for k, x in (list(v.items())[:max(n, 12)] if len(v) > 40 else v.items())}
        if isinstance(v, str) and len(v) > 160:
            return v[:160] + "…"
        return v
    return short(copy.deepcopy(dict(ep)))
