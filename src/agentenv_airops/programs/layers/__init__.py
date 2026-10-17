"""v3's realism layers (model.Layers), each built for a task from pinned public inputs.

A layer is a rule sim.execute applies and the hindsight MIP carries alike (docs/v3/DESIGN.md §1), so the anchor stays
a proven optimum. A builder returns its field of Layers; a task without layers is v2's world.
- depcaps: JetBlue's departure cap per hour at a hub, from every carrier's BTS pushbacks (§3.1).
- closures: ramp closures at a hub from its METARs and SPECIs, a departure and an arrival cap over each (§3.2).
"""
