# AirOpsEnv v4: the check against v2 and v3

## On v2 0.3.2 (rebased, 2026-10-20)

The same check on `main` at `0ac85d2` (tasks from 04:00, the whole day): the v4 pack (`--bases JFK,BOS,FLL,MCO --crew v4
--buffer 0 --max-segments 3 --refs`, reserve pools fitted on train dates: BOS 1, FLL 1, JFK 2, MCO 3) on the same 94
storm dates and 2 controls as v3's pack, checked against v2 and v3 on those dates. Wall: pack 1 h 21 min.

| | v2 | v3 | v4 | v4 target |
|---|---:|---:|---:|---|
| do-nothing's median excess over the anchor | 11.1% | 12.1% | 14.1% (1.27x v2) | >= 1.4x v2: fail |
| follower's median excess over the anchor | 3.11% | 4.00% | 4.00% (1.29x v2) | >= 1.5x v2: fail |
| anchor's cancellations / JetBlue's | 34.0% | 38.5% | 40.8% | <= 100%: PASS |
| could fly for free | 54.3% | 50.1% | 49.6% | |
| recorded day >= anchor | | | 100% of dates | >= 95%: PASS |
| replay flies JetBlue's flown flights | 99.69% | 99.69% | 99.52% | >= 97%: PASS |
| replay departures within 15 min | 97.07% | 97.05% | 96.68% | within 1 point of v2: PASS |
| calm day no worse than v3's | | | 92 of 94 dates | every date: fail |
| anchors proven / replay exactly | | 96/96 | 96/96 | PASS |

world_check's gates on the v4 pack: controls PASS (on 2025-01-15 do-nothing is now at the flat anchor, where v3's
was 8.9% over it); airline_replay 0.883 (102 auto-cancels of 60,823 flown; v3 1.000) and calm_day 0.787 (v3 0.817) fail,
as does fidelity at eval JFK 0.775 (v3 0.789). On whole days the crew binds more than on the hub windows (do-nothing
+2.0 points over v3, against +1.1 before) but the follower gains nothing, and §0 is still not met.

## Before 0.3.2 (2026-10-17)

Code: fork at `977a449` (V4-1). Pack: the same 129 storm dates and 2 controls as v3 (docs/v3/REPORT.md), built by
`scripts/v3_pack.py --bases JFK,BOS,FLL,MCO --crew v4 --buffer 0 --max-segments 3 --refs` (buffer and segment cap as
fitted on train dates in V4-1) and checked by `scripts/v3_check.py` against v2 and against v3 layers only (`--v3`).
Run: pack 1,919 s, checks 81 s and 5 s.

### DESIGN §0, per criterion

| §0 criterion | v2 | v3 | v4 | Pass |
|---|---:|---:|---:|---|
| 1 Harder: do-nothing's median excess over the anchor (>= 1.4x v2) | 9.16% | 9.55% | 10.69% (1.17x v2, 1.12x v3) | fail |
| 1 Harder: follower's median excess over the anchor (>= 1.5x v2) | 2.65% | 2.91% | 2.88% (1.09x v2, 0.97x v3) | fail |
| 2 Guard: controls' recorded day >= anchor | | PASS | PASS | PASS |
| 2 Guard: calm day <= 1 cancellation, <= 2 ferries on >= 90% of dates | | 0.806 | 0.752 (12 dates worse than v3) | fail |
| 2 Guard: recorded day >= anchor on >= 95% of dates | | | 100% | PASS |
| 2 Guard: anchor cancels <= JetBlue's | 25.6% | 30.7% | 34.4% (1,245/3,624) | PASS |
| 2 Guard: replay flies >= 97% | 99.53% | 99.53% | 99.26% | PASS |
| 2 Guard: replay within 15 min within 1 point of v2 | 95.31% | 95.27% | 94.69% | PASS |
| 3 Sound: anchors proven / replay exactly | 131/131 | 131/131 | 131/131 | PASS |
| 3 Sound: sim/MIP agreement tests | | 282 | 300 passed | PASS |
| 3 Playable: live server test | | 2 passed | 2 passed | PASS |

world_check's G1 (airline replay auto-cancels) falls to 0.899 (128 of 46,667 flights; threshold 0.9).

### Reading

- A crew legal on schedule removes the over-constraint of Edgar's proxy pairings (controls and recorded-day guards
  pass, the anchor cancels 34% of what JetBlue did instead of 129%), but it barely binds: on storm days it adds about
  1 point of do-nothing excess over v3 and nothing for the follower, even at the tightest fit (buffer 0, 3 segments).
- The crew still costs a little realism: 12 calm days force a crew cancellation or ferry v3's didn't, and G1 slips
  just under 0.9.
- §0 is not met; the loop paused here (LOOP.md "Pause early"). Raw results: `check-vs-v2.*` and `check-vs-v3.*` in
  `~/handoff/v3-loop/pack-v4` on the devbox.

### What would close it (for Alec)

1. DESIGN §2: fit the departure-cap rule (SHORT) on train dates to the realism guards; closure rates per hub and month.
2. Make the days themselves harder rather than the world stricter: longer or more storm-dense windows, earlier
   cutoffs (less known at step 0), more unseen arrivals.
3. Keep v3 as the shipped env and v4's crew layer as an option (`--crew v4`).
