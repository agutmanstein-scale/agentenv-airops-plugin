"""A mock v3 episode for the viewer's QA: a recorded v2 control-center day with invented passengers and a customer desk,
in the shape the v3 env (airops-occ3) emits (web/episode.schema.json `passengers` and the `customer` role in `occ`),
its traffic sidecar with the hub's passengers (occ/paxstage.py), and an index, written as a viewer folder beside links
to the source folder's stages, radar and twins. Nothing here is a result.

The passengers follow the env's pass (programs/pax.py on v3-passengers) in a small way: bookings are itineraries of
one leg or two through a connecting station (JetBlue's connecting shares at JFK, BOS, FLL, MCO and SJU, more on a few
spoke routes, a connection sold from the station's MCT to DWELL minutes); a group is disrupted when a leg it holds is
cancelled (at the decision, or when it gets there) or when it lands within the MCT of its outbound's pushback (a
misconnect, at the pushback); at that minute it is rebooked, earliest arrival first, onto today's JetBlue itineraries
that fly and have seats, else stays overnight for one of the following days' flights (a hotel, `until` that flight's
departure) or, some of them, is refunded (home). The customer desk asks ops to HOLD an outbound for its connecting
passengers when they would miss it by SLACK minutes or less and number CUSTOMER_PAX or more, to PROTECT a late flight
most of whose passengers would have nowhere to go if it were cancelled, and to cancel early (SUGGEST_CANCEL) a flight
ops then cancels; ops answers at its next turn. It makes no decisions of its own: the rebooking is the airline's
default, the pass.

usage: .venv/bin/python scripts/mock_v3.py VIEWER/episodes/NAME.json --out FOLDER [--run NAME]
"""

import argparse
import gzip
import heapq
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

from agentenv_airops.occ import paxstage

CONNECT = ("JFK", "BOS", "FLL", "MCO", "SJU")
SHARE = {"JFK": 0.08, "BOS": 0.05, "FLL": 0.03, "MCO": 0.012, "SJU": 0.04}
SPOKE = {"SYR": 0.4, "ROC": 0.3, "PWM": 0.3, "ACK": 0.27, "MVY": 0.27, "PQI": 0.45, "BUF": 0.15, "SJU": 0.12,
         "BQN": 0.2, "STT": 0.2, "BTV": 0.2}
SEATS = {"A320": 162, "A321": 200, "A321P": 160, "A220": 140, "E190": 100}
DWELL, MEDIAN, FREEZE = 360, 110, 60
SLACK, CUSTOMER_PAX, REFUND = 15, 10, 0.15


def mct(station: str) -> int:
    return 45 if station in ("JFK", "BOS") else 40


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("episode", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--run", default="mock-v3")
    a = p.parse_args(argv)
    src = a.episode.resolve().parent
    ep = json.loads(a.episode.read_text())
    if not ep.get("occ") or ep.get("passengers"):
        raise SystemExit("needs a v2 control-center episode")
    traffic = json.loads(gzip.decompress((src / ep["stage"]["traffic"]).read_bytes())) if ep.get("stage") else None
    stage = json.loads(gzip.decompress((src / ep["stage"]["url"]).read_bytes())) if ep.get("stage") else None
    mock(ep, random.Random(ep["task"]["task_id"]))
    stem = f"{a.run}.{ep['model'].replace('/', '-')}.{ep['task']['task_id']}"
    ep["run"] = a.run
    out = a.out / "episodes"
    out.mkdir(parents=True, exist_ok=True)
    if traffic is not None:
        ep["stage"]["traffic"] = f"{stem}.stage.json.gz"
        traffic["passengers"] = paxstage.passengers(ep, traffic, stage)
        (out / ep["stage"]["traffic"]).write_bytes(gzip.compress(json.dumps(traffic, separators=(",", ":")).encode()))
    (out / f"{stem}.json").write_text(json.dumps(ep, separators=(",", ":")))
    index = json.loads((src / "index.json").read_text()) if (src / "index.json").is_file() else {"episodes": []}
    row = next((e for e in index["episodes"] if e["url"] == a.episode.name), {})
    row = {k: v for k, v in row.items() if k != "thumb"}
    row |= {"url": f"{stem}.json", "run": a.run, "occ": ep["occ"]["label"], "passengers": True}
    (out / "index.json").write_text(json.dumps({"version": 1, "episodes": [row]}, indent=1))
    for name in ("stage", "radar", "twin", "thumbs"):
        link = a.out / name
        if (src.parent / name).exists() and not link.exists():
            link.symlink_to(src.parent / name)
    print(f"wrote {out / (stem + '.json')}: {summary(ep)}")
    return 0


def mock(ep: dict, rng: random.Random) -> None:
    sched = {s["id"]: s for s in ep["schedule"]}
    ex = {e["id"]: e for e in ep["executed"]}
    seats = {t["tail"]: t.get("seats") for t in ep["task"]["tails"]}
    cap = {i: seats.get(ex[i].get("tail") or s.get("tail")) or SEATS.get(s["family"], 162) for i, s in sched.items()}
    groups = book(sched, rng)
    occ = ep["occ"]
    one = occ["mode"] == "one"
    watches = sorted(w["t"] for w in ep["watches"])
    asks, held = customer_asks(ep, sched, ex, groups, watches, rng)
    day = Pass(ep["task"]["start"], sched, ex, cap, groups, held, rng).run()
    holds = {a["flight"]: a["time"] - ex[a["flight"]]["dep"] for a in asks if a["type"] == "HOLD"
             and a["reply"] == "accept"}
    if one:
        occ["decisions"] += [{"t": a["at"], "shown": a["at"] + 20, "desk": "one", "action": "hold",
                              "flight": a["flight"], "detail": hhmm(a["time"]), "ref": None, "via": "confirm"}
                             for a in asks if a["type"] == "HOLD" and a["reply"] == "accept"]
        occ["label"] = "one agent at every desk"
    else:
        turns = customer_turns(occ, ep["watches"])
        made = [a | {"turn": next((u for u in turns if u["t"] == a["at"]), None)} for a in asks]
        occ["requests"] = merge_requests(occ, [a for a in made if a["turn"] is not None], turns)
        occ["turns"] = renumber([*occ["turns"], *turns])
        occ["pages"] = sorted([*occ["pages"], *pages(ep, turns, day.events, groups, sched)],
                              key=lambda x: (x["t"], x["desk"]))
        occ["notes"] = sorted([*occ["notes"], *notes(turns, occ["requests"], day.events)],
                              key=lambda x: (x["shown"], x["desk"]))
        occ["desks"] = [*occ["desks"], "customer"]
        occ["label"] = "three desks"
    occ["decisions"] = sorted(occ["decisions"], key=lambda d: (d["shown"], d["desk"]))
    rows = flight_rows(sched, groups, day, holds)
    ep["passengers"] = {"flights": [r for r in rows.values() if r["booked"] or r["added"]],
                        "connections": day.connections(groups, sched),
                        "events": sorted(day.events, key=lambda e: (e["t"], e["group"], e["kind"]))}


def book(sched: dict, rng: random.Random) -> list[tuple[int, tuple[int, ...], int]]:
    """Itineraries (group id, legs, passengers): each flight's connecting passengers from the day's inbound flights at
    its connecting station, by scheduled dwell near the sold median; everyone else local."""
    into: dict[str, list] = defaultdict(list)
    for s in sched.values():
        into[s["dest"]].append(s)
    used: Counter = Counter()
    two: list[tuple[int, int, int]] = []
    for o in sorted(sched.values(), key=lambda s: (s["sched_dep"], s["id"])):
        if o["orig"] not in CONNECT:
            continue
        share = SPOKE.get(o["dest"], SHARE.get(o["orig"], 0.02))
        n = min(round(o["pax"] * share * rng.uniform(0.7, 1.3)), int(0.35 * o["pax"]) - used[o["id"]])
        cands = [i for i in into[o["orig"]] if i["orig"] != o["dest"]
                 and mct(o["orig"]) <= o["sched_dep"] - i["sched_arr"] <= DWELL and used[i["id"]] < 0.25 * i["pax"]]
        if n <= 0 or not cands:
            continue
        w = [math.exp(-(((o["sched_dep"] - i["sched_arr"]) - MEDIAN) / 70) ** 2) for i in cands]
        picks: Counter = Counter()
        for _ in range(n):
            i = rng.choices(cands, w)[0]
            if used[i["id"]] < 0.25 * i["pax"]:
                picks[i["id"]] += 1
                used[i["id"]] += 1
                used[o["id"]] += 1
        two += [(i, o["id"], k) for i, k in sorted(picks.items())]
    out = [(g, (i, o), k) for g, (i, o, k) in enumerate(two)]
    for s in sorted(sched.values(), key=lambda s: s["id"]):
        if s["pax"] - used[s["id"]] > 0:
            out.append((len(out), (s["id"],), s["pax"] - used[s["id"]]))
    return out


def customer_asks(ep: dict, sched: dict, ex: dict, groups: list, watches: list[int],
                  rng: random.Random) -> tuple[list[dict], set[tuple[int, int]]]:
    """The customer desk's requests, each at the watch it is sent (`at`), and the connections ops's accepted holds
    save: HOLD, PROTECT, SUGGEST_CANCEL."""
    flew = lambda i: ex[i]["status"] == "flown"  # noqa: E731
    late: dict[int, list] = defaultdict(list)
    for _, legs, n in groups:
        if len(legs) == 2 and flew(legs[0]) and flew(legs[1]):
            i, o = legs
            gap = ex[i]["arr"] + mct(sched[o]["orig"]) - ex[o]["dep"]
            if 0 < gap <= SLACK:
                late[o].append((i, n, ex[i]["arr"] + mct(sched[o]["orig"])))
    asks, held = [], set()
    for o, inbound in sorted(late.items()):
        n = sum(k for _, k, _ in inbound)
        t = max((w for w in watches if w <= ex[o]["dep"] - FREEZE), default=None)
        if n < CUSTOMER_PAX or t is None:
            continue
        s, ok, until = sched[o], rng.random() < 0.75, max(m for _, _, m in inbound)
        asks.append(ask("HOLD", o, t, n, ok, until=until,
                        text=f"Please hold {s['flight']} to {hhmm(until)} ({until - ex[o]['dep']} min): {n} connecting "
                             f"passengers from {', '.join(sorted({sched[i]['flight'] for i, _, _ in inbound}))} land "
                             "too late to make it, and the next seats are hours later.",
                        reply=f"Hold {s['flight']} to {hhmm(until)} accepted." if ok else
                        f"Declined: holding {s['flight']} costs its slot."))
        if ok:
            held |= {(i, o) for i, _, _ in inbound}
    for e in sorted((e for e in ep["executed"] if e["status"] == "flown" and (e.get("arr_delay") or 0) >= 150
                     and sched[e["id"]]["pax"] >= 120), key=lambda e: -(e["arr_delay"] or 0))[:3]:
        s = sched[e["id"]]
        t = max((w for w in watches if w <= s["sched_dep"] - FREEZE), default=None)
        if t is not None:
            asks.append(ask("PROTECT", e["id"], t, s["pax"], True,
                            text=f"Please don't cancel {s['flight']} {s['orig']}-{s['dest']}: most of its {s['pax']} "
                                 "passengers have no other seat today.",
                            reply=f"{s['flight']} stays in the plan, late as it is."))
    for d in [d for d in ep["occ"]["decisions"] if d["desk"] == "ops" and d["action"] == "cancel"
              and d.get("flight") in sched and not d.get("ref")][:2]:
        s = sched[d["flight"]]
        t = max((w for w in watches if w < d["t"]), default=None)
        if t is not None:
            asks.append(ask("SUGGEST_CANCEL", d["flight"], t, s["pax"], True, decision=d,
                            text=f"Suggest cancelling {s['flight']} {s['orig']}-{s['dest']} now: its {s['pax']} "
                                 "passengers get today's seats while they last.",
                            reply=f"Cancelling {s['flight']}."))
    return asks, held


def ask(kind: str, flight: int, t: int, pax: int, ok: bool, *, text: str, reply: str, until: int | None = None,
        decision: dict | None = None) -> dict:
    return {"sender": "customer", "receiver": "ops", "type": kind, "flight": flight, "time": until, "pax": pax,
            "at": t, "status": "accepted" if ok else "declined", "reply": "accept" if ok else "decline",
            "text": text, "reply_text": reply, "decision": decision}


class Day:
    def __init__(self):
        self.events: list[dict] = []
        self.flew: Counter = Counter()
        self.added: Counter = Counter()
        self.dropped: Counter = Counter()
        self.made: Counter = Counter()

    def connections(self, groups: list, sched: dict) -> list[dict]:
        return [{"from": legs[0], "to": legs[1], "at": sched[legs[1]]["orig"], "pax": n, "made": self.made[g]}
                for g, legs, n in groups if len(legs) == 2]


class Pass:
    """The day's passengers on its flights as executed, in the env's way (the module doc)."""

    def __init__(self, start: int, sched: dict, ex: dict, cap: dict, groups: list, held: set, rng: random.Random):
        self.start, self.sched, self.ex, self.cap, self.groups = start, sched, ex, cap, groups
        self.held, self.rng = held, rng
        self.hold: Counter = Counter()
        for _, legs, n in groups:
            for k in legs:
                self.hold[k] += n
        self.heap: list = []
        self.day = Day()
        self.out: dict[str, list] = defaultdict(list)
        for s in sched.values():
            if ex[s["id"]]["status"] == "flown":
                self.out[s["orig"]].append(s)

    def run(self) -> Day:
        for g, legs, n in self.groups:
            self.walk(g, n, legs, (), None, self.start, legs)
        while self.heap:
            self.disrupted(*heapq.heappop(self.heap)[-1])
        return self.day

    def walk(self, g: int, n: int, legs: tuple, flew: tuple, prev: int | None, now: int, booked: tuple) -> None:
        for x, k in enumerate(legs):
            s, r = self.sched[k], self.ex[k]
            if r["status"] != "flown":
                at = r.get("decided") if r.get("decided") is not None else s["sched_dep"] - 60
                self.push(max(at, now if prev is None else prev), g, n, k, legs[x + 1:], flew, prev, booked)
                return
            if prev is not None and r["dep"] < prev + mct(s["orig"]) and (flew[-1], k) not in self.held:
                self.day.events.append({"t": r["dep"], "kind": "misconnect", "at": s["orig"], "flight": k,
                                        "from": flew[-1], "pax": n, "group": g})
                self.push(max(r["dep"], now), g, n, k, legs[x + 1:], flew, prev, booked)
                return
            flew, prev = (*flew, k), r["arr"]
            self.day.flew[k] += n
        if flew == booked and len(booked) == 2:
            self.day.made[g] += n

    def push(self, t: int, g: int, n: int, k: int, *rest) -> None:
        heapq.heappush(self.heap, (t, self.sched[k]["sched_dep"], g, len(self.heap), (t, g, n, k, *rest)))

    def disrupted(self, t: int, g: int, n: int, was: int, rest: tuple, flew: tuple, prev: int | None,
                  booked: tuple) -> None:
        for k in rest:
            self.hold[k] -= n
            self.day.dropped[k] += n
        s = self.sched[was]["orig"]
        dest = self.sched[booked[-1]]["dest"]
        ready = t + mct(s) if prev is None else max(t, prev + mct(s))
        for legs in self.itineraries(s, dest, ready):
            m = min(n, *(self.cap[k] - self.hold[k] for k in legs))
            if m <= 0:
                continue
            for k in legs:
                self.hold[k] += m
                self.day.added[k] += m
            self.day.events.append({"t": t, "kind": "rebook", "at": s, "flight": was, "to": legs[0], "pax": m,
                                    "group": g})
            self.walk(g, m, legs, flew, prev, t, booked)
            n -= m
            if not n:
                return
        home = round(n * REFUND * 2) if self.rng.random() < 0.5 else 0
        if n - home:
            self.day.events.append({"t": t, "kind": "hotel", "at": s, "flight": was, "pax": n - home, "group": g,
                                    "until": self.tomorrow(s, dest)})
        if home:
            self.day.events.append({"t": t, "kind": "home", "at": s, "flight": was, "pax": home, "group": g})

    def itineraries(self, s: str, dest: str, ready: int) -> list[tuple[int, ...]]:
        """Today's JetBlue itineraries from s to dest that fly, leaving at or after `ready`, earliest arrival first."""
        out = []
        for f in self.out[s]:
            r = self.ex[f["id"]]
            if r["dep"] < ready:
                continue
            if f["dest"] == dest:
                out.append((r["arr"], f["sched_dep"], (f["id"],)))
            elif f["dest"] in CONNECT:
                for f2 in self.out[f["dest"]]:
                    r2 = self.ex[f2["id"]]
                    if f2["dest"] == dest and mct(f2["orig"]) <= f2["sched_dep"] - f["sched_arr"] <= DWELL \
                            and r2["dep"] >= r["arr"] + mct(f2["orig"]):
                        out.append((r2["arr"], f["sched_dep"], (f["id"], f2["id"])))
        return [legs for *_, legs in sorted(out)][:8]

    def tomorrow(self, s: str, dest: str) -> int:
        """The next day's first scheduled departure from s toward dest (today's schedule a day on)."""
        deps = [f["sched_dep"] for f in self.sched.values() if f["orig"] == s and f["dest"] == dest]
        return 1440 + (min(deps) if deps else 420 + self.rng.randrange(0, 180, 15))


def flight_rows(sched: dict, groups: list, day: Day, holds: dict[int, int]) -> dict[int, dict]:
    """Each flight's passengers as the episode carries them: booked + added = flown + Σ rebooked + stranded + home +
    dropped."""
    booked, cin, cout = Counter(), Counter(), Counter()
    for _, legs, n in groups:
        for k in legs:
            booked[k] += n
        if len(legs) == 2:
            cout[legs[0]] += n
            cin[legs[1]] += n
    rows = {i: {"id": i, "booked": booked[i], "added": day.added[i], "conn_in": cin[i], "conn_out": cout[i],
                "flown": day.flew[i], "misconnected": 0, "rebooked": [], "stranded": 0, "home": 0,
                "dropped": day.dropped[i], "hold": holds.get(i, 0)} for i in sched}
    moved: dict[int, Counter] = defaultdict(Counter)
    for e in day.events:
        r = rows[e["flight"]]
        if e["kind"] == "misconnect":
            r["misconnected"] += e["pax"]
        elif e["kind"] == "rebook":
            moved[e["flight"]][e["to"]] += e["pax"]
        elif e["kind"] == "hotel":
            r["stranded"] += e["pax"]
        else:
            r["home"] += e["pax"]
    for i, c in moved.items():
        rows[i]["rebooked"] = [{"to": k, "pax": n} for k, n in sorted(c.items())]
    return rows


def customer_turns(occ: dict, watches: list[dict]) -> list[dict]:
    """A customer turn after each watch's last turn, ten minutes or until the next watch."""
    ts = sorted(w["t"] for w in watches)
    out = []
    for k, t in enumerate(ts):
        mine = [u for u in occ["turns"] if u["t"] == t]
        if not mine:
            continue
        a = max(u["to"] for u in mine)
        b = min(a + 10, (ts[k + 1] if k + 1 < len(ts) else t + 60) - 1)
        if b - a >= 3:
            out.append({"n": 0, "watch": k, "t": t, "desk": "customer", "kind": "customer", "calls": 6,
                        "from": round(a, 2), "to": round(b, 2)})
    return out


def merge_requests(occ: dict, asks: list[dict], customer: list[dict]) -> list[dict]:
    """The customer desk's requests in v2's shape, numbered on from the day's, shown in its turn and answered in ops's
    next; an accepted hold is ops's hold decision then, an accepted suggested cancellation the cancel ops made."""
    seen: Counter = Counter()
    rows = []
    ops = sorted((u for u in occ["turns"] if u["desk"] == "ops"), key=lambda u: u["from"])
    for a in sorted(asks, key=lambda a: a["turn"]["from"]):
        turn = a["turn"]
        k = seen[turn["t"]]
        seen[turn["t"]] += 1
        shown = round(min(turn["to"] - 0.5, turn["from"] + 1.5 + 2.2 * k), 2)
        answer = next((u for u in ops if u["from"] > turn["to"]), None)
        d = a["decision"]
        if d is not None:
            answered, replied = round(d["shown"] - 0.5, 2), d["t"]
        elif answer is not None:
            answered, replied = round(answer["from"] + 2.5, 2), answer["t"]
        else:
            answered = replied = None
        row = {"id": "", "sender": "customer", "receiver": "ops", "type": a["type"], "flight": a["flight"],
               "time": a["time"], "text": a["text"], "sent": turn["t"], "status": a["status"], "reply": a["reply"],
               "replied": replied, "reply_text": a["reply_text"], "actions": [], "shown": shown,
               "answered": answered, "pax": a["pax"]}
        if a["type"] == "HOLD" and a["reply"] == "accept" and answered is not None:
            d = {"t": replied, "shown": answered + 0.5, "desk": "ops", "action": "hold", "flight": a["flight"],
                 "detail": hhmm(a["time"]), "ref": None, "via": "accept"}
            occ["decisions"].append(d)
        if d is not None and answered is not None:
            row["actions"] = [{"desk": "ops", "action": d["action"], "flight": d["flight"], "t": replied}]
        rows.append((row, d))
    first = 1 + max((int(r["id"][1:]) for r in occ["requests"] if r["id"][1:].isdigit()), default=0)
    for k, (r, d) in enumerate(sorted(rows, key=lambda x: x[0]["shown"]), first):
        r["id"] = f"R{k}"
        if d is not None:
            d["ref"] = r["id"]
    return sorted([*occ["requests"], *(r for r, _ in rows)], key=lambda r: (r["sent"], r["shown"]))


def renumber(turns: list[dict]) -> list[dict]:
    out = sorted(turns, key=lambda u: u["from"])
    for k, u in enumerate(out):
        u["n"] = k
    return out


def pages(ep: dict, turns: list[dict], events: list[dict], groups: list, sched: dict) -> list[dict]:
    n = len(ep["watches"])
    conns = [(legs, k) for _, legs, k in groups if len(legs) == 2]
    out = []
    for u in turns:
        t = u["t"]
        soon = sorted(((legs, k) for legs, k in conns if t <= sched[legs[1]]["sched_dep"] <= t + 180 and k >= 5),
                      key=lambda x: sched[x[0][1]]["sched_dep"])[:6]
        missed = [e for e in events if e["kind"] == "misconnect" and t - 60 <= e["t"] <= t]
        moved = sum(e["pax"] for e in events if e["kind"] == "rebook" and e["t"] <= t)
        night = sum(e["pax"] for e in events if e["kind"] == "hotel" and e["t"] <= t)
        lines = [f"# Customer desk, JetBlue, {ep['task']['date']}, {hhmm(t)} (watch {u['watch'] + 1} of {n}, customer "
                 f"turn); {moved:,} rebooked and {night:,} overnight so far.", "",
                 f"Connections in the next three hours: {sum(k for _, k in soon)} passengers on {len(soon)} "
                 "flights shown below.", "", "## Connections at risk"]
        lines += [f"{k} on f{a} to f{b} at {sched[b]['orig']}, departs {hhmm(sched[b]['sched_dep'])}"
                  for (a, b), k in soon] or ["None."]
        lines += ["", "## Missed this hour"]
        lines += [f"{e['pax']} from f{e['from']} missed f{e['flight']} at {e['at']}" for e in missed] or ["None."]
        out.append({"t": t, "watch": u["watch"], "desk": "customer", "text": "\n".join(lines)})
    return out


def notes(turns: list[dict], requests: list[dict], events: list[dict]) -> list[dict]:
    out = []
    for u in turns:
        asked = [r for r in requests if r["sender"] == "customer" and r["sent"] == u["t"]]
        moved = sum(e["pax"] for e in events if e["kind"] == "rebook" and u["t"] - 60 < e["t"] <= u["t"])
        if asked or moved:
            what = ", ".join(f"{r['type'].replace('_', ' ').lower()} {r['id']}" for r in asked)
            out.append({"t": u["t"], "shown": u["to"], "desk": "customer", "kind": "customer",
                        "note": f"{moved} passengers rebooked this hour" + (f"; asked ops: {what}" if what else "")
                                + "."})
    return out


def summary(ep: dict) -> str:
    p = ep["passengers"]
    k: Counter = Counter()
    for e in p["events"]:
        k[e["kind"]] += e["pax"]
    asks = Counter(r["type"] for r in ep["occ"]["requests"] if r["sender"] == "customer")
    return (f"{len(p['connections'])} connections ({sum(c['pax'] for c in p['connections'])} pax), "
            f"{k['misconnect']} misconnected, {k['rebook']} rebooked, {k['hotel']} overnight, {k['home']} refunded, "
            f"customer requests {dict(asks)}")


def hhmm(m: float) -> str:
    m = round(m)
    return f"{m // 60 % 24:02d}:{m % 60:02d}{'+1' if m >= 1440 else ''}"


if __name__ == "__main__":
    sys.exit(main())
