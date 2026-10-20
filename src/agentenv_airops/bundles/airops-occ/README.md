AirOps ops control center: a real storm day across JetBlue's whole network, run by two desks (the ops controller and the crew scheduler, each its own `airops-desk` agent in its role) or by one desk running both, in hourly watches on the gateway's virtual clock, and graded against the best plan found in hindsight.

Every task deploys the env registered as `airops-occ` (run `agent-env airops-occ setup` once) and loads one day of its pack
through `urn:airops:occ-load/v1`. Each desk's role sees only its own tools; one trigger per watch delivers the watch's FAA
advisories through the hidden `airops_notice` when an `end_turn` opens that watch, and the clock starts frozen at
04:00. The desks play concurrently, taking turns the env grants (crew, ops, then crew again if ops asked it something).
After the play, `urn:airops:end-day/v1` runs whatever is left of the day and grades it; `airops-occ-verifier` scores the run only if its
audit passes (news, turns, causality, roles, crew legality, the bound and each desk's presence).

- `<task id>-joint` and `<task id>-one`: the day with two desks, and with one.
- `wiring-noplay`: the first day with no desk, the crew system alone; it needs no model.

Built from public U.S. records: BTS on-time and T-100 data, the FAA registry and FAA Command Center advisories; crews generated from the published schedule under FAR 117 and 14 CFR 121.467.
