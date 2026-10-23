"""The calm day's bar: JetBlue's own fair-weather cancellations (docs/v4/DESIGN.md §6.2).

A fair day is a date on which JetBlue cancelled none of the flights in a whole-network task's scope (every leg of a
tail that touched a hub that day, and every flight to or from a hub) for weather (code B) or the National Airspace
System (code C). The cancellations such a day still has are the carrier's own (code A: mechanical, crew), the kind a
calm day in the model forces where an aircraft is short. `rate` is the QUANTILE of their share of the scope's flights
over the fair days of the months given, and a calm day of n flights is within the bar when it forces at most
`allowance(rate, n)` cancellations, max(1, floor(rate x n)), and at most FERRIES ferries: JetBlue's own fair days
are within it on QUANTILE of them.
"""

import math
from collections.abc import Iterable

import pandas as pd

QUANTILE, FERRIES = 0.9, 2
CARRIER = "B6"
CODES = "ABCD"


def days(frame: pd.DataFrame, hubs: Iterable[str], carrier: str = CARRIER) -> pd.DataFrame:
    """Per date of `frame` (BTS on-time rows, as bts.read_ontime reads them), the scope's flights and its
    cancellations by code."""
    hubs = set(hubs)
    x = frame[frame.Reporting_Airline == carrier]
    at_hub = x.Origin.isin(hubs) | x.Dest.isin(hubs)
    tails = x[at_hub].groupby("FlightDate").Tail_Number.agg(lambda s: set(s.dropna()))
    rows = []
    for day, g in x.groupby("FlightDate"):
        scope = g[g.Tail_Number.isin(tails.get(day, set())) | g.Origin.isin(hubs) | g.Dest.isin(hubs)]
        codes = scope.loc[scope.Cancelled.astype(float) == 1, "CancellationCode"].value_counts()
        rows.append({"date": str(day)[:10], "flights": len(scope), **{c: int(codes.get(c, 0)) for c in CODES}})
    return pd.DataFrame(rows, columns=["date", "flights", *CODES])


def rate(frames: Iterable[pd.DataFrame], hubs: Iterable[str], q: float = QUANTILE) -> dict:
    """The bar over the fair days of `frames` (a month's BTS rows each): the q-quantile of the carrier's
    cancellations' share of the scope's flights, with the counts it was read from."""
    hubs = tuple(hubs)
    d = pd.concat([days(f, hubs) for f in frames], ignore_index=True)
    fair = d[(d.B == 0) & (d.C == 0) & (d.flights > 0)]
    if fair.empty:
        raise ValueError("no fair day in the months given: no calm-day bar")
    share = fair.A / fair.flights
    return {"rate": round(float(share.quantile(q)), 6), "quantile": q, "fair_days": len(fair), "days": len(d),
            "median_cancels": float(fair.A.median()), "ferries": FERRIES, "hubs": list(hubs)}


def allowance(rate: float, flights: int) -> int:
    """The forced cancellations a calm day of `flights` flights may have: JetBlue's fair-day share of them, at least
    one."""
    return max(1, math.floor(rate * flights))


def within(cancelled: int, ferries: int, flights: int, rate: float) -> bool:
    return cancelled <= allowance(rate, flights) and ferries <= FERRIES
