# AirOpsEnv v4: the check against v2 and v3 (2026-10-17)

Code: fork at `977a449` (V4-1). Pack: the same 129 storm dates and 2 controls as v3 (docs/v3/REPORT.md), built by
`scripts/v3_pack.py --bases JFK,BOS,FLL,MCO --crew v4 --buffer 0 --max-segments 3 --refs` (buffer and segment cap as
fitted on train dates in V4-1) and checked by `scripts/v3_check.py` against v2 and against v3 layers only (`--v3`).
Run: pack 1,919 s, checks 81 s and 5 s.

## DESIGN §0, per criterion

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

## Reading

- A crew legal on schedule removes the over-constraint of Edgar's proxy pairings (controls and recorded-day guards
  pass, the anchor cancels 34% of what JetBlue did instead of 129%), but it barely binds: on storm days it adds about
  1 point of do-nothing excess over v3 and nothing for the follower, even at the tightest fit (buffer 0, 3 segments).
- The crew still costs a little realism: 12 calm days force a crew cancellation or ferry v3's didn't, and G1 slips
  just under 0.9.
- §0 is not met; the loop paused here (LOOP.md "Pause early"). Raw results: `check-vs-v2.*` and `check-vs-v3.*` in
  `~/handoff/v3-loop/pack-v4` on the devbox.

## What would close it (for Alec)

1. DESIGN §2: fit the departure-cap rule (SHORT) on train dates to the realism guards; closure rates per hub and month.
2. Make the days themselves harder rather than the world stricter: longer or more storm-dense windows, earlier
   cutoffs (less known at step 0), more unseen arrivals.
3. Keep v3 as the shipped env and v4's crew layer as an option (`--crew v4`).
