import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("record_script", ROOT / "scripts" / "record.py")
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)
EPISODES = sorted((ROOT / "cache" / "occ" / "viewer" / "episodes").glob("occ-*.json"))


def leg(flight, kind, other, city, sched, at=None, taxi=12, tag=None, tone="ok", status="flown", **extra):
    g = {"flight": flight, "kind": kind, "other": other, "city": city, "status": status, "sched": sched, "out": None,
         "off": None, "on": None, "in": None, "tag": tag or f"{flight} {'→' if kind == 'dep' else 'from'} {city}",
         "tone": tone, **extra}
    if at is not None:
        g.update({"out": at, "off": at + taxi} if kind == "dep" else {"on": at - taxi, "in": at})
    return g


def flight(i, f, o, d, a, b, pax=120):
    return {"id": i, "flight": f, "orig": o, "dest": d, "sched_dep": a, "sched_arr": b, "pax": pax}


def request(rid, sender, kind, fid, sent, shown, answered, reply_text, status="accepted"):
    return {"id": rid, "sender": sender, "receiver": "ops" if sender == "crew" else "crew", "type": kind,
            "flight": fid, "time": None, "text": "", "sent": sent, "status": status,
            "reply": "accept" if status == "accepted" else "decline", "replied": sent, "reply_text": reply_text,
            "actions": [], "shown": shown, "answered": answered}


def day():
    """A two-desk storm day at Boston: at 10:00 the crew desk asks the ops desk to cancel B6 20 to New York, which
    stays at its gate; B6 10 to Miami waits at its gate for a crew until it pushes back at 11:40; a ground stop
    arrives at the 11:00 watch; at 12:00 the ops desk asks the crew desk to cover B6 30 from Detroit, which lands at
    13:05 with the pilots it found; at 12:40 the ops desk cancels three flights at once."""
    task = {"task_id": "b6-20250216-0400", "carrier": "B6", "hub": "BOS", "date": "2025-02-16",
            "tz": "America/New_York", "window": [600, 900], "start": 240, "airports": {
                "BOS": {"name": "General Edward Lawrence Logan International Airport"},
                "MIA": {"name": "Miami International Airport"}, "JFK": {"name": "John F Kennedy International Airport"},
                "DTW": {"name": "Detroit Metropolitan Wayne County Airport"},
                "LGA": {"name": "LaGuardia Airport"}, "DCA": {"name": "Washington National Airport"}}}
    stop = {"kind": "advisory", "at": 630, "text": "GROUND STOP BOS", "data": {
        "element": "BOS", "kind": "gs", "gs_period": [625, 700], "flt_incl": "CARRIER JBU ONLY"}}
    watches = [{"t": t, "news": [stop] if t == 660 else [], "changes": [], "projected": []}
               for t in (540, 600, 660, 720, 780, 840, 900)]
    schedule = [flight(1, "B6 10", "BOS", "MIA", 610, 800, 140), flight(2, "B6 20", "BOS", "JFK", 640, 715, 90),
                flight(3, "B6 9", "MIA", "BOS", 420, 600, 150), flight(4, "B6 30", "DTW", "BOS", 640, 760, 100),
                flight(5, "B6 50", "BOS", "LGA", 800, 870), flight(6, "B6 51", "LGA", "BOS", 900, 970),
                flight(7, "B6 60", "BOS", "DCA", 820, 900)]
    executed = [{"id": 1, "status": "flown", "dep": 700, "arr": 900, "decided": None},
                {"id": 2, "status": "cancelled", "dep": None, "arr": None, "decided": 600},
                {"id": 3, "status": "flown", "dep": 440, "arr": 630, "decided": None},
                {"id": 4, "status": "flown", "dep": 670, "arr": 790, "decided": None},
                *({"id": i, "status": "cancelled", "dep": None, "arr": None, "decided": 720} for i in (5, 6, 7))]
    requests = [
        request("R1", "crew", "CANCEL", 2, 600, 606, 612,
                "Accept cancellation: no legal crew after the stop moved its relief, and a rescue costs more."),
        request("R2", "ops", "COVER", 4, 720, 728, 736, "Can cover f4 now12:40 with DTW P0754; saves6,100."),
        request("R3", "crew", "TEXT", None, 720, 731, None, None, status="delivered"),
        request("R4", "crew", "CANCEL", 7, 660, 668, 675, "Do not cancel f7.", status="declined")]
    decisions = [{"t": 600, "shown": 612, "desk": "ops", "action": "cancel", "flight": 2, "detail": None, "ref": "R1"},
                 {"t": 720, "shown": 736, "desk": "crew", "action": "assign", "flight": 4, "detail": "P-E190-0754",
                  "ref": "R2"},
                 *({"t": 720, "shown": 760, "desk": "ops", "action": "cancel", "flight": i, "detail": None,
                    "ref": None} for i in (5, 6, 7))]
    occ = {"mode": "joint", "label": "two desks", "desks": ["crew", "ops"], "requests": requests,
           "decisions": decisions}
    ep = {"task": task, "model": "openai/gpt-6.1-sol", "model_label": "GPT-6.1 Sol", "reward": 0.5, "occ": occ,
          "watches": watches, "news": [stop], "schedule": schedule, "executed": executed}
    waits = leg("B6 10", "dep", "MIA", "Miami", 610, 700, 15, tone="delay",
                crew=[[600, "no crew", None], [700, None, None]])
    traffic = {"hub": "BOS", "span": [540, 960], "weather": [{"t": 534, "vis_sm": 10}], "runways": [], "aircraft": [
        {"key": "N1", "tail": "N1", "carrier": "B6", "jetblue": True, "task": True, "visits": [
            {"stand": "C-11", "from": 630, "to": 700, "arr": leg("B6 9", "arr", "MIA", "Miami", 600, 630, 8),
             "dep": waits}]},
        {"key": "N2", "tail": "N2", "carrier": "B6", "jetblue": True, "task": True, "visits": [
            {"stand": "C-12", "from": None, "to": None, "arr": None,
             "dep": leg("B6 20", "dep", "JFK", "New York", 640, tag="B6 20 → New York · cancelled", tone="cancel",
                        status="cancelled", decided=600)}]},
        {"key": "N4", "tail": "N4", "carrier": "B6", "jetblue": True, "task": True, "visits": [
            {"stand": "C-14", "from": 790, "to": None, "dep": None,
             "arr": leg("B6 30", "arr", "DTW", "Detroit", 760, 790, 5)}]}], "featured": []}
    return ep, traffic


def frames(shot):
    return [t for t, _ in shot.clock(30)]


def at_fraction(shot, t):
    ts = frames(shot)
    return min(range(len(ts)), key=lambda i: abs(ts[i] - t)) / len(ts)


def test_a_control_center_day_cuts_between_the_room_and_the_airport_in_time_order():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=60)
    assert [(s.name, s.camera) for s in shots] == [
        ("establish", "harbour"), ("ask", "room-desks"), ("cancelled", "gate"), ("faa", "tower"), ("wait", "gate"),
        ("push", "push"), ("takeoff", "takeoff"), ("ask", "room-desks"), ("landing", "landing"), ("map", "map"),
        ("end", "harbour")]
    assert 59 <= sum(s.seconds for s in shots) <= 61
    assert all(a.t1 <= b.t0 for a, b in zip(shots[:-1], shots[1:-1], strict=False))
    assert all((a.camera, a.key) != (b.camera, b.key) for a, b in zip(shots, shots[1:], strict=False))
    assert all(max(s.speed) <= record.FAST[s.camera] for s in shots)
    assert shots[0].subline.startswith("Two desks") and "control center" in shots[0].caption
    assert shots[-1].cards == ("end",) and shots[-1].seconds == record.SECONDS["close"]


def test_a_cancel_the_desks_agree_is_asked_and_answered_in_the_room_then_stays_at_its_gate():
    ep, traffic = day()
    ask, gate = record.direct(ep, traffic, seconds=60)[1:3]
    assert (ask.desk, ask.ref, ask.cards, ask.at) == ("ops", "R1", (), 612)
    assert frames(ask)[0] <= 606 and at_fraction(ask, 612) == pytest.approx(0.7, abs=0.02)
    assert ask.caption == "The crew scheduler asks the ops controller to cancel B6 20 to New York"
    assert ask.subline == ("The ops controller accepts: “Accept cancellation: no legal crew after the stop moved its "
                           "relief”")
    assert ask.kicker == "10:12 · The control center"
    assert (gate.key, gate.cards) == ("N2", ("tags",)) and ask.t1 < gate.t0 < ask.t1 + 1
    assert gate.caption == "B6 20 to New York stays at gate 12"
    assert gate.subline == "Cancelled at the crew scheduler's request"
    assert record.tag_at(traffic["aircraft"][1], gate.t1)["flight"] == "B6 20"


def test_a_cover_plays_out_as_the_covered_flight_lands_with_the_crew_found():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=60)
    ask, landing = shots[7], shots[8]
    assert (ask.ref, ask.desk) == ("R2", "crew") and frames(ask)[0] <= 728
    assert ask.caption == "The ops controller asks the crew scheduler to find a crew for B6 30 from Detroit"
    assert ask.subline == "The crew scheduler accepts and puts pilots 0754 on it"
    assert (landing.key, landing.at) == ("N4", 785) and landing.caption == "B6 30 from Detroit lands 30 min late"
    assert landing.subline == "Flown by pilots 0754, the cover the desks agreed"


def test_a_flight_waits_at_its_gate_for_a_crew_then_leaves_with_it():
    ep, traffic = day()
    shots = {s.name: s for s in record.direct(ep, traffic, seconds=60)}
    wait, push = shots["wait"], shots["push"]
    assert wait.key == push.key == "N1" and wait.t1 < push.t0
    assert wait.caption == "B6 10 to Miami waits at gate 11 for a crew"
    assert wait.subline == "140 passengers wait with it"
    assert record.tag_at(traffic["aircraft"][0], wait.t0)["flight"] == "B6 10"
    assert push.caption == "B6 10 to Miami pushes back 1 h 30 min late, its crew aboard"
    assert record.waits(traffic["aircraft"][0]["visits"][0]["dep"]) == [(600, 700)]
    assert record.waits(traffic["aircraft"][0]["visits"][0]["arr"]) == []


def test_the_map_shows_the_network_briefly_without_cards():
    ep, traffic = day()
    m = next(s for s in record.direct(ep, traffic, seconds=60) if s.name == "map")
    assert (m.camera, m.cards, m.at) == ("map", (), 840) and frames(m)[0] < 840 < frames(m)[-1]
    assert m.caption == "1 JetBlue flight in the air"
    assert m.kicker == "14:00 · The network"


def test_without_the_3d_room_its_beats_go_on_the_rooms_card_over_the_map():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=60, room=False)
    asks = [s for s in shots if s.name == "ask"]
    assert [a.ref for a in asks] == ["R1", "R2"]
    assert all(a.camera == "map" and a.cards == ("head", "desk") and a.speed == (4, 4) for a in asks)
    assert all(frames(a)[0] < a.at < frames(a)[-1] for a in asks)
    assert not any(s.camera in record.ROOM for s in shots)
    assert all((a.camera, a.key) != (b.camera, b.key) for a, b in zip(shots, shots[1:], strict=False))


def test_one_agent_at_both_desks_cancels_several_flights_at_once_on_the_wall():
    ep, traffic = day()
    ep["occ"] |= {"mode": "one", "label": "one agent", "desks": ["one"], "requests": []}
    for d in ep["occ"]["decisions"]:
        d.update(desk="one", ref=None)
    shots = record.direct(ep, traffic, seconds=60)
    wall = next(s for s in shots if s.name == "cancels")
    assert (wall.camera, wall.at, wall.desk) == ("room-wall", 760, "one")
    assert wall.caption == "GPT-6.1 Sol cancels 3 flights at once"
    assert wall.subline == "B6 50 to LaGuardia, B6 51 from LaGuardia, B6 60 to Washington National"
    assert shots[0].subline == "One agent at both desks: the flights and the crews"
    assert "taxi" in {s.name for s in shots}


def test_the_desks_words_are_quoted_only_when_they_read_plainly():
    ep, traffic = day()
    c = record.Center(ep, traffic, None, None)
    assert c.quote("Cancel f2 now: no crew can reach it. Keep f1.") == "Cancel B6 20 now: no crew can reach it"
    assert c.quote("Own delay1289m exceeds04:42+1 deadline; cancel now.") == ""
    assert c.quote("Can cover f4 with DTW P0754") == ""
    assert c.unit("RP-A32X-BOS-A0") == "Boston Logan standby reserve pilots" and c.unit("P-A32X-0413") == "pilots 0413"
    assert c.unit("RC-E190-MIA-S2") == "Miami short-call reserve cabin crew"
    assert (record.behind(-8), record.behind(0), record.behind(90)) == ("8 min early", "on time", "1 h 30 min late")


def test_the_end_card_and_title_name_the_control_center_not_v1():
    ep, _ = day()
    card = record.end_card(ep)
    assert card["title"] == "AirOpsEnv" and "control center" in card["sub"] and "two desks" in card["who"]
    assert record.title(ep).startswith("AirOpsEnv · GPT-6.1 Sol")
    v1 = {k: v for k, v in copy.deepcopy(ep).items() if k != "occ"}
    assert record.end_card(v1)["title"] == "AirOpsEnv v1"


@pytest.mark.skipif(not EPISODES, reason="no control-center episodes are built under cache/occ/viewer/episodes")
@pytest.mark.parametrize("path", EPISODES, ids=lambda p: p.stem.split(".")[0] + "." + p.stem.split(".")[-1])
@pytest.mark.parametrize("room", [True, False], ids=["room", "card"])
def test_every_control_center_day_makes_a_minute_long_film(path, room):
    ep = json.loads(path.read_text())
    traffic = json.loads(gzip.decompress((path.parent / ep["stage"]["traffic"]).read_bytes()))
    shots = record.direct(ep, traffic, seconds=60, room=room)
    assert 55 <= sum(s.seconds for s in shots) <= 61
    assert shots[0].name == "establish" and shots[-1].name == "end"
    assert {"faa", "push", "takeoff", "wait"} <= {s.name for s in shots}
    assert all(a.t1 <= b.t0 for a, b in zip(shots[:-1], shots[1:-1], strict=False))
    assert all((a.camera, a.key) != (b.camera, b.key) for a, b in zip(shots, shots[1:], strict=False))
    assert all(max(s.speed) <= record.FAST[s.camera] for s in shots)
    assert all(traffic["span"][0] <= s.t0 and s.t1 <= traffic["span"][1] for s in shots)
    accepted = [r for r in ep["occ"]["requests"] if r["type"] != "TEXT" and r["status"] == "accepted"]
    assert bool(accepted) <= any(s.name == "ask" for s in shots)
    assert all(not any(w in s.caption + s.subline + s.kicker for w in ("v2", "f1", "f2")) for s in shots)
