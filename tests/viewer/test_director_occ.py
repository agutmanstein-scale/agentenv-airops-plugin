import copy
import gzip
import importlib.util
import itertools
import json
import re
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
    stays at its gate; B6 10 to Miami waits at its gate for a crew, whose pilots walk up at 11:32, until it pushes
    back at 11:40; a ground stop
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
             "arr": leg("B6 30", "arr", "DTW", "Detroit", 760, 790, 5)}]}], "featured": [],
        "crews": [
            {"key": "out-B610-688", "kind": "out", "crew": [2, 0], "flights": ["B6 10"], "stand": "C-11", "segs": [
                {"do": "walk", "t": [688, 692], "p": [[100, 0], [60, 0], [20, 10]], "say": "to gate 11"}]},
            {"key": "in-B69-640", "kind": "in", "crew": [2, 4], "flights": ["B6 9"], "stand": "C-11", "segs": [
                {"do": "walk", "t": [640, 642], "p": [[20, 10], [100, 0]], "say": "to the crew room"}]}]}
    return ep, traffic


def frames(shot):
    return [t for t, _ in shot.clock(30)]


def at_fraction(shot, t):
    ts = frames(shot)
    return min(range(len(ts)), key=lambda i: abs(ts[i] - t)) / len(ts)


def test_a_control_center_day_cuts_between_the_room_and_the_airport_in_time_order():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=70)
    assert [(s.name, s.camera) for s in shots] == [
        ("open", "room"), ("establish", "harbour"), ("ask", "room-desks"), ("cancelled", "gate"), ("faa", "tower"),
        ("crew", "crew"), ("push", "push"), ("takeoff", "takeoff"), ("map", "map"), ("ask", "room-desks"),
        ("landing", "landing"), ("end", "harbour")]
    assert 69 <= sum(s.seconds for s in shots) <= 71
    assert all(a.t1 <= b.t0 for a, b in zip(shots[:-1], shots[1:-1], strict=False))
    assert all((a.camera, a.key) != (b.camera, b.key) for a, b in zip(shots, shots[1:], strict=False))
    assert all(max(s.speed) <= record.FAST[s.camera] for s in shots)
    assert shots[0].kicker == "AirOpsEnv v2 · the ops control center" and "control center" in shots[0].caption
    assert shots[0].subline == ("Boston Logan · Sun 16 Feb 2025 · 09:02 · Two desks, an ops controller for the flights "
                                "and a crew scheduler for the crews")
    assert (shots[1].caption, shots[1].cards) == ("First light over Boston Logan", ())
    assert shots[-1].cards == ("end",) and shots[-1].seconds >= record.SECONDS["close"]


def test_a_cancel_the_desks_agree_is_asked_and_answered_in_the_room_then_stays_at_its_gate():
    ep, traffic = day()
    ask, gate = record.direct(ep, traffic, seconds=70)[2:4]
    assert (ask.desk, ask.ref, ask.cards, ask.at) == ("crew", "R1", (), 612)
    assert frames(ask)[0] <= 606 and frames(ask)[-1] >= 612 + record.REPLY - 0.1
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
    shots = record.direct(ep, traffic, seconds=70)
    k = next(i for i, s in enumerate(shots) if s.ref == "R2")
    ask, landing = shots[k], shots[k + 1]
    assert (ask.ref, ask.desk) == ("R2", "ops") and frames(ask)[0] <= 728 and ask.t1 >= 736 + record.REPLY - 0.1
    assert ask.caption == "The ops controller asks the crew scheduler to find a crew for B6 30 from Detroit"
    assert ask.subline == "The crew scheduler accepts and puts pilots 0754 on it"
    assert (landing.key, landing.at) == ("N4", 785) and landing.caption == "B6 30 from Detroit lands 30 min late"
    assert landing.subline == "Flown by pilots 0754, the cover the desks agreed"


def test_the_crew_a_flight_waits_for_walks_up_to_its_gate_then_it_leaves():
    ep, traffic = day()
    shots = {s.name: s for s in record.direct(ep, traffic, seconds=70)}
    crew, push = shots["crew"], shots["push"]
    assert (crew.key, crew.ref, crew.path) == ("N1", "out-B610-688", ((60, 0), (20, 10)))
    assert (crew.speed, crew.cards) == ((6, 6), ("tags",))
    assert frames(crew)[-1] < 692 < push.t0 and crew.t0 > 680
    assert crew.caption == "The pilots of B6 10 to Miami walk up to gate 11"
    assert crew.subline == "140 passengers have waited 1 h 32 min"
    assert push.caption == "B6 10 to Miami pushes back 1 h 30 min late, its crew aboard"


def test_without_crews_a_flight_waits_at_its_gate_for_one_then_leaves():
    ep, traffic = day()
    del traffic["crews"]
    shots = {s.name: s for s in record.direct(ep, traffic, seconds=70)}
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
    m = next(s for s in record.direct(ep, traffic, seconds=70) if s.name == "map")
    assert (m.camera, m.cards, m.at) == ("map", (), 720) and frames(m)[0] < 720 < frames(m)[-1]
    assert m.caption == "2 JetBlue flights in the air, 1 of them bound for Boston Logan"
    assert m.kicker == "12:00 · The network"


def test_without_the_3d_room_its_beats_go_on_the_rooms_card_over_the_map():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=70, room=False)
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
    shots = record.direct(ep, traffic, seconds=70)
    wall = next(s for s in shots if s.name == "cancels")
    assert (wall.camera, wall.at, wall.desk) == ("room-wall", 760, "one")
    assert wall.caption == "GPT-6.1 Sol cancels 3 flights at once"
    assert wall.subline == "B6 50 to LaGuardia, B6 51 from LaGuardia, B6 60 to Washington National"
    assert shots[0].subline.endswith("One agent at both desks, the flights and the crews")
    assert "taxi" in {s.name for s in shots}


def test_the_customer_desk_asks_for_a_hold_for_connecting_passengers_and_rebooks_a_cancelled_flight():
    ep, traffic = day()
    occ = ep["occ"]
    occ |= {"label": "three desks", "desks": ["crew", "ops", "customer"]}
    occ["requests"] = [r for r in occ["requests"] if r["id"] != "R1"] + [
        request("R5", "customer", "HOLD", 1, 600, 604, 611, "Hold accepted.") | {"receiver": "ops", "pax": 12}]
    occ["decisions"].append({"t": 600, "shown": 650, "desk": "customer", "action": "rebook", "flight": 2, "to": 5,
                             "pax": 40, "detail": None, "ref": None})
    ep["passengers"] = {"flights": [], "connections": [], "events": []}
    shots = record.direct(ep, traffic, seconds=70)
    ask = next(s for s in shots if s.ref == "R5")
    assert (ask.camera, ask.desk) == ("room-desks", "customer")
    assert ask.caption == ("The customer desk asks the ops controller to hold B6 10 to Miami for 12 connecting "
                           "passengers")
    moved = next(s for s in shots if s.desk == "customer" and s.name == "move")
    assert moved.camera == "room"
    assert moved.caption == "The customer desk rebooks 40 passengers off B6 20 to New York"
    assert moved.subline == "onto B6 50"
    assert shots[0].subline.endswith("Three desks, an ops controller for the flights, a crew scheduler for the crews "
                                     "and a customer desk for the passengers")
    assert shots[0].kicker == "AirOpsEnv v3 · the ops control center"
    assert record.end_card(ep)["title"] == "AirOpsEnv v3" and "its passengers" in record.end_card(ep)["sub"]
    assert record.title(ep).startswith("AirOpsEnv v3 · ")


def test_the_line_at_the_service_counter_is_filmed_through_its_lounge_s_glass_at_its_longest():
    ep, traffic = day()
    ep["passengers"] = {"flights": [], "connections": [], "events": []}
    traffic["passengers"] = {"lounges": [{"stand": "C-12", "door": [10.0, 26.0], "n": [[0.0, 1.0], [0.0, 1.0]]}],
                             "counter": 0, "queue": [[560, 4], [610, 30], [640, 12], [700, 0]], "groups": []}
    [[shot]] = record.Center(ep, traffic, None, None).counter()
    assert (shot.name, shot.camera, shot.at) == ("counter", "lounge", 611)
    assert shot.caption == "30 passengers wait at the service counter to be rebooked"
    assert shot.subline == "After B6 20 to New York was cancelled" and shot.path == ((10.0, 26.0), (10.0, 25.0))


def test_the_customer_desk_s_protect_and_suggested_cancel_cut_to_the_room_and_a_declined_one_says_so():
    ep, traffic = day()
    occ = ep["occ"]
    occ |= {"label": "three desks", "desks": ["crew", "ops", "customer"]}
    occ["requests"] = [r for r in occ["requests"] if r["id"] != "R1"] + [
        request("R5", "customer", "SUGGEST_CANCEL", 2, 540, 552, 606, "Cancelling B6 20.") | {"receiver": "ops",
                                                                                                "pax": 90}]
    occ["decisions"][0]["ref"] = "R5"
    ep["passengers"] = {"flights": [], "connections": [], "events": []}
    shots = record.direct(ep, traffic, seconds=70)
    asked = next(s for s in shots if s.ref == "R5")
    assert asked.caption == ("The customer desk asks the ops controller to cancel early B6 20 to New York, 90 "
                             "passengers booked")
    gate = shots[shots.index(asked) + 1]
    assert (gate.name, gate.subline) == ("cancelled", "Cancelled as the customer desk suggested")
    occ["requests"].append(request("R6", "customer", "PROTECT", 1, 660, 664, 671, "B6 10 stays.", status="declined")
                           | {"receiver": "ops", "pax": 140})
    [room, push] = record.Center(ep, traffic, None, None).asks(("PROTECT",))[0]
    assert room.subline == "The ops controller declines: “B6 10 stays”" and push.name == "pushback"


def test_passengers_who_missed_their_connection_and_the_stranded_s_shuttle_are_filmed_at_the_airport():
    ep, traffic = day()
    ep["passengers"] = {"flights": [], "connections": [], "events": [
        {"t": 700, "kind": "hotel", "at": "BOS", "flight": 1, "pax": 12, "until": 1860}]}
    walk = {"do": "walk", "t": [640, 646], "p": [[0, 30], [40, 30], [10.0, 26.0]], "z": 5.0, "say": "x"}
    hotel = {"do": "wait", "t": [700, 719.5], "at": [-80, 40], "z": 0.0, "say": "y"}
    van = {"do": "van", "t": [720, 721], "p": [[-80, 40], [-80, 60], [-80, 120]], "v": "shuttle", "dir": "out",
           "park": 3.0, "z": 0.0, "say": "Hotel shuttle · 12 passengers stranded overnight"}
    traffic["passengers"] = {"lounges": [{"stand": "C-12", "door": [10.0, 26.0], "n": [[0.0, 1.0], [0.0, 1.0]]}],
                             "counter": 0, "queue": [], "groups": [
                                 {"key": "m", "kind": "misconnect", "n": 7, "flights": ["B6 9", "B6 10"],
                                  "segs": [walk]},
                                 {"key": "h", "kind": "hotel", "n": 12, "flights": ["B6 10"], "segs": [hotel]},
                                 {"key": "s", "kind": "shuttle", "n": 0, "flights": [], "segs": [van]}]}
    c = record.Center(ep, traffic, None, None)
    [[missed]] = c.missed()
    assert (missed.camera, missed.at) == ("lounge", 646)
    assert missed.caption == "7 passengers who missed B6 10 to Miami reach the service counter"
    [[bus]] = c.shuttle()
    assert (bus.camera, bus.at, bus.path) == ("curb", 720, ((-80, 40), (-80, 60)))
    assert bus.caption == "12 passengers stranded overnight leave for the hotel"
    assert bus.subline == "They fly on tomorrow from 07:00"


def test_the_desks_words_are_quoted_only_when_they_read_plainly():
    ep, traffic = day()
    c = record.Center(ep, traffic, None, None)
    assert c.quote("Cancel f2 now: no crew can reach it. Keep f1.") == "Cancel B6 20 now: no crew can reach it"
    assert c.quote("Own delay1289m exceeds04:42+1 deadline; cancel now.") == ""
    assert c.quote("Can cover f4 with DTW P0754") == ""
    assert c.quote("Checked recovery saves 116,123 pax-min against 101,000 for a cancellation of the flight and its "
                   "partner") == ""
    assert c.unit("RP-A32X-BOS-A0") == "Boston Logan standby reserve pilots" and c.unit("P-A32X-0413") == "pilots 0413"
    assert c.unit("RC-E190-MIA-S2") == "Miami short-call reserve cabin crew"
    assert (record.behind(-8), record.behind(0), record.behind(90)) == ("8 min early", "on time", "1 h 30 min late")


def test_the_end_card_and_title_name_the_control_center_not_v1():
    ep, _ = day()
    card = record.end_card(ep)
    assert card["title"] == "AirOpsEnv v2" and "control center" in card["sub"] and "two desks" in card["who"]
    assert record.title(ep).startswith("AirOpsEnv v2 · GPT-6.1 Sol")
    v1 = {k: v for k, v in copy.deepcopy(ep).items() if k != "occ"}
    assert record.end_card(v1)["title"] == "AirOpsEnv v1"


@pytest.mark.skipif(not EPISODES, reason="no control-center episodes are built under cache/occ/viewer/episodes")
@pytest.mark.parametrize("path", EPISODES, ids=lambda p: p.stem.split(".")[0] + "." + p.stem.split(".")[-1])
@pytest.mark.parametrize("room", [True, False], ids=["room", "card"])
def test_every_control_center_day_makes_a_film_of_about_seventy_seconds(path, room):
    ep = json.loads(path.read_text())
    traffic = json.loads(gzip.decompress((path.parent / ep["stage"]["traffic"]).read_bytes()))
    shots = record.direct(ep, traffic, seconds=70, room=room, light=330)
    assert all(s.t0 >= 330 for s in shots[:-1] if s.camera not in (*record.ROOM, "map"))
    assert 60 <= sum(s.seconds for s in shots) <= 71
    assert shots[0].name == ("open" if room else "establish") and shots[-1].name == "end"
    assert {"faa", "push", "takeoff"} <= {s.name for s in shots} and {"wait", "crew"} & {s.name for s in shots}
    assert all(a.t1 <= b.t0 for a, b in zip(shots[:-1], shots[1:-1], strict=False))
    assert all((a.camera, a.key) != (b.camera, b.key) for a, b in zip(shots, shots[1:], strict=False))
    assert all(max(s.speed) <= record.FAST[s.camera] for s in shots)
    assert all(traffic["span"][0] <= s.t0 and s.t1 <= traffic["span"][1] for s in shots)
    accepted = [r for r in ep["occ"]["requests"] if r["type"] != "TEXT" and r["status"] == "accepted"]
    assert bool(accepted and room) <= any(s.name == "ask" for s in shots)
    assert not any(re.search(r"\bf\d+\b", s.caption + s.subline + s.kicker) for s in shots)


def test_the_airport_waits_for_first_light_while_the_room_opens_the_day():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=70, light=650)
    assert shots[0].camera == "room" and shots[0].t0 < 600
    airport = [s for s in shots[:-1] if s.camera not in (*record.ROOM, "map")]
    assert airport[0].name == "establish" and all(s.t0 >= 650 for s in airport)
    assert "cancelled" not in {s.name for s in shots} and any(s.ref == "R1" for s in shots)


def test_a_held_flight_pushes_back_at_its_hold_and_a_crew_leaves_for_the_hotel_by_van():
    ep, traffic = day()
    ep["occ"]["requests"].append(request("R9", "crew", "HOLD", 1, 640, 646, 652, "Hold f1 to 11:40 for its crew."))
    ep["occ"]["requests"][-1]["time"] = 700
    ep["occ"]["units"] = {"P-A32X-0007": {"duties": [{"legs": [[3, 440, 630]]}], "hotels": [
        {"station": "BOS", "from": 650, "to": 1300}]}}
    traffic["crews"].append({"key": "hotel-B69-640", "kind": "hotel", "crew": [2, 4], "flights": ["B6 9"],
                             "stand": "C-11", "segs": [{"do": "walk", "t": [640, 643], "p": [[20, 10], [5, 5]]},
                                                       {"do": "van", "t": [644, 645], "p": [[5, 5], [0, 90]],
                                                        "v": "hotel", "dir": "out"}]})
    c = record.Center(ep, traffic, None, None)
    room, push = next(b for b in c.asks(("HOLD",)) if b[0].ref == "R9")
    assert push.caption == "B6 10 to Miami, held until 11:40, pushes back" and push.t0 > room.t1
    (van,), = c.van()
    assert (van.camera, van.ref, van.speed) == ("van", "hotel-B69-640", (10, 10)) and van.t0 < 644 < van.t1
    assert van.caption == "The crew of B6 9 from Miami leave for the hotel"
    assert van.subline == "6 crew members, resting until 21:40"


def test_the_end_card_board_is_each_mode_s_mean_over_the_board_days(tmp_path):
    ep, _ = day()
    rows = []
    for k, (mode, run, reward, std) in enumerate([("two desks", "occ-board-sol-joint", 0.6, 0.3),
                                                  ("two desks", "occ-board-sol-joint", 0.8, 0.4),
                                                  ("one agent at both desks", "occ-board-sol-one", 0.5, 0.3),
                                                  ("two desks", "occ-pilot-sol-joint", 0.1, 0.1)]):
        url = f"{run}.{k}.json"
        (tmp_path / url).write_text(json.dumps({"occ": {"refs": [{"label": "Standard crew desk", "reward": std}]}}))
        rows.append({"url": url, "task_id": f"day{k % 2}", "run": run, "occ": mode, "reward": reward})
    (tmp_path / "index.json").write_text(json.dumps({"episodes": rows}))
    b = record.board(tmp_path / "index.json", ep)
    assert b["title"] == "GPT-6.1 Sol on the 2 eval days"
    assert b["rows"] == [["Two desks", pytest.approx(0.7), True], ["One agent at both desks", 0.5, False],
                         ["Best plan found", 1.0, False], ["Standard crew desk", pytest.approx(0.35), False]]
    assert record.board(tmp_path / "missing.json", ep) is None


def test_the_webp_of_a_control_center_film_is_a_request_where_it_plays_out_a_crew_and_the_takeoff():
    ep, traffic = day()
    shots = record.direct(ep, traffic, seconds=70)
    start = dict(zip((id(s) for s in shots), itertools.accumulate((s.seconds for s in shots), initial=0),
                     strict=False))
    parts = record.highlight(shots)
    ask, gate, crew, up = (next(s for s in shots if s.name == n) for n in ("ask", "cancelled", "crew", "takeoff"))
    assert parts == [(start[id(ask)], ask.seconds), (start[id(gate)], gate.seconds), (start[id(crew)], crew.seconds),
                     (start[id(up)] + 2, 4.5)]
    assert 15 <= sum(d for _, d in parts) <= 21


def test_a_program_the_desks_hear_of_before_it_starts_is_announced_from_its_start():
    ep, traffic = day()
    gdp = {"kind": "advisory", "at": 575, "text": "GDP BOS", "data": {
        "element": "BOS", "kind": "gdp", "program_period": [720, 899], "program_rate": [30, 34]}}
    ep["news"].append(gdp)
    c = record.Center(ep, traffic, None, None)
    (faa,), = [b for b in c.faa(540) if b[0].at == 600]
    assert faa.kicker == "10:00 · Ground delay program"
    assert faa.caption == "From 12:00, the FAA holds arrivals at Boston Logan to 30–34 an hour until 15:00"
    ep["news"].append({"kind": "advisory", "at": 590, "text": "GDP CNX", "data": {"element": "BOS", "kind": "gdp_cnx"}})
    assert not [b for b in record.Center(ep, traffic, None, None).faa(540) if b[0].at == 600]
