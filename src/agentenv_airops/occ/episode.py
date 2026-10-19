"""A recorded ops control center day as the viewer's Episode JSON (web/episode.schema.json): v1's shape at one hub, so
the 3D airport, the US map, the board and the timeline play it, and `occ`, what the room did: the requests between the
desks with their replies, each desk's decisions and the note it left at the end of each turn, and each flight's crew
status over the day for the tags at the gate.

The flights fly as the grade's executed rows (the day as it happened under the desks' plan); each watch's `projected`,
when given, is the plan as that watch left it on the day as known (`projections`, a replay). The FAA's advisories and
the hub's METARs are read from the raw inputs the pack was built from (cache/inputs), as v1's pack reads them."""

import gzip
import json
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
from ..programs import occ as programs
from .day import OccDay, reward
from .pack import World
from .replay import replay

SOON = 60
"""A crew's clock is worth a tag once the flight leaves within this many minutes of its last legal departure, as the
desks' crew flags read it (run.flags)."""
LOOKBACK = 720
MODES = {"joint": "two desks", "one": "one agent at both desks"}
OPS_CHANGES = {"cancel": "cancel", "cancel_pair": "cancel", "hold": "hold", "release": "release"}
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
        "occ": {
            "mode": data["mode"], "label": MODES.get(data["mode"], data["mode"]), "desks": list(data["desks"]),
            "requests": [request(r) for r in data["requests"]],
            "decisions": [{"t": c["t"], "desk": c["desk"], "action": c["action"], "flight": c["target"],
                           "detail": c["detail"], "ref": c["ref"], "via": c["via"]} for c in data["changes"]],
            "notes": [{"t": u["t"], "desk": u["desk"], "kind": u["kind"], "note": u["note"]}
                      for u in data["turns"] if u.get("note")],
            "absent": data["absent"],
            "crew": crew_tags(data["status"] or {}, {x["id"]: x for x in executed}),
            "refs": [{"label": "Best plan found", "reward": 1.0, "note": "the best plan over aircraft and crews found "
                                                                       "in hindsight"},
                     {"label": "Standard crew desk", "reward": round(reward(b0, best, b0), 6),
                      "note": "scripted standard crew-desk practice, aircraft doing nothing"}],
            "grade": {k: grade[k] for k in ("cost", "best", "bound", "B0", "flat", "gap_pct", "over_best_pct",
                                             "avoidable_share", "counts", "costs") if k in grade},
        },
    }
    ep["stage"] = stage_of(ep, cache)
    return ep


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


def request(r: Mapping) -> dict:
    p = r.get("params") or {}
    return {"id": r["id"], "sender": r["sender"], "receiver": r["receiver"], "type": r["type"],
            "flight": p.get("flight"), "time": p.get("time"), "text": r.get("text"), "sent": r["sent"],
            "status": r["status"], "reply": r.get("reply"), "replied": r.get("replied"),
            "reply_text": r.get("reply_text"),
            "actions": [{"desk": a["desk"], "action": a["action"], "flight": a.get("target"), "t": a.get("minute")}
                        for a in r.get("actions") or []]}


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
    """Each watch's plan as the watch left it, run on the day as known then (the projection the desks saw), from a
    replay of the recorded calls; the replay must be exact."""
    out: list[list[dict]] = []

    class Watched(OccDay):
        def _close(self):
            super()._close()
            flights = self.project(self.plan).day.run.flights
            out.append([{"id": i, "status": r.status, "tail": r.tail, "dep": r.dep, "arr": r.arr,
                         "arr_delay": r.arr - self.task.flight(i).sched_arr if r.status == FLOWN else None,
                         "held": False, "decided": r.decided} for i, r in sorted(flights.items())])

    replayed = replay(world, dict(data), Watched)
    if not replayed.exact:
        raise ValueError(f"{data['task_id']} {data['mode']}: the replay differs at {replayed.mismatches[:3]}")
    return out
