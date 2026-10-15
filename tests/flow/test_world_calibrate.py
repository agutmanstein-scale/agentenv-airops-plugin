import json

import pytest
from flow_helpers import CACHE, DERIVED, arrival, needs_cache, obs, world

from agentenv_airops.flow import calibrate, costs, demand, queue
from agentenv_airops.flow import world as flow_world
from agentenv_airops.flow.calibrate import Hour
from agentenv_airops.flow.capacity import Reductions
from agentenv_airops.flow.weather import Bin

NB = "narrowbody 165k lb or more"


def test_naive_releases_queue_at_the_runway_and_cost_air_minutes_and_late_passengers():
    w = world([arrival(0, 100, 0), arrival(1, 100, 0), arrival(2, 125, 125, unseen=True)], [1] * 192)
    out = flow_world.evaluate(w)
    a, b, u = out.flights[0], out.flights[1], out.flights[2]
    assert (a.eta, a.landing, a.held) == (60, 60, 0) and (b.landing, b.held) == (75, 15)
    assert b.air == 15 * 95 and b.pax == 0 and a.ground == 0
    assert (u.eta, u.landing, u.pax) == (125, 135, 150 * 20)
    assert out.totals["system"] == sum(f.cost for f in out.flights.values())
    assert out.totals["unseen"] == u.cost and out.totals["diversions"] == 0


def test_a_ground_hold_costs_gate_minutes_and_a_diversion_costs_its_hold_block_and_passengers():
    w = world([arrival(k, 70, 0) for k in range(6)] + [arrival(9, 300, 0, cancelled=True)], [1] * 192)
    naive = flow_world.evaluate(w)
    gone = naive.flights[5]
    assert gone.landing is None and gone.held == 60 and 9 not in naive.flights
    assert gone.air == 95 * (60 + costs.DIVERSION_BLOCK) and gone.pax == 150 * costs.DIVERSION_PAX
    assert naive.totals["cancelled"] == {"flights": 1, "pax": 150}
    held = flow_world.evaluate(w, {5: 30})
    assert held.flights[5].ground == 34 * 30 and held.flights[5].landing == 135
    assert flow_world.bands(w)[90]["diversions"] == 0 and flow_world.bands(w)[45]["diversions"] == 2
    with pytest.raises(ValueError, match="before its d0"):
        flow_world.evaluate(w, {0: -1})


def hour(throughput: float, storm: float = 0.0, saturated: bool = True, gdp: bool = False) -> Hour:
    return Hour("2024-07-12", "JFK", 15, gdp, False, 20, throughput, 15.0, (15.0,) * 4, saturated, (40.0,) * 4,
                (storm,) * 4, (False,) * 4, (False,) * 4, "visual", "arrivals 31")


def test_the_fit_recovers_a_planted_storm_factor_and_leaves_thin_factors_at_zero():
    sample = [hour(40.0) for _ in range(20)] + [hour(40.0 * 0.6, storm=1.0) for _ in range(12)]
    got = calibrate.fit(sample)
    assert got["reductions"] == Reductions(0.4, 0.0, 0.0) and got["fixed_at_zero"] == ["vicinity", "frozen"]
    assert got["loss"] == pytest.approx(0.0, abs=1e-12) and got["hours"] == 32
    thin = calibrate.fit([hour(40.0) for _ in range(20)] + [hour(20.0, storm=1.0) for _ in range(9)])
    assert thin["reductions"] == Reductions() and "storm" in thin["fixed_at_zero"]


def test_the_capacity_guard_counts_saturated_hours_without_a_gdp_within_twenty_percent():
    sample = [hour(40.0)] * 8 + [hour(30.0)] * 2 + [hour(10.0, gdp=True), hour(10.0, saturated=False)]
    got = calibrate.guard_capacity(sample, Reductions())
    assert (got["hours"], got["within"], got["share"], got["pass"]) == (10, 8, 0.8, True)


def test_the_airborne_guard_reads_correlation_and_the_median_ratio():
    rows = [{"modelled": m, "observed": o} for m, o in ((0, 1), (2, 3), (10, 9), (20, 18), (4, 6))]
    got = calibrate.guard_airborne(rows)
    assert got["correlation"] > 0.95 and got["ratio_hours"] == 3 and got["median_ratio"] == pytest.approx(10 / 9)
    assert got["pass"] is True
    assert calibrate.guard_airborne([{**r, "modelled": 3 * r["modelled"]} for r in rows])["pass"] is False


def test_holding_releases_every_flight_at_its_recorded_wheels_off_plus_its_baseline():
    base = arrival(0, 100, 0)
    flown = [demand.Arrival(**{**base.__dict__, "id": k, "number": k, "wheels_off": 0, "wheels_on": 70 + 15 * k,
                               "air_time": 70.0 + 15 * k}) for k in range(3)]
    w = world(flown, [1] * 192)
    rows = calibrate.holding(w, {k: 60.0 for k in range(3)})
    assert [(r["eta"], r["held"], r["excess"]) for r in rows] == [(60, 0, 10.0), (60, 15, 25.0), (60, 30, 40.0)]
    assert queue.DIVERT_AFTER == 60


def test_a_calibration_round_trips_through_json(tmp_path):
    med = demand.Medians({("ORD", "JFK", "summer"): 120.0}, {("ORD", "JFK"): 118.0}, {("JFK", "summer"): (30.0, 0.12),
                         ("JFK", "winter"): (28.0, 0.11)}, {("AA", "JFK"): 9.0}, {"JFK": 10.0}, {"ORD": 18.0}, 19.0)
    path = tmp_path / "calibration.json"
    calibrate.save(path, med, Reductions(0.5, 0.2, 0.1), {"note": 1})
    back, r = calibrate.load(path)
    assert back == med and r == Reductions(0.5, 0.2, 0.1) and json.loads(path.read_text())["note"] == 1
    assert back.tau("ORD", "JFK", "summer", 640) == 120 and back.tau("BOS", "JFK", "winter", 100) == 39


def test_with_reductions_rebuilds_the_bins():
    w = world([arrival(0, 100, 0)], [13] * 8)
    w = flow_world.World(**{**w.__dict__, "timeline": tuple(Bin(obs(ts=True), 1.0) for _ in range(8))})
    assert calibrate.with_reductions(w, Reductions(storm=0.5)).caps[:4] == (7, 6, 7, 6)


@needs_cache
def test_a_train_day_builds_and_prices_its_naive_day():
    med = demand.medians(CACHE, DERIVED, ["2022-12-07", "2024-08-18"])
    w = flow_world.build(CACHE, DERIVED, "2022-12-07", "JFK", med, Reductions())
    assert len(w.caps) == flow_world.BINS and [a.id for a in w.arrivals] == list(range(len(w.arrivals)))
    assert sum(a.unseen for a in w.arrivals) > 200 and sum(not a.unseen for a in w.arrivals) > 300
    out = flow_world.evaluate(w)
    assert out.totals["flights"] == sum(not a.cancelled for a in w.arrivals) and out.totals["ground"] == 0
