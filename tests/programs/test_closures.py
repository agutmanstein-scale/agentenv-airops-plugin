import csv
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
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


def test_build_caps_each_closure_at_the_rate_of_jetblues_schedule_never_below_what_it_pushed_back():
    """JFK closed 600-660: 4 scheduled, 1 pushed back inside, 0.5 x 4 = 2. Closed 700-760: 1 scheduled, 2 pushed back
    inside (flight 2, late, and flight 4), so 2. One before the first watch and one past the span drop; BOS has no
    JetBlue flight inside its closure, so nothing may leave it then."""
    base = 1_000_000
    flights = [leaving(0, "JFK", 600, 605), leaving(1, "JFK", 610, 680), leaving(2, "JFK", 620, 700),
               leaving(3, "JFK", 650, None), leaving(4, "JFK", 720, 725), leaving(5, "BOS", 500, 500)]
    task = replace(day(flights, [tail("N1", "JFK")], start=300), base=base)
    spans = {"JFK": [(base + 100, base + 200), (base + 600, base + 660), (base + 700, base + 760),
                     (base + 5000, base + 5100)], "BOS": [(base + 650, base + 700)]}
    assert closures.build(task, spans, {"JFK": 0.5, "BOS": 0.75}) == {
        "JFK": ((600, 660, 2), (700, 760, 2)), "BOS": ((650, 700, 0),)}
    assert closures.build(task, spans, {"JFK": 1.0, "BOS": 0.75})["JFK"] == ((600, 660, 4), (700, 760, 2))


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
