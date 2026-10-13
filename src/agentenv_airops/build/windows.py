"""The disruption window of a (day, hub): whole local hours, 3 to 6 of them, holding the hub's weather and ATC
disruption (B6's movements there cancelled for weather or the National Airspace System, plus their weather and NAS
delay minutes)."""

from collections.abc import Callable

import pandas as pd

CANCEL_WEIGHT = 523
"""A weather or NAS cancellation weighs as the cost model's cancellation per passenger, in minutes of delay."""
MIN_HOURS, MAX_HOURS = 3, 6
FIRST_HOUR, LAST_HOUR = 5, 24
SHARE = 0.8
"""The window is the shortest that holds this share of the most disruption any allowed window holds."""


def hourly(day: pd.DataFrame, hub: str) -> dict[int, float]:
    """Disruption by local hour at the hub; `day` is the carrier's flights of the date with sd/sa as local minutes."""
    weather = (day.CancellationCode.isin(["B", "C"]) & (day.Cancelled == 1)) * CANCEL_WEIGHT
    weather = weather + day.WeatherDelay.fillna(0) + day.NASDelay.fillna(0)
    out: dict[int, float] = {}
    for col, place in (("sd", "Origin"), ("sa", "Dest")):
        at = day[day[place] == hub]
        for h, v in weather[at.index].groupby((at[col] // 60).astype(int)).sum().items():
            out[int(h)] = out.get(int(h), 0.0) + float(v)
    return out


def movements(day: pd.DataFrame, hub: str) -> dict[int, int]:
    out: dict[int, int] = {}
    for col, place in (("sd", "Origin"), ("sa", "Dest")):
        for h, n in (day[day[place] == hub][col] // 60).astype(int).value_counts().items():
            out[int(h)] = out.get(int(h), 0) + int(n)
    return out


def choose(disruption: dict[int, float], busy: dict[int, int],
           allowed: Callable[[int, int], bool]) -> tuple[int, int, float] | None:
    """(first hour, end hour, disruption) of the shortest allowed window holding SHARE of the best allowed window's
    disruption; among those the most disrupted, then the busiest, then the earliest. None if no window is allowed."""
    windows = []
    for length in range(MIN_HOURS, MAX_HOURS + 1):
        for a in range(FIRST_HOUR, LAST_HOUR - length + 1):
            score = sum(disruption.get(h, 0.0) for h in range(a, a + length))
            windows.append((length, a, score, sum(busy.get(h, 0) for h in range(a, a + length))))
    windows = [w for w in windows if allowed(w[1], w[1] + w[0])]
    if not windows:
        return None
    best = max(w[2] for w in windows)
    length, a, score, _ = min((w for w in windows if w[2] >= SHARE * best), key=lambda w: (w[0], -w[2], -w[3], w[1]))
    return a, a + length, score
