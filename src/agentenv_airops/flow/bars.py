"""The Command Center premise check's bars, exactly as pre-registered (X6, P11, P13, P14), from each day-hub's costs.

C1 room vs the real FAA: the FAA's programs as they ran cost at least C1_OVER more than hindsight H (system cost).
C2 forecast skill: TAF-based beats reactive by at least POINTS points of avoidable share, (naive - cost) / (naive - H).
C3 the link: with both channels on, joint cost beats both off by at least POINTS points of joint avoidable share,
(joint naive - joint) / (joint naive - joint hindsight). Each passes on DAYS of the 20 day-hubs or more. A day whose
naive equals its hindsight has no share and fails C2 and C3 (C1 stays defined); a solve that timed out, the joint
hindsight or a desk watch of the run with both channels on or off, fails the day's C3. LB, the relaxation below
every policy, stands in for H in a second reading of C1 and C2 that is only reported (`flip` marks a day whose
verdict it changes). A failed guard means stop whatever the bars say.
"""

from collections.abc import Iterable, Mapping

C1_OVER = 0.15
POINTS = 5.0
DAYS = 12


def share(naive: float, cost: float, floor: float) -> float | None:
    return None if naive == floor else (naive - cost) / (naive - floor)


def c1(day: Mapping) -> dict:
    h, lb, faa = day["hindsight"], day["lb"], day["faa"]
    over, over_lb = (faa - h) / h, (faa - lb) / lb
    ok, ok_lb = over >= C1_OVER, over_lb >= C1_OVER
    return {"over": over, "pass": ok, "over_lb": over_lb, "flip": ok != ok_lb,
            "split_over": (day["split"] - h) / h}


def c2(day: Mapping) -> dict:
    n, h, lb = day["naive"], day["hindsight"], day["lb"]
    taf, react = share(n, day["taf"], h), share(n, day["reactive"], h)
    gain = None if taf is None else 100 * (taf - react)
    taf_lb, react_lb = share(n, day["taf"], lb), share(n, day["reactive"], lb)
    gain_lb = None if taf_lb is None else 100 * (taf_lb - react_lb)
    ok = gain is not None and gain >= POINTS
    return {"taf": taf, "reactive": react, "gain": gain, "pass": ok, "gain_lb": gain_lb,
            "flip": ok != (gain_lb is not None and gain_lb >= POINTS)}


def c3(runs: Mapping[str, Mapping]) -> dict:
    """runs: naive, hindsight, off, a, b, ab, each its joint result (a time-out flagged `timeout`)."""
    hind = runs["hindsight"]
    timeouts = sorted(k for k in ("hindsight", "off", "a", "b", "ab")
                      if runs[k].get("timeout") or (k == "hindsight" and not runs[k].get("proven")))
    n = runs["naive"]["joint"]
    floor = hind.get("joint")
    shares = {k: None if runs[k].get("timeout") or floor is None else share(n, runs[k]["joint"], floor)
              for k in ("off", "a", "b", "ab")}

    def gain(k: str) -> float | None:
        return None if shares[k] is None or shares["off"] is None else 100 * (shares[k] - shares["off"])

    fatal = [k for k in timeouts if k in ("hindsight", "off", "ab")]
    together = gain("ab")
    return {"shares": shares, "gain": together, "gain_a": gain("a"), "gain_b": gain("b"), "timeouts": timeouts,
            "pass": not fatal and together is not None and together >= POINTS}


def tally(days: Iterable[Mapping], guards_pass: bool) -> dict:
    days = list(days)
    out = {}
    for bar in ("c1", "c2", "c3"):
        passed = sum(1 for d in days if d[bar]["pass"])
        out[bar] = {"days": passed, "of": len(days), "needed": DAYS, "pass": passed >= DAYS}
    if not guards_pass or not out["c1"]["pass"] or not out["c2"]["pass"]:
        reading = "stop"
    elif out["c3"]["pass"]:
        reading = "build both seats"
    else:
        reading = "Edgar decides on an FAA-only v2"
    return {**out, "guards_pass": guards_pass, "reading": reading}
