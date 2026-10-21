"""The passengers at the hub in 3D (web/stage/passengers.js): the gate lounges behind the home terminal's airside glass,
each departure's crowd there, connecting passengers walking from gate to gate, the queue at the service counter and the
hotel shuttle at the curb, from the episode's `passengers` and the stands the sidecar gave the aircraft. Display only:
the lounges, the counter, the concourse's walking line and the shuttle are inferred from the stage's geometry, like the
crew room and the vans, and are not JetBlue's.

A departure's passengers come to its lounge from ARRIVE minutes before pushback, all there by FULL before, and board
from BOARD to CLOSED before. An arrival's passengers come off DEPLANE minutes after block-in: those ending their trip
walk the concourse to the landside, its connecting passengers to their next gate, or, when the departure left without
them, to the counter. A cancelled departure's crowd goes to the
counter when it is cancelled, QUEUE of it in line (the rest rebook on their phones). Passengers leave the line when
they are rebooked (to their new gate), go home, or are moved to a later day and find a room for the night (to the
curb and the next shuttle, every SHUTTLE minutes)."""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence

from ..stage import HOME, hhmm, num
from .crewstage import Site, dist, length, shortcut

FLOOR = 5.0
"""The departure level's floor above the apron, where the terminal's glass starts (web/stage/buildings.js)."""
DEPTH, HALF, INSIDE, WALK = 10.0, 13.0, 3.0, 75.0
"""A lounge reaches this far in from the glass and at most this far either side of its jet bridge; passengers walk
along the concourse this far in from the glass, this many metres a minute."""
ARRIVE, FULL, BOARD, CLOSED, DEPLANE, LEAVE = 110, 40, 35, 8, 9, 6
MAKE_IT = 20
"""Minutes a connecting passenger needs from block-in to the outbound's pushback."""
QUEUE, SERVE = 0.6, 4
"""Share of a disrupted group at the airport that queues at the counter; minutes the last of a group takes to be
served after its turn at the desk."""
SHUTTLE, DRIVE, CURB_OFF, CLEAR = 30, 200.0, (9.0, 14.0, 20.0), 3.5
"""Minutes between hotel shuttles; metres a minute they drive; how far out from the terminal's landside face the
passengers' stop may be, nearest first, when the crews' curb is under a building; and how far from any building a stop
and its road keep."""
GATE = 15.0
"""A stand is on the lounge face when its jet bridge root is this close to the home terminal's outline."""


def passengers(ep: Mapping, sidecar: Mapping, stage: Mapping) -> dict | None:
    """The hub's passengers for the sidecar: `terminal` (index of the home terminal in stage.terminals), `floor`,
    `lounges` [{stand, p: facade polyline, n: inward normals, door, u: [u0, u1] along the outline}], `counter` (a
    lounge index), `crowds` [{lounge, flight, n, t: [arrive, full, board, gone], cancel}], `queue` [[t, n]], `groups`
    (as the crews' segs, each seg with z its floor) and `tags` by flight {conn, held, missed}."""
    pax = ep.get("passengers")
    site = Site.of(stage)
    if not pax or site is None:
        return None
    face = Face.home(stage, site)
    if face is None:
        return None
    hub = ep["task"]["hub"]
    sched = {s["id"]: s for s in ep["schedule"]}
    rows = {r["id"]: r for r in pax["flights"]}
    lounges = face.lounges(site, HOME.get(hub))
    if not lounges:
        return None
    by_stand = {lg["stand"]: k for k, lg in enumerate(lounges)}
    legs = legs_at(sidecar)
    counter = face.counter(lounges, legs)
    curb = Curb.of(site, stage)
    events = defaultdict(list)
    for e in pax["events"]:
        if e["at"] == hub:
            events[e["flight"]].append(e)
    conn_in, conn_out = defaultdict(int), defaultdict(int)
    for c in pax["connections"]:
        if c["at"] == hub:
            conn_in[c["to"]] += c["pax"]
            conn_out[c["from"]] += c["pax"]
    crowds, joins, groups = [], [], []
    for (flight, kind), found in legs.items():
        if kind != "dep":
            continue
        for leg, stand in found:
            if stand not in by_stand:
                continue
            fid = next((i for i, s in sched.items() if s["flight"] == flight and s["orig"] == hub
                        and abs(s["sched_dep"] - leg["sched"]) <= 5), None)
            r = rows.get(fid)
            if r is None:
                continue
            if leg["status"] == "flown" and leg["out"] is not None:
                n = r["flown"]
                out = leg["out"]
                crowds.append({"lounge": by_stand[stand], "flight": flight, "n": n,
                               "t": [out - ARRIVE, out - FULL, out - BOARD, out - CLOSED], "cancel": None})
            elif leg["status"] == "cancelled":
                at = leg.get("decided") if leg.get("decided") is not None else leg["sched"]
                n = r["booked"] + r.get("added", 0)
                t = [leg["sched"] - ARRIVE, leg["sched"] - FULL, leg["sched"] - BOARD, leg["sched"] - CLOSED]
                there = n * share(at, t)
                crowds.append({"lounge": by_stand[stand], "flight": flight, "n": n, "t": t, "cancel": at})
                if there >= 1:
                    k = round(there * QUEUE)
                    joins.append((at + LEAVE, fid, k, stand, "cancel"))
    for (flight, kind), found in legs.items():
        if kind != "arr":
            continue
        for leg, stand in found:
            fid = next((i for i, s in sched.items() if s["flight"] == flight and s["dest"] == hub
                        and abs(s["sched_arr"] - leg["sched"]) <= 5), None)
            if stand not in by_stand or leg["status"] != "flown" or leg["in"] is None or rows.get(fid) is None:
                continue
            n = rows[fid]["flown"] - conn_out[fid]
            if n > 0:
                walk = face.walk(lounges[by_stand[stand]], face.exit(curb), leg["in"] + DEPLANE, site)
                city = leg.get("city") or leg.get("other") or ""
                groups.append(group("off", n, [flight], walk, f"{n} off {flight}{f' from {city}' if city else ''}"
                                                              " · to baggage claim"))
    for c in pax["connections"]:
        if c["at"] != hub:
            continue
        a, b = sched.get(c["from"]), sched.get(c["to"])
        if not a or not b:
            continue
        ga, gb = gate_of(legs, a, "arr"), gate_of(legs, b, "dep")
        if ga is None or ga["stand"] not in by_stand:
            continue
        t0 = ga["at"] + DEPLANE
        start = lounges[by_stand[ga["stand"]]]
        if c["made"] and gb is not None and gb["stand"] in by_stand:
            end = lounges[by_stand[gb["stand"]]]
            if end is not start:
                groups.append(group("connect", c["made"], [a["flight"], b["flight"]], face.walk(start, end, t0, site),
                                    f"{c['made']} connecting to {b['flight']} · to {gate(gb['stand'])}"))
        missed = c["pax"] - c["made"]
        if missed and (gb is None or gb["at"] - t0 < MAKE_IT):
            dest = lounges[counter]
            segs = face.walk(start, dest, t0, site)
            groups.append(group("misconnect", missed, [a["flight"], b["flight"]], segs,
                                f"{missed} missed {b['flight']} · to the service counter"))
            joins.append((segs[-1]["t"][1], c["to"], missed, None, "misconnect"))
    line = queue(joins, events, groups, lounges, counter, face, site, sched, by_stand, legs, curb)
    tags = {}
    for i, r in rows.items():
        s = sched.get(i)
        if s is None or s["orig"] != hub:
            continue
        conn = conn_in[i]
        if conn or r.get("hold") or r.get("misconnected"):
            tags[s["flight"]] = {"conn": conn, "held": r.get("hold") or 0, "missed": r.get("misconnected") or 0,
                                 "sched": s["sched_dep"]}
    return {"terminal": face.index, "floor": FLOOR, "depth": DEPTH, "lounges": lounges, "counter": counter,
            "crowds": crowds, "queue": line, "groups": sorted(groups, key=lambda g: g["segs"][0]["t"][0]),
            "tags": tags}



def share(t: float, span: Sequence[float]) -> float:
    """The share of a departure's passengers at its lounge at t on the way to its full crowd."""
    a, b = span[0], span[1]
    return 0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a)


def queue(joins, events, groups, lounges, counter, face, site, sched, by_stand, legs, curb) -> list[list[float]]:
    """The line at the counter as [t, n] steps, and the groups leaving it: to a new gate, out, or to the curb and the
    hotel shuttle."""
    desk = lounges[counter]
    waiting: dict[int, list[list[float]]] = defaultdict(list)
    steps: list[tuple[float, int]] = []
    for t, fid, n, _, _ in sorted(joins):
        if n > 0:
            waiting[fid].append([t, n])
            steps.append((t, n))
    shuttles: dict[int, list] = defaultdict(list)
    for fid, lots in waiting.items():
        evs = sorted(events.get(fid, []), key=lambda e: e["t"])
        for e in evs:
            if e["kind"] not in ("rebook", "hotel", "home"):
                continue
            want = e["pax"]
            for lot in lots:
                if want <= 0 or lot[1] <= 0:
                    continue
                k = min(want, lot[1])
                want -= k
                lot[1] -= k
                t = max(e["t"], lot[0] + 2) + SERVE * (k > 6)
                steps.append((t, -k))
                if e["kind"] == "rebook":
                    g = sched.get(e["to"])
                    gb = g and gate_of(legs, g, "dep")
                    if gb and gb["stand"] in by_stand and lounges[by_stand[gb["stand"]]] is not desk:
                        groups.append(group("rebook", k, [sched[fid]["flight"], g["flight"]],
                                            face.walk(desk, lounges[by_stand[gb["stand"]]], t, site),
                                            f"{k} rebooked onto {g['flight']} · to {gate(gb['stand'])}"))
                elif e["kind"] == "hotel":
                    segs = face.to_curb(desk, t, site, curb)
                    shuttles[math.ceil((segs[-1]["t"][1] + 3) / SHUTTLE) * SHUTTLE].append((k, segs, fid))
        for lot in lots:
            if lot[1] > 0:
                steps.append((lot[0] + 45, -lot[1]))
                lot[1] = 0
    for t, rides in sorted(shuttles.items()):
        n = sum(k for k, _, _ in rides)
        for k, segs, fid in rides:
            wait = {"do": "wait", "t": [segs[-1]["t"][1], t - 0.5], "at": segs[-1]["p"][-1], "z": 0.0,
                    "h": curb.heading}
            walk = [*segs, wait] if t - 0.5 > segs[-1]["t"][1] else segs
            say = f"{k} stranded overnight · hotel shuttle {hhmm(t)}"
            groups.append(group("hotel", k, [sched[fid]["flight"]], walk, say))
        van = curb.van(t)
        groups.append(group("shuttle", 0, [], [van], f"Hotel shuttle · {n} passengers stranded overnight"))
    out, n = [], 0
    for t, d in sorted(steps):
        n = max(0, n + d)
        if out and abs(out[-1][0] - t) < 0.05:
            out[-1][1] = n
        else:
            out.append([round(t, 1), n])
    return out


class Curb:
    """Where the hotel shuttle stops for passengers and its road away, display only: the crews' curb and van lane when
    they are clear of every building, else the nearest point along the home terminal's landside face, toward its middle
    and CURB_OFF out, whose stop and road are."""

    def __init__(self, xy: Sequence[float], walk: Sequence[float], lane: Sequence[Sequence[float]]):
        self.xy, self.walk, self.lane = tuple(xy), tuple(walk), [tuple(p) for p in lane]
        (x0, y0), (x1, y1) = self.lane[0], self.lane[1]
        self.heading = round(math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360)

    @classmethod
    def of(cls, site: Site, stage: Mapping) -> "Curb":
        near = [b["outer"] for kind in ("terminals", "buildings", "hangars") for b in stage.get(kind) or ()
                if len(b["outer"]) >= 3 and any(dist(q, site.curb.xy) < 600 for q in b["outer"])]

        def clear(p: Sequence[float]) -> bool:
            return not any(inside(r, p) or edge(r, p) < CLEAR for r in near)

        if all(clear(p) for p in [site.curb.xy, *along(site.lane, 10.0)[:-1]]):
            return cls(site.curb.xy, site.curb.walk, site.lane)
        ring, s0 = site.ring, site.curb.s
        span = (site.landside - s0) % ring.L
        step = 8.0 if span < ring.L / 2 else -8.0
        for k in range(1, int(2 * min(span, ring.L - span) / 8)):
            s = (s0 + step * k) % ring.L
            for off in CURB_OFF:
                if not clear(ring.at(s, off)):
                    continue
                for sign in (1, -1):
                    road = [ring.at(s + sign * d, off) for d in range(0, 161, 10)]
                    if all(clear(q) for q in road):
                        (x0, y0), (x1, y1) = road[-2], road[-1]
                        d = math.hypot(x1 - x0, y1 - y0) or 1.0
                        out = (x1 + (x1 - x0) / d * 60, y1 + (y1 - y0) / d * 60)
                        return cls(road[0], ring.at(s, 3.5), [*road, out])
        return cls(site.curb.xy, site.curb.walk, site.lane)

    def van(self, t0: float) -> dict:
        """The shuttle pulling away from the stop at t0, having stood there PARK minutes."""
        return {"do": "van", "t": [round(t0, 1), round(t0 + length(self.lane) / DRIVE, 1)],
                "p": [[num(x), num(y)] for x, y in self.lane], "v": "shuttle", "dir": "out", "park": 3.0, "z": 0.0}


def along(path: Sequence[Sequence[float]], step: float) -> list[Sequence[float]]:
    out = [path[0]]
    for a, b in zip(path, path[1:], strict=False):
        n = max(1, int(dist(a, b) / step))
        out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    return out


def edge(ring: Sequence[Sequence[float]], p: Sequence[float]) -> float:
    best = math.inf
    for i in range(len(ring)):
        (ax, ay), (bx, by) = ring[i - 1], ring[i]
        dx, dy = bx - ax, by - ay
        w = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ((dx * dx + dy * dy) or 1.0)))
        best = min(best, math.hypot(ax + dx * w - p[0], ay + dy * w - p[1]))
    return best


def group(kind: str, n: int, flights: list[str], segs: list[dict], say: str) -> dict:
    t0 = segs[0]["t"][0]
    return {"key": f"{kind}-{'-'.join(f.replace(' ', '') for f in flights) or 'x'}-{round(t0)}", "kind": kind, "n": n,
            "flights": flights, "segs": [s | {"say": say} for s in segs]}


def gate(ref: str) -> str:
    return f"gate {ref.split('-', 1)[1]}" if "-" in ref else f"gate {ref}"


def gate_of(legs: Mapping, s: Mapping, kind: str) -> dict | None:
    """The stand and minute a flight's flown leg at the hub used (an arrival's block-in, a departure's pushback), the
    sidecar's leg of that flight scheduled when the schedule has it; None when it didn't fly there."""
    sched = s["sched_arr"] if kind == "arr" else s["sched_dep"]
    for leg, stand in legs.get((s["flight"], kind), ()):
        at = leg["in"] if kind == "arr" else leg["out"]
        if leg["status"] == "flown" and at is not None and abs((leg.get("sched") or sched) - sched) <= 5:
            return {"stand": stand, "at": at}
    return None


def legs_at(sidecar: Mapping) -> dict[tuple[str, str], list[tuple[dict, str]]]:
    out: dict[tuple[str, str], list] = defaultdict(list)
    for a in sidecar["aircraft"]:
        for v in a["visits"] if a.get("task") else ():
            for kind in ("arr", "dep"):
                if v.get(kind):
                    out[(v[kind]["flight"], kind)].append((v[kind], v["stand"]))
    return out


class Face:
    """The home terminal's outline as buildings.js walls it (its points in order, arc length u from the first), with
    the inward normal of each edge."""

    @classmethod
    def home(cls, stage: Mapping, site: Site) -> "Face | None":
        k = next((i for i, t in enumerate(stage["terminals"]) if t["outer"] is site.ring.outer
                  or t["outer"] == site.ring.outer), None)
        return None if k is None else cls(stage["terminals"][k]["outer"], k)

    def __init__(self, outer: Sequence[Sequence[float]], index: int):
        pts = [(float(x), float(y)) for x, y in outer]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        self.index, self.p = index, pts
        self.u = [0.0]
        for i in range(len(pts)):
            self.u.append(self.u[-1] + dist(pts[i], pts[(i + 1) % len(pts)]))
        self.L = self.u[-1]
        self.n = []
        for i in range(len(pts)):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % len(pts)]
            d = math.hypot(bx - ax, by - ay) or 1.0
            nx, ny = -(by - ay) / d, (bx - ax) / d
            mx, my = (ax + bx) / 2, (ay + by) / 2
            self.n.append((nx, ny) if inside(pts, (mx + nx * 0.5, my + ny * 0.5)) else (-nx, -ny))

    def project(self, q: Sequence[float]) -> tuple[float, float]:
        best = (math.inf, 0.0)
        for i in range(len(self.p)):
            (ax, ay), (bx, by) = self.p[i], self.p[(i + 1) % len(self.p)]
            dx, dy = bx - ax, by - ay
            ll = dx * dx + dy * dy or 1.0
            w = max(0.0, min(1.0, ((q[0] - ax) * dx + (q[1] - ay) * dy) / ll))
            d = math.hypot(ax + dx * w - q[0], ay + dy * w - q[1])
            if d < best[0]:
                best = (d, self.u[i] + w * math.sqrt(ll))
        return best[1], best[0]

    def edge(self, u: float) -> int:
        u %= self.L
        return next(i for i in range(len(self.p)) if self.u[i] <= u < self.u[i + 1] or i == len(self.p) - 1)

    def at(self, u: float, inward: float = 0.0) -> tuple[float, float]:
        u %= self.L
        i = self.edge(u)
        (ax, ay), (bx, by) = self.p[i], self.p[(i + 1) % len(self.p)]
        w = (u - self.u[i]) / ((self.u[i + 1] - self.u[i]) or 1.0)
        nx, ny = self.n[i]
        return ax + (bx - ax) * w + nx * inward, ay + (by - ay) * w + ny * inward

    def mitre(self, k: int) -> tuple[float, float]:
        n0, n1 = self.n[k - 1], self.n[k]
        bx, by = n0[0] + n1[0], n0[1] + n1[1]
        m = math.hypot(bx, by)
        bx, by = (bx / m, by / m) if m > 1e-6 else n1
        c = max(0.5, bx * n1[0] + by * n1[1])
        return bx / c, by / c

    def span(self, u0: float, u1: float) -> list[tuple[float, float, tuple[float, float]]]:
        """The outline from u0 forward to u1: (x, y, inward normal) at its ends and at each corner between."""
        out = [(*self.at(u0), self.n[self.edge(u0)])]
        length_ = (u1 - u0) % self.L
        for k in sorted(range(len(self.p)), key=lambda k: (self.u[k] - u0) % self.L):
            if 0 < (self.u[k] - u0) % self.L < length_:
                out.append((*self.p[k], self.mitre(k)))
        out.append((*self.at(u1), self.n[self.edge(u1)]))
        return out

    def lounges(self, site: Site, home: str | None) -> list[dict]:
        """A lounge behind the glass at each home stand whose jet bridge meets the outline, as wide as the stands
        either side allow."""
        at = []
        for ref, root in site.roots.items():
            if site.stands[ref].get("terminal") != home:
                continue
            u, d = self.project(root)
            if d < GATE:
                at.append((u, ref))
        at.sort()
        out = []
        for k, (u, ref) in enumerate(at):
            lo = (u - at[k - 1][0]) % self.L if len(at) > 1 else 2 * HALF
            hi = (at[(k + 1) % len(at)][0] - u) % self.L if len(at) > 1 else 2 * HALF
            a, b = min(HALF, lo / 2 - 0.5), min(HALF, hi / 2 - 0.5)
            if a + b < 8:
                continue
            u0, u1 = (u - a) % self.L, (u + b) % self.L
            pts = self.span(u0, u1)
            out.append({"stand": ref, "u": [round(u0, 2), round(u1, 2)], "c": round(u, 2),
                        "p": [[num(x), num(y)] for x, y, _ in pts], "n": [[round(n[0], 4), round(n[1], 4)]
                                                                        for _, _, n in pts],
                        "door": [num(v) for v in self.at(u, 1.0)]})
        return out

    def counter(self, lounges: list[dict], legs: Mapping) -> int:
        """The service counter's lounge: of the middle half of the lounges by position, the one with the fewest
        departures over the day."""
        deps = defaultdict(int)
        for (_, kind), found in legs.items():
            for _, stand in found:
                if kind == "dep":
                    deps[stand] += 1
        k = len(lounges)
        mid = list(range(k // 4, max(k // 4 + 1, 3 * k // 4)))
        return min(mid, key=lambda i: (deps[lounges[i]["stand"]], abs(i - k / 2)))

    def walk(self, a: dict, b: dict, t0: float, site: Site) -> list[dict]:
        """From lounge a's door along the concourse to lounge b's, the airside way round, on the departure level."""
        ua, ub = a["c"], b["c"]
        land = self.project(site.ring.at(site.landside))[0]
        forward = (land - ua) % self.L >= (ub - ua) % self.L
        pts = [tuple(a["door"]), self.at(ua, INSIDE)]
        span = (ub - ua) % self.L if forward else (ua - ub) % self.L
        corners = sorted(((self.u[k] - ua) % self.L if forward else (ua - self.u[k]) % self.L, k)
                         for k in range(len(self.p)))
        pts += [tuple(x + m * INSIDE for x, m in zip(self.p[k], self.mitre(k), strict=True))
                for d, k in corners if 0 < d < span]
        pts += [self.at(ub, INSIDE), tuple(b["door"])]
        path = shortcut([p for i, p in enumerate(pts) if i == 0 or dist(p, pts[i - 1]) > 0.3])
        return [seg(t0, path, FLOOR)]

    def exit(self, curb: "Curb") -> dict:
        """The concourse's way down to the landside, by the curb, as a lounge-like place to walk to."""
        u = self.project(curb.walk)[0]
        return {"c": u, "door": list(self.at(u, INSIDE))}

    def to_curb(self, a: dict, t0: float, site: Site, curb: "Curb") -> list[dict]:
        """From lounge a along the concourse to the landside, down and out to the curb."""
        exit_ = self.exit(curb)
        u = exit_["c"]
        inside_ = self.walk(a, exit_, t0, site)
        down = inside_[-1]["t"][1] + 2.0
        return [*inside_, seg(down, [self.at(u, -1.0), curb.xy], 0.0)]


def seg(t0: float, path: Sequence[Sequence[float]], z: float) -> dict:
    return {"do": "walk", "t": [round(t0, 1), round(t0 + length(path) / WALK, 1)],
            "p": [[num(x), num(y)] for x, y in path], "z": z}


def inside(ring: Sequence[Sequence[float]], q: Sequence[float]) -> bool:
    x, y, c = q[0], q[1], False
    for i in range(len(ring)):
        (xi, yi), (xj, yj) = ring[i], ring[i - 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
    return c
