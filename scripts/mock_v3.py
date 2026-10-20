"""A mock v3 episode for the viewer's QA: a recorded v2 control-center day with invented passengers and a customer desk
(web/episode.schema.json `passengers` and the `customer` role in `occ`), its traffic sidecar with the hub's passengers
(occ/paxstage.py), and an index, written as a viewer folder beside links to the source folder's stages, radar and
twins. The passengers are synthetic, drawn from public connecting shares (about 5% of B6's departing passengers at BOS
connect, 8% at JFK, more on a few spoke routes), not the env's; nothing here is a result.

usage: .venv/bin/python scripts/mock_v3.py VIEWER/episodes/NAME.json --out FOLDER
"""

import argparse
import gzip
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

from agentenv_airops.occ import paxstage

SHARE = {"JFK": 0.08, "BOS": 0.05, "FLL": 0.03, "MCO": 0.012, "SJU": 0.04}
SPOKE = {"SYR": 0.4, "ROC": 0.3, "PWM": 0.3, "ACK": 0.27, "MVY": 0.27, "PQI": 0.45, "BUF": 0.15, "SJU": 0.12,
         "BQN": 0.2, "STT": 0.2, "BTV": 0.2}
SEATS = {"A320": 162, "A321": 200, "A321P": 160, "A220": 140, "E190": 100}
MCT, MAKE, DWELL, AWAY = 45, 20, 110, 0.45
"""Connections are sold with 45 to 240 minutes' dwell (most near 110); a passenger makes one with 20 minutes from
block-in to the outbound's pushback; 45% of a cancelled flight's passengers are away from home."""


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
    row |= {"url": f"{stem}.json", "run": a.run, "occ": ep["occ"]["label"]}
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
    cap = {i: seats.get(ex[i].get("tail")) or SEATS.get(s["family"], 162) for i, s in sched.items()}
    rows = {i: {"id": i, "booked": s["pax"], "added": 0, "conn_in": 0, "conn_out": 0, "flown": 0, "misconnected": 0,
                "rebooked": [], "stranded": 0, "home": 0, "hold": 0} for i, s in sched.items()}
    flew = lambda i: ex[i]["status"] == "flown"  # noqa: E731
    conns = connections(sched, rng)
    occ = ep["occ"]
    turns = customer_turns(occ, ep["watches"])
    requests, decisions, events = [], [], []
    holds: dict[int, list] = defaultdict(list)
    for c in conns:
        rows[c["to"]]["conn_in"] += c["pax"]
        rows[c["from"]]["conn_out"] += c["pax"]
        if not (flew(c["from"]) and flew(c["to"])):
            c["made"] = 0
            continue
        gap = ex[c["to"]]["dep"] - ex[c["from"]]["arr"]
        c["made"] = c["pax"] if gap >= MAKE else 0
        if MAKE - 15 <= gap < MAKE and c["pax"] >= 4:
            holds[c["to"]].append((c, MAKE - gap))
    for o, list_ in sorted(holds.items(), key=lambda x: ex[x[0]]["dep"]):
        pax, need = sum(c["pax"] for c, _ in list_), max(n for _, n in list_)
        turn = before(turns, ex[o]["dep"] - 25)
        if turn is None:
            continue
        ok = rng.random() < 0.75
        s = sched[o]
        until = ex[o]["dep"] + need
        requests.append({"sender": "customer", "receiver": "ops", "type": "HOLD", "flight": o, "time": until,
                         "pax": pax, "turn": turn, "status": "accepted" if ok else "declined",
                         "reply": "accept" if ok else "decline",
                         "text": f"Please hold {s['flight']} to {hhmm(until)} ({need} min): {pax} connecting "
                                 "passengers "
                                 f"from {', '.join(sorted({sched[c['from']]['flight'] for c, _ in list_}))} are on "
                                 f"their way to the gate, and the next seats to {s['dest']} are hours later.",
                         "reply_text": (f"Hold {s['flight']} to {hhmm(until)} accepted; aircraft and slot allow it."
                                        if ok else f"Declined: holding {s['flight']} costs its slot; please rebook.")})
        if ok:
            rows[o]["hold"] = need
            for c, _ in list_:
                c["made"] = c["pax"]
    groups = []
    for i, s in sched.items():
        if not flew(i):
            t = ex[i].get("decided") or s["sched_dep"] - 60
            groups.append((t, i, rows[i]["booked"], "cancel"))
    for c in conns:
        if flew(c["from"]) and flew(c["to"]) and c["pax"] > c["made"]:
            n = c["pax"] - c["made"]
            t = ex[c["to"]]["dep"]
            events.append({"t": t, "kind": "misconnect", "at": c["at"], "flight": c["to"], "from": c["from"], "pax": n})
            rows[c["to"]]["misconnected"] += n
            groups.append((t, c["to"], n, "misconnect"))
    free = {i: max(0, cap[i] - rows[i]["booked"]) for i in sched}
    for t, i, n, why in sorted(groups):
        s, turn = sched[i], after(turns, t)
        if turn is None:
            rows[i]["home"] += n
            events.append({"t": round(t), "kind": "home", "at": s["orig"], "flight": i, "pax": n})
            continue
        at, tr = s["orig"], round(turn["from"] + rng.uniform(1, max(1.5, turn["to"] - turn["from"] - 1)))
        later = sorted((g for g in sched.values() if g["orig"] == s["orig"] and g["dest"] == s["dest"] and flew(g["id"])
                        and ex[g["id"]]["dep"] >= tr + 30), key=lambda g: ex[g["id"]]["dep"])
        left = n
        for g in later[:3]:
            k = min(left, free[g["id"]])
            if k <= 0:
                continue
            free[g["id"]] -= k
            left -= k
            rows[i]["rebooked"].append({"to": g["id"], "pax": k})
            rows[g["id"]]["added"] += k
            events.append({"t": tr, "kind": "rebook", "at": at, "flight": i, "to": g["id"], "pax": k})
            decisions.append({"t": turn["t"], "shown": tr, "desk": "customer", "action": "rebook", "flight": i,
                              "to": g["id"], "pax": k, "detail": None, "ref": None, "via": "rebook"})
        if left <= 0:
            continue
        away = left if why == "misconnect" else round(left * AWAY)
        if away:
            th = max(tr, 17 * 60) if tr < 17 * 60 and later else tr
            rows[i]["stranded"] += away
            events.append({"t": th, "kind": "hotel", "at": at, "flight": i, "pax": away,
                           "until": 1440 + 300 + rng.randrange(0, 120, 15)})
            decisions.append({"t": turn["t"], "shown": tr + 0.5, "desk": "customer", "action": "hotel", "flight": i,
                              "pax": away, "at": at, "detail": None, "ref": None, "via": "hotel"})
        if left - away:
            rows[i]["home"] += left - away
            events.append({"t": tr, "kind": "home", "at": at, "flight": i, "pax": left - away})
    for i, r in rows.items():
        r["flown"] = r["booked"] + r["added"] - sum(x["pax"] for x in r["rebooked"]) - r["stranded"] - r["home"]
        if not flew(i):
            r["flown"] = 0
    for t, i, n, why in sorted(groups):
        if why == "cancel" and n >= 40:
            turn = after(turns, t)
            if turn:
                s, r = sched[i], rows[i]
                requests.append({"sender": "ops", "receiver": "customer", "type": "TEXT", "flight": None, "time": None,
                                 "pax": n, "turn": turn, "early": True,
                                 "text": f"Cancelling {s['flight']} {s['orig']}-{s['dest']}: {n} passengers to rebook, "
                                         f"{r['conn_in']} of them connecting."})
                moved = sum(x["pax"] for x in r["rebooked"])
                requests.append({"sender": "customer", "receiver": "ops", "type": "TEXT", "flight": None, "time": None,
                                 "pax": n, "turn": turn,
                                 "text": f"{s['flight']}: {moved} rebooked on later flights to {s['dest']}, "
                                         f"{r['stranded']} at hotels in {s['orig']} tonight, {r['home']} went home."})
    big = max(holds, key=lambda o: sum(c["pax"] for c, _ in holds[o]), default=None)
    if big is not None and (turn := before(turns, ex[big]["dep"] - 25)):
        s = sched[big]
        requests.append({"sender": "customer", "receiver": "crew", "type": "TEXT", "flight": None, "time": None,
                         "turn": turn, "text": f"Asking ops to hold {s['flight']} for connecting passengers: does its "
                                               f"crew stay legal if it leaves up to 15 minutes late?"})
        requests.append({"sender": "crew", "receiver": "customer", "type": "TEXT", "flight": None, "time": None,
                         "turn": next_turn(occ["turns"], turn, "crew"),
                         "text": f"{s['flight']} crew is legal to {hhmm(ex[big]['dep'] + 40)}: a 15-minute hold "
                                 "is fine."})
    occ["requests"] = merge_requests(occ["requests"], requests, occ["turns"], turns)
    occ["decisions"] = sorted([*occ["decisions"], *decisions], key=lambda d: (d["shown"], d["desk"]))
    occ["turns"] = renumber([*occ["turns"], *turns])
    occ["pages"] = sorted([*occ["pages"], *pages(ep, turns, rows, events, conns)], key=lambda x: (x["t"], x["desk"]))
    occ["notes"] = sorted([*occ["notes"], *notes(turns, events, sched)], key=lambda x: (x["shown"], x["desk"]))
    occ["desks"] = [*occ["desks"], "customer"]
    occ["label"] = "three desks"
    ep["passengers"] = {"flights": [r for r in rows.values() if r["booked"] or r["added"]],
                        "connections": [{k: c[k] for k in ("from", "to", "at", "pax", "made")} for c in conns],
                        "events": sorted(events, key=lambda e: (e["t"], e["kind"], e["flight"]))}


def connections(sched: dict, rng: random.Random) -> list[dict]:
    """Inbound -> outbound pairs at the carrier's connecting stations, by scheduled dwell near the sold median."""
    into: dict[str, list] = defaultdict(list)
    for s in sched.values():
        into[s["dest"]].append(s)
    used: dict[int, int] = defaultdict(int)
    out = []
    for o in sorted(sched.values(), key=lambda s: s["sched_dep"]):
        share = SPOKE[o["dest"]] if o["dest"] in SPOKE and o["orig"] in SHARE else SHARE.get(o["orig"], 0)
        n = round(o["pax"] * share * rng.uniform(0.7, 1.3))
        cands = [i for i in into[o["orig"]] if i["orig"] != o["dest"] and MCT <= o["sched_dep"] - i["sched_arr"] <= 240
                 and used[i["id"]] < 0.25 * i["pax"]]
        if n <= 0 or not cands:
            continue
        w = [math.exp(-(((o["sched_dep"] - i["sched_arr"]) - DWELL) / 70) ** 2) for i in cands]
        picks = {}
        for _ in range(n):
            i = rng.choices(cands, w)[0]
            if used[i["id"]] < 0.25 * i["pax"]:
                picks[i["id"]] = picks.get(i["id"], 0) + 1
                used[i["id"]] += 1
        out += [{"from": i, "to": o["id"], "at": o["orig"], "pax": k} for i, k in sorted(picks.items())]
    return out


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


def before(turns: list[dict], t: float) -> dict | None:
    return next((u for u in reversed(turns) if u["to"] <= t), None)


def after(turns: list[dict], t: float) -> dict | None:
    return next((u for u in turns if u["from"] >= t), None)


def next_turn(turns: list[dict], turn: dict, desk: str) -> dict:
    return next((u for u in sorted(turns, key=lambda u: u["from"]) if u["desk"] == desk and u["from"] > turn["to"]),
                turn)


def merge_requests(old: list[dict], new: list[dict], all_turns: list[dict], customer: list[dict]) -> list[dict]:
    """The new requests in v2's shape, shown in their sender's turn and answered in the receiver's next."""
    seen: dict[int, int] = defaultdict(int)
    rows = []
    for r in sorted(new, key=lambda r: (r["turn"]["from"], not r.get("early"))):
        turn = r.pop("turn")
        r.pop("early", None)
        k = seen[id(turn)]
        seen[id(turn)] += 1
        shown = round(min(turn["to"] - 0.5, turn["from"] + 1.5 + 2.2 * k), 2)
        typed = r["type"] != "TEXT"
        answer = next_turn([*all_turns, *customer], turn, r["receiver"]) if typed else None
        answered = round(answer["from"] + 2.5, 2) if answer else None
        rows.append({"id": "", "sender": r["sender"], "receiver": r["receiver"], "type": r["type"],
                     "flight": r["flight"], "time": r["time"], "text": r["text"], "sent": turn["t"],
                     "status": r.get("status", "delivered"), "reply": r.get("reply"),
                     "replied": answer["t"] if answer else None, "reply_text": r.get("reply_text"),
                     "actions": [{"desk": "ops", "action": "hold", "flight": r["flight"], "t": answer["t"]}]
                     if r.get("reply") == "accept" else [], "shown": shown, "answered": answered}
                    | ({"pax": r["pax"]} if r.get("pax") is not None else {}))
    out = sorted([*old, *rows], key=lambda r: (r["sent"], r["shown"]))
    for k, r in enumerate(out, 1):
        r["id"] = f"R{k}"
    return out


def renumber(turns: list[dict]) -> list[dict]:
    out = sorted(turns, key=lambda u: u["from"])
    for k, u in enumerate(out):
        u["n"] = k
    return out


def pages(ep: dict, turns: list[dict], rows: dict, events: list[dict], conns: list[dict]) -> list[dict]:
    sched, n = {s["id"]: s for s in ep["schedule"]}, len(ep["watches"])
    out = []
    for u in turns:
        t = u["t"]
        soon = sorted((c for c in conns if t <= sched[c["to"]]["sched_dep"] <= t + 180 and c["pax"] >= 5),
                      key=lambda c: sched[c["to"]]["sched_dep"])[:6]
        waiting = [e for e in events if e["kind"] in ("misconnect",) and t - 60 <= e["t"] <= t]
        done = sum(e["pax"] for e in events if e["kind"] == "rebook" and e["t"] <= t)
        hotel = sum(e["pax"] for e in events if e["kind"] == "hotel" and e["t"] <= t)
        lines = [f"# Customer desk, JetBlue, {ep['task']['date']}, {hhmm(t)} (watch {u['watch'] + 1} of {n}, customer "
                 f"turn); {done:,} rebooked and {hotel:,} at hotels so far.", "",
                 f"Connections in the next three hours: {sum(c['pax'] for c in soon)} passengers on {len(soon)} "
                 "flights shown below.", "", "## Connections at risk"]
        lines += [f"{c['pax']} on f{c['from']} to f{c['to']} at {c['at']}, departs {hhmm(sched[c['to']]['sched_dep'])}"
                  for c in soon] or ["None."]
        lines += ["", "## Missed this hour"]
        lines += [f"{e['pax']} from f{e['from']} missed f{e['flight']} at {e['at']}" for e in waiting] or ["None."]
        out.append({"t": t, "watch": u["watch"], "desk": "customer", "text": "\n".join(lines)})
    return out


def notes(turns: list[dict], events: list[dict], sched: dict) -> list[dict]:
    out = []
    for u in turns:
        mine = [e for e in events if u["from"] <= e["t"] <= u["to"] + 0.5]
        moved = sum(e["pax"] for e in mine if e["kind"] == "rebook")
        hotel = sum(e["pax"] for e in mine if e["kind"] == "hotel")
        if moved or hotel:
            out.append({"t": u["t"], "shown": u["to"], "desk": "customer", "kind": "customer",
                        "note": f"Rebooked {moved} passengers onto later flights" + (f"; {hotel} at hotels overnight"
                                                                                       if hotel else "") + "."})
    return out


def summary(ep: dict) -> str:
    p = ep["passengers"]
    k = defaultdict(int)
    for e in p["events"]:
        k[e["kind"]] += e["pax"]
    return (f"{len(p['connections'])} connections ({sum(c['pax'] for c in p['connections'])} pax), "
            f"{k['misconnect']} misconnected, {k['rebook']} rebooked, {k['hotel']} at hotels, {k['home']} home, "
            f"{sum(r['sender'] == 'customer' or r['receiver'] == 'customer' for r in ep['occ']['requests'])} "
            "customer requests")


def hhmm(m: float) -> str:
    m = round(m)
    return f"{m // 60 % 24:02d}:{m % 60:02d}{'+1' if m >= 1440 else ''}"


if __name__ == "__main__":
    sys.exit(main())
