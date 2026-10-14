AirOps: a storm day at a JetBlue hub played live in hourly watches on the gateway's virtual clock by the airops-llm planner, the FAA advisories and the weather arriving as issued, graded against the hindsight optimum.

Every task deploys the env registered as `airops-day` on the gateway provider (run `agent-env airops setup --agent` once)
and loads one day of the b6-v1 pack through the env's `urn:airops:live-load/v1` extension. It hides the env's
`airops_notice` tool from the agent's role, registers one trigger per watch that delivers the watch's news through
`airops_notice` when the agent's `advance` reaches it, and arms the clock frozen at the first watch. After the play,
`urn:airops:end-day/v1` runs any watches the agent didn't reach, flies the final plan on the day as it happened and grades it.
The `airops-verifier` artifact reads the day from the env's `data/get` and scores the run with its reward; a day that never
finished, or whose news broke the reveal schedule, fails the step, so the run is not scored.

- `<task id>` plays that day with the `airops-llm` agent.
- `wiring-noplay` runs the first day's steps without the agent and needs no model: end-day delivers every watch's news
  itself and the day flies with no change, as the naive reference does.

Built from public U.S. records: BTS on-time and T-100 data, the FAA registry, FAA Command Center advisories and NWS METARs, TAFs and forecast discussions via the Iowa Environmental Mesonet.
