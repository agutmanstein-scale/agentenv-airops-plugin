import pytest
from flow_helpers import obs

from agentenv_airops.flow import capacity
from agentenv_airops.flow.capacity import INSTRUMENT, MARGINAL, VISUAL, Reductions
from agentenv_airops.flow.weather import Bin


def test_categories_follow_each_profiles_thresholds_at_their_edges():
    assert capacity.category("JFK", 2000, 4) == VISUAL and capacity.category("JFK", 1999, 10) == MARGINAL
    assert capacity.category("JFK", 5000, 3.5) == MARGINAL and capacity.category("JFK", 999, 10) == INSTRUMENT
    assert capacity.category("BOS", 3000, 3) == VISUAL and capacity.category("BOS", 2900, 10) == MARGINAL
    assert capacity.category("BOS", 5000, 2.5) == INSTRUMENT and capacity.category("JFK", None, None) == VISUAL


def test_jfk_trades_arrivals_for_departures_and_never_lets_worse_weather_land_more():
    assert capacity.jfk_rates(20) == {VISUAL: 52, MARGINAL: 48, INSTRUMENT: 48}
    mid = capacity.jfk_rates(42)
    assert mid[VISUAL] == pytest.approx(45.0) and mid[MARGINAL] == pytest.approx(45.0)
    assert mid[INSTRUMENT] == pytest.approx(43.8)
    assert capacity.jfk_rates(52) == capacity.jfk_rates(80)
    assert capacity.jfk_rates(80)[VISUAL] == 38
    for d in range(0, 80, 3):
        r = capacity.jfk_rates(d)
        assert r[VISUAL] >= r[MARGINAL] >= r[INSTRUMENT]


def test_bos_flies_the_first_wind_feasible_flow_at_its_usage_weighted_rate():
    assert capacity.bos_rates(obs()) == ("north", {VISUAL: 61, MARGINAL: 32, INSTRUMENT: 32})
    flow, rates = capacity.bos_rates(obs(210, 15))
    assert flow == "south" and rates[VISUAL] == pytest.approx((48 * 5.76 + 32 * 10.2) / 15.96)
    assert rates[MARGINAL] == rates[VISUAL] and rates[INSTRUMENT] == 32
    assert capacity.bos_rates(obs(130, 20)) == ("south", {VISUAL: 32, MARGINAL: 32, INSTRUMENT: 32})
    assert capacity.bos_rates(obs(300, 25)) == ("crosswind", {VISUAL: 44, MARGINAL: 32, INSTRUMENT: 32})
    assert capacity.bos_rates(obs(120, 30))[0] == "north"


def test_wind_limits_are_the_orders_on_the_sustained_wind():
    assert capacity.excess(obs(110, 20), "BOS", ("04",)) == pytest.approx(0.0, abs=0.01)
    assert capacity.excess(obs(200, 5), "BOS", ("04",)) == pytest.approx(0.0)
    assert capacity.excess(obs(200, 6), "BOS", ("04",)) == pytest.approx(1.0)


def test_the_reduction_is_the_largest_factor_and_bins_round_cumulatively():
    r = Reductions(storm=0.5, vicinity=0.2, frozen=0.3)
    assert r.of(Bin(obs(ts=True), 0.0)) == 0.5 and r.of(Bin(obs(), 0.05)) == pytest.approx(0.25)
    assert r.of(Bin(obs(vicinity=True, frozen=True), 0.0)) == 0.3 and r.of(Bin(obs(), None)) == 0.0
    caps = capacity.bins("BOS", [Bin(obs(), 0.0)] * 8, [30.0, 30.0], Reductions())
    assert caps == [15, 16, 15, 15, 15, 16, 15, 15] and sum(caps) == 122
    stormy = capacity.bins("JFK", [Bin(obs(ts=True), 1.0)] * 4, [10.0], r)
    assert sum(stormy) == 26
