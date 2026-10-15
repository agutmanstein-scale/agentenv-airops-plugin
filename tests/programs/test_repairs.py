from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agentenv_airops.build.fleet import Aircraft
from agentenv_airops.programs import repairs


@pytest.mark.parametrize(("model", "seats", "family"), [
    ("A320-232", 162, "A320"), ("A320-232", 150, "A320"), ("A321-231", 190, "A321"), ("A321-271NX", 200, "A321"),
    ("A321-231", 159, "A321P"), ("A321-271NX", 138, "A321P"), ("BD-500-1A11", 140, "A220"), ("E190-100", 100, "E190"),
])
def test_the_a32x_family_splits_by_seats_and_type(model, seats, family):
    old = "A32X" if "A32" in model else "A220" if "BD-500" in model else "E190"
    assert repairs.family(Aircraft("N1", old, model, seats, "B-43 2024")) == family


def test_schedule_turns_are_the_fifth_percentile_floored():
    rows = []
    for k in range(40):
        rows += [{"Tail_Number": f"N{k}", "FlightDate": "2024-07-01", "Origin": "JFK", "Dest": "BOS",
                  "sched_dep": 600, "sched_arr": 680},
                 {"Tail_Number": f"N{k}", "FlightDate": "2024-07-01", "Origin": "BOS", "Dest": "JFK",
                  "sched_dep": 680 + 20 + k, "sched_arr": 800}]
    fleet = {f"N{k}": Aircraft(f"N{k}", "E190", "E190-100", 100, "B-43 2024") for k in range(40)}
    turns = repairs.turns(pd.DataFrame(rows), fleet)
    assert turns["E190"] == 25 and set(turns) == set(repairs.FAMILIES)
    fleet["N39"] = Aircraft("N39", "A32X", "A320-232", 162, "B-43 2024")
    assert repairs.turns(pd.DataFrame(rows), fleet)["A320"] == 59


@pytest.mark.parametrize(("dep", "late", "nas", "removed", "lb"), [
    (60, 20, 30, False, 40), (60, 20, 30, True, 10), (5, 0, 60, True, 0), (-8, np.nan, np.nan, True, -8),
    (-30, np.nan, np.nan, False, -15), (45, np.nan, 50, True, 0),
])
def test_lb_drops_nas_delay_but_never_departs_before_the_flight_did(dep, late, nas, removed, lb):
    row = SimpleNamespace(DepDelay=dep, LateAircraftDelay=late, NASDelay=nas)
    assert repairs.lb_of(row, removed) == lb
    frame = pd.DataFrame([{"DepDelay": dep, "LateAircraftDelay": late, "NASDelay": nas, "Cancelled": 0}])
    assert repairs.new_lb(frame, pd.Series([removed])).iloc[0] == lb


def test_a_cancelled_flight_takes_the_upper_quartile_by_destination_then_origin_then_day():
    pool = pd.DataFrame([{"Origin": o, "Dest": d, "sd": sd, "lb": lb} for o, d, sd, lb in [
        ("BOS", "JFK", 600, 10), ("DCA", "JFK", 630, 20), ("ORD", "JFK", 660, 30), ("ORD", "JFK", 640, 40),
        ("MCO", "JFK", 900, 400), ("PIT", "ATL", 610, 5), ("PIT", "LAX", 620, 15), ("PIT", "MIA", 630, 25),
    ]])
    assert repairs.imputed_lb(pool, "BOS", "JFK", 620) == 33
    assert repairs.imputed_lb(pool, "PIT", "SEA", 620) == 20
    assert repairs.imputed_lb(pool, "SAV", "SEA", 300) == 33


def test_the_truth_block_is_clipped_and_a_diverted_flight_takes_its_routes_median():
    day = pd.DataFrame([{"Origin": "MKE", "Dest": "JFK", "sd": sd, "ad": sd + 5, "aa": sd + 5 + b, "Cancelled": 0,
                         "Diverted": 0} for sd, b in ((600, 130), (630, 150), (650, 140), (900, 100))])
    flown = SimpleNamespace(Origin="MKE", Dest="JFK", sd=600, ad=610, aa=1000, CRSElapsedTime=130, Cancelled=0,
                            Diverted=0, DivReachedDest=np.nan)
    assert repairs.truth_block(flown, day) == 250
    diverted = SimpleNamespace(**{**vars(flown), "Diverted": 1, "aa": 2058})
    assert repairs.truth_block(diverted, day) == 140
    reached = SimpleNamespace(**{**vars(flown), "Diverted": 1, "DivReachedDest": 1, "aa": 900})
    assert repairs.truth_block(reached, day) == 140
    assert repairs.truth_block(SimpleNamespace(**{**vars(flown), "Cancelled": 1, "Origin": "SAV"}), day) == 130


def test_deadlines_run_dmax_past_the_schedule_capped_at_the_hub_cutoff():
    assert repairs.deadline(600, "MCO", 540) == 1500
    assert repairs.deadline(900, "JFK", 540) == repairs.CUTOFF
    assert repairs.deadline(100, "MCO", 1080, dmax=900) == 1140
    assert repairs.deadline(100, "MCO", 1080, dmax=360) == 1140


def test_out_and_backs_pair_from_a_hub():
    legs = [(0, "JFK", "BOS"), (1, "BOS", "JFK"), (2, "JFK", "MCO"), (3, "MCO", "FLL"), (4, "FLL", "MCO"),
            (5, "MCO", "JFK"), (6, "JFK", "MCO")]
    assert repairs.pairs(legs) == {0: 1, 1: 0}
    assert repairs.pairs([(7, "MCO", "JFK"), (8, "JFK", "MCO"), (9, "MCO", "JFK")]) == {8: 9, 9: 8}


def test_every_chain_connects_or_repositions_and_pax_fit_the_smallest_tail(task):
    seats = defaultdict(list)
    for t in task.tails:
        seats[t.family].append(t.seats)
        legs = [task.flight(i) for i in t.legs]
        assert not legs or legs[0].orig == t.pos
        breaks = [(a.dest, b.orig) for a, b in zip(legs, legs[1:], strict=False) if a.dest != b.orig]
        assert breaks == [(m.orig, m.dest) for m in t.repositions]
        assert all(m.orig == m.dest and m.ready > m.leave for m in t.busy)
    assert all(f.pax == min(f.pax_raw, *seats[f.family]) for f in task.flights)
    assert {f.family for f in task.flights} <= set(task.turn)
