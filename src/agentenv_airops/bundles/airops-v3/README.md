AirOps v3: a real storm day across JetBlue's whole network, from 04:00, inside the FAA's programs at JFK and BOS and v3's realism layers (hub departure caps, ramp closures from lightning at the field, outstation arrival caps), played live in watches on the gateway's virtual clock by the airops-llm planner and graded against the hindsight optimum.

Every task deploys the env registered as `airops-v3-day` on the gateway provider (run `agent-env airops-v3 setup` once, and
`agent-env airops setup --agent` for the airops-llm agent) and loads one day of the b6-v3 pack through the env's
`urn:airops:v3-live-load/v1` extension, with the anchor's and the follower's costs stored with the pack. The day delivers each
watch's FAA advisories, METARs, SPECIs and TAFs itself, in the tool results, at their delivery lags; watches come
one an hour and one more when an advisory or SPECI reaches the desk or a ramp closure begins or ends. The clock
starts frozen at the first watch. After the play, `urn:airops:v3-end-day/v1` grades the final plan on the day as it happened if
the planner stopped early; the `airops-verifier` artifact reads the day from the env's `data/get` and scores the run with
its reward, and a day that never finished, or whose news broke the reveal schedule, fails the step, so the run is
not scored.

- `<task id>` plays that day with the `airops-llm` agent.
- `wiring-noplay` runs the first day's steps without the agent and needs no model: end-day flies the day with no change, as
  the naive reference does.

Built from public U.S. records: BTS on-time data of every carrier, the FAA registry, FAA Command Center advisories and NWS METARs, SPECIs and TAFs via the Iowa Environmental Mesonet.
