"""The crews at the hub in 3D (web/stage/crews.js): each crew's comings and goings there over the sidecar's span, from
the episode's crew units (occ.units) and the stands the sidecar gave the aircraft. Display only: the crew room, the
walkway along the home terminal's airside face, the landside curb and the vans' route are inferred from the stage's
geometry, like the stands and taxi routes, and are not JetBlue's.

A crew reporting at the hub comes by the employee shuttle (from a hub hotel, by the hotel van) to the curb and walks
to the crew room; it leaves the room LEAVE minutes before pushback, walks to the stand's jet bridge and boards BOARD
minutes before (a deadheading crew DEADHEAD minutes before, with the passengers). An arriving crew deplanes DEPLANE
minutes after block-in and walks to its next stand when it leaves within CONNECT minutes (it stays aboard when that is
the same stand), else to the crew room; released at the hub, it goes off duty by the employee exit beside the crew
room, or, away from base or resting at a hub hotel, to the curb and the hotel van."""

import json
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence

from ..stage import HOME, hhmm, num

WALK = 70.0
"""Metres a crew with its bags walks a minute."""
DRIVE = 200.0
"""Metres a van drives a minute along the curb."""
PARK = {"in": 2.0, "out": 3.0}
"""Minutes a van stands at the curb after it drops a crew off, before it picks one up."""
BAY = 12.0
"""A van finding the curb taken stops this much further up the road for each one there."""
WALKWAY = 3.5
"""The walkway's offset from the terminal's face, under the jet bridges."""
CURB = 11.0
"""The curb lane's offset from the terminal's landside face."""
ROOM_ALONG, CURB_ALONG, ROAD = 25.0, 110.0, 140.0
"""The crew room this far past the end gate into the landside face, the curb this far past the gate and the vans'
road on along the face from the curb this far."""
RAMP = 60.0
"""A stand without a jet bridge farther than this from the walkway is a remote one, reached by the apron bus from the
walkway point nearest it."""
CUT = 45.0
"""A walk cuts through the building (a pier's root) across a gap this narrow."""
LEAVE, BOARD, DEADHEAD, DEPLANE, CONNECT = 50, 35, 30, 10, 75
LATE = 12
"""A flight still short of a legal crew when boarding would start (its gate tag waits for crew) has its crew from
the crew room walk up and board this many minutes before pushback; the rest of its crew waits at the gate."""
REPORT = 20
"""A reporting crew is at the curb this many minutes before its report time."""
DOCKED = 12
"""A crew boards no sooner than this after its aircraft blocks in at the stand."""
NEAR = 45
"""A duty's leg finds its gate among the sidecar's legs of its flight this close in time."""
STAY = 90
"""A hotel stay at the hub this close to a release or a report is the crew's."""
TIMED_OUT = 45
"""A release this close to the duty's legal limit is a crew that timed out."""

Point = tuple[float, float]


def crews(ep: Mapping, sidecar: Mapping, stage: Mapping) -> list[dict]:
    """The crews' movements at the hub, a group per crew or per crews moving together: `key`, `kind` (report, out,
    in, off, hotel), `crew` ([pilots, cabin crew]), the `flights` it relates to, `stand` and `segs`, each {do: walk |
    wait | van, t: [from, to] minutes, p: path ([x, y] metres) or at: [x, y], say: its tag}, a van's with v: shuttle |
    hotel and dir: in | out."""
    occ = ep.get("occ") or {}
    site = Site.of(stage)
    if not occ.get("units") or site is None:
        return []
    hub, span = ep["task"]["hub"], sidecar["span"]
    flights = {r["id"]: r for r in ep["schedule"]}
    gates = Gates(sidecar)
    raw = [m for uid, u in sorted(occ["units"].items()) for m in moves(uid, u, hub, flights, gates, site)]
    return merged([m for m in raw if m["segs"][0]["t"][0] < span[1] and m["segs"][-1]["t"][1] > span[0]])


def moves(uid: str, u: Mapping, hub: str, flights: Mapping[int, Mapping], gates: "Gates", site: "Site") -> list[dict]:
    """One unit's visible moves at the hub over its duties, from its events there in time order."""
    me = {"unit": uid, "kind": u["kind"], "size": u["size"], "reserve": bool(u["reserve"])}
    events = hub_events(u, hub, flights)
    out: list[dict] = []
    where: tuple = ("away",)
    for i, (t, _, what, d, ride) in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        if what == "report":
            stay = hotel(u, hub, t, "to")
            at_curb = t - REPORT
            first = next((e[4][0]["flight"] for e in events[i + 1:] if e[2] == "out"), None)
            out.append(move(me, "report", None, None, [
                site.van("in", at_curb, "hotel" if stay else "shuttle"),
                seg("walk", at_curb + 0.5, site.route(site.curb, site.room))], report=t, stay=stay is not None,
                first=first))
            where = ("room", out[-1]["segs"][-1]["t"][1])
        elif what == "in":
            g = gates.at(ride[0]["flight"], "arr", t)
            if g is None:
                where = ("away",)
                continue
            door, t0, flight = site.door(g["stand"]), t + DEPLANE, ride[0]["flight"]
            if nxt and nxt[2] == "out" and nxt[0] - t0 <= CONNECT:
                g2 = gates.at(nxt[4][0]["flight"], "dep", nxt[0])
                where = ("aboard",) if g2 and g2["stand"] == g["stand"] else ("door", door, t0, flight)
            elif nxt and nxt[2] == "release" and (hotel(u, hub, nxt[0], "from") or u["home"] != hub):
                out.append(to_hotel(me, flight, g["stand"], site, door, t0, nxt[3], hotel(u, hub, nxt[0], "from")))
                where = ("away",)
            elif nxt and nxt[2] == "release":
                out.append(move(me, "off", flight, g["stand"], [seg("walk", t0, site.route(door, site.room))]))
                where = ("away",)
            else:
                out.append(move(me, "in", flight, g["stand"], [seg("walk", t0, site.route(door, site.room))]))
                where = ("room", out[-1]["segs"][-1]["t"][1])
        elif what == "out":
            f, dh = ride
            g = gates.at(f["flight"], "dep", t)
            if g is not None and where[0] != "aboard":
                out.append(to_gate(me, f["flight"], dh, g, t, site, where))
            where = ("away",)
        elif what == "release":
            stay = hotel(u, hub, t, "from")
            if where[0] == "room" and (stay or u["home"] != hub):
                out.append(to_hotel(me, None, None, site, site.room, max(t, where[1]), d, stay))
            where = ("away",)
    return out


def hub_events(u: Mapping, hub: str, flights: Mapping[int, Mapping]) -> list[tuple]:
    """A unit's reports, block-ins, pushbacks and releases at the hub: (minute, order on a tie, what, duty, (flight,
    deadhead))."""
    events = []
    for d in u["duties"]:
        if d["at"] == hub:
            events.append((d["report"], 2, "report", d, None))
        rides = sorted([(x, False) for x in d["legs"]] + [(x, True) for x in d["deadheads"]], key=lambda r: r[0][1])
        for (fid, dep, arr), dh in rides:
            f = flights.get(fid)
            if f is not None and f["dest"] == hub:
                events.append((arr, 0, "in", d, (f, dh)))
            if f is not None and f["orig"] == hub:
                events.append((dep, 3, "out", d, (f, dh)))
        ends = (flights.get(rides[-1][0][0]) or {}).get("dest") if rides else d["at"]
        if d["release"] is not None and ends == hub:
            events.append((d["release"], 1, "release", d, None))
    return sorted(events, key=lambda e: e[:2])


def to_gate(me: Mapping, flight: str, deadhead: bool, g: Mapping, t: float, site: "Site", where: tuple) -> dict:
    """From the crew room, or the gate it came in at, to the jet bridge of its departure; it waits there to board."""
    dest = site.door(g["stand"])
    late = short(g["crew"], t - BOARD)
    if where[0] == "door":
        w, inbound = seg("walk", where[2], site.route(where[1], dest)), where[3]
    else:
        path = site.route(site.room, dest)
        start = max(t - LEAVE, where[1] + 1) if where[0] == "room" else t - LEAVE
        w, inbound = seg("walk", max(start, t - LATE - length(path) / WALK) if late else start, path), None
    arrive = w["t"][1]
    board = max(t - (LATE if late else DEADHEAD if deadhead else BOARD), arrive,
                g["from"] + DOCKED if g["from"] is not None else 0)
    board = round(min(board, max(arrive, t - 3)), 1)
    face = round(math.degrees(math.atan2(dest.face[0] - dest.xy[0], dest.face[1] - dest.xy[1])) % 360)
    segs = [w] + ([{"do": "wait", "t": [arrive, board], "at": w["p"][-1], "h": face}] if board > arrive else [])
    return move(me, "out", flight, g["stand"], segs, deadhead=deadhead, inbound=inbound, bus=dest.bus)


def short(rows: Sequence[Sequence] | None, t: float) -> bool:
    """Whether a departure's crew log has it short of a legal crew at t."""
    row = next((r for r in reversed(rows or ()) if r[0] <= t), None)
    return bool(row) and row[1] == "no crew"


def to_hotel(me: Mapping, flight: str | None, stand: str | None, site: "Site", src: "Place", t0: float,
             duty: Mapping, stay: Mapping | None) -> dict:
    w = seg("walk", t0, site.route(src, site.curb))
    timed_out = duty.get("limit") is not None and duty["release"] >= duty["limit"] - TIMED_OUT
    return move(me, "hotel", flight, stand, [w, site.van("out", w["t"][1] + 1, "hotel")],
                timed_out=timed_out, until=stay["to"] if stay else None)


def hotel(u: Mapping, hub: str, t: float, end: str) -> Mapping | None:
    return next((h for h in u["hotels"] if h["station"] == hub and abs(h[end] - t) <= STAY), None)


def move(me: Mapping, kind: str, flight: str | None, stand: str | None, segs: list[dict], **note) -> dict:
    return {"kind": kind, "flight": flight, "stand": stand, "units": [me], "segs": segs, "note": note}


def seg(do: str, t0: float, path: list[Point]) -> dict:
    return {"do": do, "t": [round(t0, 1), round(t0 + length(path) / WALK, 1)], "p": [[num(x), num(y)] for x, y in path]}


def merged(raw: list[dict]) -> list[dict]:
    """The moves of crews moving together (the same path at the same times) as one group, each with its tags."""
    groups: dict[str, dict] = {}
    for m in raw:
        sig = json.dumps(m["segs"], separators=(",", ":"))
        if sig in groups:
            groups[sig]["units"] = groups[sig]["units"] + m["units"]
            groups[sig]["also"].append(m)
        else:
            groups[sig] = m | {"also": [m]}
    out, keys = [], set()
    curb: list[tuple[float, float, int]] = []
    for g in sorted(groups.values(), key=lambda g: (g["segs"][0]["t"][0], g["kind"], g["flight"] or "")):
        g["segs"] = [bay(s, curb) if s["do"] == "van" else s for s in g["segs"]]
        key = f"{g['kind']}-{(g['flight'] or 'x').replace(' ', '')}-{round(g['segs'][0]['t'][0])}"
        while key in keys:
            key += "+"
        keys.add(key)
        out.append({"key": key, "kind": g["kind"],
                    "crew": [sum(u["size"] for u in g["units"] if u["kind"] == k) for k in ("pilot", "cabin")],
                    "flights": sorted({f for m in g["also"] for f in (m["flight"], m["note"].get("inbound")) if f}),
                    "stand": g["stand"], "segs": [s | {"say": say(g, s)} for s in g["segs"]]})
    return out


def bay(s: dict, curb: list[tuple[float, float, int]]) -> dict:
    """A van's stop moved up the road by a bay for each van standing at the curb while it does."""
    lo, hi = (s["t"][1], s["t"][1] + s["park"]) if s["dir"] == "in" else (s["t"][0] - s["park"], s["t"][0])
    taken = {k for a, b, k in curb if a < hi and lo < b}
    k = next(k for k in range(len(taken) + 1) if k not in taken)
    curb.append((lo, hi, k))
    if not k:
        return s
    path = s["p"] if s["dir"] == "out" else s["p"][::-1]
    path = cut(path, BAY * k)
    return s | {"p": [[num(x), num(y)] for x, y in (path if s["dir"] == "out" else path[::-1])]}


def cut(path: Sequence[Sequence[float]], d: float) -> list[Point]:
    """The path without its first d metres."""
    for i in range(1, len(path)):
        step = dist(path[i - 1], path[i])
        if step >= d:
            u = d / (step or 1.0)
            a, b = path[i - 1], path[i]
            return [(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u), *map(tuple, path[i:])]
        d -= step
    return [tuple(path[-1])]


def say(g: Mapping, s: Mapping) -> str:
    """The tag over a group: who it is and where it is going."""
    units, note, flight = g["units"], g["note"], g["flight"]
    kinds = {u["kind"] for u in units}
    who = "pilots" if kinds == {"pilot"} else "cabin crew" if kinds == {"cabin"} else "crew"
    reserve = all(u["reserve"] for u in units)
    if g["kind"] == "report":
        firsts = {m["note"]["first"] for m in g["also"]}
        head = (f"Reserve {who}" if reserve else "Crew from the hotel" if note.get("stay") else
                f"Crew of {firsts.pop()}" if len(firsts) == 1 and None not in firsts else "Crews")
        return f"{head} · reporting {hhmm(note['report'])}"
    if g["kind"] == "out" and note.get("deadhead"):
        return f"Deadheading crew · boarding {flight} with the passengers"
    if g["kind"] == "out":
        head = f"Reserve {who} for {flight}" if reserve else f"Crew of {flight}"
        where = f"the bus to stand {g['stand']}" if note.get("bus") else gate_name(g["stand"])
        return f"{head} · {'to' if s['do'] == 'walk' else 'at'} {where}"
    if g["kind"] == "hotel":
        until = f"rest until {hhmm(note['until'])}" if note.get("until") is not None else "layover"
        return f"Crew to the hotel · {'timed out' if note.get('timed_out') else until}"
    if g["kind"] == "off":
        return f"Crew of {flight} · off duty"
    return f"Crew of {flight} · to the crew room"


def gate_name(ref: str) -> str:
    return f"gate {ref.split('-', 1)[1]}" if "-" in ref else f"gate {ref}"


class Gates:
    """The stand each of the task's flown legs used at the hub, from the sidecar's task aircraft."""

    def __init__(self, sidecar: Mapping):
        self.legs: dict[tuple[str, str], list[dict]] = {}
        for a in sidecar["aircraft"]:
            for v in a["visits"] if a.get("task") else ():
                for kind in ("arr", "dep"):
                    leg = v.get(kind)
                    at = leg and (leg["in"] if kind == "arr" else leg["out"])
                    if leg and leg["status"] == "flown" and at is not None:
                        self.legs.setdefault((leg["flight"], kind), []).append(
                            {"stand": v["stand"], "at": at, "from": v["from"], "crew": leg.get("crew")})

    def at(self, flight: str, kind: str, t: float) -> dict | None:
        near = [g for g in self.legs.get((flight, kind), []) if abs(g["at"] - t) <= NEAR]
        return min(near, key=lambda g: abs(g["at"] - t), default=None)


class Place:
    """Where a crew stands (xy), the walkway point it reaches it from (walk), that point's arc length on the
    terminal's outline (s) and what it faces while it waits there (face)."""

    def __init__(self, xy: Point, s: float, walk: Point, face: Point | None = None, bus: bool = False):
        self.xy, self.s, self.walk, self.face, self.bus = xy, s, walk, face or xy, bus


class Site:
    """The home terminal's crew geography, display only: the walkway along its airside face, the stands' doors on it,
    the crew room just past the gates at the end nearer their middle, the curb past it on the landside face and the
    vans' lane along that face and out."""

    @classmethod
    def of(cls, stage: Mapping) -> "Site | None":
        try:
            return cls(stage)
        except LookupError:
            return None

    def __init__(self, stage: Mapping):
        home = HOME.get(stage["hub"])
        self.stands = {s["ref"]: s for s in stage["stands"]}
        mine = [s for s in stage["stands"] if s["terminal"] == home]
        self.roots = bridge_roots(stage, self.stands)
        rings = [Ring(t["outer"]) for t in stage["terminals"] if len(t["outer"]) >= 3]
        if not mine or not rings:
            raise LookupError("no home terminal")

        def touches(r: Ring, s: Mapping) -> bool:
            return r.project(self.roots.get(s["ref"], s["xy"]))[1] < (15 if s["ref"] in self.roots else 60)

        ring = max(rings, key=lambda r: sum(touches(r, s) for s in mine))
        feet = sorted(ring.project(self.roots.get(s["ref"], s["xy"]))[0] for s in mine if touches(ring, s))
        if not feet:
            raise LookupError("no gates on the home terminal")
        self.ring, L = ring, ring.L
        gap, a = max(((feet[(i + 1) % len(feet)] - f) % L or L, f) for i, f in enumerate(feet))
        b = (a + gap) % L
        self.landside = (a + gap / 2) % L
        centre = (sum(s["xy"][0] for s in mine) / len(mine), sum(s["xy"][1] for s in mine) / len(mine))
        sign = 1 if dist(ring.at(a), centre) <= dist(ring.at(b), centre) else -1
        end = a if sign > 0 else b
        room_s = (end + sign * min(ROOM_ALONG, gap * 0.2)) % L
        curb_s = (end + sign * min(CURB_ALONG, gap * 0.45)) % L
        self.room = Place(ring.at(room_s, WALKWAY), room_s, ring.at(room_s, WALKWAY))
        self.curb = Place(ring.at(curb_s, CURB - 2), curb_s, ring.at(curb_s, WALKWAY))
        coarse = Ring(ring.outer, 15.0)
        c0 = coarse.project(ring.at(curb_s))[0]
        c1 = c0 + sign * ROAD
        self.lane = [ring.at(curb_s, CURB), *coarse.between(c0, c1, CURB, sign > 0), coarse.at(c1, CURB)]

    def door(self, ref: str) -> Place:
        """The walkway point under a stand's jet bridge, else (no bridge on the terminal) its aircraft's front door, or
        for a remote stand the walkway point its apron bus leaves from."""
        root = self.roots.get(ref)
        if root is not None:
            s, d = self.ring.project(root)
            if d < 15:
                w = self.ring.at(s, WALKWAY)
                return Place(w, s, w, self.ring.at(s))
        st = self.stands[ref]
        h = math.radians(st["heading"])
        fx, fy = math.sin(h), math.cos(h)
        xy = (st["xy"][0] - fx * 6 - fy * 4, st["xy"][1] - fy * 6 + fx * 4)
        s, d = self.ring.project(xy)
        w = self.ring.at(s, WALKWAY)
        if d > RAMP:
            return Place(w, s, w, tuple(st["xy"]), bus=True)
        return Place(xy, s, w, tuple(st["xy"]))

    def route(self, a: Place, b: Place) -> list[Point]:
        """From a to b along the walkway, the airside way round."""
        forward = (self.landside - a.s) % self.ring.L >= (b.s - a.s) % self.ring.L
        pts = [a.xy, a.walk, *self.ring.between(a.s, b.s, WALKWAY, forward), b.walk, b.xy]
        return shortcut([p for i, p in enumerate(pts) if i == 0 or dist(p, pts[i - 1]) > 0.3])

    def van(self, direction: str, t0: float, kind: str) -> dict:
        """A van's drive along the curb lane: in, stopping at the curb at t0; out, pulling away from it at t0."""
        path = self.lane if direction == "out" else self.lane[::-1]
        mins = length(path) / DRIVE
        t = [t0, t0 + mins] if direction == "out" else [t0 - mins, t0]
        return {"do": "van", "t": [round(t[0], 1), round(t[1], 1)], "p": [[num(x), num(y)] for x, y in path],
                "v": kind, "dir": direction, "park": PARK[direction]}


class Ring:
    """A terminal's outline, simplified and counter-clockwise, walked by arc length s; offsets are outward."""

    def __init__(self, outer: Sequence[Sequence[float]], tol: float = 5.0):
        self.outer = outer
        pts = simplify([(float(x), float(y)) for x, y in outer], tol)
        if signed_area(pts) < 0:
            pts.reverse()
        n = len(pts)
        self.p = pts
        self.s = [0.0]
        for i in range(n):
            self.s.append(self.s[-1] + dist(pts[i], pts[(i + 1) % n]))
        self.L = self.s[-1] or 1.0
        self.n = []
        for i in range(n):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
            d = math.hypot(bx - ax, by - ay) or 1.0
            self.n.append(((by - ay) / d, -(bx - ax) / d))
        self.miter = []
        for i in range(n):
            n0, n1 = self.n[i - 1], self.n[i]
            bx, by = n0[0] + n1[0], n0[1] + n1[1]
            m = math.hypot(bx, by)
            bx, by = (bx / m, by / m) if m > 1e-6 else n1
            c = max(0.5, bx * n1[0] + by * n1[1])
            self.miter.append((bx / c, by / c))

    def project(self, p: Sequence[float]) -> tuple[float, float]:
        """The arc length of the outline's point nearest p, and how far p is from it."""
        best = (math.inf, 0.0)
        n = len(self.p)
        for i in range(n):
            (ax, ay), (bx, by) = self.p[i], self.p[(i + 1) % n]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy or 1.0
            u = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ll))
            d = math.hypot(ax + dx * u - p[0], ay + dy * u - p[1])
            if d < best[0]:
                best = (d, self.s[i] + u * math.sqrt(ll))
        return best[1], best[0]

    def at(self, s: float, off: float = 0.0) -> Point:
        s %= self.L
        i = min(bisect_right(self.s, s) - 1, len(self.p) - 1)
        (ax, ay), (bx, by) = self.p[i], self.p[(i + 1) % len(self.p)]
        u = (s - self.s[i]) / ((self.s[i + 1] - self.s[i]) or 1.0)
        nx, ny = self.n[i]
        return ax + (bx - ax) * u + nx * off, ay + (by - ay) * u + ny * off

    def between(self, s0: float, s1: float, off: float, forward: bool) -> list[Point]:
        """The outline's corners passed going from s0 to s1, forward or back, offset outward by off."""
        span = ((s1 - s0) if forward else (s0 - s1)) % self.L
        ahead = [(((self.s[k] - s0) if forward else (s0 - self.s[k])) % self.L, k) for k in range(len(self.p))]
        out: list[tuple[Point, Point]] = [(self.at(s0), self.at(s0, off))]
        for _, k in sorted(x for x in ahead if 0 < x[0] < span):
            p, q = self.p[k], (self.p[k][0] + self.miter[k][0] * off, self.p[k][1] + self.miter[k][1] * off)
            (p0, q0) = out[-1]
            if (q[0] - q0[0]) * (p[0] - p0[0]) + (q[1] - q0[1]) * (p[1] - p0[1]) > 0:
                out.append((p, q))
        return [q for _, q in out[1:]]


def shortcut(path: list[Point]) -> list[Point]:
    """The path with its detours round a pier cut across the pier's root, where two points of it lie within CUT of
    each other but far apart along it."""
    out, i = [path[0]], 0
    along = [0.0]
    for k in range(1, len(path)):
        along.append(along[-1] + dist(path[k - 1], path[k]))
    while i < len(path) - 1:
        j = next((j for j in range(len(path) - 1, i + 1, -1) if dist(path[i], path[j]) <= CUT
                  and along[j] - along[i] > 3 * dist(path[i], path[j]) + 40), i + 1)
        out.append(path[j])
        i = j
    return out


def bridge_roots(stage: Mapping, stands: Mapping[str, Mapping]) -> dict[str, Point]:
    """Each stand's jet bridge root at the terminal: the end of its line away from the stand (the nearest stand's
    within 45 m when the bridge names none); of several, the one nearest the stand."""
    out: dict[str, Point] = {}
    for jb in stage.get("jet_bridges") or []:
        line = jb["line"]
        st = stands.get(jb.get("stand") or "")
        if st is None:
            near = min(stage["stands"], key=lambda s: min(dist(s["xy"], line[0]), dist(s["xy"], line[-1])))
            st = near if min(dist(near["xy"], line[0]), dist(near["xy"], line[-1])) < 45 else None
        if st is None or len(line) < 2:
            continue
        root = tuple(line[0] if dist(line[0], st["xy"]) >= dist(line[-1], st["xy"]) else line[-1])
        if st["ref"] not in out or dist(root, st["xy"]) < dist(out[st["ref"]], st["xy"]):
            out[st["ref"]] = root
    return out


def simplify(pts: list[Point], tol: float) -> list[Point]:
    """Douglas-Peucker on a closed outline."""
    if len(pts) > 1 and dist(pts[0], pts[-1]) < 1e-6:
        pts = pts[:-1]
    if len(pts) <= 4:
        return pts
    far = max(range(len(pts)), key=lambda i: dist(pts[0], pts[i]))
    return dp(pts[: far + 1], tol)[:-1] + dp(pts[far:] + [pts[0]], tol)[:-1]


def dp(pts: list[Point], tol: float) -> list[Point]:
    if len(pts) < 3:
        return pts
    (ax, ay), (bx, by) = pts[0], pts[-1]
    dx, dy = bx - ax, by - ay
    ll = math.hypot(dx, dy) or 1.0
    i, d = max(((i, abs((p[0] - ax) * dy - (p[1] - ay) * dx) / ll) for i, p in enumerate(pts[1:-1], 1)),
               key=lambda x: x[1])
    if d <= tol:
        return [pts[0], pts[-1]]
    return dp(pts[: i + 1], tol)[:-1] + dp(pts[i:], tol)


def signed_area(pts: Sequence[Point]) -> float:
    return sum(pts[i - 1][0] * p[1] - p[0] * pts[i - 1][1] for i, p in enumerate(pts)) / 2


def length(path: Sequence[Sequence[float]]) -> float:
    return sum(dist(path[i - 1], path[i]) for i in range(1, len(path)))


def dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
