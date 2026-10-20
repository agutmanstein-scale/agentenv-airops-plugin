"""The next day's published schedule for passengers stranded overnight (programs.pax.Seat): every flight BTS on-time
data has the carrier scheduled on the day after the task's date, cancelled ones included, in minutes from the task
date's midnight, each with its seats left after its booked passengers: its tail's seats (Form 41 B-43, else the FAA
registry; the day's median where neither has it) less seats x its T-100 route-month load factor, as the task's own
flights are loaded (fleet.pax), its B-43 years the build's (candidates.b43_years: 2026 has no published inventory)."""

import statistics
from datetime import date, timedelta
from pathlib import Path

from ..programs import candidates
from ..programs.model import Task
from ..programs.pax import Seat
from . import bts, fleet


def seats(task: Task, inputs: Path, derived: Path) -> tuple[Seat, ...]:
    day = (date.fromisoformat(task.date) + timedelta(days=1)).isoformat()
    month = day[:7]
    df = bts.read_ontime(bts.ontime_path(inputs, month), derived)
    df = df[(df.Reporting_Airline == task.carrier) & (df.FlightDate == day) & df.sched_dep.notna()]
    craft = candidates.typed(set(df.Tail_Number.dropna()), candidates.b43_years(month), inputs)
    median = int(statistics.median(a.seats for a in craft.values()))
    lf = fleet.load_factors(task.carrier, month, inputs)
    out = []
    for r in df.assign(num=df.Flight_Number_Reporting_Airline.astype(int)).sort_values(
            ["sched_dep", "num", "Origin"]).itertuples():
        n = craft[r.Tail_Number].seats if isinstance(r.Tail_Number, str) and r.Tail_Number in craft else median
        out.append(Seat(f"{task.carrier} {r.num} {r.Origin}", r.Origin, r.Dest, int(r.sched_dep) - task.base,
                        int(r.sched_arr) - task.base, max(0, n - fleet.pax(n, lf, r.Origin, r.Dest))))
    return tuple(out)
