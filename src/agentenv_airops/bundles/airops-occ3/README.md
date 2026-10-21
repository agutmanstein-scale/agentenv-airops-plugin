AirOps ops control center with passengers: a real storm day across JetBlue's whole network with its passengers, run by three desks (the crew scheduler, the customer desk and the ops controller, each its own `airops-desk` agent in its role) or by one agent at all three, in hourly watches on the gateway's virtual clock, under cost P (each passenger's delay at the final destination, refunds, nights away and refunded fares in place of v2's per-flight delay and cancellation price), and graded against the best plan found in hindsight under P.

Every task deploys the env registered as `airops-occ3` (run `agent-env airops-occ3 setup` once) and loads one day of its pack
(b6-v3) through `urn:airops:occ-load/v1`. Each desk's role sees only its own tools; one trigger per watch delivers the watch's FAA
advisories through the hidden `airops_notice` when an `end_turn` opens that watch, and the clock starts frozen at 04:00.
The desks play concurrently, taking turns the env grants (crew, customer, ops, then the crew desk again if ops asked it
something, then the customer desk if ops texted it). Rebooking is the passenger system's, automatic: no desk rebooks.
After the play, `urn:airops:end-day/v1` runs whatever is left of the day and grades it; `airops-occ3-verifier` scores the run only if its
audit passes (news, turns, causality, roles, crew and passenger legality, the bound and each desk's presence).

- `<task id>-joint` and `<task id>-one`: the day with three desks, and with one agent at all three; each desk's cost
  cap $5 in a three-desk run, $12 for one agent.
- `wiring-noplay`: the first day with no desk, the crew and passenger systems alone; it needs no model.

Built from public U.S. records: BTS on-time and T-100 data, the DB1B and DB1C passenger surveys, the FAA registry and FAA Command Center advisories; crews generated from the published schedule under FAR 117 and 14 CFR 121.467; passengers' itineraries generated from the surveys' connecting shares.
