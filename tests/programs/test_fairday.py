import pandas as pd
import pytest

from agentenv_airops.programs.layers import fairday


def flight(date, orig, dest, tail, cancelled=0, code=None, carrier="B6"):
    return {"FlightDate": date, "Reporting_Airline": carrier, "Tail_Number": tail, "Origin": orig, "Dest": dest,
            "Cancelled": float(cancelled), "CancellationCode": code}


def test_a_days_scope_is_every_leg_of_a_tail_that_touched_a_hub_and_every_hub_flight_of_the_carrier():
    """On d1, N1 touches JFK so its ORD-DEN leg is in scope (cancelled for the carrier), N2 never does and is out, a
    hub flight with no tail is in, and another carrier's hub flight is not; d2's hub flight is cancelled for weather."""
    frame = pd.DataFrame([
        flight("2024-07-01", "JFK", "ORD", "N1"), flight("2024-07-01", "ORD", "DEN", "N1", 1, "A"),
        flight("2024-07-01", "LAX", "SFO", "N2", 1, "A"), flight("2024-07-01", "BOS", "MCO", None, 1, "C"),
        flight("2024-07-01", "JFK", "MIA", "N9", 1, "A", carrier="AA"),
        flight("2024-07-02", "JFK", "BOS", "N1", 1, "B"), flight("2024-07-02", "BOS", "JFK", "N3"),
    ])
    d = fairday.days(frame, ("JFK", "BOS"))
    assert d.to_dict("records") == [
        {"date": "2024-07-01", "flights": 3, "A": 1, "B": 0, "C": 1, "D": 0},
        {"date": "2024-07-02", "flights": 2, "A": 0, "B": 1, "C": 0, "D": 0},
    ]


def test_the_rate_is_the_quantile_of_the_carriers_share_over_fair_days_alone():
    """Fair days: d1 (1 of 100 cancelled for the carrier), d2 (none of 100), d4 (3 of 100); d3 has a weather
    cancellation and is not one, whatever its carrier cancellations."""
    rows = []
    for day, carrier_cancels, weather in (("2024-07-01", 1, 0), ("2024-07-02", 0, 0), ("2024-07-03", 40, 1),
                                          ("2024-07-04", 3, 0)):
        rows += [flight(day, "JFK", "ORD", f"N{k}", 1 if k < carrier_cancels else 0, "A") for k in range(100 - weather)]
        rows += [flight(day, "JFK", "BOS", "W", 1, "B")] * weather
    bar = fairday.rate([pd.DataFrame(rows)], ("JFK", "BOS"), q=0.5)
    assert (bar["fair_days"], bar["days"], bar["quantile"], bar["ferries"]) == (3, 4, 0.5, fairday.FERRIES)
    assert bar["rate"] == pytest.approx(0.01)
    assert fairday.rate([pd.DataFrame(rows)], ("JFK", "BOS"))["rate"] == pytest.approx(0.026)


def test_months_with_no_fair_day_have_no_bar():
    frame = pd.DataFrame([flight("2024-07-01", "JFK", "BOS", "N1", 1, "B")])
    with pytest.raises(ValueError, match="no fair day"):
        fairday.rate([frame], ("JFK",))


def test_a_calm_day_is_within_the_bar_at_the_rate_times_its_flights_at_least_one_and_two_ferries():
    assert [fairday.allowance(0.0092, n) for n in (50, 108, 109, 687)] == [1, 1, 1, 6]
    assert fairday.within(6, 2, 687, 0.0092) and not fairday.within(7, 0, 687, 0.0092)
    assert fairday.within(1, 0, 50, 0.0092) and not fairday.within(0, 3, 50, 0.0092)
