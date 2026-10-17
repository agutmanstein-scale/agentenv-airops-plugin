import csv
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
import pytest
from day_helpers import day, flight, tail
from program_helpers import SLICE

from agentenv_airops.build import bts
from agentenv_airops.flow import sources
from agentenv_airops.programs.candidates import midnight
from agentenv_airops.programs.layers import closures


def at(s: str) -> int:
    return bts.epoch_minutes(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC))


def reports(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["station", "valid", "wxcodes", "metar"])
        for station, valid, wx, remarks in rows:
            w.writerow([station, valid, wx, f"K{station} 000000Z 10SM {wx} A2990 RMK {remarks}"])
    return path


ROUTINE = [("JFK", "2024-06-14 21:51", "", "AO2"),
           ("JFK", "2024-06-14 22:51", "TS", "AO2 LTG DSNT SW TSB47"),
           ("JFK", "2024-06-14 23:51", "+TSRA", "AO2"),
           ("JFK", "2024-06-15 00:51", "RA", "AO2 LTG DSNT NE TSE10"),
           ("JFK", "2024-06-15 01:51", "VCTS", "AO2 LTG DSNT ALQDS"),
           ("JFK", "2024-06-15 02:51", "TS", "AO2"),
           ("JFK", "2024-06-15 03:51", "", "AO2"),
           ("JFK", "2024-06-15 04:51", "", "AO2 TSB0412E0430"),
           ("BOS", "2024-06-15 00:51", "TS", "AO2 TSB30")]
SPECI = [("JFK", "2024-06-15 02:20", "TS", "AO2 TSB18"),
         ("JFK", "2024-06-15 03:05", "", "AO2 TSE03")]


def test_intervals_read_ts_at_the_field_from_its_begin_and_end_remarks_and_skip_vcts(tmp_path):
    metar = reports(tmp_path / "JFK_2024_06.csv", ROUTINE)
    assert closures.intervals("JFK", [metar], [tmp_path / "missing.csv"]) == [
        (at("2024-06-14 22:47"), at("2024-06-15 00:10")), (at("2024-06-15 02:51"), at("2024-06-15 03:51")),
        (at("2024-06-15 04:12"), at("2024-06-15 04:30"))]


def test_intervals_take_onset_and_end_to_the_minute_from_the_specis(tmp_path):
    metar = reports(tmp_path / "metar.csv", ROUTINE)
    speci = reports(tmp_path / "speci.csv", SPECI)
    assert closures.intervals("JFK", [metar], [speci]) == [
        (at("2024-06-14 22:47"), at("2024-06-15 00:10")), (at("2024-06-15 02:18"), at("2024-06-15 03:03")),
        (at("2024-06-15 04:12"), at("2024-06-15 04:30"))]
    assert closures.intervals("BOS", [metar], [speci]) == [(at("2024-06-15 00:30"), at("2024-06-15 00:51"))]


def test_intervals_on_the_slice_close_one_still_open_at_the_last_report():
    month = "2023-06"
    metar = SLICE / "iem" / "metar" / "JFK_2023_06.csv"
    assert not sources.speci_path(SLICE, "JFK", month).exists()
    assert closures.intervals("JFK", [metar], [sources.speci_path(SLICE, "JFK", month)]) == [
        (at("2023-06-26 14:29"), at("2023-06-26 15:00")), (at("2023-06-27 05:16"), at("2023-06-27 05:51"))]


def leaving(i: int, orig: str, dep: int, pushed: int | None):
    f = flight(i, orig, "BUF", dep, 60)
    return replace(f, actual=replace(f.actual, cancelled=pushed is None, dep=pushed, arr=None))


def test_build_caps_each_closure_at_the_rate_of_jetblues_normal_hourly_departures_never_below_what_it_pushed_back():
    """JFK closed 600-660: 4 scheduled in hour 10, 1 pushed back inside, 0.5 x 4/h x 1 h = 2. Closed 700-760: 1
    scheduled in hours 11-12, 2 pushed back inside (flight 2, late, and flight 4), so 2. One before the first watch and
    one past the span drop. BOS closes 650-700 with no JetBlue flight scheduled inside it but 3 in hours 10-11, all
    pushed back before it: 0.75 x 1.5/h x 50/60 h = 0.94, so 1 (to the nearest flight, not down)."""
    base = 1_000_000
    flights = [leaving(0, "JFK", 600, 605), leaving(1, "JFK", 610, 680), leaving(2, "JFK", 620, 700),
               leaving(3, "JFK", 650, None), leaving(4, "JFK", 720, 725), leaving(5, "BOS", 600, 600),
               leaving(6, "BOS", 610, 610), leaving(7, "BOS", 615, 615)]
    task = replace(day(flights, [tail("N1", "JFK")], start=300), base=base)
    spans = {"JFK": [(base + 100, base + 200), (base + 600, base + 660), (base + 700, base + 760),
                     (base + 5000, base + 5100)], "BOS": [(base + 650, base + 700)]}
    assert closures.build(task, spans, {"JFK": 0.5, "BOS": 0.75}) == {
        "JFK": ((600, 660, 2, 0), (700, 760, 2, 0)), "BOS": ((650, 700, 1, 0),)}
    assert closures.build(task, spans, {"JFK": 1.0, "BOS": 0.75})["JFK"] == ((600, 660, 4, 0), (700, 760, 2, 0))
    assert closures.build(task, spans, {"JFK": 1.0, "BOS": 0.25})["BOS"] == ((650, 700, 0, 0),)


def landing(i: int, dest: str, arr: int, gated: int | None, end: str | None = None):
    f = flight(i, "BUF", dest, arr - 60, 60)
    return replace(f, actual=replace(f.actual, cancelled=gated is None, dep=None if gated is None else gated - 60,
                                     arr=gated, end=None if gated is None else end or dest))


def test_build_caps_each_closures_gate_ins_at_the_rate_of_jetblues_normal_hourly_arrivals_never_below_what_gated_in():
    """JFK closed 600-660: 5 scheduled to arrive in hour 10 (flight 3 cancelled, flight 5 diverted to EWR), and 4
    gated in inside (flight 4, scheduled at 580, late). At 0.5, 0.5 x 5/h x 1 h = 2.5, so 3, floored at 4; at 1.0, 5.
    Flight 6, scheduled into BOS, diverted to JFK and gated in there inside: it counts in neither. BOS closes 650-680
    with no arrival scheduled inside but 4 in hours 10-11: 0.75 x 2/h x 0.5 h = 0.75, so 1."""
    base = 1_000_000
    flights = [landing(0, "JFK", 600, 610), landing(1, "JFK", 620, 630), landing(2, "JFK", 640, 650),
               landing(3, "JFK", 650, None), landing(4, "JFK", 580, 655), landing(5, "JFK", 630, 640, "EWR"),
               landing(6, "BOS", 600, 620, "JFK"), landing(7, "BOS", 610, 610), landing(8, "BOS", 615, 615),
               landing(9, "BOS", 620, 620)]
    task = replace(day(flights, [tail("N1", "BUF")], start=300), base=base)
    spans = {"JFK": [(base + 600, base + 660)], "BOS": [(base + 650, base + 680)]}
    assert closures.build(task, spans, {"JFK": 0.5, "BOS": 0.75}) == {
        "JFK": ((600, 660, 0, 4),), "BOS": ((650, 680, 0, 1),)}
    assert closures.build(task, spans, {"JFK": 1.0, "BOS": 0.75})["JFK"] == ((600, 660, 0, 5),)


def test_fit_rate_is_every_carriers_pushbacks_inside_over_the_same_minutes_before_on_the_dates_given():
    """JFK closes 30 min on the 14th: 3 pushbacks inside, 4 in the 30 min before; its closure on the 15th, and BOS's
    with nothing before it, don't count."""
    a = midnight("2024-06-14") + 1200
    b = midnight("2024-06-15") + 1200
    rows = [("JFK", a + k) for k in (2, 9, 20)] + [("JFK", a - 30 + k) for k in (0, 5, 10, 29)] + [("JFK", a - 31)]
    rows += [("JFK", b + k) for k in range(20)] + [("JFK", float("nan")), ("BOS", a + 1)]
    frame = pd.DataFrame(rows, columns=["Origin", "act_dep"])
    spans = {"JFK": [(a, a + 30), (b, b + 30)], "BOS": [(a, a + 10)]}
    assert closures.fit_rate(["2024-06-14"], frame, spans) == {"JFK": 0.75}
    assert closures.fit_rate(["2024-06-14", "2024-06-15"], frame, spans) == {"JFK": 23 / 4}


def test_fit_rate_leaves_out_a_closure_whose_minutes_before_reach_into_another():
    """A steady pushback a minute, one every two inside closures 100-160 and 170-230: the second's 60 minutes before
    hold the first closure, so only the first counts."""
    m = midnight("2024-06-14")
    deps = [m + t for t in range(400) if not ((100 <= t < 160 or 170 <= t < 230) and t % 2)]
    frame = pd.DataFrame({"Origin": "JFK", "act_dep": [float(x) for x in deps]})
    spans = {"JFK": [(m + 100, m + 160), (m + 170, m + 230)]}
    assert closures.fit_rate(["2024-06-14"], frame, spans) == {"JFK": 0.5}


def test_an_end_remark_older_than_the_open_closure_belongs_to_an_earlier_storm(tmp_path):
    """BOS 2024-06-14 as reported: TSB22 at 17:24, "TSE13B24" at 17:40 and 17:54, TS until TSE36 at 18:36."""
    rows = [("BOS", "2024-06-14 16:54", "", "AO2 LTG DSNT W"),
            ("BOS", "2024-06-14 17:24", "TS", "AO2 TSB22"),
            ("BOS", "2024-06-14 17:40", "+TSRA", "AO2 RAB30 TSE13B24"),
            ("BOS", "2024-06-14 17:54", "TSRA BR", "AO2 RAB30 TSE13B24"),
            ("BOS", "2024-06-14 18:36", "", "AO2 RAE36 TSE36")]
    assert closures.intervals("BOS", [reports(tmp_path / "m.csv", rows)]) == [
        (at("2024-06-14 17:22"), at("2024-06-14 18:36"))]


def test_a_remark_minute_past_its_reports_is_in_the_hour_or_day_before(tmp_path):
    """TSB55 in the 00:51 report began at 23:55 the day before; TSE2358 in the 00:20 report ended at 23:58."""
    rows = [("JFK", "2024-06-15 00:51", "TS", "AO2 TSB55"), ("JFK", "2024-06-15 01:51", "", "AO2 TSE20"),
            ("BOS", "2024-06-14 23:51", "TS", "AO2"), ("BOS", "2024-06-15 00:20", "", "AO2 TSE2358")]
    path = reports(tmp_path / "m.csv", rows)
    assert closures.intervals("JFK", [path]) == [(at("2024-06-14 23:55"), at("2024-06-15 01:20"))]
    assert closures.intervals("BOS", [path]) == [(at("2024-06-14 23:51"), at("2024-06-14 23:58"))]


def test_build_names_a_closed_hub_that_fit_rate_gave_no_rate():
    """fit_rate gives BOS no rate (nothing pushed back before its closure); building a task over that closure says so
    rather than failing on a bare key, and a hub with no closure over the task needs no rate."""
    m = midnight("2024-06-14")
    frame = pd.DataFrame({"Origin": ["JFK", "JFK"], "act_dep": [float(m + 50), float(m + 105)]})
    spans = {"JFK": [(m + 100, m + 160)], "BOS": [(m + 100, m + 160)]}
    rate = closures.fit_rate(["2024-06-14"], frame, spans)
    assert rate == {"JFK": 1.0}
    task = replace(day([flight(0, "JFK", "BUF", 120, 60), flight(1, "BOS", "JFK", 120, 60)],
                       [tail("N1", "JFK"), tail("N2", "BOS")]), base=m)
    with pytest.raises(ValueError, match="BOS"):
        closures.build(task, spans, rate)
    assert closures.build(task, {"JFK": spans["JFK"], "BOS": [(m + 5000, m + 5100)]}, rate) == {
        "JFK": ((100, 160, 1, 0),)}

