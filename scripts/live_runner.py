"""A model's three-desk storm days on airops-occ3 back to back, for the live stream. Each day is one `agent-env run` of
its task, played as the board's pilot plays one (occ.sweep: the same bundle, caps, rows, days and transcripts, under
results/runs/<name>), and starts on a grid of slots, after an intermission until the last day's slot ends, only while
the spend so far plus a worst day stays within the budget. current.json tells the live server the running day, its
instance as soon as agent-env records it and its env once deployed. A day that fails is tried once more, then skipped;
one whose audit refuses what the desks did is final, as on the board. stop.flag (beside current.json) or SIGTERM
finishes the day and exits; a second SIGTERM stops the day now. The settings and the days' order are
scripts/live_runner.toml, read again before every day.

usage: .venv/bin/python scripts/live_runner.py run [--config TOML] [--current PATH] [--policy script] [--name NAME]
                                                   [--days N] [--dry-run]
       .venv/bin/python scripts/live_runner.py order [--write scripts/live_runner.toml]
"""

import argparse
import gzip
import json
import os
import re
import signal
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, date, datetime
from pathlib import Path

from agent_env.bundle.parse import BundleKind, parse_bundle
from agent_env.store.routing import namespace_routing
from agent_env.task.store import get_task_instance_store

from agentenv_airops.occ import sweep
from agentenv_airops.occ.pack import Pack3

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("live_runner.toml")
HUBS = ("JFK", "BOS")
WINTER = (11, 12, 1, 2, 3)
ATTEMPTS = 2
POLL_SECONDS = 5.0
IDLE_SECONDS = 10.0
KILL_AFTER_SECONDS = 300.0
SPENT = re.compile(r"request \d+: [^\n]*?\$([0-9.]+) spent")
STAMP = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Settings:
    """live_runner.toml: the pilot, the money, the pacing and the days in play order."""

    name: str = "occ3-live-astra"
    model: str = "openai/gpt-6-astra"
    policy: str | None = None
    pack: str | None = None
    cap_joint_usd: float = 23.5
    request_margin_usd: float = 4.0
    budget_usd: float = 430.0
    worst_day_usd: float = 42.0
    typical_day_usd: float = 22.68
    slot_minutes: float = 75.0
    min_intermission_minutes: float = 5.0
    card_seconds: float = 60.0
    window_hours: float = 24.0
    start_utc: str | None = None
    longest_day_minutes: float = 150.0
    days: tuple[str, ...] = ()

    @classmethod
    def read(cls, path: Path, **overrides) -> "Settings":
        raw = tomllib.loads(path.read_text())
        if unknown := set(raw) - {f.name for f in fields(cls)}:
            raise ValueError(f"{path}: unknown settings {sorted(unknown)}")
        given = {k: v for k, v in overrides.items() if v is not None}
        s = replace(cls(**raw), days=tuple(raw.get("days", ())), **given)
        if s.policy == "llm":
            s = replace(s, policy=None)
        known = Pack3(s.pack).tasks
        if bad := [t for t in s.days if t not in known or known[t]["split"] == "control"]:
            raise ValueError(f"{path}: not a train or eval day of the pack: {', '.join(bad)}")
        if len(set(s.days)) != len(s.days):
            raise ValueError(f"{path}: a day is listed twice")
        if s.start_utc is not None:
            stamp(s.start_utc)
        return s

    def pilot(self, runs: list[str]) -> sweep.Pilot3:
        return sweep.Pilot3(self.name, self.model, runs, {"joint": self.cap_joint_usd, "one": self.cap_joint_usd},
                            self.budget_usd, self.pack, self.policy, self.request_margin_usd)


def utc(t: float | None = None) -> str:
    return time.strftime(STAMP, time.gmtime(time.time() if t is None else t))


def stamp(text: str) -> float:
    return datetime.strptime(text, STAMP).replace(tzinfo=UTC).timestamp()


# ---------------------------------------------------------------------------------------------- the days' order


def hours(spans) -> float:
    """The hours the minute spans cover, overlaps counted once."""
    total, end = 0, float("-inf")
    for a, b in sorted(spans):
        if b > end:
            total += b - max(a, end)
            end = b
    return round(total / 60, 1)


def storm(task_id: str, meta: dict, task: dict) -> dict:
    """A day's storm from its task: per hub the hours its GDPs governed departures (their segments) and its ground
    stops (count and hours), the flights JetBlue cancelled in reality, and the score: GDP hours plus twice the
    ground-stop hours at both hubs, plus one per ten flights cancelled."""
    hubs = {}
    for hub in HUBS:
        p = task["programs"].get(hub) or {"segments": [], "stops": []}
        hubs[hub] = {"gdp_h": hours((s["since"], s["until"]) for s in p["segments"]), "gs": len(p["stops"]),
                     "gs_h": hours((s["start"], s["end"]) for s in p["stops"])}
    cancelled = sum(f["actual"]["cancelled"] for f in task["flights"])
    score = sum(h["gdp_h"] + 2 * h["gs_h"] for h in hubs.values()) + cancelled / 10
    return {"task_id": task_id, "date": meta["date"], "split": meta["split"],
            "season": "winter" if int(meta["date"][5:7]) in WINTER else "summer", "hubs": hubs,
            "cancelled": cancelled, "score": round(score, 1)}


def storms(pack: Pack3) -> list[dict]:
    out = []
    for task_id, meta in pack.tasks.items():
        if meta["split"] in ("train", "eval"):
            raw = gzip.decompress((pack.path / "dates" / f"{meta['date']}.json.gz").read_bytes())
            out.append(storm(task_id, meta, json.loads(raw)["task"]))
    return out


def order(days: list[dict]) -> list[dict]:
    """The play order: the training days, which no model played on the board, then the eval days as the fallback. In
    each, a season's days by score, a day a calendar day from a higher one of the season (the same storm) after the
    others, and the seasons interleaved in proportion, the top day first."""
    return [d for split in ("train", "eval")
            for d in interleave([ranked([x for x in days if x["split"] == split and x["season"] == season])
                                 for season in ("summer", "winter")])]


def ranked(days: list[dict]) -> list[dict]:
    lead, later = [], []
    for d in sorted(days, key=lambda d: (-d["score"], d["date"])):
        near = any(abs((date.fromisoformat(d["date"]) - date.fromisoformat(x["date"])).days) <= 1 for x in lead)
        (later if near else lead).append(d)
    return lead + later


def interleave(lists: list[list[dict]]) -> list[dict]:
    """Each next day from the list furthest behind its share of the whole, ties to the higher score."""
    taken, out = [0] * len(lists), []
    while left := [i for i, x in enumerate(lists) if taken[i] < len(x)]:
        i = min(left, key=lambda i: ((taken[i] + 0.5) / len(lists[i]), -lists[i][taken[i]]["score"]))
        out.append(lists[i][taken[i]])
        taken[i] += 1
    return out


def describe(d: dict) -> str:
    parts = []
    for hub, h in d["hubs"].items():
        bits = ([f"GDP {h['gdp_h']:g} h"] if h["gdp_h"] else []) + \
            ([f"{h['gs']} ground stop{'s' if h['gs'] > 1 else ''} {h['gs_h']:g} h"] if h["gs"] else [])
        parts.append(f"{hub} {', '.join(bits) or 'no program'}")
    return f"{d['season']}; {'; '.join(parts)}; {d['cancelled']} cancelled"


HEAD = """\
# The live runner's settings (scripts/live_runner.py), read again before every day: edits apply from the next day on.
name = "occ3-live-astra"          # results/runs/<name>: pilot.json, results.jsonl, days/, transcripts/, logs/
model = "openai/gpt-6-astra"
# policy = "script"                 # the env's scripted desks, $0
cap_joint_usd = 23.5              # each desk's cap, as registered for the board
request_margin_usd = 4.0          # one worst request a desk may make past its cap
budget_usd = 430.0                # model spend may not pass it
worst_day_usd = 42.0              # a day starts only if the spend so far plus this fits (the board's worst: $41.46)
typical_day_usd = 22.68           # the board's mean Astra day: --dry-run's projection only
slot_minutes = 75.0               # a day that ends early is followed by an intermission until its slot ends
min_intermission_minutes = 5.0    # after a day past its slot, or a failed attempt
card_seconds = 60.0               # status "done" for this long after a day, then "intermission"
window_hours = 24.0               # a day starts only if its slot ends within the window
# start_utc = "2026-10-23T16:00:00Z"  # the first day's start; default: when the runner first runs
longest_day_minutes = 150.0       # a day still running then is stopped (the board's longest: 84)

# The days in play order (`live_runner.py order`): the training days by storm (GDP hours, twice the ground-stop hours,
# at JFK and BOS, plus one per ten flights cancelled), each season by score with a storm's second day after the rest,
# the seasons interleaved; then the eval days the board played, the same way. Reorder or delete freely.
"""


def config_text(days: list[dict]) -> str:
    lines, split = [HEAD + "days = ["], None
    for i, d in enumerate(days, 1):
        if d["split"] != split:
            split = d["split"]
            lines.append("  # training days" if split == "train" else "  # eval days: Astra played these on the board")
        lines.append(f'  "{d["task_id"]}",  # {i:2} {d["date"]} score {d["score"]:g}: {describe(d)}')
    return "\n".join(lines + ["]", ""])


# ---------------------------------------------------------------------------------------------- the money and pacing


def spend(row: dict, margin: float) -> float:
    """An attempt's model spend: each desk's own record, else its last live log line and one request past it, else
    nothing (it never made a request)."""
    live = row.get("live_usd") or {}
    return round(sum(d["cost_usd"] if d.get("cost_usd") is not None else live[r] + margin if r in live else 0.0
                     for r, d in row["desks"].items()), 6)


def spent(rows: list[dict]) -> float:
    return round(sum(r["spend_usd"] if "spend_usd" in r else r["cost_usd"] for r in rows), 6)


def affordable(so_far: float, s: Settings) -> bool:
    """The guard: a day starts only if the spend so far plus a worst day stays within the budget."""
    return so_far + s.worst_day_usd <= s.budget_usd + 1e-9


def over(so_far: float, live: float, s: Settings) -> bool:
    """The backstop while a day runs: the spend so far and the day's live estimate reach the budget."""
    return so_far + live >= s.budget_usd


def grouped(rows: list[dict]) -> dict[str, list[dict]]:
    out = {}
    for r in rows:
        out.setdefault(r["run"], []).append(r)
    return out


def settled(attempts: list[dict]) -> str | None:
    """scored or unscored (the audit refused what the desks did), skipped after ATTEMPTS failed, else None."""
    for r in attempts:
        if r["outcome"] in ("scored", "unscored"):
            return r["outcome"]
    return "skipped" if len(attempts) >= ATTEMPTS else None


def pending(s: Settings, rows: list[dict]) -> list[str]:
    groups = grouped(rows)
    return [t for t in s.days if settled(groups.get(f"{t}-joint", [])) is None]


def history(rows: list[dict]) -> list[dict]:
    out = []
    for run, attempts in grouped(rows).items():
        if (state := settled(attempts)) is None:
            continue
        last = next((r for r in attempts if r["outcome"] == state), attempts[-1])
        out.append({"date": last.get("date"), "task_id": run.rsplit("-", 1)[0], "run_id": f"{run}-a{last['attempt']}",
                    "instance_id": last["instance"], "outcome": state, "reward": last["reward"],
                    "reward_L": last["reward_L"], "cost_usd": round(spent(attempts), 4),
                    "wall_seconds": last["wall_seconds"], "attempts": len(attempts)})
    return out


def next_start(s: Settings, last: dict | None, window_start: float, now: float) -> float:
    """The next attempt's start: the window's; after a day played, its slot's end, or the least intermission after
    it if it ran past; after a failed attempt, the least intermission."""
    if last is None:
        return max(now, window_start)
    after = stamp(last["ended_utc"]) + 60 * s.min_intermission_minutes
    if last["outcome"] == "failed":
        return max(now, after)
    return max(now, after, stamp(last["started_utc"]) + 60 * s.slot_minutes)


def projection(s: Settings, rows: list[dict], window_start: float, now: float) -> tuple[list[dict], str]:
    """The days ahead as they would start if each took its slot and cost the typical day, and why the last one is the
    last."""
    start, so_far, end = next_start(s, rows[-1] if rows else None, window_start, now), spent(rows), \
        window_start + 3600 * s.window_hours
    plan = []
    for task_id in pending(s, rows):
        if not affordable(so_far, s):
            return plan, f"the budget: ${so_far:.2f} spent + a worst day ${s.worst_day_usd:g} > ${s.budget_usd:g}"
        if start + 60 * s.slot_minutes > end + 1e-6:
            return plan, f"the window: the next slot would end after {utc(end)}"
        plan.append({"task_id": task_id, "starts": start, "spent_before": round(so_far, 2)})
        start, so_far = start + 60 * s.slot_minutes, so_far + s.typical_day_usd
    return plan, "the order: no days left"


# ---------------------------------------------------------------------------------------------- the running day


def bundle_task(bundle: Path, run: str) -> str:
    """The id agent-env gives the bundle's task `run`, which its instances carry."""
    return next(e.id for e in parse_bundle(bundle.absolute()).entries if e.kind is BundleKind.TASK and e.name == run)


def instances(task: str) -> list[str]:
    with namespace_routing():
        return [i.instance_id for i in get_task_instance_store().find(task, limit=10)]


def endpoints(ctx: dict) -> dict | None:
    for e in ctx.get("deployed_envs") or []:
        if e.get("mcp_url"):
            return {"base": e["mcp_url"].removesuffix("/mcp"), "mcp_url": e["mcp_url"],
                    "gateway_url": e.get("gateway_url")}
    return None


def desk_spend(ctx: dict) -> dict[str, float]:
    """Each deployed desk's spend so far: the last "request N: … $X spent" line of its container's log."""
    out = {}
    for a in ctx.get("deployed_agents") or []:
        if not (a.get("role") and a.get("sandbox_id")) or a.get("sandbox_type") not in (None, "local"):
            continue
        try:
            logs = subprocess.run(["docker", "logs", "--tail", "400", f"agent-{a['sandbox_id']}"], capture_output=True,
                                  text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found := SPENT.findall(logs.stdout + logs.stderr):
            out[a["role"]] = float(found[-1])
    return out


def kill(pid: int, sig: int) -> None:
    """SIGTERM to `agent-env run` alone, which cancels the day and tears its sandboxes down itself; SIGKILL to its
    group."""
    try:
        (os.killpg if sig == signal.SIGKILL else os.kill)(pid, sig)
    except ProcessLookupError:
        pass


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    ps = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return "agent_env.cli" in ps.stdout


class Runner:
    def __init__(self, config: Path, current: Path | None, overrides: dict, limit: int | None):
        self.config, self.overrides, self.limit = config, overrides, limit
        self.s = Settings.read(config, **overrides)
        self.out = sweep.RUNS / self.s.name
        self.current = (current or self.out / "current.json").absolute()
        self.flag = self.current.with_name("stop.flag")
        self.signals, self.started = 0, set()
        old = json.loads(self.current.read_text()) if self.current.is_file() else {}
        self.doc = {k: old.get(k) for k in ("instance_id", "run_id", "date", "task_id", "started_utc", "attempt",
                                            "day", "env", "day_file", "result")}
        self.seq = old.get("seq") or 0

    # --------------------------------------------------------------------------------------- bookkeeping

    def log(self, text: str) -> None:
        print(f"[{utc()}] {text}", flush=True)

    def on_signal(self, signum, _frame) -> None:
        self.signals += 1
        self.log(f"signal {signum}: " + ("finishing the day, then stopping (again to stop the day now)"
                                         if self.signals == 1 else "stopping the day now"))

    def stopping(self) -> bool:
        return self.signals > 0 or self.flag.exists()

    def reload(self) -> None:
        try:
            s = Settings.read(self.config, **self.overrides)
        except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as e:
            self.log(f"keeping the settings: {self.config} doesn't read: {e}")
            return
        if s.name != self.s.name:
            self.log(f"keeping the pilot {self.s.name!r}: the name can't change while it runs")
            s = replace(s, name=self.s.name)
        self.s = s

    def state(self) -> dict:
        path = self.out / "live_state.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def save(self, **changes) -> None:
        state = self.state() | changes
        self.out.mkdir(parents=True, exist_ok=True)
        tmp = self.out / "live_state.json.tmp"
        tmp.write_text(json.dumps(state, indent=1) + "\n")
        os.replace(tmp, self.out / "live_state.json")

    def publish(self, rows: list[dict], **changes) -> None:
        self.seq += 1
        self.doc.update(changes)
        self.doc.update(model=self.s.model, mode="joint", pilot=self.s.name, policy=self.s.policy or "llm",
                        budget_usd=self.s.budget_usd, spent_usd=round(spent(rows), 4),
                        history=history(rows), seq=self.seq, updated_utc=utc())
        self.doc.setdefault("final", False)
        self.current.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.current.with_name(self.current.name + ".tmp")
        tmp.write_text(json.dumps(self.doc, indent=1) + "\n")
        os.replace(tmp, self.current)

    def instances(self, task: str) -> list[str]:
        try:
            return instances(task)
        except Exception as e:
            self.log(f"the store didn't read: {e!r}")
            return []

    def dates(self) -> dict[str, str]:
        return {t: v["date"] for t, v in Pack3(self.s.pack).tasks.items()}

    # --------------------------------------------------------------------------------------- the loop

    def run(self) -> int:
        window = stamp(self.save_window())
        if running := self.state().get("running"):
            if any(r["run"] == running["run"] and r["attempt"] == running["attempt"]
                   for r in sweep.results(self.out)):
                self.save(running=None)
            else:
                self.log(f"picking up {running['run']} a{running['attempt']}, pid {running['pid']}")
                self.started.add(running["task_id"])
                self.watch(running, None)
                self.record(running, None)
        while True:
            self.reload()
            rows = sweep.results(self.out)
            left, now = pending(self.s, rows), time.time()
            last = rows[-1] if rows and "ended_utc" in rows[-1] else None
            starts = next_start(self.s, last, window, now)
            reason = None
            if not left:
                reason = "dates: no day left in the order"
            elif not affordable(spent(rows), self.s):
                reason = f"budget: ${spent(rows):.2f} spent + a worst day ${self.s.worst_day_usd:g} > " \
                         f"${self.s.budget_usd:g}"
            elif starts + 60 * self.s.slot_minutes > window + 3600 * self.s.window_hours + 1e-6:
                reason = f"window: the next slot would end after {utc(window + 3600 * self.s.window_hours)}"
            elif self.limit is not None and len(self.started) >= self.limit and left[0] not in self.started:
                reason = f"days: {self.limit} played"
            elif self.stopping():
                reason = "stopped"
            if reason:
                self.end(rows, reason)
                return 0
            task_id = left[0]
            upcoming = {"date": self.dates()[task_id], "task_id": task_id, "starts_utc": utc(starts)}
            if now < starts:
                fresh = last is not None and last["outcome"] != "failed" and \
                    now < stamp(last["ended_utc"]) + self.s.card_seconds
                self.publish(rows, status="done" if fresh else "intermission", next=upcoming)
                self.sleep(min(starts - now, IDLE_SECONDS))
                continue
            attempts = grouped(rows).get(f"{task_id}-joint", [])
            self.started.add(task_id)
            self.attempt(task_id, len(attempts) + 1, upcoming["date"])

    def save_window(self) -> str:
        state = self.state()
        window = self.s.start_utc or state.get("window_start_utc") or utc()
        if window != state.get("window_start_utc"):
            self.save(window_start_utc=window)
        return window

    def sleep(self, seconds: float) -> None:
        until = time.time() + seconds
        while time.time() < until and not self.stopping():
            time.sleep(min(1.0, max(0.0, until - time.time())))

    def end(self, rows: list[dict], reason: str) -> None:
        last = rows[-1] if rows and "ended_utc" in rows[-1] else None
        fresh = last is not None and last["outcome"] != "failed" and \
            time.time() < stamp(last["ended_utc"]) + self.s.card_seconds
        self.publish(rows, status="done" if fresh else "intermission", next=None, final=True, reason=reason)
        self.flag.unlink(missing_ok=True)
        self.log(f"finished, {reason}; spent ${spent(rows):.2f} on {len(history(rows))} days")

    def attempt(self, task_id: str, number: int, day: str) -> dict:
        s, run = self.s, f"{task_id}-joint"
        rows = sweep.results(self.out)
        self.out.mkdir(parents=True, exist_ok=True)
        pilot = s.pilot(list(dict.fromkeys([r["run"] for r in rows] + [run])))
        (self.out / "pilot.json").write_text(json.dumps(asdict(pilot), indent=2) + "\n")
        bundle = self.out / "bundle"
        sweep.KIT3.generate(bundle, task_ids=[task_id], modes=("joint",),
                            pack=Path(s.pack).expanduser() if s.pack else None, cap=s.cap_joint_usd, policy=s.policy)
        task = bundle_task(bundle, run)
        log = self.out / "logs" / f"{run}-a{number}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        running = {"task_id": task_id, "date": day, "run": run, "attempt": number, "log": str(log), "task": task,
                   "before": self.instances(task), "started_utc": utc(), "spent_before": spent(rows), "live": {},
                   "day": sum(h["outcome"] != "skipped" for h in history(rows)) + 1}
        args = [*sweep.AGENT_ENV, "run", str(bundle.absolute()), "--task", run, "--model", s.model]
        with log.open("w") as f:
            proc = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        running["pid"] = proc.pid
        self.save(running=running)
        self.log(f"start {run} a{number} ({day}), spent ${running['spent_before']:.2f} of ${s.budget_usd:g}")
        self.publish(rows, status="starting", instance_id=None, run_id=f"{run}-a{number}", date=day, task_id=task_id,
                     started_utc=running["started_utc"], attempt=number, day=running["day"], env=None, day_file=None,
                     result=None, live_usd=0.0, desks={}, next=self.following(rows, running))
        self.watch(running, proc)
        return self.record(running, proc.returncode)

    def following(self, rows: list[dict], running: dict) -> dict | None:
        """The day after the running one, were it to take its slot and cost the typical day."""
        s, window = self.s, stamp(self.state()["window_start_utc"])
        start = stamp(running["started_utc"]) + 60 * s.slot_minutes
        left = [t for t in pending(s, rows) if t != running["task_id"]]
        if not left or not affordable(running["spent_before"] + s.typical_day_usd, s) or \
                start + 60 * s.slot_minutes > window + 3600 * s.window_hours + 1e-6:
            return None
        return {"date": self.dates()[left[0]], "task_id": left[0], "starts_utc": utc(start)}

    def watch(self, running: dict, proc: subprocess.Popen | None) -> None:
        """Until the day's run ends: its instance (the store's newest of its task, new since the launch), its env, each
        desk's live spend, the backstops (the budget, the longest day, a second signal), and current.json."""
        rows, began, env = sweep.results(self.out), stamp(running["started_utc"]), None
        upcoming = self.following(rows, running)
        while proc.poll() is None if proc else alive(running["pid"]):
            try:
                if not running.get("instance"):
                    running["instance"] = next((i for i in self.instances(running["task"])
                                                if i not in running["before"]), None)
                    if running["instance"]:
                        self.log(f"instance {running['instance']}")
                ctx = sweep.context(running["instance"]) if running.get("instance") else {}
            except Exception as e:
                self.log(f"the store didn't read: {e!r}")
                ctx = {}
            env = endpoints(ctx) or env
            for role, usd in desk_spend(ctx).items():
                running["live"][role] = max(running["live"].get(role, 0.0), usd)
            live = round(sum(running["live"].values()), 4)
            why = "the budget" if over(running["spent_before"], live, self.s) else \
                "the longest day" if time.time() - began > 60 * self.s.longest_day_minutes else \
                "a second signal" if self.signals > 1 else None
            if why and not running.get("stopped"):
                running["stopped"], running["stopped_at"] = why, time.time()
                self.log(f"stopping {running['run']} a{running['attempt']} now: {why} (live ${live:.2f})")
                kill(running["pid"], signal.SIGTERM)
            elif running.get("stopped") and time.time() - running["stopped_at"] > KILL_AFTER_SECONDS:
                kill(running["pid"], signal.SIGKILL)
            self.save(running=running)
            self.publish(rows, status="playing" if env else "starting", instance_id=running.get("instance"),
                         env=env, live_usd=live, desks=dict(running["live"]), next=upcoming)
            time.sleep(POLL_SECONDS)

    def record(self, running: dict, code: int | None) -> dict:
        log, instance = Path(running["log"]), running.get("instance")
        if instance and not sweep.INSTANCE.findall(log.read_text(errors="replace")):
            with log.open("a") as f:
                f.write(f"live_runner: {time.time() - stamp(running['started_utc']):.1f}s, instance {instance}\n")
        rows = sweep.results(self.out)
        pilot = self.s.pilot(list(dict.fromkeys([r["run"] for r in rows] + [running["run"]])))
        try:
            row = sweep.outcome(pilot, running["run"], running["attempt"], log, code, running["started_utc"])
        except Exception as e:
            self.log(f"the attempt's record didn't read: {e!r}")
            row = {"pilot": pilot.name, "run": running["run"], "attempt": running["attempt"],
                   "started_utc": running["started_utc"], "wall_seconds": None, "exit": code, "instance": instance,
                   "outcome": "failed", "failed_step": None, "error": f"live_runner: {e!r}"[:500],
                   "cost_usd": None, "desks": {r: {"cost_usd": None} for r in running["live"]}, "absent": {},
                   "day": None, "reward": None, "reward_L": None}
        if instance and row["instance"] != instance:
            self.log(f"agent-env reported instance {row['instance']}, the store had {instance}")
        row.update(date=running["date"], ended_utc=utc(), live_usd=running["live"], stopped=running.get("stopped"))
        row["spend_usd"] = spend(row, self.s.request_margin_usd)
        if row["cost_usd"] is None:
            row["cost_usd"] = row["spend_usd"]
        with (self.out / "results.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        self.save(running=None)
        rows.append(row)
        self.log(f"{row['outcome']} {row['run']} a{row['attempt']}: reward {row['reward']}, ${row['spend_usd']:.2f}, "
                 f"{row['wall_seconds'] or 0:.0f}s{', stopped: ' + row['stopped'] if row['stopped'] else ''}"
                 f"{', ' + row['error'][:200] if row['error'] else ''}")
        done = settled(grouped(rows)[row["run"]])
        self.publish(rows, status="done" if done in ("scored", "unscored") else "intermission",
                     instance_id=row["instance"] or instance, live_usd=0.0,
                     day_file=str((self.out / row["day"]).absolute()) if row["day"] else None,
                     result=next((h for h in history(rows)
                                  if h["run_id"] == f"{row['run']}-a{row['attempt']}"), None))
        return row


def dry_run(runner: Runner) -> int:
    s, rows = runner.s, sweep.results(runner.out)
    window = stamp(s.start_utc or runner.state().get("window_start_utc") or utc())
    plan, why = projection(s, rows, window, time.time())
    by = {d["task_id"]: d for d in storms(Pack3(s.pack)) if d["task_id"] in s.days}
    print(f"pilot {s.name}: {s.model}{' (' + s.policy + ' desks)' if s.policy else ''}, caps ${s.cap_joint_usd:g} a "
          f"desk; budget ${s.budget_usd:g}, a day starts only if spent + ${s.worst_day_usd:g} fits; "
          f"{s.slot_minutes:g}-minute slots in {s.window_hours:g} h from {utc(window)}")
    print(f"spent ${spent(rows):.2f} on {len(history(rows))} days; current.json at {runner.current}")
    for i, p in enumerate(plan, 1):
        d = by[p["task_id"]]
        print(f"{i:3} {utc(p['starts'])}  {d['date']} {d['split']:5} ${p['spent_before']:7.2f}  {describe(d)}")
    print(f"{len(plan)} days at the board's typical ${s.typical_day_usd:g}; the last is the last because of {why}")
    return 0


def setting(pairs: list[str]) -> dict:
    kinds = {f.name: f.type for f in fields(Settings) if f.name != "days"}
    out = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        if key not in kinds:
            raise SystemExit(f"--set {pair}: settings are {', '.join(kinds)}")
        out[key] = float(value) if kinds[key] is float else value
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="Play the days.")
    r.add_argument("--config", type=Path, default=CONFIG)
    r.add_argument("--current", type=Path, help="Where current.json goes. Default: results/runs/<name>/current.json.")
    r.add_argument("--policy", choices=("llm", "script"), help="The desks' policy. Default: the config's.")
    r.add_argument("--name", help="The pilot's name. Default: the config's.")
    r.add_argument("--days", type=int, help="Play at most this many days, then stop.")
    r.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="Override a setting of the config (not days), e.g. slot_minutes=15. Repeatable.")
    r.add_argument("--dry-run", action="store_true", help="Print the schedule and play nothing.")
    o = sub.add_parser("order", help="Rank the pack's days by storm and print them.")
    o.add_argument("--pack", type=Path, help="Default: the packaged b6-v3.")
    o.add_argument("--write", type=Path, help="Write a config with the default settings and this order.")
    args = p.parse_args(argv)
    for key in ("config", "current", "pack", "write"):
        if getattr(args, key, None) is not None:
            setattr(args, key, getattr(args, key).expanduser().absolute())
    os.chdir(ROOT)
    if args.command == "order":
        days = order(storms(Pack3(args.pack)))
        for i, d in enumerate(days, 1):
            print(f"{i:3} {d['task_id']} {d['split']:5} {d['score']:6.1f}  {describe(d)}")
        if args.write:
            args.write.write_text(config_text(days))
        return 0
    runner = Runner(args.config, args.current, {"policy": args.policy, "name": args.name, **setting(args.set)},
                    args.days)
    if args.dry_run:
        return dry_run(runner)
    signal.signal(signal.SIGTERM, runner.on_signal)
    signal.signal(signal.SIGINT, runner.on_signal)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
