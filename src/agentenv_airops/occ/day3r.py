"""One day of the ops control center with passengers in the realistic world (airops-occ3, realistic): v3's day (day3.py)
on a b6-v3r world, graded on JetBlue's own cost J.

The truth is the realistic one (realworld.truth: v4's caps and closures in the task, at most so many JetBlue aircraft
on the ground at a station, the day's tails that break, aircraft swaps only at the hubs), and the day as known at t is
realworld.known (the tails broken by t, the gates and the swaps rule as they are). The desks' cost lines, estimates
and reviews price J on the projection in dollars: the aircraft's minutes with SAVE of what cancellations save at FAA's
rates, the crews' deadheads, hotels and end of day, tomorrow's first departures at the aircraft's own cost, and the
revenue lost (the refunded's fares, TAKE of the stranded's). The grade's reward is J's, 0.5 at JetBlue's own replay
and 1.0 at the cheapest plan the realism report found (realworld.reward); cost P's figures stay in the grade under
`P`, and the passengers still fly, rebook and are stranded or refunded as in v3."""

from collections import Counter

from .. import realworld
from ..core.sim import AUTO_CANCELLED, CANCELLED
from ..programs import crewsim, pax
from .day import CREW, ONE, OPS
from .day3 import CUSTOMER, OccDay3
from .pack import World

CRITERION = ("the day's reward on the realistic truth under JetBlue's own cost, 0.5 at JetBlue's replay and 1.0 at "
             "the cheapest plan found in hindsight")


class OccDay3R(OccDay3):

    def __init__(self, world: World, mode: str = "joint"):
        if world.real is None:
            raise ValueError("a realistic day needs a b6-v3r world")
        super().__init__(world, mode)

    def truth_world(self):
        return realworld.truth(self.task, self.w.real["gates"], self.w.real["broken"])

    def known_world(self, t: int):
        return realworld.known(self.task, t, self.w.real["gates"], self.w.real["broken"])

    def airline(self, day: crewsim.CrewDay) -> dict:
        r = self.w.real
        return realworld.airline(self.task, day, r["firsts"], self.w.passengers, r["fares"])

    def costs(self, day: crewsim.CrewDay) -> dict[str, float]:
        """J by part in dollars."""
        r, a = self.w.real, self.airline(day)
        usd = r["usd_per_pax_min"]
        parts = {"aircraft": a["aircraft"] + (1 - r["save"]) * a["saved"], "crew": a["crew"],
                 "tomorrow": a["tomorrow"], "refunds": a["refunded_rev"], "stranded": r["take"] * a["stranded_rev"]}
        return {"total": round(usd * sum(parts.values())), **{k: round(usd * v) for k, v in parts.items()}}

    def cost_lines(self, desk: str, proj) -> list[str]:
        c = self.costs(proj.day)
        statuses = Counter(x.status for x in proj.day.run.flights.values())
        tail = f"; {statuses[CANCELLED]} cancelled, {statuses[AUTO_CANCELLED]} auto-cancel"
        out = []
        if desk in (OPS, ONE):
            out.append(f"JetBlue's cost of the plan as projected: ${c['total']:,}: aircraft ${c['aircraft']:,}, crew "
                       f"${c['crew']:,}, tomorrow ${c['tomorrow']:,}, revenue lost ${c['refunds'] + c['stranded']:,}"
                       f"{tail}.")
        if desk in (CREW, ONE):
            crew, usd = proj.day.cost, self.w.real["usd_per_pax_min"]
            out.append(f"Crew cost of the day as projected: deadhead ${usd * crew['deadhead']:,.0f}, hotel "
                       f"${usd * crew['hotel']:,.0f}, end of day ${usd * crew['end']:,.0f}{tail}.")
        if desk in (CUSTOMER, ONE):
            d = proj.day.run.pax
            o = d.outcomes()
            out.append(f"Passengers as projected: of {sum(o.values()):,} booked today {d.misconnected():,} misconnect, "
                       f"{o[pax.REBOOKED]:,} rebooked the same day, {o[pax.STRANDED]:,} on a later day (revenue lost "
                       f"${c['stranded']:,}), {o[pax.REFUNDED]:,} refunded (${c['refunds']:,}).")
        return out

    def _finish(self) -> None:
        super()._finish()
        r, g = self.w.real, self.grade
        refs = r["refs"]
        a = self.airline(self.truth)
        c = realworld.jetblue(a, r["take"], r["save"])
        g["P"] = {"cost": g["cost"], "reward": g["reward"], "reward_L": g["reward_L"], "best": g["best"]}
        g.update(cost=round(c, 1), reward=round(realworld.reward(c, refs["top"], refs["jetblue"]), 6), reward_L=None,
                 best=refs["top"], anchor=refs["jetblue"], below_best=c < refs["top"] - 0.5, criterion=CRITERION,
                 realistic={"airline": a, "take": r["take"], "save": r["save"], "refs": refs,
                            "usd": round(c * r["usd_per_pax_min"])})
