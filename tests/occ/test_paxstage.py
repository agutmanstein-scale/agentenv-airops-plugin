"""The passengers at the hub in 3D: the display-only gate lounges behind the home terminal's glass, each departure's
crowd there, connecting passengers walking the concourse gate to gate, the line at the service counter and the hotel
shuttle, and the gate tags; and nothing of it on a day without passengers."""

import pytest
from test_crewstage import stage, visit

from agentenv_airops.occ import paxstage as ps

SCHEDULE = [
    {"id": 1, "flight": "B6 101", "orig": "BOS", "dest": "JFK", "sched_dep": 520, "sched_arr": 595, "pax": 150},
    {"id": 2, "flight": "B6 202", "orig": "JFK", "dest": "MCO", "sched_dep": 690, "sched_arr": 860, "pax": 160},
    {"id": 3, "flight": "B6 303", "orig": "JFK", "dest": "LAX", "sched_dep": 715, "sched_arr": 1080, "pax": 180},
    {"id": 4, "flight": "B6 404", "orig": "SFO", "dest": "JFK", "sched_dep": 300, "sched_arr": 690, "pax": 140},
    {"id": 5, "flight": "B6 505", "orig": "JFK", "dest": "BUF", "sched_dep": 880, "sched_arr": 960, "pax": 100},
]


def sidecar() -> dict:
    """B6 101 in at T5-2 at 10:00, out again as B6 202 at 11:40; B6 303 out of T5-7 at 12:00; B6 404 in late at T5-4
    at 11:50, after B6 202 has gone; B6 505 there cancelled at 13:00 for 14:40."""
    late = visit("T5-4", 710, None, arr="B6 404")
    late["dep"] = {"flight": "B6 505", "kind": "dep", "status": "cancelled", "sched": 880, "decided": 780, "out": None}
    return {"span": [240, 1500], "aircraft": [
        {"key": "N1", "task": True, "visits": [visit("T5-2", 600, 700, arr="B6 101", dep="B6 202")]},
        {"key": "N2", "task": True, "visits": [visit("T5-7", 560, 720, dep="B6 303")]},
        {"key": "N3", "task": True, "visits": [late]}]}


def episode() -> dict:
    rows = [{"id": 1, "booked": 150, "flown": 150, "conn_out": 30},
            {"id": 2, "booked": 160, "flown": 152, "conn_in": 8, "misconnected": 8,
             "rebooked": [{"to": 3, "pax": 5}], "stranded": 3},
            {"id": 3, "booked": 180, "added": 5, "flown": 185, "conn_in": 22, "hold": 9},
            {"id": 4, "booked": 140, "flown": 140, "conn_out": 8},
            {"id": 5, "booked": 100, "flown": 0, "rebooked": [{"to": 3, "pax": 0}], "home": 100}]
    events = [{"t": 700, "kind": "misconnect", "at": "JFK", "flight": 2, "from": 4, "pax": 8},
              {"t": 740, "kind": "rebook", "at": "JFK", "flight": 2, "to": 3, "pax": 5},
              {"t": 745, "kind": "hotel", "at": "JFK", "flight": 2, "pax": 3, "until": 1800},
              {"t": 790, "kind": "home", "at": "JFK", "flight": 5, "pax": 100}]
    return {"task": {"hub": "JFK"}, "schedule": SCHEDULE, "occ": {},
            "passengers": {"flights": rows, "connections": [
                {"from": 1, "to": 3, "at": "JFK", "pax": 22, "made": 22},
                {"from": 4, "to": 2, "at": "JFK", "pax": 8, "made": 0}], "events": events}}


def scene() -> dict:
    side = sidecar()
    for a in side["aircraft"]:
        for v in a["visits"]:
            for k in ("arr", "dep"):
                if v[k] and "sched" not in v[k]:
                    v[k]["sched"] = next(s["sched_dep" if k == "dep" else "sched_arr"] for s in SCHEDULE
                                         if s["flight"] == v[k]["flight"])
    return ps.passengers(episode(), side, stage())


def test_each_jet_bridge_gate_gets_a_lounge_behind_the_glass_as_wide_as_its_neighbours_allow():
    doc = scene()
    assert doc["terminal"] == 0 and doc["floor"] == ps.FLOOR
    assert [lg["stand"] for lg in doc["lounges"]] == [f"T5-{i + 1}" for i in range(8)]
    for lg in doc["lounges"]:
        assert all(p[1] == pytest.approx(25) for p in lg["p"])
        assert all(n == pytest.approx([0, 1]) for n in lg["n"])
        width = abs(lg["p"][-1][0] - lg["p"][0][0])
        assert 8 <= width <= 2 * ps.HALF and lg["door"][1] == pytest.approx(26)
    assert doc["lounges"][doc["counter"]]["stand"] in {f"T5-{i}" for i in range(3, 7)}


def test_a_departure_s_crowd_fills_its_lounge_before_it_boards_and_a_cancelled_one_leaves_when_it_is_cancelled():
    crowds = {c["flight"]: c for c in scene()["crowds"]}
    assert crowds["B6 202"]["n"] == 152 and crowds["B6 202"]["t"] == [700 - ps.ARRIVE, 700 - ps.FULL, 700 - ps.BOARD,
                                                                        700 - ps.CLOSED]
    assert crowds["B6 303"]["cancel"] is None and crowds["B6 505"]["cancel"] == 780
    assert ps.share(780, crowds["B6 505"]["t"]) > 0


def test_connecting_passengers_walk_the_concourse_gate_to_gate_and_those_who_missed_theirs_go_to_the_counter():
    doc = scene()
    walks = {g["kind"]: g for g in doc["groups"]}
    made = walks["connect"]
    assert made["n"] == 22 and made["flights"] == ["B6 101", "B6 303"]
    [seg] = made["segs"]
    assert seg["z"] == ps.FLOOR and seg["t"][0] == 600 + ps.DEPLANE
    assert seg["p"][0] == pytest.approx(doc["lounges"][1]["door"]) and seg["p"][-1] == pytest.approx(
        doc["lounges"][6]["door"])
    assert all(25 < y < 90 for _, y in seg["p"])
    missed = walks["misconnect"]
    assert missed["n"] == 8 and missed["segs"][-1]["p"][-1] == pytest.approx(doc["lounges"][doc["counter"]]["door"])
    assert missed["segs"][0]["say"] == "8 missed B6 202 · to the service counter"


def test_the_line_at_the_counter_grows_with_them_and_empties_as_the_desk_rebooks_them_or_books_a_hotel():
    doc = scene()
    line = doc["queue"]
    peak = max(n for _, n in line)
    assert peak >= 8 and line[-1][1] == 0
    kinds = [g["kind"] for g in doc["groups"]]
    assert {"rebook", "hotel", "shuttle"} <= set(kinds)
    hotel = next(g for g in doc["groups"] if g["kind"] == "hotel")
    assert hotel["n"] == 3 and hotel["segs"][-1]["do"] == "wait" and hotel["segs"][-1]["z"] == 0
    shuttle = next(g for g in doc["groups"] if g["kind"] == "shuttle")
    assert shuttle["segs"][0]["do"] == "van" and shuttle["segs"][0]["t"][0] % ps.SHUTTLE == 0
    assert shuttle["segs"][0]["say"] == "Hotel shuttle · 3 passengers stranded overnight"


def test_departures_carry_their_connecting_passengers_hold_and_missed_connections_for_their_gate_tags():
    tags = scene()["tags"]
    assert tags["B6 303"] == {"conn": 22, "held": 9, "missed": 0, "sched": 715}
    assert tags["B6 202"]["missed"] == 8 and "B6 101" not in tags


def test_a_day_without_passengers_has_none_at_the_hub():
    ep = episode()
    del ep["passengers"]
    assert ps.passengers(ep, sidecar(), stage()) is None
