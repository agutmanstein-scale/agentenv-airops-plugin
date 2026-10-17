"""v3's realism layers (model.Layers), each built for a task from pinned public inputs.

A layer is a rule sim.execute applies and the hindsight MIP carries alike (docs/v3/DESIGN.md §1), so the anchor stays
a proven optimum. A builder returns its field of Layers; a task without layers is v2's world.
- depcaps: JetBlue's departure cap per hour at a hub, from every carrier's BTS pushbacks (§3.1).
- arrcaps: JetBlue's arrival cap per hour at an outstation, from every carrier's BTS arrivals (§3.3).
- closures: ramp closures at a hub from its METARs and SPECIs, a departure and an arrival cap over each (§3.2).
- weather: the hubs' METARs, SPECIs and TAFs as the desk's news (§4), each delivered after its kind's lag.
- config: the constants a pack fits, and every layer of a task at them (network.build's `layers`).
- calibrate: the constants fitted on a pack's train dates: closure rates and reserve pools (§3.4, §6.1).
"""
