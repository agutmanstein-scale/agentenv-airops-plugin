"""What the desk sees at a watch (docs/v4/DESIGN.md §5), as a JSON-able dict and as text in v1's style: the time and the
freeze line, the programs as issued (sim.known's GDP segments, a proposed one included, and ground-stop pieces), the
departure caps and outstation arrival caps known so far (the truth's `layers.dep_caps` and `layers.arr_caps` for the
hours within CAP_LOOK, as sim.known has them), the ramp closures under way or over (sim.known's: begun by the watch,
each with its end once reported, else the end expected), the latest METAR or SPECI and TAF delivered per hub, every
flight on the day as known (status, known delay, the plan, where the plan has it leave and land, its slot or hold, its
crew's deadline and rest, and for a departure from a base with a reserve pool whether the plan has a reserve crew on
it), each such base's reserve pool and what the plan's calls leave of it (sim.granted), the aircraft as the plan has
them now, and the news delivered so far."""

from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING

from ..core.known import CAP_LOOK, LOOK
from ..core.model import hhmm
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN
from ..day import fid
from ..programs import sim
from ..programs.advisories import PROPOSED
from ..programs.model import Task
from .clock import WEATHER, News

if TYPE_CHECKING:
    from .day import Day

KEYS = ("watch", "time", "clock", "freeze_line", "frozen", "cost", "programs", "dep_caps", "arr_caps", "closures",
        "weather", "reserves", "flights", "aircraft", "news")


def build(day: "Day") -> dict:
    task, t, run, world = day.task, day.t, day.project(day.plan), day.known()
    return {"watch": day.watch, "time": hhmm(t), "clock": hhmm(day.clock), "freeze_line": _time(day.line),
            "frozen": len(day.frozen), "cost": run.cost.to_dict() | {"counts": run.counts},
            "programs": programs(task, world), "dep_caps": dep_caps(task, t), "arr_caps": arr_caps(task, t),
            "closures": closures(world, t), "weather": weather(day),
            "reserves": reserves(task, run.reserved),
            "flights": [flight_row(day, f.id, run.flights[f.id], run.reserved)
                        for f in sorted(task.flights, key=lambda f: (f.sched_dep, f.id))],
            "aircraft": aircraft(task, run, day.clock), "news": [day.items[n].to_dict() for n in day.delivered]}


def programs(task: Task, world: sim.World) -> dict:
    """The programs of the day as known, per hub: GDP segments (proposed or issued) and stop pieces."""
    kinds = {a.id: a.kind for p in task.programs.values() for a in p.advisories}
    return {h: {"gdps": [{"id": s.id, "kind": "proposed" if kinds.get(s.id) == PROPOSED else "gdp",
                          "period": [hhmm(s.start), hhmm(s.end)], "governs": [hhmm(s.since), hhmm(s.until)],
                          "jetblue_slots": [hhmm(x) for x in s.slots]} for s in world.segments.get(h, ())],
                "stops": [{"chain": p.chain, "from": hhmm(p.start), "to": hhmm(p.end)} for p in world.stops
                          if p.hub == h]}
            for h in sorted(task.programs)}


def dep_caps(task: Task, t: int) -> dict[str, dict[str, int]]:
    """JetBlue's departure caps per station and hour known at t: the hours starting within CAP_LOOK."""
    return _hours(task.layers.dep_caps if task.layers else {}, t)


def arr_caps(task: Task, t: int) -> dict[str, dict[str, int]]:
    """JetBlue's arrival caps per outstation and hour known at t: the hours starting within CAP_LOOK."""
    return _hours(task.layers.arr_caps if task.layers else {}, t)


def _hours(caps: Mapping[str, Mapping[int, int]], t: int) -> dict[str, dict[str, int]]:
    return {s: {f"{h % 24:02d}" + (f"+{h // 24}" if h >= 24 else ""): c for h, c in sorted(by.items())
                if 60 * h <= t + CAP_LOOK} for s, by in sorted(caps.items())}


def closures(world: sim.World, t: int) -> dict[str, list[dict]]:
    """The ramp closures the day as known at watch t has per hub: from, to, whether that end is reported (it has come
    by t; else it is expected, sim.known), and JetBlue's departure and arrival caps over it."""
    return {s: [{"from": hhmm(a), "to": hhmm(b), "reported": b <= t, "dep_cap": dep, "arr_cap": arr}
                for a, b, dep, arr in spans] for s, spans in sorted(world.closures.items())}


def weather(day: "Day") -> dict[str, dict]:
    """Per hub the latest observation (METAR or SPECI) and the latest TAF delivered so far: the one observed or issued
    last, of those observed or issued at one minute the last delivered (kinds' lags differ, so an older one can
    arrive with or after a newer one)."""
    latest: dict[tuple[str, str], News] = {}
    for n in (day.items[i] for i in day.delivered):
        if n.kind in WEATHER:
            key = (n.hub, "taf" if n.kind == "taf" else "observation")
            if key not in latest or n.at >= latest[key].at:
                latest[key] = n
    out: dict[str, dict] = {}
    for (hub, kind), n in sorted(latest.items()):
        out.setdefault(hub, {})[kind] = n.to_dict()
    return out


def reserves(task: Task, granted: frozenset[int]) -> dict[str, dict]:
    """Each crew base's reserve pool, the flights the plan has on its reserve crews and how many are left."""
    pools = task.layers.reserves if task.layers else {}
    on = {b: sorted(i for i in granted if task.flight(i).orig == b) for b in pools}
    return {b: {"pool": n, "called": [fid(i) for i in on[b]], "left": n - len(on[b])} for b, n in sorted(pools.items())}


def flight_row(day: "Day", i: int, r: sim.FlightRun, granted: frozenset[int] = frozenset()) -> dict:
    f, entry = day.task.flight(i), day.plan.get(i)
    row = {"id": fid(i), "flight": f.flight, "orig": f.orig, "dest": f.dest, "sched_dep": hhmm(f.sched_dep),
           "sched_arr": hhmm(f.sched_arr), "pax": f.pax, "family": f.family, "status": status(day, i, r),
           "known_delay": f.lb if f.sched_dep <= day.t + LOOK else None, "plan": plan_text(entry),
           "hold": _time(entry.get("not_before")) if entry and not entry.get("cancel") else None,
           "dep": _time(r.dep), "arr": _time(r.arr),
           "late": r.arr - f.sched_arr if r.status == FLOWN else None, "tail": r.tail,
           "slot": {"program": r.program, "at": hhmm(r.slot)} if r.slot is not None else None}
    if f.crew:
        c = day.task.crew(f)
        row["crew"] = {"pairing": c.pairing, "deadline": _time(c.deadline), "rest": _time(c.rest)}
        if day.task.reservable(f):
            row["crew"]["reserve"] = i in granted
    return row


def status(day: "Day", i: int, r: sim.FlightRun) -> str:
    if r.status == FLOWN and r.dep < day.clock:
        return "flown" if r.arr <= day.clock else "airborne"
    if i not in day.frozen:
        return {CANCELLED: "cancelled", AUTO_CANCELLED: "auto-cancel"}.get(r.status, "open")
    return "cancelled" if r.status == CANCELLED else "frozen"


def aircraft(task: Task, run: sim.Execution, t: int) -> dict:
    """Where each aircraft is at t as the plan has it: at a station (ready then, after its turn and any Move it is
    bound to), or airborne on a leg."""
    cancelled = {i for i, r in run.flights.items() if r.status == CANCELLED}
    carried, own = sim.routes(task, cancelled)
    at: dict[str, list[dict]] = defaultdict(list)
    airborne = []
    for tail in task.tails:
        station, ready = tail.pos, tail.ready
        if tail.tail in own:
            station, ready = sim.through(own[tail.tail], ready, {})
        for i in run.legs.get(tail.tail, ()):
            r = run.flights[i]
            if r.dep >= t:
                break
            f = task.flight(i)
            if r.arr > t:
                airborne.append({"tail": tail.tail, "flight": fid(i), "to": f.dest, "lands": hhmm(r.arr)})
                station = None
                break
            back = task.back(f, r.arr)
            station, ready = sim.through(carried[i], back, {}) if i in carried else (f.dest, back)
        if station is not None:
            at[station].append({"tail": tail.tail, "family": tail.family, "ready": _time(ready if ready > t else None)})
    return {"stations": {s: at[s] for s in sorted(at, key=lambda s: (s not in task.storms, s))}, "airborne": airborne}


def render(task: Task, s: Mapping) -> str:
    hubs = "/".join(task.storms)
    freeze = ("First watch: nothing is frozen; changes take effect at once." if s["freeze_line"] is None else
              f"Freeze line {s['freeze_line']}: flights your plan has leaving before it are frozen ({s['frozen']} "
              "now), cancellations are final, and no other flight leaves before it.")
    c, n = s["cost"], s["cost"]["counts"]
    weekday = date.fromisoformat(task.date).strftime("%A")
    parts = [f"# JetBlue operations desk, {hubs}, {weekday} {task.date}; times are local ({task.tz}).",
             f"Now {s['clock']} (watch {s['watch']}, opened {s['time']}). {freeze}",
             f"Your plan on the day as known costs {c['total']:,} (delay {c['delay']:,}, cancellations {c['cancel']:,},"
             f" notice {c['notice']:,}, ferries {c['ferry']:,}): {n[CANCELLED]} cancelled, {n[AUTO_CANCELLED]} "
             f"cancelled automatically, {n['slots']} on slots.",
             _programs_text(s["programs"]), _caps_text(s["dep_caps"]), _arr_caps_text(s["arr_caps"]),
             _closures_text(s["closures"]), _weather_text(s["weather"]), *_reserves_text(s["reserves"]),
             _flights_text(s["flights"]),
             _aircraft_text(s["aircraft"]), _news_text(s["news"])]
    return "\n\n".join(parts)


def _programs_text(p: Mapping) -> str:
    lines = []
    for h, by in p.items():
        for g in by["gdps"]:
            name = "proposed ground delay program" if g["kind"] == "proposed" else "ground delay program"
            slots = " ".join(g["jetblue_slots"]) or "none"
            lines.append(f"- {h} {name} {g['id']}: arrivals {g['period'][0]}-{g['period'][1]}, governs departures "
                         f"{g['governs'][0]}-{g['governs'][1]}; JetBlue's slots {slots}")
        lines += [f"- {h} ground stop {x['chain']}: {x['from']}-{x['to']}" for x in by["stops"]]
    return "## Programs as issued (FAA Command Center)\n" + ("\n".join(lines) or "None.")


def _caps_text(caps: Mapping) -> str:
    lines = [f"{st}: " + " ".join(f"{h}h {c}" for h, c in by.items()) for st, by in caps.items() if by]
    return (f"## Departure caps: JetBlue departures per hour, known {CAP_LOOK} min ahead\n"
            + ("\n".join(lines) or "None known."))


def _arr_caps_text(caps: Mapping) -> str:
    lines = [f"{st}: " + " ".join(f"{h}h {c}" for h, c in by.items()) for st, by in caps.items() if by]
    return (f"## Outstation arrival caps: JetBlue arrivals per hour, known {CAP_LOOK} min ahead\n"
            + ("\n".join(lines) or "None known."))


def _closures_text(shut: Mapping) -> str:
    lines = [f"- {st} {c['from']}-{c['to']}{'' if c['reported'] else ' (end expected)'}: at most {c['dep_cap']} "
             f"departures and {c['arr_cap']} arrivals" for st, spans in shut.items() for c in spans]
    return ("## Ramp closures (lightning at the field; known once begun)\n" + ("\n".join(lines) or "None."))


def _weather_text(wx: Mapping) -> str:
    lines = [f"[{n['at']}] {n['text']}" for by in wx.values() for n in by.values()]
    return "## Latest weather (METAR or SPECI, and TAF)\n" + ("\n".join(lines) or "None.")


def _reserves_text(pools: Mapping) -> list[str]:
    """The reserve pools, when the day has any: a base departure past its crew deadline flies only on one."""
    if not pools:
        return []
    lines = [f"{b}: {p['left']} of {p['pool']} left" + (f" (called: {' '.join(p['called'])})" if p["called"] else "")
             for b, p in pools.items()]
    return ["## Reserve crews (call_reserve: a departure from the base may leave past its crew deadline on one; "
            "granted in flight-id order while the pool lasts; release_reserve gives it back)\n" + "\n".join(lines)]


def _flights_text(rows: list[dict]) -> str:
    out = ["## Flights (sched = scheduled dep-arr; delay = known delay, ? not yet; plan = cancel or hold@HH:MM; "
           "proj = where your plan has it leave and land on the day as known; late = arrival delay; slot = its GDP "
           "slot; crew = crew deadline/rest end, +R on a reserve crew)",
           "id flight route sched pax type status delay plan proj late tail slot crew"]
    for r in rows:
        proj = f"{r['dep']}-{r['arr']}" if r["dep"] else "-"
        late = f"{r['late']:+d}" if r["late"] is not None else "-"
        slot = f"{r['slot']['program']}@{r['slot']['at']}" if r["slot"] else "-"
        crew = f"{r['crew']['deadline'] or '-'}/{r['crew']['rest'] or '-'}" if r.get("crew") else "-"
        crew += "+R" if r.get("crew", {}).get("reserve") else ""
        delay = "?" if r["known_delay"] is None else str(r["known_delay"])
        out.append(f"{r['id']} {r['flight'].split()[-1]} {r['orig']}-{r['dest']} {r['sched_dep']}-{r['sched_arr']} "
                   f"{r['pax']} {r['family']} {r['status']} {delay} {r['plan']} {proj} {late} {r['tail'] or '-'} "
                   f"{slot} {crew}")
    return "\n".join(out)


def _aircraft_text(a: Mapping) -> str:
    lines = ["## Aircraft now, as your plan has them (@ = ready at)"]
    for s, tails in a["stations"].items():
        lines.append(f"{s}: " + " ".join(x["tail"] + (f"@{x['ready']}" if x["ready"] else "") for x in tails))
    lines.append("airborne: " + (" ".join(f"{x['tail']} {x['flight']}>{x['to']}@{x['lands']}" for x in a["airborne"])
                                 or "none"))
    return "\n".join(lines)


def _news_text(items: list[dict]) -> str:
    return "## News so far\n" + ("\n".join(f"[{n['at']}] {n['text']}" for n in items) or "None.")


def plan_text(e: Mapping | None) -> str:
    if e and e.get("cancel"):
        return "cancel"
    text = f"hold@{hhmm(e['not_before'])}" if e and e.get("not_before") is not None else "-"
    return (text + "+reserve" if text != "-" else "reserve") if e and e.get("reserve") else text


def proj_text(task: Task, r: sim.FlightRun) -> str:
    if r.status == FLOWN:
        return f"{hhmm(r.dep)}-{hhmm(r.arr)} {r.arr - task.flight(r.id).sched_arr:+d} {r.tail}"
    return "cancelled" if r.status == CANCELLED else "auto-cancel"


def _time(minute: int | None) -> str | None:
    return None if minute is None else hhmm(minute)
