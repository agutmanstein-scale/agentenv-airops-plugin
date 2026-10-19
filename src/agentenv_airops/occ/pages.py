"""What the desks read: the page each turn opens with (ops, crew, or both in one-agent mode) and the lookups, all
compact text on the projection at the watch. A page lists only what needs attention; the rest is a lookup. Times are
the hub's clock, "+1" after midnight; flights are f<id>, crew units by their ids."""

from collections import Counter, defaultdict
from collections.abc import Mapping

from ..core.known import LOOK
from ..core.model import hhmm, parse_hhmm
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN
from ..programs import crews, crewsim, sim
from ..programs.advisories import GDP, GDP_CNX, GS, GS_CNX, PROPOSED, Advisory
from ..programs.cost import cancel_price
from . import run

ROWS, LOOKUP_ROWS, CREW_ROWS = 30, 60, 40
AHEAD, LATE, NEAR, SLOT_HOURS = 240, 30, 180, 6
KINDS = {GDP: "GDP", PROPOSED: "proposed GDP", GDP_CNX: "GDP cancelled", GS: "ground stop", GS_CNX: "ground stop "
         "cancelled"}
STATUSES = ("open", "attention", "frozen", "flown", "cancelled")
OPTION = {"own": "(a) no move needed, its own crew", "standby": "(b) a reserve there", "call": "(b) call a reserve",
          "swap": "(c) swap", "deadhead": "(d) deadhead", "extend": "(e) extension"}


def fid(i: int) -> str:
    return f"f{i}"


def jsonable(plan: Mapping) -> dict:
    return {str(k): v for k, v in plan.items()}


def news(task) -> dict[str, dict]:
    """The FAA's advisories at the two hubs as one-line items, each at its signature."""
    out = {}
    for hub, p in sorted(task.programs.items()):
        for a in p.advisories:
            out[a.id] = {"id": a.id, "at": a.issued, "hub": hub, "kind": a.kind, "text": advisory_text(a)}
    return out


def advisory_text(a: Advisory) -> str:
    what = KINDS.get(a.kind, a.kind)
    parts = [f"{hhmm(a.issued)} FAA {a.hub} {what}"]
    if a.start is not None:
        parts.append(f"{hhmm(a.start)}-{hhmm(a.end)}" if a.end is not None else f"from {hhmm(a.start)}")
    if a.rates:
        parts.append(f"rate {'/'.join(map(str, sorted(set(a.rates))))}/h all carriers")
    if a.avg_delay is not None:
        parts.append(f"avg delay {a.avg_delay}, max {a.max_delay}")
    if a.extension:
        parts.append(f"extension {a.extension.lower()}")
    if a.scope is not None:
        parts.append(scope_text(a.scope))
    if a.only:
        parts.append(f"only {a.only} (JetBlue not held)")
    return ", ".join(parts) + f" [{a.id}]"


def scope_text(s) -> str:
    if s.every:
        out = "all origins"
    else:
        out = " ".join(sorted(s.artccs)) + (f" within {s.nm} NM" if s.nm is not None else "")
    return out.strip() + (f", exempt {' '.join(sorted(s.exempt))}" if s.exempt else "")


def slot_text(r) -> str:
    return "no slot" if r.slot is None else f"slot {hhmm(r.slot)} ({r.program})"


def proj_text(r, f) -> str:
    if r.status == FLOWN:
        return f"{hhmm(r.dep)}-{hhmm(r.arr)} +{max(0, r.arr - f.sched_arr)}"
    return "cancelled" if r.status == CANCELLED else f"auto-cancels {hhmm(r.decided)}"


def crew_text(c: Mapping) -> str:
    return f" P {c['pilot']} C {c['cabin']}" if c.get("pilot") and c.get("cabin") else \
        "".join(f" {k[0].upper()} {v}" for k, v in sorted(c.items()))


def flag_text(kinds: Mapping) -> str:
    return "/".join(flag(kinds.get(k, (run.LEGAL, None))) for k in crews.KINDS)


def flag(v) -> str:
    name, minute = v
    return name if minute is None else f"{name} {hhmm(minute)}"


def plan_text(day, i: int) -> str:
    e = day.plan.get(i, {})
    if e.get("cancel"):
        return "cancel"
    out = []
    if (nb := run_hold(e, day.t)) is not None:
        out.append(f"hold {hhmm(nb)}")
    if (c := run_claim(e, day.t)) is not None:
        out.append(f"claim {hhmm(c)}")
    return " ".join(out) or "-"


def run_hold(e: Mapping, t: int) -> int | None:
    return next((nb for d, nb in reversed(e.get("holds", ())) if d <= t), None)


def run_claim(e: Mapping, t: int) -> int | None:
    return next((c for d, c in reversed(e.get("claims", ())) if d <= t), None)


def header(day, desk: str) -> str:
    task = day.task
    kind = {"crew": "crew turn", "ops": "ops turn", "reply": "crew reply turn", "one": "your turn"}[day.turn["kind"]]
    committed = sum(not day.gone(i) and not day.cancelled(i) for i in day.committed)
    line = "nothing is committed yet; holds from now" if day.line is None else \
        f"freeze line {hhmm(day.line)}: holds go no earlier, and {committed} open flights are committed"
    return (f"# {'Ops controller' if desk == 'ops' else 'Crew scheduler' if desk == 'crew' else 'Ops control'}, "
            f"JetBlue, {task.date}, {hhmm(day.t)} (watch {day.k + 1} of {len(day.times)}, {kind}); {line}.")


def cost_line(proj, desk: str) -> str:
    c = proj.day.run.cost
    crew = proj.day.cost
    statuses = Counter(x.status for x in proj.day.run.flights.values())
    tail = f"; {statuses[CANCELLED]} cancelled, {statuses[AUTO_CANCELLED]} auto-cancel"
    if desk == "crew":
        return (f"Crew cost of the day as projected: deadhead {crew['deadhead']:,.0f}, hotel {crew['hotel']:,.0f}, end "
                f"of day {crew['end']:,.0f} pax-min{tail}.")
    return (f"The plan as projected costs {proj.day.total:,.0f} pax-min: delay {c.delay:,}, cancel {c.cancel:,}, "
            f"notice {c.notice:,}, aircraft {c.gate + c.overrun - c.saved + c.ferry:,}, crew "
            f"{sum(crew.values()):,.0f}{tail}.")


def page(day, desk: str, proj, flags) -> str:
    parts = [header(day, desk)]
    if desk in ("ops", "one"):
        parts.append(cost_line(proj, "ops"))
    if desk in ("crew", "one"):
        parts.append(cost_line(proj, "crew"))
    parts.append(messages(day, desk))
    if desk in ("ops", "one"):
        parts.append(attention(day, proj, flags))
        parts.append(programs(day, proj))
        parts.append(spares(day, proj))
    if desk in ("crew", "one"):
        parts.append(alerts(day, desk, proj))
        parts.append(near(day, proj))
        parts.append(reserve_line(day, proj))
    return "\n\n".join(p for p in parts if p)


def messages(day, desk: str) -> str:
    lines = []
    if desk in ("ops", "one"):
        fresh = [n for n in day.notices if n["news_id"] not in day.seen[desk]]
        day.seen[desk] |= {n["news_id"] for n in fresh}
        lines += [f"FAA: {day.news[n['news_id']]['text']}" for n in fresh]
    for m in day.inbox[desk]:
        lines.append(f"{m['request']} {m['status']}" + (f": {m.get('why') or m.get('text')}" if m.get("why") or
                                                         m.get("text") else ""))
    day.inbox[desk] = []
    for r in day.requests:
        if r["receiver"] == desk and r["status"] == "open":
            what = r["type"] + (f" {fid(r['params']['flight'])}" if "flight" in r["params"] else "") + \
                (f" {'until' if r['type'] == 'HOLD' else 'by'} {hhmm(r['params']['time'])}" if "time" in r["params"]
                 else "")
            lines.append(f"{r['id']} from {r['sender']} at {hhmm(r['sent'])}: {what}" +
                         (f' "{r["text"]}"' if r["text"] else ""))
    return "## Messages\n" + ("\n".join(lines) if lines else "None new.")


def attention(day, proj, flags) -> str:
    rows = attention_rows(day, proj, flags)
    out = ["## Attention: open flights in the next 4 h late 30+ min, auto-cancelling, with a crew flag or a request",
           "id flight route sched pax type delay plan proj(dep-arr +late) slot pilot/cabin deadline"]
    out += [row_text(day, proj, flags, i) for i in rows[:ROWS]]
    if len(rows) > ROWS:
        out.append(f'{len(rows) - ROWS} more: ops_flights(status="attention")')
    if not rows:
        out.append("None.")
    return "\n".join(out)


def attention_rows(day, proj, flags) -> list[int]:
    task, t = day.task, day.t
    asked = {r["params"].get("flight") for r in day.requests if r["status"] == "open"}
    out = []
    for i in sorted(flags, key=lambda i: (task.flight(i).sched_dep, i)):
        f, r = task.flight(i), proj.day.run.flights[i]
        soon = min(f.sched_dep, r.dep if r.dep is not None else f.sched_dep) < t + AHEAD
        late = r.status == FLOWN and r.arr - f.sched_arr >= LATE
        if soon and (late or r.status == AUTO_CANCELLED or i in asked or
                     any(v[0] != run.LEGAL for v in flags[i].values())):
            out.append(i)
    return out


def row_text(day, proj, flags, i: int, units: bool = False) -> str:
    task, t = day.task, day.t
    f, r = task.flight(i), proj.day.run.flights[i]
    delay = f.lb if f.sched_dep <= t + LOOK else "?"
    out = (f"{fid(i)} {f.flight.split()[-1]} {f.orig}-{f.dest} {hhmm(f.sched_dep)} {f.pax} {f.family} {delay} "
           f"{plan_text(day, i)} {proj_text(r, f)} {'-' if r.slot is None else hhmm(r.slot)} "
           f"{flag_text(flags.get(i, {}))} {hhmm(f.deadline)}")
    if units and (c := proj.day.crews.get(i)):
        out += crew_text(c)
    return out


def programs(day, proj) -> str:
    out = ["## FAA programs as issued (JetBlue's slots / its scheduled arrivals, by hour)"]
    known = day.known_at(day.t)
    for hub in day.task.storms:
        segs = [s for s in known.segments.get(hub, ()) if s.until > day.t]
        stops = [s for s in known.stops if s.hub == hub and s.end > day.t]
        if not segs and not stops:
            out.append(f"{hub}: none")
            continue
        for s in stops:
            out.append(f"{hub} ground stop {hhmm(s.start)}-{hhmm(s.end)} ({scope_text(s.scope)})")
        sched = Counter(f.sched_arr // 60 for f in day.task.flights if f.dest == hub)
        for s in segs:
            have = Counter(x // 60 for x in s.slots)
            hours = [h for h in range(max(day.t, s.start) // 60, s.end // 60 + 1)][:SLOT_HOURS]
            out.append(f"{hub} GDP {s.id} arrivals {hhmm(s.start)}-{hhmm(s.end)}, governs departures to "
                       f"{hhmm(s.until)}: " + " ".join(f"{h % 24:02d}h {have[h]}/{sched[h]}" for h in hours))
    return "\n".join(out)


def spares(day, proj) -> str:
    task, t = day.task, day.t
    nxt = {}
    for tail, legs in proj.day.run.legs.items():
        later = [proj.day.run.flights[i].dep for i in legs if proj.day.run.flights[i].dep >= t]
        nxt[tail] = min(later, default=None)
    family = {x.tail: x.family for x in task.tails}
    idle = Counter()
    for tail, (station, _, airborne) in proj.aircraft.items():
        if not airborne and (nxt.get(tail) is None or nxt[tail] > t + NEAR):
            idle[(station, family[tail])] += 1
    short = Counter((x.dest, x.family) for x in proj.day.run.ferries)
    line = " ".join(f"{s} {fam} {n}" for (s, fam), n in sorted(idle.items(), key=lambda kv: (-kv[1], kv[0]))[:16])
    out = ["## Aircraft", f"Idle 3 h or more: {line or 'none'}"]
    if short:
        out.append("Short for tomorrow (ferried tonight as projected): " +
                   " ".join(f"{s} {fam} {n}" for (s, fam), n in sorted(short.items())))
    return "\n".join(out)


def alerts(day, desk: str, proj) -> str:
    t = day.t
    seen = day.seen[desk]
    fixes = {(x["flight"], x["kind"], x["t"]): x for x in day.truth.trace.get("fixes", ())}
    now = [m for m in day.truth.messages if m["sent"] <= t and key(m) not in seen]
    seen |= {key(m) for m in now}
    latest = {}
    for m in sorted(now, key=lambda m: m["sent"]):
        latest[m["params"]["flight"]] = m
    ahead = {}
    for m in sorted(proj.day.messages, key=lambda m: m["sent"]):
        if m["sent"] > t and m["params"]["flight"] not in latest:
            ahead[m["params"]["flight"]] = m
    out = ["## Crew system alerts (the standard crew desk's word on each flight it works; its fix when it found one)"]
    rows = [alert_text(day, m, fixes, "now") for m in sorted(latest.values(), key=lambda m: (m["sent"],
                                                                                            m["params"]["flight"]))]
    rows += [alert_text(day, m, {}, "projected") for m in sorted(ahead.values(), key=lambda m: (m["sent"],
                                                                                               m["params"]["flight"]))]
    out += rows[:ROWS]
    if len(rows) > ROWS:
        out.append(f'{len(rows) - ROWS} more: crew_flights(status="attention")')
    if not rows:
        out.append("None.")
    return "\n".join(out)


def key(m: Mapping) -> tuple:
    return m["sent"], m["type"], m["params"]["flight"], m["params"]["minute"]


def alert_text(day, m: Mapping, fixes: Mapping, when: str) -> str:
    i, p = m["params"]["flight"], m["params"]
    f = day.task.flight(i)
    what = "CANCEL: no crew can reach it by its deadline" if m["type"] == crewsim.CANCEL else \
        f"HOLD {hhmm(p['minute'])} ({'its crew comes' if p['reason'] == 'coming' else 'crew ready then'})"
    out = (f"{when} {fid(i)} {f.flight.split()[-1]} {f.orig}-{f.dest} {hhmm(f.sched_dep)} {p['kind']}: {what}, "
           f"at {hhmm(m['sent'])}")
    if (x := fixes.get((i, p["kind"], m["sent"]))) is not None:
        out += f"; fix {OPTION.get(x['act'], x['act'])} {x['unit']} cost {x['cost']:,.0f} leaves {hhmm(x['dep'])}"
    return out


def near(day, proj) -> str:
    t = day.t
    rows = []
    for uid, s in sorted(proj.units.items()):
        legs = [j for j in s["trip"] if not day.gone(j) and not day.cancelled(j)]
        if s["limit"] is None or not legs or not t <= s["limit"] <= t + NEAR or (s["release"] is not None and
                                                                                 s["release"] <= t):
            continue
        nxt = " ".join(f"{fid(j)} {hhmm(day.task.flight(j).sched_dep)} {day.task.flight(j).orig}-"
                       f"{day.task.flight(j).dest}" for j in legs[:3])
        rows.append((s["limit"], f"{uid} at {s['station']}: last block-in {hhmm(s['limit'])}, next {nxt}"))
    out = ["## Crews within 3 h of their limits with legs left (pilots with the 2 h extension)"]
    out += [r for _, r in sorted(rows)[:ROWS]] or ["None."]
    if len(rows) > ROWS:
        out.append(f"{len(rows) - ROWS} more: crew_crews(...)")
    return "\n".join(out)


def reserve_line(day, proj) -> str:
    by = defaultdict(lambda: [0, None, 0, None])
    for uid, s in proj.units.items():
        u = day.units[uid]
        if not u.reserve:
            continue
        k = (u.home, u.kind, u.group)
        first = crews.on_hour(max(day.t, u.rap or 0, day.task.start) + u.lead)
        if u.reserve == "short" and s["report"] is None and s["held"] is None and first - u.lead <= u.until:
            by[k][0] += 1
            by[k][1] = min(x for x in (by[k][1], first) if x is not None)
        elif u.reserve == "standby" and not s["trip"] and s["held"] is None and u.until and u.until > day.t and \
                s["station"] == u.home and s["landed"] is None:
            by[k][2] += 1
            by[k][3] = max(x for x in (by[k][3], u.until) if x is not None)
    out = ["## Reserves free (short-call: count, earliest report; airport standby: count, until)"]
    out += [f"{b} {kind} {g}: short {n}" + (f" from {hhmm(e)}" if e is not None else "") +
            (f", standby {m} until {hhmm(until)}" if m else "")
            for (b, kind, g), (n, e, m, until) in sorted(by.items()) if n or m] or ["None."]
    return "\n".join(out)


def window(value, default: int | None) -> int | None:
    return default if value is None else parse_hhmm(str(value))


def flights(day, ids, station, status, start, end, units: bool) -> dict:
    """At most LOOKUP_ROWS flights matching every filter given, by scheduled departure."""
    proj = day.project(day.plan)
    flags = run.flags(proj, day.task)
    if status is not None and status not in STATUSES:
        return {"error": f"status is one of {', '.join(STATUSES)}"}
    try:
        lo, hi = window(start, None), window(end, None)
    except ValueError as e:
        return {"error": str(e)}
    pick = None
    if ids is not None:
        try:
            pick = {int(str(x).strip().lower().lstrip("f")) for x in ids}
        except ValueError:
            return {"error": "ids are flight ids like f12"}
    attention = set(attention_rows(day, proj, flags)) if status == "attention" else set()
    if status == "attention" and units:
        attention |= {m["params"]["flight"] for m in proj.day.messages}
    out = []
    for f in sorted(day.task.flights, key=lambda f: (f.sched_dep, f.id)):
        i = f.id
        if pick is not None and i not in pick or station is not None and station not in (f.orig, f.dest):
            continue
        if lo is not None and f.sched_dep < lo or hi is not None and f.sched_dep > hi:
            continue
        gone, cancelled = day.gone(i), day.cancelled(i)
        state = "flown" if gone else "cancelled" if cancelled else "frozen" if i in day.committed else "open"
        if status is not None and status != state and not (status == "attention" and i in attention) and \
                not (status == "open" and state == "frozen"):
            continue
        if status == "attention" and i not in attention:
            continue
        line = row_text(day, proj, flags, i, units)
        extra = []
        if not gone and not cancelled:
            extra.append(f"cancel now {cancel_price(f, day.t):,}")
        if (j := day.w.partner.get(i)) is not None:
            extra.append(f"pair {fid(j)}")
        out.append(f"{line} [{state}{'; ' + '; '.join(extra) if extra else ''}]")
    rows = out[:LOOKUP_ROWS]
    return {"columns": "id flight route sched pax type delay plan proj slot pilot/cabin deadline" +
            (" crews" if units else "") + " [status; cancel cost now; round-trip partner]", "rows": rows,
            "more": len(out) - len(rows)}


def program(day, hub) -> dict:
    if hub not in day.task.programs:
        return {"error": f"hub is one of {', '.join(sorted(day.task.programs))}"}
    t = day.t
    proj = day.project(day.plan)
    issued = [day.news[n["news_id"]]["text"] for n in day.notices if day.news[n["news_id"]]["hub"] == hub]
    known = day.known_at(t)
    segs = known.segments.get(hub, ())
    holders = {}
    for i, x in proj.day.run.flights.items():
        if x.slot is not None:
            holders[(x.program, x.slot)] = i
    claimed = {}
    for i, e in day.plan.items():
        if isinstance(i, int) and (c := run_claim(e, t)) is not None:
            claimed[c] = i
    out = {"advisories": issued[-20:], "programs": []}
    for s in segs:
        if s.until <= t and s.end < t:
            continue
        slots = []
        for m in s.slots:
            if m < t:
                continue
            i = holders.get((s.id, m))
            tag = "free" if i is None else fid(i) + ("*" if claimed.get(m) == i else "")
            slots.append(f"{hhmm(m)} {tag}")
        controlled = [fid(i) for i, x in proj.day.run.flights.items() if x.status == FLOWN and x.program is None and
                      day.task.flight(i).dest == hub and x.dep >= t and
                      sim_governs(day, known, i, x.dep) == s.id]
        out["programs"].append({"id": s.id, "arrivals": f"{hhmm(s.start)}-{hhmm(s.end)}",
                                "governs": f"{hhmm(s.since)}-{hhmm(s.until)}", "slots": slots[:LOOKUP_ROWS],
                                "without_slot": controlled[:20]})
    out["stops"] = [f"{hhmm(s.start)}-{hhmm(s.end)} {scope_text(s.scope)}" for s in known.stops if s.hub == hub
                    and s.end > t]
    out["note"] = "* = held by a claim; a flight's EDCT is its slot less its scheduled block"
    return out


def sim_governs(day, known, i: int, dep: int) -> str | None:
    seg = sim.Rules(day.task, known).governs(day.task.flight(i), dep)
    return None if seg is None else seg.id


def aircraft(day, station) -> dict:
    proj = day.project(day.plan)
    family = {x.tail: x.family for x in day.task.tails}
    rows = []
    for tail, (where, ready, airborne) in sorted(proj.aircraft.items(), key=lambda kv: (kv[1][0], kv[0])):
        if station is not None and where != station:
            continue
        legs = [i for i in proj.day.run.legs.get(tail, ()) if proj.day.run.flights[i].dep >= day.t]
        nxt = " ".join(f"{fid(i)} {hhmm(proj.day.run.flights[i].dep)}" for i in legs[:4])
        rows.append(f"{tail} {family[tail]} {'to ' if airborne else 'at '}{where} ready {hhmm(ready)}"
                    f"{' next ' + nxt if nxt else ' idle'}")
    needs = Counter((fam, s) for (fam, s), n in day.task.needed.items() for _ in range(n)
                    if station is None or s == station)
    ferries = [f"{x.tail} {x.family} {x.orig}-{x.dest} {x.cost:,}" for x in proj.day.run.ferries
               if station is None or station in (x.orig, x.dest)]
    return {"aircraft": rows[:LOOKUP_ROWS], "more": max(0, len(rows) - LOOKUP_ROWS),
            "tomorrow": " ".join(f"{s} {fam} {n}" for (fam, s), n in sorted(needs.items()))[:2000],
            "ferries_tonight": ferries[:30]}


def crews_rows(day, proj, uid, station, base, kind, group, state) -> list[str]:
    t, out = day.t, []
    for u in day.w.units:
        s = proj.units[u.id]
        if uid is not None and u.id != uid or station is not None and s["station"] != station or \
                base is not None and u.home != base or kind is not None and u.kind != kind or \
                group is not None and u.group != group:
            continue
        on = s["report"] is not None and (s["release"] is None or s["release"] > t or s["trip"])
        st = "reserve" if u.reserve and s["report"] is None else "on_duty" if on and s["ready"] <= t + NEAR else \
            "resting" if s["report"] is not None else "off"
        if state is not None and st != state:
            continue
        trip = " ".join(f"{fid(j)} {hhmm(day.task.flight(j).sched_dep)} {day.task.flight(j).orig}-"
                        f"{day.task.flight(j).dest}" for j in s["trip"][:3])
        lim = "" if s["limit"] is None else f" limit {hhmm(s['limit'])}"
        rest = "" if s["release"] is None else \
            f" release {hhmm(s['release'])} rested {hhmm(s['release'] + crews.rest(u.kind))}"
        res = f" {u.reserve} reserve" + (f" from {hhmm(s['earliest'])}" if s.get("earliest") else "") + \
            (f" until {hhmm(u.until)}" if u.until is not None else "") if u.reserve else ""
        out.append(f"{u.id} {u.kind} {u.group} base {u.home} at {s['station']} ready {hhmm(s['ready'])} "
                   f"{'report ' + hhmm(s['report']) if s['report'] is not None else 'not reported'} legs "
                   f"{s['flown']} flown {s['minutes']}m{lim}{rest}{res}"
                   f"{' held for ' + fid(s['held']) if s['held'] is not None else ''}"
                   f"{' trip ' + trip if trip else ''}")
    return out


def crew_list(day, unit, station, base, kind, group, state) -> dict:
    proj = day.project(day.plan)
    rows = crews_rows(day, proj, unit, station, base, kind, group, state)
    return {"rows": rows[:CREW_ROWS], "more": max(0, len(rows) - CREW_ROWS)}


def reserves(day, base) -> dict:
    proj = day.project(day.plan)
    rows = [r for r in crews_rows(day, proj, None, None, base, None, None, None) if " reserve" in r]
    return {"rows": rows[:LOOKUP_ROWS], "more": max(0, len(rows) - LOOKUP_ROWS)}


def options(day, i: int, kind) -> dict:
    """The standard crew desk's options for the flight's kind at this minute, as it prices them: decision support."""
    if kind not in crews.KINDS:
        return {"error": f"kind is one of {', '.join(crews.KINDS)}"}
    task = day.task
    pool, d = crewsim.start(task, day.plan, day.w.units, day.world, day.w.local, airports=day.w.airports, desk=True,
                            drop=False)
    d.until(day.t)
    f = task.flight(i)
    t0 = max(day.t, task.earliest(f, min(0, f.lb)))
    found = pool.options(kind, crews.TYPE[f.family], f, day.t, t0)
    rows = [f"{OPTION.get(act[0], act[0])} {uid}{' via ' + fid(act[1]) if act[0] == 'deadhead' else ''}"
            f"{' report ' + hhmm(act[1]) if act[0] == 'call' else ''}{' with extension' if rank == 5 else ''}: "
            f"leaves {hhmm(dep)}, cost {cost:,.0f}" for cost, rank, dep, uid, act in sorted(found, key=lambda o: o[:4])]
    return {"flight": fid(i), "kind": kind, "options": rows[:20], "more": max(0, len(rows) - 20),
            "note": "cost: the wait from now, deadhead and hotel, and the end of the day as the day prices it, the "
                    "unit taking the rest of the planned crew's trip. (a) needs no move; (b) or (c) with the planned "
                    "crew there: swap the two; else assign, which names this flight only: the planned crew's later "
                    "legs go back to the crew system (crew_check: handed_back)"}


def legality(day, plan, moves, touched) -> dict:
    """Per unit a move names: what the projection has it fly, and with what margin; and the legs the moved units, or
    the crews they replace, flew on the projection and no longer do, unnamed: back with the crew system."""
    now, then = day.project(day.plan), day.project(plan)
    moves = [m for m in moves if isinstance(m, Mapping)]
    units = {str(m.get("unit")) for m in moves} | {str(m.get("other")) for m in moves if m.get("other")}
    out = {}
    for uid in sorted(units & set(day.units)):
        flown = [(i, kinds) for i, kinds in then.day.crews.items() if uid in kinds.values() and
                 then.day.run.flights[i].dep >= day.t]
        legs = []
        for i, _ in sorted(flown, key=lambda x: then.day.run.flights[x[0]].dep):
            x = then.day.run.flights[i]
            lim = then.day.trace["limits"].get(i, {})
            last = next((v for v in lim.values() if v[0] == uid), None)
            note = ""
            if last is not None and last[1] is not None and x.dep > last[1]:
                note = f" with extension until {hhmm(last[2])}"
            elif last is not None and last[2] is not None:
                note = f" legal until {hhmm(last[1])}"
            legs.append(f"{fid(i)} {hhmm(x.dep)}{note}")
        out[uid] = legs[:8] or ["flies nothing on the projection"]
    return {"units": out, "handed_back": handed_back(day, plan, touched, now, then)}


def handed_back(day, plan, touched, now, then) -> list[str]:
    names = {(x[2], int(k)) for k, e in plan.items() if k not in sim.EXTRA for x in e.get("names", ()) if x[1] is None}
    whose = {uid for *_, uid in touched} | {now.day.crews.get(i, {}).get(day.units[uid].kind)
                                            for _, i, action, uid in touched if action in ("assign", "call_reserve")}
    rows = []
    for i, kinds in sorted(now.day.crews.items(), key=lambda x: (now.day.run.flights[x[0]].dep or 0, x[0])):
        if (now.day.run.flights[i].dep or 0) < day.t:
            continue
        for kind, uid in kinds.items():
            if uid in whose and (kind, i) not in names and then.day.crews.get(i, {}).get(kind) != uid:
                y = then.day.run.flights[i]
                who = then.day.crews.get(i, {}).get(kind) or ("cancelled" if y.status in (CANCELLED, AUTO_CANCELLED)
                                                              else "no crew")
                rows.append(f"{fid(i)} {kind}: {uid}'s, now {who}")
    return rows[:12] + ([f"{len(rows) - 12} more"] if len(rows) > 12 else [])
