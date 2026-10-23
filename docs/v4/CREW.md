# AirOpsEnv v4's crew: harder days, still no stricter than reality

Status: design, 2026-10-17; calm guard at JetBlue's fair weather, 2026-10-22. Part of v4 (docs/v4/DESIGN.md): the
layers and this crew ship as one version. Results: docs/v4/REPORT.md.

## §0 What done means

The layers alone made the days more realistic but barely harder (do-nothing's median excess over the anchor 9.2% -> 9.6%).
Edgar's crew model made them much harder (20.6%) by being stricter than reality: on schedule alone, with no delay and
no programs, the calm day cancelled flights on every date, the controls' recorded days cost less than the anchor, and
the anchor cancelled 129% of what JetBlue did.

The crew is done when, on the pack's dates, measured by `scripts/v4_check.py` against v2 and against the same
dates without crews (`--nocrew`):
1. **Harder:** do-nothing's median excess over the anchor >= 1.4x v2's, and the follower's >= 1.5x v2's.
2. **No stricter than reality (guards):** world_check's controls gate passes (each control's recorded day >= its
   anchor); the calm day is held to JetBlue's fair weather (docs/v4/DESIGN.md §6.2): on every date whose calm day
   without crews is within that bar, the crew's is too, so crews push no fair day past the cancellations JetBlue's own
   fair days have (DECISIONS 2026-10-22; until then the guard was relative, no more cancellations and ferries than
   without crews on every date, which counted against the crew the round trip it cancels where the base world is an
   aircraft short and the layers alone fly it up to 200 minutes late); the recorded day's excess over the anchor is
   >= 0 on >= 95% of dates; the anchor cancels no more than JetBlue did (<= 100%); the replay flies >= 97% of
   JetBlue's flown flights and its departures within 15 min stay within 1 point of v2's.
3. **Sound and playable:** as the layers (every anchor proven or flagged and replaying exactly; agreement tests; the live
   server test).

## §1 Lever 1: a crew layer legal on schedule

Edgar's proxy pairings (`repairs.pairings`) chain each tail's legs from a base departure to the next base arrival
with no duty limit, so a long chain through outstations exceeds FAR 117 on schedule and its later legs can never fly.
v4 keeps Edgar's crew API (`model.Crew`, `Flight.crew`, `Flight.legal`, `Task.bases`, `Task.auto_at`, the sim and MIP
enforcement, the reserve pools of docs/v4/DESIGN.md §3.4) and replaces only how pairings are cut, in `programs/layers/crew.py`:
- a duty starts at a tail's first leg, at a base departure, or after a rest (a ground gap >= REST, 10 h);
- it takes the tail's next leg while the duty would still end, on schedule, `buffer` minutes inside its Table B limit
  (report REPORT minutes before its first departure, 117 Table B by the report's local hour and the duty's segments)
  and holds at most `max_segments` legs; otherwise a new duty starts there (a crew change at a base, or an
  overnight crew at an outstation);
- each leg's crew deadline is report + limit + EXTENSION (2 h, 117.19) - its scheduled block, as Edgar's; a base
  departure past it flies only on a reserve (the pools).

So every duty is legal on schedule and the crew binds only when delays push a duty past its limit, as real crews time
out. `max_segments` is kept but has no effect in practice: every base departure starts a duty, so duties rarely
exceed 3 legs (V4-1's fit on hub windows: the same scores at 3 to 6 for every buffer from 30 min up; only at 0 does a cap of 3
keep one sample date's calm day at the layers' own).
`buffer` and `max_segments` are fitted on train dates only: the values that maximise do-nothing's excess while
every §0 guard holds on the train dates (defaults 60 min and 4, the spike's), on whole days since V4-3. Reserve
pools are then fitted as for the layers.

## §2 Further levers (only if §1 alone misses §0)

Candidates, each with the same guards: fitting the departure-cap rule (SHORT) on train dates to the realism guard;
closure rates per hub and month. The loop pauses for Alec before any of these (LOOP.md pause rules).

## §3 Units

| Unit | What | Done when |
|---|---|---|
| V4-0 | This doc | merged |
| V4-1 | `layers/crew.py` pairings legal on schedule, `network.build(crew=...)`, `v4_pack --crew v4`, train-date fit of buffer and max_segments under the guards, tests (sim/MIP agreement on a v4-crewed day; the calm day of a v4-crewed date cancels nothing for crews) | gates green; a sample of real dates passes the calm and controls guards |
| V4-2 | The v4 pack and check against v2 and v3 on the same dates; docs/v4/REPORT.md | §0 met, or the loop pauses with the numbers |
| V4-3 | One v4 on Edgar's v0.5.0: the layers and the crew as one version (pack b6-v4, bundle airops-v4), the calm day at JetBlue's fair-weather bar, the crew fitted on whole days under it; the pack, the check against v2 and the no-crew build, docs/v4/REPORT.md | the check run, its numbers in REPORT.md |
