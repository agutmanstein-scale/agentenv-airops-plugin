"""The crews at the hub in 3D: the display-only crew room, walkway, curb and apron-bus points inferred from the home
terminal, and each crew's moves there from its duties: from the crew room or the gate it came in at to its departure's
jet bridge, gate to gate on a short connection and aboard on the same aircraft, reporting by shuttle, off duty or to
the hotel van, late to a flight short of a crew, and the crews moving together as one group."""

import pytest

from agentenv_airops.occ import crewstage as cs

WALK = 3.5


def stage() -> dict:
    """Eight Terminal 5 gates 45 m apart facing north into a terminal along y = 25..90, each with its jet bridge, a
    Terminal 4 gate past it and a remote stand facing south out on the apron, its lead off a taxilane."""
    stands = [{"ref": f"T5-{i + 1}", "terminal": "T5", "xy": [45.0 * i, 0.0], "heading": 0.0} for i in range(8)]
    stands += [{"ref": "T4-1", "terminal": "T4", "xy": [1200.0, 0.0], "heading": 0.0},
               {"ref": "H1", "terminal": "remote", "xy": [100.0, -300.0], "heading": 180.0}]
    bridges = [{"line": [[45.0 * i + 5, 25.0], [45.0 * i + 5, 12.0], [45.0 * i + 3, 6.0]], "stand": f"T5-{i + 1}"}
               for i in range(8)]
    nodes = [[-40, -120], [100, -120], [300, -120], [300, -1000], [100, -300]]
    edges = [[0, 1, 140, "taxilane"], [1, 2, 200, "taxilane"], [2, 3, 880, "runway"], [1, 4, 180, "lead"]]
    return {"hub": "JFK", "stands": stands, "jet_bridges": bridges,
            "taxi_graph": {"nodes": nodes, "edges": edges, "stand_nodes": {"H1": 4}, "thresholds": {}},
            "terminals": [{"name": "Terminal 5", "outer": [[-60, 25], [900, 25], [900, 90], [-60, 90]], "holes": []},
                          {"name": "Terminal 4", "outer": [[1100, 25], [1300, 25], [1300, 60], [1100, 60]]}]}


def visit(stand, frm, to, arr=None, dep=None, crew=None):
    """A sidecar visit with its flown arrival in at `frm` and departure out at `to`."""
    return {"stand": stand, "from": frm, "to": to,
            "arr": arr and {"flight": arr, "kind": "arr", "status": "flown", "in": frm, "out": None},
            "dep": dep and {"flight": dep, "kind": "dep", "status": "flown", "in": None, "out": to, "crew": crew}}


SCHEDULE = [
    {"id": 1, "flight": "B6 101", "orig": "BOS", "dest": "JFK"},
    {"id": 2, "flight": "B6 202", "orig": "JFK", "dest": "MCO"},
    {"id": 3, "flight": "B6 303", "orig": "JFK", "dest": "LAX"},
    {"id": 4, "flight": "B6 404", "orig": "SFO", "dest": "JFK"},
    {"id": 5, "flight": "B6 505", "orig": "JFK", "dest": "BUF"},
    {"id": 6, "flight": "B6 606", "orig": "JFK", "dest": "ORD"},
]


def sidecar(crew=None) -> dict:
    return {"span": [240, 1500], "aircraft": [
        {"key": "N1", "task": True, "visits": [visit("T5-2", 600, 700, arr="B6 101", dep="B6 202", crew=crew)]},
        {"key": "N2", "task": True, "visits": [visit("T5-7", 560, 720, dep="B6 303")]},
        {"key": "N3", "task": True, "visits": [visit("T5-4", 650, 900, arr="B6 404", dep="B6 505")]},
        {"key": "N4", "task": False, "visits": [visit("T4-1", 600, 700, dep="B6 202")]},
        {"key": "N5", "task": True, "visits": [visit("H1", 500, 800, dep="B6 606")]},
    ]}


def unit(kind="pilot", home="JFK", reserve=None, duties=(), hotels=(), size=None):
    return {"kind": kind, "group": "A32X", "home": home, "size": size or (2 if kind == "pilot" else 4),
            "reserve": reserve, "call": None, "hotels": list(hotels), "end": home,
            "duties": [{"report": 0, "at": "BOS", "begins": "on duty", "legs": [], "deadheads": [], "release": None,
                        "limit": 2000, "ext": None} | d for d in duties]}


def episode(units: dict) -> dict:
    return {"task": {"hub": "JFK"}, "schedule": SCHEDULE, "occ": {"units": units}}


def run(units: dict, crew=None) -> list[dict]:
    return cs.crews(episode(units), sidecar(crew), stage())


def test_the_crew_room_and_curb_sit_past_the_end_gate_nearer_the_middle_and_walks_keep_to_the_airside_face():
    site = cs.Site(stage())
    assert site.room.xy == pytest.approx((5 - cs.ROOM_ALONG, 25 - WALK))
    assert site.curb.xy[0] < -60 and 25 < site.curb.xy[1] < 90
    walk = site.route(site.door("T5-2"), site.door("T5-7"))
    assert walk[0] == pytest.approx((50, 25 - WALK)) and walk[-1] == pytest.approx((275, 25 - WALK))
    assert all(p[1] == pytest.approx(25 - WALK) for p in walk)
    out = site.route(site.door("T5-8"), site.curb)
    assert all(not (-60 < x < 900 and 25 < y < 90) for x, y in out)
    assert max(x for x, _ in out) <= 320 and site.lane[0][0] < -60 and site.lane[-1][1] > site.lane[0][1]
    remote = site.door("H1")
    assert remote.bus and remote.xy == pytest.approx(site.stop) and remote.walk == site.room.walk
    assert remote.door == pytest.approx((102.5, -299)) and remote.near == pytest.approx((102.5 + cs.ALIGHT, -302))


def test_a_crew_from_the_crew_room_walks_to_its_gate_and_waits_to_board_and_pilots_and_cabin_go_together():
    legs = [{"report": 560, "at": "JFK", "legs": [[2, 700, 900]], "release": 915}]
    [report, group] = run({"P-1": unit("pilot", duties=legs), "C-1": unit("cabin", duties=legs)})
    assert (report["kind"], report["crew"], [s["do"] for s in report["segs"]]) == ("report", [2, 4], ["van", "walk"])
    assert report["segs"][0]["t"][1] == 560 - cs.REPORT and report["segs"][0]["v"] == "shuttle"
    assert report["segs"][1]["say"] == "Crew of B6 202 · reporting 09:20"
    assert (group["kind"], group["crew"], group["flights"], group["stand"]) == ("out", [2, 4], ["B6 202"], "T5-2")
    walk, wait = group["segs"]
    assert walk["t"][0] == 700 - cs.LEAVE and walk["p"][-1] == pytest.approx([50, 25 - WALK])
    assert wait["t"] == [walk["t"][1], 700 - cs.BOARD] and wait["h"] == 0
    assert (walk["say"], wait["say"]) == ("Crew of B6 202 · to gate 2", "Crew of B6 202 · at gate 2")


def test_an_arriving_crew_walks_gate_to_gate_on_a_short_connection_and_stays_aboard_on_the_same_aircraft():
    [hop] = run({"P-1": unit(duties=[{"legs": [[4, 450, 650], [3, 720, 900]], "release": 915}])})
    assert (hop["kind"], hop["flights"], hop["stand"]) == ("out", ["B6 303", "B6 404"], "T5-7")
    assert hop["segs"][0]["t"][0] == 650 + cs.DEPLANE
    assert hop["segs"][0]["p"][0] == pytest.approx([140, 25 - WALK]) and hop["segs"][-1]["t"][1] == 720 - cs.BOARD
    assert run({"P-1": unit(duties=[{"legs": [[1, 450, 600], [2, 660, 900]], "release": 915}])}) == []


def test_reserves_report_by_the_shuttle_and_based_crews_go_off_duty_by_the_crew_room():
    reserve = unit("cabin", reserve="short", duties=[{"report": 900, "at": "JFK", "begins": "call", "legs": [],
                                                     "release": 1200}])
    based = unit(duties=[{"legs": [[4, 450, 650]], "release": 665}])
    [off, report] = run({"RC-1": reserve, "P-1": based})
    assert report["segs"][1]["say"] == "Reserve cabin crew · reporting 15:00"
    assert (off["kind"], off["segs"][0]["say"]) == ("off", "Crew of B6 404 · off duty")
    assert off["segs"][0]["p"][-1] == pytest.approx(list(cs.Site(stage()).room.xy), abs=0.1)


def test_a_crew_released_away_from_base_takes_the_hotel_van_and_one_at_its_limit_has_timed_out():
    rest = unit(home="BOS", duties=[{"legs": [[4, 450, 650]], "release": 665, "limit": 1000}],
                hotels=[{"station": "JFK", "from": 665, "to": 1300}])
    out = unit(home="BOS", duties=[{"legs": [[1, 450, 600]], "release": 615, "limit": 640}], size=3)
    groups = run({"P-1": rest, "P-2": out})
    assert [(g["kind"], g["flights"]) for g in groups] == [("hotel", ["B6 101"]), ("hotel", ["B6 404"])]
    [walk, van] = groups[1]["segs"]
    assert (van["do"], van["v"], van["dir"], van["t"][0]) == ("van", "hotel", "out", walk["t"][1] + 1)
    assert walk["t"][0] == 650 + cs.DEPLANE and walk["p"][-1] == pytest.approx(list(cs.Site(stage()).curb.xy), abs=0.1)
    assert [g["segs"][0]["say"] for g in groups] == ["Crew to the hotel · timed out",
                                                     "Crew to the hotel · rest until 21:40"]


def test_a_van_finding_the_curb_taken_stops_a_bay_up_the_road():
    site, curb = cs.Site(stage()), []
    first, second = (cs.bay(site.van("in", t, "shuttle"), curb) for t in (600, 601))
    assert first["p"][-1] == pytest.approx(list(site.lane[0]), abs=0.1)
    assert cs.dist(second["p"][-1], first["p"][-1]) == pytest.approx(cs.BAY, abs=0.2)
    assert cs.bay(site.van("in", 700, "shuttle"), curb)["p"][-1] == first["p"][-1]


def test_a_deadheading_crew_boards_with_the_passengers_and_a_flight_short_of_crew_gets_its_crew_late():
    [dh] = run({"P-1": unit(duties=[{"report": 400, "at": "JFK", "legs": [], "deadheads": [[5, 900, 990]],
                                    "release": None}])})[1:]
    assert dh["segs"][-1]["t"][1] == 900 - cs.DEADHEAD
    assert dh["segs"][0]["say"] == "Deadheading crew · boarding B6 505 with the passengers"
    duty = [{"report": 400, "at": "JFK", "legs": [[2, 700, 900]], "release": 915}]
    [late] = run({"P-1": unit(duties=duty)}, crew=[[620, "no crew", None], [700, None, None]])[1:]
    assert late["segs"][-1]["t"][1] == pytest.approx(700 - cs.LATE)


def test_no_crews_without_the_room_or_a_home_terminal():
    assert cs.crews({"task": {"hub": "JFK"}, "schedule": []}, sidecar(), stage()) == []
    assert cs.crews(episode({"P-1": unit()}), sidecar(), stage() | {"hub": "LGA"}) == []


def test_a_crew_for_a_remote_stand_rides_the_apron_bus_along_the_taxilanes_and_round_the_wingtip_to_its_door():
    [_, group] = run({"P-1": unit(duties=[{"report": 600, "at": "JFK", "legs": [[6, 800, 900]], "release": 915}])})
    walk, bus, alight, wait = group["segs"]
    assert (bus["do"], bus["v"], bus["dir"]) == ("van", "bus", "via") and bus["t"][0] == walk["t"][1] + 1
    wing, side = 100 + 36 / 2 + cs.CLEAR, 102.5 + cs.ALIGHT
    assert bus["p"] == [walk["p"][-1], [-40, -120], [100, -120], [wing, -256], [wing, -314], [side, -312],
                        alight["p"][0]]
    assert alight["p"][-1] == pytest.approx([102.5, -299]) and wait["t"][1] == 800 - cs.BOARD
    assert [x["say"] for x in (walk, bus, wait)] == ["Crew of B6 606 · to the bus to stand H1",
                                                   "Crew of B6 606 · on the bus to stand H1",
                                                   "Crew of B6 606 · at stand H1"]
