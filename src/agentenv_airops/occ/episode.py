"""A recorded ops control center day as the viewer's Episode JSON (web/episode.schema.json): v1's shape at one hub, so
the 3D airport, the US map, the board and the timeline play it, and `occ`, what the room did: the requests between the
desks with their replies, each desk's decisions and the note it left at the end of each turn, and each flight's crew
status over the day for the tags at the gate.

The flights fly as the grade's executed rows (the day as it happened under the desks' plan); each watch's `projected`,
when given, is the plan as that watch left it on the day as known (`projections`, a replay). The FAA's advisories and
the hub's METARs are read from the raw inputs the pack was built from (cache/inputs), as v1's pack reads them.

The crews come from the day's crew log: each unit's duties with their legal limits, hotels and deadheads, the reserves
by base and the desks' pages. The desks act at a watch's minute, in turns; the room spreads each watch's turns over the
hour that follows (`shown`, display only) so that the requests, replies and decisions can be watched one by one.

A day with passengers (airops-occ3) adds `passengers` (`passengers`, the viewer's schema): the day played again from its
plan, each flight's passengers by the pass's ledger, the booked connections and the pass's events; a flight's `hold`,
the minutes its departure waited for the customer desk's holds ops accepted (the day without them played again)."""

import gzip
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from .. import export
from .. import stage as hub_stage
from ..build import advisories as raw_advisories
from ..build import weather
from ..build.pack import utc_days
from ..core.model import parse_hhmm
from ..core.sim import FLOWN
from ..programs import crews as rules
from ..programs import occ as programs
from ..programs import pax, sim
from . import run as runs
from .day import MUTATING, SHARED, OccDay, reward
from .day3 import CUSTOMER, OccDay3
from .pack import World
from .replay import ROLE

SOON = 60
"""A crew's clock is worth a tag once the flight leaves within this many minutes of its last legal departure, as the
desks' crew flags read it (run.flags)."""
LOOKBACK = 720
MODES = {"joint": "two desks", "one": "one agent at both desks"}
MODES3 = {"joint": "three desks", "one": "one agent at every desk"}
OPS_CHANGES = {"cancel": "cancel", "cancel_pair": "cancel", "hold": "hold", "release": "release"}
GLUED_ID = re.compile(r"([A-Za-z]{2,})(f\d)")
TIMED_ID = re.compile(r"\bf(\d{1,3}?)(\d{2}:\d{2}(?:\+\d)?)")
FLIGHT_ID = re.compile(r"\bf(\d{1,3})\b()")
GLUED_WORD = re.compile(r"\b([a-z]{2,})(\d)")
LISTED = re.compile(r",(?=[A-Z][A-Z0-9] \d)")
ROOM = (2, 50)
"""The minutes after a watch over which the room shows its turns."""
TURN = (6, 24)
"""A turn's share of them, at least and at most, before they are scaled to fit."""
SOURCES = export.SOURCES | {"crews": "Pairings and reserves generated from the published schedule under FAR 117 and "
                                     "14 CFR 121.467 (stand-ins, not JetBlue's)"}


def episode(world: World, data: Mapping, *, model: str, run: str, inputs: Path, cache: Path = export.CACHE,
            hub: str | None = None, record: Mapping | None = None,
            projected: Sequence[Sequence[Mapping]] | None = None) -> dict:
    """``data`` is the env's data/get record of a finished day (days/<run>-a<n>.json); ``hub`` the airport shown in 3D,
    the task's by default."""
    task, grade = world.task, data["grade"]
    hub = hub or task.hub
    times = programs.watches(task)[: data["watch"] + 1]
    news = advisories(task, inputs, times[-1]) + metars(task, hub, inputs, times[-1])
    by_id = {n["id"]: n for n in news}
    plan = data["plan"]
    executed = [outcome(task, x, plan.get(str(x["id"]), {})) for x in grade["executed"]]
    estimates = {e["watch"]: e["total"] for e in data["estimates"]}
    best, b0 = grade["best"], grade["B0"]
    ep = {
        "version": 1,
        "task": {"task_id": task.task_id, "carrier": task.carrier, "hub": hub, "date": task.date, "tz": task.tz,
                 "window": list(task.window), "start": task.start, "turn": dict(task.turn), "dmax": task.dmax,
                 "airports": {c: a.to_dict() for c, a in sorted(task.airports.items())},
                 "tails": [{"tail": t.tail, "family": t.family, "type": t.type, "seats": t.seats, "pos": t.pos,
                            "ready": t.ready, "end_req": t.end_req, "actual_end": t.actual_end} for t in task.tails]},
        "model": model, "model_label": export.NAMES.get(model, model), "run": run,
        "reward": grade["reward"],
        "cost": {"total": grade["cost"], "pax_delay": grade["costs"]["delay"],
                 "cancel": grade["costs"]["cancel"] + grade["costs"]["notice"], **grade["costs"]},
        "reference": {"anchor": best, "best": best, "bound": grade["bound"], "B0": b0, "flat": grade["flat"],
                      "gap_pct": grade["gap_pct"]},
        "schedule": [schedule_row(f) for f in task.flights],
        "executed": executed,
        "ferries": [],
        "frozen": [],
        "watches": [{"t": t, "news": [by_id[n["news_id"]] for n in data["notices"]
                                      if n["watch"] == k and n["news_id"] in by_id],
                     "changes": changes(task, data["changes"], k),
                     **({"est_cost": estimates[k]} if k in estimates else {}),
                     **({"projected": list(projected[k])} if projected and k < len(projected) else {})}
                    for k, t in enumerate(times)],
        "steps": [{"t": c["t"], "tool": c["tool"], "args": c["args"], "desk": c["desk"], "watch": c["watch"],
                   "turn": c["turn"]} for c in data["calls"]],
        "news": sorted(news, key=lambda n: (n["at"], n["id"])),
        "caps": {}, "nominal": {},
        "radar": export.radar(task, cache),
        "twin": export.twin(SimpleNamespace(hub=hub), cache),
        "stage": None,
        "end_reason": grade["end_reason"], "audit": data["audit"], "record": dict(record or {}),
        "sources": SOURCES,
        "occ": room(world, data, executed),
    }
    if world.passengers is not None and data.get("passengers") is not None:
        ep["passengers"] = passengers(world, data)
    ep["stage"] = stage_of(ep, cache)
    return ep


def passengers(world: World, data: Mapping) -> dict:
    """The day's passengers in the viewer's shape: per flight booked + added = flown + rebooked + stranded + home +
    dropped (the pass's ledger), the booked connections and how many made each, and the pass's events; from the plan
    played again, which must give the passengers the day recorded."""
    task, units = world.task, {u.id: u for u in world.units}
    plan = plan_of(data["plan"])
    truth = sim.truth(task)
    day = runs.truth(world, plan, truth)
    seated = {i: sum(units[u].size for u in us) for i, us in day.riders.items() if us}
    done, led = pax.ledger(task, world.passengers, day.run.flights, seated)
    if json.loads(json.dumps(done.to_dict())) != data["passengers"]:
        raise ValueError(f"{data['task_id']} {data['mode']}: the plan played again doesn't give the recorded "
                         "passengers")
    held = held_minutes(world, data, plan, day, truth)
    booked, cin, cout = Counter(), Counter(), Counter()
    for g in world.passengers.groups:
        for k in g.legs:
            booked[k] += g.pax
        if len(g.legs) == 2:
            cout[g.legs[0]] += g.pax
            cin[g.legs[1]] += g.pax
    rows = {f.id: {"id": f.id, "booked": booked[f.id], "added": led["added"][f.id], "conn_in": cin[f.id],
                   "conn_out": cout[f.id], "flown": led["flown"][f.id], "misconnected": 0, "rebooked": [],
                   "stranded": 0, "home": 0, "dropped": led["dropped"][f.id], "hold": held.get(f.id, 0)}
            for f in task.flights}
    moved: dict[int, Counter] = {}
    for e in done.events:
        r = rows[e["flight"]]
        if e["kind"] == "misconnect":
            r["misconnected"] += e["pax"]
        elif e["kind"] == "rebook":
            moved.setdefault(e["flight"], Counter())[e["to"]] += e["pax"]
        elif e["kind"] == "hotel":
            r["stranded"] += e["pax"]
        else:
            r["home"] += e["pax"]
    for i, c in moved.items():
        rows[i]["rebooked"] = [{"to": k, "pax": n} for k, n in sorted(c.items())]
    return {"flights": [r for r in rows.values() if r["booked"] or r["added"]],
            "connections": [{"from": g.legs[0], "to": g.legs[1], "at": task.flight(g.legs[1]).orig, "pax": g.pax,
                             "made": led["made"][g.id]} for g in world.passengers.groups if len(g.legs) == 2],
            "events": sorted(({k: v for k, v in e.items() if k != "until" or v is not None} for e in done.events),
                             key=lambda e: (e["t"], e["kind"], e["flight"], e["group"]))}


def held_minutes(world: World, data: Mapping, plan: dict, day, truth) -> dict[int, int]:
    """Per flight held for the customer desk (its HOLD accepted), the minutes it left later than without those holds."""
    asked = {r["id"] for r in data["requests"] if r["sender"] == CUSTOMER and r["type"] == "HOLD"}
    mine = [c for c in data["changes"] if c["action"] == "hold" and c["ref"] in asked]
    if not mine:
        return {}
    without = json.loads(json.dumps({str(k): v for k, v in plan.items()}))
    for c in mine:
        e = without[str(c["target"])]
        e["holds"] = [h for h in e["holds"] if not (h[0] == c["t"] and h[1] == parse_hhmm(c["detail"]))]
    other = runs.truth(world, plan_of(without), truth).run.flights
    out = {}
    for c in mine:
        i = c["target"]
        a, b = day.run.flights[i], other[i]
        if a.status == FLOWN and b.status == FLOWN and a.dep > b.dep:
            out[i] = a.dep - b.dep
    return out


def plan_of(raw: Mapping) -> dict:
    return {int(k) if k.isdigit() else k: v for k, v in raw.items()}


def room(world: World, data: Mapping, executed: Sequence[Mapping]) -> dict:
    """What the room did and the crews it moved, for the control-center card, the 3D room, the crews at the airport,
    the network inset and the expert panes."""
    grade = data["grade"]
    best, b0 = grade["best"], grade["B0"]
    shown, turns = timing(data)
    calls = {c["seq"]: c for c in data["calls"]}
    units = crews(world, data["crews"] or {})
    return {
        "mode": data["mode"], "label": (MODES3 if world.passengers is not None else MODES).get(data["mode"],
                                                                                               data["mode"]),
        "desks": list(data["desks"]),
        "turns": turns,
        "requests": [request(r, world.task) | answered(r, data["calls"], shown, turns) for r in data["requests"]],
        "decisions": [{"t": c["t"], "shown": shown.get(c["seq"], c["t"]), "desk": c["desk"], "action": c["action"],
                       "flight": c["target"], "detail": c["detail"], "ref": c["ref"], "via": c["via"],
                       "tool": calls[c["seq"]]["tool"] if c["seq"] in calls else None} for c in data["changes"]],
        "notes": [{"t": u["t"], "shown": turns[u["n"]]["to"], "desk": u["desk"], "kind": u["kind"],
                   "note": prose(u["note"], world.task)}
                  for u in data["turns"] if u.get("note")],
        "pages": [{"t": p["t"], "watch": p["watch"], "desk": p["desk"], "text": p["text"]} for p in data["pages"]],
        "absent": data["absent"],
        "crew": crew_tags(data["status"] or {}, {x["id"]: x for x in executed}),
        "units": units,
        "reserves": reserves(units),
        "hotels": sorted(({"unit": uid, **h} for uid, u in units.items() for h in u["hotels"]),
                         key=lambda h: (h["from"], h["unit"])),
        "deadheads": sorted(({"unit": uid, "flight": i, "dep": dep, "arr": arr} for uid, u in units.items()
                             for d in u["duties"] for i, dep, arr in d["deadheads"]),
                            key=lambda x: (x["dep"], x["unit"])),
        "refs": [{"label": "Best plan found", "reward": 1.0, "note": "the best plan over aircraft and crews found "
                                                                   "in hindsight"},
                 {"label": "Standard crew desk", "reward": round(reward(b0, best, b0), 6),
                  "note": "scripted standard crew-desk practice, aircraft doing nothing"}],
        "grade": {k: grade[k] for k in ("cost", "best", "bound", "B0", "flat", "gap_pct", "over_best_pct",
                                         "reward_L", "avoidable_share", "counts", "costs") if k in grade},
    }


def timing(data: Mapping) -> tuple[dict[int, int], list[dict]]:
    """Each turn's window in the hour after its watch, ROOM minutes split between the watch's turns by their calls
    within TURN, and each call's minute in its turn's window, by seq; display only."""
    calls: dict[int, list[dict]] = {}
    for c in data["calls"]:
        if c["turn"] is not None:
            calls.setdefault(c["turn"], []).append(c)
    by_watch: dict[int, list[dict]] = {}
    for u in data["turns"]:
        by_watch.setdefault(u["watch"], []).append(u)
    turns, shown = [], {}
    for k, group in sorted(by_watch.items()):
        want = [min(TURN[1], max(TURN[0], 4 + u["calls"])) for u in group]
        scale = min(1.0, (ROOM[1] - ROOM[0]) / sum(want))
        at = group[0]["t"] + ROOM[0]
        for u, w in zip(group, want, strict=True):
            lo, hi = at, at + w * scale
            mine = sorted(calls.get(u["n"], []), key=lambda c: c["seq"])
            for j, c in enumerate(mine):
                shown[c["seq"]] = round(lo + (j + 1) * (hi - lo) / (len(mine) + 1), 2)
            turns.append({"n": u["n"], "watch": k, "t": u["t"], "desk": u["desk"], "kind": u["kind"],
                          "calls": u["calls"], "from": round(lo, 2), "to": round(hi, 2)})
            at = hi
    return shown, sorted(turns, key=lambda u: u["n"])


def answered(r: Mapping, calls: Sequence[Mapping], shown: Mapping[int, float], turns: Sequence[Mapping]) -> dict:
    """When the room shows a request sent, at the sender's call that made it, and answered, at the reply call."""
    asks = [c for c in calls if c["turn"] == r["turn"] and c["tool"].endswith("_request") and c["seq"] in shown
            and c["args"].get("type") == r["type"]
            and (c["args"].get("text", c["args"].get("note")) or None) == (r.get("text") or None)]
    reply = [c for c in calls if c["tool"].endswith("_reply") and c["args"].get("request") == r["id"]
             and c["seq"] in shown]
    return {"shown": shown[asks[0]["seq"]] if asks else turns[r["turn"]]["to"],
            "answered": shown[reply[0]["seq"]] if reply else None}


def crews(world: World, log: Mapping[str, Mapping]) -> dict[str, dict]:
    """Each crew unit of the day's log: its kind, group, base and size, its reserve type and call, and each duty with
    its legs and deadheads ([flight, dep, arr]) and the latest legal block-in, `limit` (FAR 117 Table B for pilots,
    121.467's scheduled duty for cabin crews) and `ext` (the pilots' 2 h extension); its hotel nights and where it
    ends the day."""
    units = {u.id: u for u in world.units}
    out = {}
    for uid, u in sorted(log.items()):
        unit = units.get(uid)
        duties = []
        for d in u["duties"]:
            limit = ext = None
            if u["kind"] == "pilot":
                hour = world.local(d["at"], d["report"]) // 60 % 24 if d["at"] in world.airports else \
                    (d["report"] // 60) % 24
                rap = unit.rap if unit is not None and d["begins"] == "call" else None
                limit = rules.pilot_limit(d["report"], hour, max(1, len(d["legs"])), rap, extend=False)
                ext = limit + rules.EXTENSION
            else:
                limit = d["report"] + rules.CABIN_DUTY - rules.DEBRIEF
            duties.append({"report": d["report"], "at": d["at"], "begins": d["begins"], "legs": d["legs"],
                           "deadheads": d["deadheads"], "release": d["release"], "limit": limit, "ext": ext})
        out[uid] = {"kind": u["kind"], "group": u["group"], "home": u["home"],
                    "size": rules.size(u["kind"], u["group"]), "reserve": u["reserve"] or None, "call": u["call"],
                    "duties": duties, "hotels": u["hotels"], "end": u["station"]}
    return out


def reserves(units: Mapping[str, Mapping]) -> list[dict]:
    """The reserves by base: each with its type and when it was taken, `used` (its call, else an hour before the first
    flight it works or rides), None when it never was."""
    out = []
    for uid, u in units.items():
        if not u["reserve"]:
            continue
        deps = [x[1] for d in u["duties"] for x in (*d["legs"], *d["deadheads"])]
        used = u["call"] if u["call"] is not None else (min(deps) - 60 if deps else None)
        out.append({"unit": uid, "base": u["home"], "kind": u["kind"], "group": u["group"], "type": u["reserve"],
                    "used": used})
    return sorted(out, key=lambda r: (r["base"], r["kind"], r["unit"]))


def outcome(task, x: Mapping, entry: Mapping) -> dict:
    """An executed row with v1's extras: its lateness, the hold the plan left on it and whether it bound."""
    f = task.flight(x["id"])
    holds = entry.get("holds") or []
    nb = holds[-1][1] if holds and not entry.get("cancel") else None
    flown = x["status"] == FLOWN
    return {**x, "arr_delay": x["arr"] - f.sched_arr if flown else None, "not_before": nb,
            "held": flown and nb is not None and nb > f.sched_dep and x["dep"] >= nb}


def schedule_row(f) -> dict:
    a = f.actual
    return {"id": f.id, "flight": f.flight, "orig": f.orig, "dest": f.dest, "sched_dep": f.sched_dep,
            "sched_arr": f.sched_arr, "family": f.family, "pax": f.pax, "tail": a.tail,
            "airline": {"cancelled": a.cancelled, "dep": a.dep, "arr": a.arr, "cancel_code": a.code}}


def changes(task, log: Sequence[Mapping], k: int) -> list[dict]:
    """Ops's changes at watch k in v1's shape (cancel, hold, release); the crew desk's moves are in occ.decisions."""
    out = {}
    for c in log:
        if c["watch"] != k or c["desk"] not in ("ops", "one") or c["action"] not in OPS_CHANGES or c["target"] is None:
            continue
        f = task.flight(c["target"])
        action = OPS_CHANGES[c["action"]]
        nb = parse_hhmm(c["detail"]) if action == "hold" and c["detail"] else None
        out[f.id] = {"id": f.id, "flight": f.flight, "orig": f.orig, "dest": f.dest, "action": action,
                     "cancel": action == "cancel", "not_before": nb}
    return [out[i] for i in sorted(out)]


def request(r: Mapping, task) -> dict:
    p = r.get("params") or {}
    return {"id": r["id"], "sender": r["sender"], "receiver": r["receiver"], "type": r["type"],
            **({"pax": r["pax"]} if r.get("pax") is not None else {}),
            "flight": p.get("flight"), "time": p.get("time"), "text": prose(r.get("text"), task), "sent": r["sent"],
            "status": r["status"], "reply": r.get("reply"), "replied": r.get("replied"),
            "reply_text": prose(r.get("reply_text"), task),
            "actions": list({json.dumps(x, sort_keys=True): x for x in (
                {"desk": a["desk"], "action": a["action"], "flight": a.get("target"), "t": a.get("minute")}
                for a in r.get("actions") or [])}.values())}


def prose(text: str | None, task) -> str | None:
    """A desk's words for display: its flight ids (f483) as the flights' numbers, split from a time run into them
    (f52618:32 is f526 at 18:32), and a word run into a number or a flight id given its space (cost169,847, DHf354)."""
    if not text:
        return text
    by_id = task.by_id

    def flight(m: re.Match) -> str:
        i = int(m[1])
        return f"{by_id[i].flight}{' ' + m[2] if m[2] else ''}" if i in by_id else m[0]

    text = GLUED_ID.sub(r"\1 \2", text)
    text = TIMED_ID.sub(lambda m: flight(m) if int(m[1]) in by_id else m[0], text)
    text = FLIGHT_ID.sub(flight, text)
    return LISTED.sub(", ", GLUED_WORD.sub(r"\1 \2", text))


def crew_tags(status: Mapping[str, Sequence[Mapping]], executed: Mapping[int, Mapping]) -> dict[str, list]:
    """Each flight's crew status over the day as the tags at the gate show it, [minute, status, until] from the
    minute it holds: "no crew" while no legal crew can take it, "times out" with the crew's last legal departure
    once the flight leaves within SOON of it (or never leaves), None when neither. Flights with no tag are left out."""
    out = {}
    for key, log in status.items():
        x = executed.get(int(key))
        dep = x["dep"] if x and x["status"] == FLOWN else None
        rows: list[list] = []
        for s in log:
            at = parse_hhmm(s["at"])
            until = parse_hhmm(s["until"]) if s.get("until") else None
            if s["status"] == "no crew":
                row = [at, "no crew", None]
            elif s["status"] == "times out" and until is not None and (dep is None or dep >= until - SOON):
                row = [at, "times out", until]
            else:
                row = [at, None, None]
            if rows and rows[-1][0] == at:
                rows[-1] = row
            elif not rows or rows[-1][1:] != row[1:]:
                rows.append(row)
        while rows and rows[0][1] is None:
            rows.pop(0)
        if rows:
            out[key] = rows
    return out


def advisories(task, inputs: Path, last: int) -> list[dict]:
    """The FAA's advisories for both hubs signed from LOOKBACK before local midnight to the last watch, as v1's news."""
    out = []
    for hub in sorted(task.programs):
        for day in utc_days(task.base - LOOKBACK, task.base + last):
            if (raw_advisories.day_dir(inputs, day) / "list.html").is_file():
                out += raw_advisories.news(raw_advisories.load_day(inputs, day, hub), hub, task.base, -LOOKBACK,
                                           last)
    return list({n["id"]: n for n in out}.values())


def metars(task, hub: str, inputs: Path, last: int) -> list[dict]:
    """The hub's METARs from two hours before the start to the last watch."""
    out = []
    for month in sorted({d[:7] for d in utc_days(task.base + task.start - 120, task.base + last)}):
        path = weather.metar_path(inputs, hub, month)
        if path.is_file():
            out += weather.metar_news(path.read_text(), hub, task.base, task.start - 120, last)
    return out


def view_task(task, hub: str, news: Sequence[Mapping]):
    """The task as stage.traffic reads a v1 task, at ``hub``: its flights, tails, METARs and advisories."""
    items = [SimpleNamespace(**{k: n[k] for k in ("id", "at", "kind", "text", "data")}) for n in news]
    return SimpleNamespace(task_id=task.task_id, hub=hub, date=task.date, tz=task.tz, start=task.start,
                           dmax=task.dmax, window=task.window, flights=task.flights, tails=task.tails, frozen=(),
                           news=[n for n in items if n.kind == "metar" or n.data.get("element") == hub])


def traffic(world: World, ep: Mapping, inputs: Path) -> dict:
    """stage.traffic for the episode's hub: every carrier's movements there that day, the task's flights among them."""
    return hub_stage.traffic(view_task(world.task, ep["task"]["hub"], ep["news"]), inputs)


def write_traffic(doc: Mapping, cache: Path) -> Path:
    path = cache / "traffic" / f"{doc['task_id']}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0))
    return path


def stage_of(ep: Mapping, cache: Path) -> dict | None:
    hub, task_id = ep["task"]["hub"], ep["task"]["task_id"]
    needs = (cache / "stage" / hub / "stage.json.gz", cache / "traffic" / f"{task_id}.json.gz")
    if not all(p.is_file() for p in needs):
        return None
    stem = f"{export.slug(ep['run'])}.{export.slug(ep['model'])}.{task_id}"
    return {"hub": hub, "url": f"../stage/{hub}/stage.json.gz", "traffic": f"{stem}.stage.json.gz"}


def projections(world: World, data: Mapping) -> list[list[dict]]:
    """Each watch's plan as the watch left it, run on the day as known then (the projection the desks saw): the
    recorded calls that change the day played again in order (lookups and checks left out, pages and reviews not
    drawn), checked against the recorded plan and cost."""
    out: list[list[dict]] = []

    class Quick(OccDay3 if world.passengers is not None else OccDay):
        def page(self, desk):
            return ""

        def review(self, plan):
            return {}

        def crew_confirm(self, desk, moves):
            return self.confirmed(desk, moves, self.crew_entry)

        def _close(self):
            super()._close()
            flights = self.project(self.plan).day.run.flights
            out.append([{"id": i, "status": r.status, "tail": r.tail, "dep": r.dep, "arr": r.arr,
                         "arr_delay": r.arr - self.task.flight(i).sched_arr if r.status == FLOWN else None,
                         "held": False, "decided": r.decided} for i, r in sorted(flights.items())])

    day = Quick(world, data["mode"])
    events = [(c["seq"], "call", c) for c in data["calls"] if c["tool"] in MUTATING or c["tool"] in SHARED] + \
        [(n["seq"], "notice", n) for n in data["notices"] if n["via"] in ("trigger", "late")]
    for _, kind, x in sorted(events, key=lambda e: e[0]):
        if kind == "notice":
            day.notice(x["news_id"], x["via"])
        else:
            day.call(ROLE[x["desk"]], x["tool"], x["args"])
    if data["done"] and not day.done:
        day.end()
    if json.loads(json.dumps({str(k): v for k, v in day.plan.items()})) != data["plan"] or \
            day.grade["cost"] != data["grade"]["cost"]:
        raise ValueError(f"{data['task_id']} {data['mode']}: the replay doesn't reach the recorded plan and cost")
    return out
