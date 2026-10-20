"""The 3D viewer's traffic: BTS movements read from the fixture month, METARs decoded, every visit parked with no two
aircraft sharing a stand or touching wings, cancelled legs kept on an aircraft at the gate, and the flow in use
following the wind."""

import math
from pathlib import Path

import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from agentenv_airops import stage
from agentenv_airops.core.model import Task

INPUTS = Path(__file__).resolve().parents[1] / "pack" / "fixtures" / "inputs"
METAR = "KJFK 091951Z 11017G27KT 7SM -RA FEW013 FEW028 OVC050 06/04 A3000 RMK AO2 PK WND 10028/1919 SLP160"


def synthetic_stage() -> dict:
    """Eight Terminal 5 gates 45 m apart below a terminal, four Terminal 4 gates 60 m apart and six remote stands
    facing south, and JFK's four runways."""
    stands = [{"ref": f"T5-{i + 1}", "terminal": "T5", "xy": [45.0 * i, 0.0], "heading": 0.0} for i in range(8)]
    stands += [{"ref": f"T4-{i + 1}", "terminal": "T4", "xy": [600.0 + 60 * i, 0.0], "heading": 0.0} for i in range(4)]
    stands += [{"ref": f"H{i + 1}", "terminal": "remote", "xy": [80.0 * i, -400.0], "heading": 180.0} for i in range(6)]
    ends = {"4L": 31.0, "4R": 31.0, "13L": 121.0, "13R": 121.0}
    runways = []
    for i, (a, b) in enumerate((("4L", "22R"), ("4R", "22L"), ("13L", "31R"), ("13R", "31L"))):
        h = math.radians(ends[a])
        x0, y0 = 2000.0 + 300 * i, -2000.0
        runways.append({"ref": f"{a}/{b}", "ends": [
            {"id": a, "xy": [x0, y0], "heading": ends[a]},
            {"id": b, "xy": [x0 + 3000 * math.sin(h), y0 + 3000 * math.cos(h)], "heading": (ends[a] + 180) % 360}]})
    return {"hub": "JFK", "stands": stands, "runways": runways, "hangars": [],
            "terminals": [{"name": "Terminal 5", "outer": [[-60, 25], [900, 25], [900, 90], [-60, 90]], "holes": []}],
            "taxi_graph": {"stand_nodes": {s["ref"]: i for i, s in enumerate(stands)}, "thresholds": {}}}


@pytest.mark.parametrize(("text", "fields", "words"), [
    ("KBOS 162154Z 33008KT 8SM -RA BKN007 OVC013 02/02 A2912 RMK AO2 SLP860 P0007 T00220017 $",
     {"wind_dir": 330, "wind_kt": 8, "gust_kt": None, "vis_sm": 8.0, "wx": ["-RA"],
      "clouds": [["BKN", 700], ["OVC", 1300]], "ceiling_ft": 700, "temp_c": 2, "dew_c": 2},
     "Light rain · visibility 8 mi · cloud at 700 ft · wind NNW 9 mph"),
    ("KBOS 161954Z 03020G26KT 1 1/2SM R04R/5500VP6000FT RA BR OVC004 02/01 A2926 RMK AO2 PK WND 03028/1942",
     {"wind_dir": 30, "wind_kt": 20, "gust_kt": 26, "vis_sm": 1.5, "wx": ["RA", "BR"], "ceiling_ft": 400},
     "Rain and mist · visibility 1 1/2 mi · cloud at 400 ft · wind NNE 23 mph gusting 30"),
    ("KJFK 142051Z VRB05KT 2SM +TSRA VCSH BKN015CB OVC035 24/22 A2990",
     {"wind_dir": None, "wind_kt": 5, "wx": ["+TSRA", "VCSH"], "clouds": [["BKN", 1500], ["OVC", 3500]]},
     "Thunderstorm with heavy rain and showers nearby · visibility 2 mi · cloud at 1,500 ft · wind variable 6 mph"),
    ("SPECI KBOS 070512Z AUTO 00000KT M1/4SM FZFG VV002 M02/M03 A3001",
     {"wind_dir": 0, "wind_kt": 0, "vis_sm": 0.25, "wx": ["FZFG"], "ceiling_ft": 200, "temp_c": -2, "dew_c": -3},
     "Freezing fog · visibility under 1/4 mi · cloud at 200 ft · calm"),
    ("KJFK 041751Z 22012KT 10SM FEW045 SCT250 29/18 A2998",
     {"wx": [], "ceiling_ft": None, "clouds": [["FEW", 4500], ["SCT", 25000]]},
     "Partly cloudy · visibility 10 mi · wind SW 14 mph"),
    ("KBOS 071254Z 05018G28KT 1/2SM +SN BLSN BKN006 OVC012 M01/M02 A2981",
     {"vis_sm": 0.5, "wx": ["+SN", "BLSN"], "ceiling_ft": 600, "temp_c": -1},
     "Heavy snow and blowing snow · visibility 1/2 mi · cloud at 600 ft · wind NE 21 mph gusting 32"),
])
def test_a_metar_decodes_to_numbers_and_a_plain_line(text, fields, words):
    w = stage.metar(615, text)
    assert {k: w[k] for k in fields} == fields
    assert (w["t"], w["metar"], w["plain"]) == (615, text, words)


def test_a_bts_city_keeps_a_second_name_only_when_it_adds_one():
    assert [stage.town(c) for c in ("West Palm Beach/Palm Beach, FL", "Dallas/Fort Worth, TX", "New York, NY")] == [
        "West Palm Beach", "Dallas/Fort Worth", "New York"]


def fixture_task(**overrides) -> Task:
    """2023-06-26 at JFK from 16:00 with four of the fixture's eight JetBlue tails; the other four fly outside it."""
    flight = {"id": 0, "flight": "B6 2602", "orig": "JFK", "dest": "BUF", "sched_dep": 1900, "sched_arr": 1998,
              "block": 98, "pax": 120, "family": "E190", "lb": 0, "dmax": 360,
              "actual": {"cancelled": 1, "cancel_code": "C", "dep": None, "arr": None, "end": None, "tail": "N337JB"}}
    tails = [{"tail": t, "family": "E190", "type": "E190-100", "seats": 100, "pos": "JFK", "ready": 960,
              "end_req": "JFK", "actual_end": "JFK"} for t in ("N249JB", "N337JB", "N229JB", "N306JB")]
    doc = {"task_id": "b6-jfk-20230626-1700", "split": "eval", "carrier": "B6", "hub": "JFK", "date": "2023-06-26",
           "tz": "America/New_York", "window": [1020, 1320], "start": 960, "turn": {"E190": 37}, "turn_all": 45,
           "dmax": 360, "airports": {"JFK": {"name": "JFK", "lat": 40.6399, "lon": -73.7787,
                                             "tz": "America/New_York"}},
           "tails": tails, "flights": [flight], "caps": {}, "nominal": {},
           "news": [{"id": "m1", "at": 951, "kind": "metar", "speaker": "JFK METAR", "text": METAR, "data": {}}],
           "frozen": [], "reference": {}, "sources": {}} | overrides
    return Task.from_dict(doc)


def test_traffic_reads_every_carrier_at_the_hub_from_bts_on_its_real_times():
    doc = stage.traffic(fixture_task(), INPUTS)
    aircraft = {a["key"]: a for a in doc["aircraft"]}
    assert not {"N249JB", "N337JB", "N229JB", "N306JB"} & set(aircraft)
    assert {k for k, a in aircraft.items() if a["jetblue"]} == {"N729JB", "N763JB", "N976JT"}
    assert all(not a["task"] and a["carrier"] == ("B6" if a["jetblue"] else a["carrier"]) for a in aircraft.values())
    assert doc["horizon"] == [960, 1900 + 360 + stage.HORIZON]
    n729 = aircraft["N729JB"]
    assert (n729["class"], n729["type"], n729["terminal"]) == ("a320", "A320-232", "T5")
    first, overnight = n729["visits"]
    assert (first["from"], first["to"], first["arr"]["flight"], first["dep"]["flight"]) == (
        962, 1041, "B6 86", "B6 2093")
    assert (first["arr"]["sched"], first["arr"]["on"], first["dep"]["off"]) == (894, 962 - 7, 1041 + 15)
    assert (overnight["from"], overnight["arr"]["sched"], overnight["to"], overnight["dep"]) == (1499, 1453, None, None)
    assert aircraft["N976JT"]["visits"] == [{"from": 1166, "to": None, "arr": aircraft["N976JT"]["visits"][0]["arr"],
                                             "dep": None}]
    late = aircraft["N172DN"]["visits"][0]["arr"]
    assert (late["sched"], late["in"], late["other"], late["tag"], late["tone"]) == (1379, 1511, "LAX", None, None)
    assert aircraft["N101NN"]["visits"][0]["arr"]["in"] == 1454 + 87
    assert aircraft["N308RD"]["visits"][0]["arr"]["in"] == 1413 + 895
    assert "N109NN" not in aircraft and aircraft["N111ZM"]["terminal"] == "T8"
    assert doc["taxi"] == {"dep": {"day": 15.0}, "arr": {"day": 7.0}}
    assert [w["t"] for w in doc["weather"]] == [951] and doc["weather"][0]["wind_dir"] == 110
    assert [f["id"] for f in doc["flights"]] == [0] and doc["stops"] == []


def visit(lo, hi) -> dict:
    return {"from": lo, "to": hi, "arr": None, "dep": None}


def test_stands_are_never_shared_and_parked_outlines_keep_clear():
    st = synthetic_stage()
    sizes = [("widebody", 63.7, 60.3), ("a321", 44.5, 35.8), ("regional", 31.7, 26.0), ("narrowbody", 39.5, 35.8)]
    aircraft = []
    for i in range(40):
        cls, length, span = sizes[i % 4]
        lo = (i * 37) % 300
        aircraft.append({"key": f"K{i:02d}", "task": i < 6, "jetblue": i < 10, "class": cls, "length": length,
                         "span": span, "terminal": "T5" if i < 10 else "T4",
                         "visits": [visit(None if i % 7 == 0 else lo, lo + 90 + (i % 5) * 40)]})
    unplaced = stage.assign(aircraft, st, [0, 600])
    assert unplaced and set(unplaced) == {a["key"] for a in aircraft if not a["visits"][0]["stand"]}
    xy = {s["ref"]: s for s in st["stands"]}
    terminal = Polygon(st["terminals"][0]["outer"])
    parked = []
    for a in aircraft:
        v = a["visits"][0]
        if not v["stand"]:
            continue
        s = xy[v["stand"]]
        shape = unary_union([Polygon(stage.placed(p, s["xy"], s["heading"])) for p in stage.plan(a["length"],
                                                                                               a["span"])])
        assert not shape.intersects(terminal), a["key"]
        lo = 0 if v["from"] is None else v["from"]
        parked.append((a, v["stand"], shape, lo - stage.PAD_IN, v["to"] + stage.PAD_OUT))
    for i, (a, ref, shape, lo, hi) in enumerate(parked):
        for b, ref2, shape2, lo2, hi2 in parked[i + 1:]:
            if lo < hi2 and lo2 < hi:
                assert ref != ref2 and not shape.intersects(shape2), (a["key"], b["key"])
    assert all(xy[v["stand"]]["terminal"] == "T5" for a in aircraft[:6] for v in a["visits"])
    wide = [xy[a["visits"][0]["stand"]] for a in aircraft if a["class"] == "widebody" and a["visits"][0]["stand"]]
    assert wide


def run_traffic() -> dict:
    """Two tails: N1 parked at JFK from the start, N2 arriving from Boston; no other traffic."""
    flights = [(0, "B6 10", "JFK", "BOS", 650, 720, "A32X", "N1"), (1, "B6 12", "JFK", "MCO", 720, 900, "A32X", "N1"),
               (2, "B6 20", "BOS", "JFK", 630, 700, "E190", "N2"), (3, "B6 22", "JFK", "BOS", 800, 870, "E190", "N2"),
               (4, "B6 30", "MCO", "JFK", 610, 790, "A32X", "N9")]
    return {"task_id": "t", "hub": "JFK", "date": "2024-01-09", "tz": "America/New_York", "horizon": [600, 1500],
            "flights": [{"id": i, "flight": f, "orig": o, "dest": d, "sched_dep": sd, "sched_arr": sa, "family": fam,
                         "tail": tail} for i, f, o, d, sd, sa, fam, tail in flights],
            "frozen": [], "tails": [{"tail": "N1", "family": "A32X", "type": "A321-271NX", "pos": "JFK"},
                                    {"tail": "N2", "family": "E190", "type": "E190-100", "pos": "BOS"}],
            "cities": {"BOS": "Boston", "MCO": "Orlando"}, "real": {}, "taxi": {"dep": {"day": 15.0},
                                                                                "arr": {"day": 7.0}},
            "aircraft": [], "weather": [stage.metar(590, METAR)], "stops": []}


def test_a_run_over_the_traffic_tags_its_flights_and_keeps_cancelled_ones_at_the_gate():
    executed = [{"id": 0, "tail": None, "status": "cancelled", "dep": None, "arr": None, "not_before": None,
                 "held": False},
                {"id": 1, "tail": "N1", "status": "flown", "dep": 760, "arr": 940, "not_before": 760, "held": True},
                {"id": 2, "tail": "N2", "status": "flown", "dep": 645, "arr": 715, "not_before": None, "held": False},
                {"id": 3, "tail": "N2", "status": "flown", "dep": 805, "arr": 875, "not_before": None, "held": False},
                {"id": 4, "tail": None, "status": "auto_cancelled", "dep": None, "arr": None, "not_before": None,
                 "held": False, "decided": 800}]
    task = {"task_id": "t", "hub": "JFK", "date": "2024-01-09", "tz": "America/New_York", "start": 600,
            "window": [660, 900]}
    crew = {1: [[700, "times out", 770]], 2: [[600, "no crew", None]], 3: [[700, "no crew", None], [790, None, None]]}
    doc = stage.sidecar(task, executed, run_traffic(), synthetic_stage(), crew)
    assert doc["span"] == [600, 805 + 15 + stage.MARGIN]
    n1, n2 = (next(a for a in doc["aircraft"] if a["key"] == k) for k in ("N1", "N2"))
    assert (n1["task"], n1["jetblue"], n1["class"]) == (True, True, "a321")
    gate, held = n1["visits"]
    assert (gate["from"], gate["to"], gate["dep"]["tag"], gate["dep"]["tone"]) == (None, 650, "B6 10 → Boston · "
                                                                                    "cancelled", "cancel")
    assert (held["from"], held["to"], held["arr"], held["dep"]["tag"], held["dep"]["tone"]) == (
        650, 760, None, "B6 12 → Orlando · held until 12:40", "hold")
    assert gate["stand"] == held["stand"] and gate["stand"].startswith("T5-")
    landed, cancelled = n2["visits"]
    assert (landed["from"], landed["to"], landed["arr"]["tag"], landed["arr"]["on"]) == (
        715, 800, "B6 20 from Boston · delayed 15 min", 708)
    assert (cancelled["arr"]["tag"], cancelled["arr"]["decided"], cancelled["dep"]["tag"], cancelled["dep"]["off"]) == (
        "B6 30 from Orlando · cancelled", 800, "B6 22 → Boston · delayed 5 min", 820)
    assert landed["stand"] == cancelled["stand"]
    assert (held["dep"]["crew"], cancelled["dep"]["crew"]) == (crew[1], crew[3])
    assert "crew" not in landed["arr"] and "crew" not in gate["dep"]
    assert doc["runways"] == [{"t": 600, "arrivals": "13L", "departures": "13R"}]
    assert doc["weather"][0]["plain"] == "Light rain · visibility 7 mi · cloud at 5,000 ft · wind ESE 20 mph gusting 31"
    assert [(f["reason"], f["key"], f["flight"], f["t"]) for f in doc["featured"]] == [("held", "N1", "B6 12", 760)]


def test_the_flow_turns_with_the_wind_but_not_for_a_puff_or_twice_an_hour():
    """A light tailwind keeps the flow; a stronger one, or a crosswind another flow takes as a much stronger headwind,
    turns it, at most once an hour."""
    st = synthetic_stage()
    winds = [(600, "13015KT"), (660, "31004KT"), (720, "31012KT"), (750, "04015KT"), (780, "04015KT"),
             (840, "VRB03KT")]
    weather = [stage.metar(t, f"KJFK 0{t}Z {w} 10SM CLR 20/10 A3000") for t, w in winds]
    assert stage.runways(weather, st, [600, 900]) == [
        {"t": 600, "arrivals": "13L", "departures": "13R"}, {"t": 720, "arrivals": "31R", "departures": "31L"},
        {"t": 780, "arrivals": "4R", "departures": "4L"}]
