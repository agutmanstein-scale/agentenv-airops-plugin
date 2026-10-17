"""One playable day on the programs world (docs/v3/DESIGN.md §5), pure and deterministic: v1's day.py ported onto
programs.sim, the follower's day as known and its freeze.

The desk plays the follower's watches (clock.watch_times). At a watch t the day so far is history and the rest is the
day as known: the plan runs as `references.so_far` has it at the clock (a flight that has left on the truth by then
left when it did, as a desk would know, every other one no earlier than the clock) in `sim.known(task, t)` (delays
known LOOK ahead, the programs as issued). From the
second watch on, what that projection has leaving before the freeze line t + FREEZE commits to fly, every
cancellation is final, and a projected auto-cancel decided before the line stands (`references.frozen`, kept as a
union over the watches); an open flight the plan would have leaving before the line (at the first watch, before the
clock) is held to it, as the follower's watch solve starts every open flight at the line. What is frozen and what has
gone by the clock stands: the sim reroutes the tails on every cancellation, the day so far included, so a change that
would move such a flight's outcome, departure or aircraft is refused, in the projection and on the truth the grade
prices (a flight gone on the truth by the clock), and a decision never reaches back past it.

Actions are v1's: cancel, hold (not_before) and restore, read by v1's `day.read_changes` and confirmed as plan entries
`{"cancel": True, "decided": minute}` and `{"not_before": minute}`, and, with a finite reserve pool at a crew base
(docs/v3/DESIGN.md §3.4), call_reserve: the plan entry gains `"reserve": True`, so the flight may leave its base past
its crew deadline on a reserve crew, and release_reserve drops the call. A call is refused for a flight that doesn't
leave a base with a pool, a cancelled one, or one the pool has no crew left for (it would take one from a flight already
holding it: sim.granted grants in flight-id order); a hold or a restore keeps the call, a cancellation drops it, and a
call stands, like any change, once its flight is frozen or gone. In a batch, as in v1, a flight's last change of each
kind counts (cancel, restore or hold; call or release); releases go first and calls last, so one batch can move a
reserve from one flight to another, and a flight both cancelled and called keeps the cancellation. Every tool call (the
situation, a check, a confirm) costs CALL_MIN minutes of the watch's virtual time and acts at the clock's minute when it
starts, so a cancellation is priced from the minute it was confirmed; when the clock reaches the next watch, the watch
ends by itself, and the last watch ends STEP minutes on with the day. advance() ends the watch at once. The news, the
hubs' advisories, is queued at its watch (clock.timetable) for the desk to drain. The grade runs the final plan on the
truth (grade.grade)."""

import json
from collections.abc import Callable, Mapping

from ..core.known import FREEZE, STEP
from ..core.model import hhmm
from ..core.sim import AUTO_CANCELLED, CANCELLED, FLOWN
from ..day import USAGE, ChangeError, _flight, fid, read_changes
from ..programs import references, sim
from ..programs.model import Task
from . import situation as view
from .clock import CALL_MIN, News, news, timetable
from .grade import grade

DESK = "Duty manager"


class Day:
    def __init__(self, task: Task, anchor: int | None = None, follower: int | None = None):
        """`anchor` is the anchor's cost when known (the grade solves it otherwise); `follower` the follower's, for the
        grade to report alongside."""
        self.task = task
        self.anchor, self.follower = anchor, follower
        self.watches = timetable(task)
        self.items: dict[str, News] = {n.id: n for n in news(task)}
        self.watch = 0
        self.elapsed = 0
        self.calls = 0
        self.plan: dict[int, dict] = {}
        self.frozen: set[int] = set()
        self.delivered: list[str] = []
        self.queue: list[News] = []
        self.log: list[dict] = []
        self.changes: list[dict] = []
        self.refusals: list[dict] = []
        self.freezes = [{"watch": 0, "t": task.start, "line": None, "frozen": []}]
        self.done = False
        self.end_reason: str | None = None
        self.result: dict | None = None
        self._worlds: dict[int, sim.World] = {}
        self._deliver("load")

    @property
    def t(self) -> int:
        return self.watches[self.watch].t

    @property
    def clock(self) -> int:
        """The virtual minute: the watch's time plus the minutes its calls have used."""
        return self.t + self.elapsed

    @property
    def line(self) -> int | None:
        """The freeze line; none at the first watch, where the desk sets the day up."""
        return self.t + FREEZE if self.t > self.task.start else None

    @property
    def last(self) -> bool:
        return self.watch == len(self.watches) - 1

    @property
    def ends(self) -> int:
        """The minute the watch's time runs out: the next watch, or STEP after the last."""
        return self.t + STEP if self.last else self.watches[self.watch + 1].t

    def known(self) -> sim.World:
        if self.watch not in self._worlds:
            self._worlds[self.watch] = sim.known(self.task, self.t)
        return self._worlds[self.watch]

    def project(self, plan: Mapping[int, Mapping]) -> sim.Execution:
        """`plan` run on the day as known at the watch, the day so far (to the clock) as history."""
        return sim.execute(self.task, references.so_far(self.task, plan, self.clock), self.known())

    def situation(self) -> dict:
        return self._call(lambda at: view.build(self))

    def text(self) -> str:
        return view.render(self.task, view.build(self))

    def check(self, raw) -> dict:
        def act(at: int) -> dict:
            plan, _, _, refused, problems, held = self._merge(raw, at)
            now, then = self.project(self.plan), self.project(plan)
            return {"cost": then.cost.to_dict(), "cost_now": now.cost.total, "flights": self._moved(now, then, plan),
                    "auto_cancelled": _auto(then), "held_to_freeze_line": [fid(i) for i in held],
                    "refused": refused, "problems": problems}

        return self._call(act)

    def confirm(self, raw) -> dict:
        def act(at: int) -> dict:
            plan, confirmed, unchanged, refused, problems, held = self._merge(raw, at)
            for i in sorted(set(confirmed) | set(held)):
                self.changes.append({"watch": self.watch, "t": at, "flight": i, "was": self.plan.get(i, {}),
                                     "now": plan.get(i, {}), "by": "desk" if i in confirmed else "freeze_line"})
            self.plan = dict(sorted(plan.items()))
            self.refusals += [{"watch": self.watch, **r} for r in refused]
            run = self.project(self.plan)
            return {"confirmed": [fid(i) for i in confirmed], "unchanged": [fid(i) for i in unchanged],
                    "held_to_freeze_line": [fid(i) for i in held], "refused": refused, "problems": problems,
                    "cost": run.cost.to_dict(), "auto_cancelled": _auto(run)}

        return self._call(act)

    def advance(self) -> dict:
        """End the watch now: on to the next, or, after the last, the end of the day and its grade."""
        if self.done:
            return {"error": "the day is over"}
        return self._next("done")

    def drain(self) -> list[dict]:
        """The news delivered since the last drain."""
        out, self.queue = [n.to_dict() for n in self.queue], []
        return out

    def _call(self, act: Callable[[int], dict]) -> dict:
        if self.done:
            return {"error": "the day is over"}
        try:
            out = act(self.clock)
        except ChangeError as e:
            out = {"error": str(e)}
        self.calls += 1
        self.elapsed += CALL_MIN
        out["now"] = hhmm(self.clock)
        if self.clock >= self.ends:
            out["watch_over"] = self._next("time")
        return out

    def _next(self, reason: str) -> dict:
        if self.last:
            return self.finish(reason)
        self.watch += 1
        self.elapsed = 0
        if self.line is not None:
            self.frozen |= references.frozen(self.project(self.plan), self.t)
        self.freezes.append({"watch": self.watch, "t": self.t, "line": self.line, "frozen": sorted(self.frozen)})
        got = self._deliver("watch")
        return {"watch": self.watch, "time": hhmm(self.t), "freeze_line": _time(self.line),
                "frozen": len(self.frozen), "news": got}

    def _deliver(self, via: str) -> list[str]:
        got = list(self.watches[self.watch].news)
        for n in got:
            self.delivered.append(n)
            self.queue.append(self.items[n])
            self.log.append({"news_id": n, "watch": self.watch, "t": self.t, "at": self.items[n].at, "via": via})
        return got

    def finish(self, reason: str) -> dict:
        self.done, self.end_reason = True, reason
        self.result = grade(self.task, self.plan, self.anchor, self.follower)
        return {"done": True, "end_reason": reason, "cost": self.result["cost"]["total"],
                "reward": self.result["reward"]}

    def _merge(self, raw, at: int) -> tuple[dict, list[int], list[int], list[dict], list[str], list[int]]:
        """The plan with `raw`'s changes, one at a time: a change to a frozen flight, or one that would move a frozen
        flight or one gone by `at` in the projection or on the truth (`sim.routes` reroutes the tails on every
        cancellation, the day so far included), is refused; what is left open before the line is held to it."""
        rows, problems = _read(self.task, raw)
        plan, confirmed, unchanged, refused, held = dict(self.plan), [], [], [], set()
        base, truth = self.project(self.plan).flights, sim.execute(self.task, self.plan).flights
        pinned = self.frozen | _gone(base, at)
        gone = _gone(truth, at)
        for i, entry in rows:
            if (_called(plan.get(i)) == entry["reserve"] if "reserve" in entry else _entry(self.plan.get(i)) == entry):
                unchanged.append(i)
                continue
            if i in pinned or i in gone:
                why = self._pinned_reason(i, base[i] if i in pinned else truth[i], at)
                refused.append({"flight": fid(i), "from": DESK, "reason": why})
                continue
            then = dict(plan)
            old = then.pop(i, None) or {}
            kept = {"reserve": True} if old.get("reserve") else {}
            if entry.get("reserve"):
                if why := self._uncallable(i, plan):
                    refused.append({"flight": fid(i), "from": DESK, "reason": why})
                    continue
                then[i] = {**old, "reserve": True}
            elif "reserve" in entry:
                if rest := {k: v for k, v in old.items() if k != "reserve"}:
                    then[i] = rest
            elif entry["cancel"]:
                then[i] = {"cancel": True, "decided": at}
            elif entry["not_before"] is not None:
                then[i] = {"not_before": entry["not_before"]} | kept
            elif kept:
                then[i] = kept
            early, run = self._settle(then, pinned, at)
            after = sim.execute(self.task, then).flights
            moved = [(k, base) for k in sorted(pinned) if _fixed(run.flights[k]) != _fixed(base[k])] + \
                    [(k, truth) for k in sorted(gone) if _fixed(after[k]) != _fixed(truth[k])]
            if moved:
                k, runs = moved[0]
                refused.append({"flight": fid(i), "from": DESK, "reason": self._moves_reason(i, k, runs, at)})
                continue
            plan = then
            confirmed.append(i)
            held.update(early)
        return plan, confirmed, unchanged, refused, problems, sorted(held)

    def _settle(self, plan: dict, pinned: set[int], at: int) -> tuple[list[int], sim.Execution]:
        """Holds every open flight `plan` has leaving before the freeze line (at the first watch, the clock) to it;
        returns them and the plan's projection."""
        line = max(at, self.line if self.line is not None else at)
        held: set[int] = set()
        while True:
            run = self.project(plan)
            early = [i for i, r in run.flights.items() if r.status == FLOWN and i not in pinned and r.dep < line]
            if not early:
                return sorted(held), run
            for i in early:
                plan[i] = {**plan.get(i, {}), "not_before": line}
            held.update(early)

    def _uncallable(self, i: int, plan: Mapping[int, Mapping]) -> str | None:
        """Why a reserve can't be called for i under `plan`, or None."""
        task, f = self.task, self.task.flight(i)
        name = f"{fid(i)} ({f.flight} {f.orig}-{f.dest})"
        if not task.reservable(f):
            return f"{name} doesn't leave a crew base with a reserve pool: no reserve to call."
        if (plan.get(i) or {}).get("cancel"):
            return f"{name} is cancelled: restore it before calling a reserve."
        held = sim.granted(task, plan)
        after = sim.granted(task, {**plan, i: {**plan.get(i, {}), "reserve": True}})
        if i not in after or not held <= after:
            on = " ".join(fid(j) for j in sorted(held) if task.flight(j).orig == f.orig)
            pool = task.layers.reserves[f.orig]
            return f"{name}: no reserve crew is left at {f.orig}: " + (
                f"its pool of {pool} is on {on}; release one first." if on else "its pool is empty.")
        return None

    def _pinned_reason(self, i: int, r: sim.FlightRun, at: int) -> str:
        if i in self.frozen:
            return self._frozen_reason(i, r)
        f = self.task.flight(i)
        how = f"it left at {hhmm(r.dep)}" if r.status == FLOWN else f"it auto-cancelled at {hhmm(r.decided)}"
        return f"{fid(i)} ({f.flight} {f.orig}-{f.dest}) is gone: {how}, before now ({hhmm(at)}); it stands."

    def _moves_reason(self, i: int, k: int, base: Mapping[int, sim.FlightRun], at: int) -> str:
        f = self.task.flight(i)
        return f"{fid(i)} ({f.flight} {f.orig}-{f.dest}) stays as it is: the change would reroute the aircraft and " \
               f"move {fid(k)}, which can't move. {self._pinned_reason(k, base[k], at)}"

    def _frozen_reason(self, i: int, r: sim.FlightRun) -> str:
        f = self.task.flight(i)
        if r.status == FLOWN:
            how = f"the plan has it leaving at {hhmm(r.dep)}"
        elif r.status == CANCELLED:
            how = "it is cancelled, and a cancellation is final"
        else:
            how = f"it can't leave in time and auto-cancels at {hhmm(r.decided)}"
        return f"{fid(i)} ({f.flight} {f.orig}-{f.dest}) is frozen: {how}, before the freeze line at " \
               f"{hhmm(self.line)}; it stands."

    def _moved(self, now: sim.Execution, then: sim.Execution, plan: Mapping[int, Mapping]) -> list[str]:
        """The flights the changes alter, as they would be (was: as they are)."""
        out = []
        for i in sorted(now.flights):
            a, b = now.flights[i], then.flights[i]
            if ((a.status, a.dep, a.arr) != (b.status, b.dep, b.arr) or _entry(self.plan.get(i)) != _entry(plan.get(i))
                    or _called(self.plan.get(i)) != _called(plan.get(i))):
                f = self.task.flight(i)
                out.append(f"{fid(i)} {f.flight} {f.orig}-{f.dest} {view.plan_text(plan.get(i))} "
                           f"{view.proj_text(self.task, b)} (was {view.plan_text(self.plan.get(i))} "
                           f"{view.proj_text(self.task, a)})")
        return out

    def data(self) -> dict:
        return {"task_id": self.task.task_id, "watches": len(self.watches), "watch": self.watch, "t": self.t,
                "clock": self.clock, "calls": self.calls, "done": self.done, "end_reason": self.end_reason,
                "plan": [{"flight": i, **e} for i, e in sorted(self.plan.items())], "frozen": sorted(self.frozen),
                "notices": self.log, "freezes": self.freezes, "changes": self.changes, "refusals": self.refusals,
                "grade": self.result}


def changes_to(day: Day, target: Mapping[int, Mapping]) -> list[dict]:
    """The changes that make the day's plan `target` (programs plan entries by flight id) on the flights that aren't
    frozen."""
    want = {int(i): _entry(e) for i, e in target.items()}
    calls = {int(i) for i, e in target.items() if e.get("reserve") and not e.get("cancel")}
    rows = []
    for i in sorted(set(day.plan) | set(want)):
        if i in day.frozen:
            continue
        a, b = _entry(day.plan.get(i)), want.get(i, _entry(None))
        if a != b:
            action = "cancel" if b["cancel"] else "restore" if a["cancel"] else "hold"
            rows.append({"flight": fid(i), "action": action, "not_before": _time(b["not_before"])})
        if i in calls and not _called(day.plan.get(i)):
            rows.append({"flight": fid(i), "action": CALL})
        elif i not in calls and _called(day.plan.get(i)) and not b["cancel"]:
            rows.append({"flight": fid(i), "action": RELEASE})
    return rows


def play(task: Task, policy: Callable[[Day], Mapping[int, Mapping]], anchor: int | None = None) -> Day:
    """The day a scripted policy (the plan it wants at each watch) plays: one confirm per watch, then advance."""
    day = Day(task, anchor)
    while not day.done:
        if rows := changes_to(day, policy(day)):
            day.confirm(rows)
        if not day.done:
            day.advance()
    return day


CALL, RELEASE = "call_reserve", "release_reserve"
V1_ACTIONS, ACTIONS = "cancel, restore or hold", "cancel, restore, hold, call_reserve or release_reserve"


def _read(task: Task, raw) -> tuple[list[tuple[int, dict]], list[str]]:
    """v1's read_changes, plus call_reserve and release_reserve rows ({"flight": "f3", "action": "call_reserve"}) as
    `{"reserve": True}` and `{"reserve": False}`: a flight's last row of each kind counts, the releases first, v1's
    changes next, the calls last, each by flight; a call on a flight the batch cancels is dropped."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ChangeError(f"changes are not valid JSON ({e.msg}): send a list like {USAGE}") from None
    if isinstance(raw, dict) and isinstance(raw.get("changes"), list):
        raw = raw["changes"]
    if not isinstance(raw, list):
        raise ChangeError(f"changes must be a list like {USAGE}")
    ours = [isinstance(c, dict) and c.get("action") in (CALL, RELEASE) for c in raw]
    rows, problems = read_changes(task, [c for c, o in zip(raw, ours, strict=True) if not o])
    problems = [p.replace(V1_ACTIONS, ACTIONS) for p in problems]
    reserve: dict[int, bool] = {}
    for c in (c for c, o in zip(raw, ours, strict=True) if o):
        i = _flight(task, c.get("flight"))
        if i is None:
            problems.append(f"unknown flight {json.dumps(c.get('flight'))}: use the ids from the flight table (f0, f1, "
                            "...)")
            continue
        if i in reserve:
            problems.append(f"{fid(i)} has more than one reserve call or release: the last counts")
        reserve[i] = c["action"] == CALL
    for i, e in rows:
        if e["cancel"] and reserve.get(i):
            problems.append(f"{fid(i)} is cancelled and calls a reserve: the cancellation counts and drops the call")
            del reserve[i]
    return ([(i, {"reserve": False}) for i, r in sorted(reserve.items()) if not r] + rows
            + [(i, {"reserve": True}) for i, r in sorted(reserve.items()) if r]), problems


def _gone(flights: Mapping[int, sim.FlightRun], at: int) -> set[int]:
    """The flights a run has gone before `at`: left, or auto-cancelled."""
    return {i for i, r in flights.items()
            if (r.status == FLOWN and r.dep < at) or (r.status == AUTO_CANCELLED and r.decided < at)}


def _fixed(r: sim.FlightRun) -> tuple:
    """What a frozen or past flight keeps: its outcome, its departure and its aircraft."""
    return r.status, r.dep, r.tail


def _entry(e: Mapping | None) -> dict:
    """A plan entry as an action reads it: cancelled, or flying no earlier than `not_before`."""
    cancel = bool(e and e.get("cancel"))
    return {"cancel": cancel, "not_before": None if cancel or not e else e.get("not_before")}


def _called(e: Mapping | None) -> bool:
    return bool(e and e.get("reserve"))


def _time(minute: int | None) -> str | None:
    return None if minute is None else hhmm(minute)


def _auto(run: sim.Execution) -> list[str]:
    return [fid(i) for i, r in run.flights.items() if r.status == AUTO_CANCELLED]
