"""`agent-env airops-occ pilot`: the ops control center's days in a fixed order, one `agent-env run` at a time (or a
few), each starting only while the spend so far plus its cap stays within the budget, an attempt that ends unscored for
an infrastructure reason tried again at most twice; and the report: per run its reward and its reward against the
bound, its cost against the best plan, the bound and B0, the share of the avoidable cost it saved, how it does against
the env's scripted desks, the requests and their outcomes, and per desk its tokens, cost, turns, largest context, wall
time and end; every recorded day replayed through the env's calls and its audit checked."""

import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import click
from agent_env.config import get_config
from agent_env.store.routing import namespace_routing
from agent_env.task.store import get_task_instance_store

from .pack import Pack
from .replay import replay
from .tasks import DESKS, generate

AGENT_ENV = [sys.executable, "-m", "agent_env.cli"]
RUNS = Path("results/runs")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
INSTANCE = re.compile(r"(\d+(?:\.\d+)?)s, instance (\S+)")
VERIFIER = "airops-occ"
MAX_ATTEMPTS = 3
POLL_SECONDS = 5.0
GRADED = ("done", "stand_down", "cost_cap", "context_full", "turn_limit", "time_limit")


@dataclass(frozen=True)
class Pilot:
    name: str
    model: str
    runs: list[str]
    caps: dict[str, float]
    budget: float
    pack: str | None = None
    policy: str | None = None

    @property
    def out(self) -> Path:
        return RUNS / self.name

    def cap(self, run: str) -> float:
        """A run's most: its cap per desk times its desks."""
        mode = run.rsplit("-", 1)[1]
        return self.caps[mode] * len(DESKS[mode])


def results(out: Path) -> list[dict]:
    path = out / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def final(rows: list[dict]) -> bool:
    """Scored, or MAX_ATTEMPTS attempts."""
    return any(r["outcome"] == "scored" for r in rows) or len(rows) >= MAX_ATTEMPTS


def context(instance: str) -> dict:
    with namespace_routing():
        return get_task_instance_store().get(instance).context or {}


def stored(uri: str) -> bytes:
    with namespace_routing():
        return get_config().get_object_store_at(uri).get(uri)


def prepare(p: Pilot) -> None:
    if not NAME.fullmatch(p.name):
        raise click.UsageError("a pilot's name is 1 to 41 lowercase letters, digits and dashes")
    path = p.out / "pilot.json"
    if path.is_file():
        if json.loads(path.read_text()) != asdict(p):
            raise click.UsageError(f"{path} is another pilot: {path.read_text().strip()}")
        return
    pack = Path(p.pack).expanduser() if p.pack else None
    ids = sorted({r.rsplit("-", 1)[0] for r in p.runs})
    for mode in ("joint", "one"):
        chosen = [i for i in ids if f"{i}-{mode}" in p.runs]
        if chosen:
            generate(p.out / "bundle", task_ids=chosen, modes=(mode,), pack=pack, cap=p.caps[mode], policy=p.policy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(p), indent=2) + "\n")


def outcome(p: Pilot, run: str, number: int, log: Path, code: int, started: str) -> dict:
    """The attempt's row from the instance its run stored: the verifier's score and the day it read, and each desk's
    summary."""
    found = INSTANCE.findall(log.read_text(errors="replace"))
    wall, instance = (float(found[-1][0]), found[-1][1]) if found else (None, None)
    ctx = context(instance) if instance else {}
    m = ctx.get("metadata") or {}
    v = (m.get("verifications") or {}).get(VERIFIER) or {}
    scored = "score" in v
    mode = run.rsplit("-", 1)[1]
    desks = {}
    for role, name, _ in DESKS[mode]:
        pr = next((x for x in ctx.get("prompt_responses") or [] if x.get("step_id") == f"play-{name}"), None)
        s = (pr or {}).get("structured_output") or {}
        desks[role] = {k: s.get(k) for k in ("end_reason", "desk_turns", "turns", "tool_calls", "input_tokens",
                                             "output_tokens", "cached_tokens", "context_max", "cost_usd",
                                             "priced_cost_usd", "failed_requests", "seconds", "error")}
        if pr and pr.get("agent_trajectory_object_url"):
            path = p.out / "transcripts" / f"{run}-a{number}-{role}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(stored(pr["agent_trajectory_object_url"]))
            desks[role]["transcript"] = path.relative_to(p.out).as_posix()
    spent = sum(p.caps[mode] if d["cost_usd"] is None else d["cost_usd"] for d in desks.values())
    day = v["results"][0]["episode"] if scored else {}
    if day:
        path = p.out / "days" / f"{run}-a{number}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(day))
    failed = m.get("failed_steps") or []
    absent = {d: a["reason"] for d, a in (day.get("absent") or {}).items()}
    return {"pilot": p.name, "run": run, "attempt": number, "started_utc": started, "wall_seconds": wall,
            "exit": code, "instance": instance, "outcome": "scored" if scored else "failed",
            "failed_step": failed[-1]["step_id"] if failed else None,
            "error": (failed[-1].get("error") or "")[:500] if failed else None, "cost_usd": round(spent, 6),
            "desks": desks, "absent": absent, "day": f"days/{run}-a{number}.json" if day else None,
            "reward": v.get("score"), **{k: (day.get("grade") or {}).get(k) for k in (
                "reward_L", "cost", "best", "bound", "gap_pct", "B0", "over_best_pct", "over_bound_pct",
                "avoidable_share", "above_script", "script", "below_best")}}


def play(p: Pilot, parallel: int = 1, echo=click.echo) -> int:
    rows = results(p.out)
    spent = sum(r["cost_usd"] for r in rows)
    queue = [r for r in p.runs if not final([x for x in rows if x["run"] == r])]
    running: list[tuple] = []
    while running or queue:
        while queue and len(running) < parallel and \
                spent + sum(p.cap(r) for r, *_ in running) + p.cap(queue[0]) <= p.budget + 1e-9:
            run = queue.pop(0)
            number = len([x for x in rows if x["run"] == run]) + 1
            log = p.out / "logs" / f"{run}-a{number}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            args = [*AGENT_ENV, "run", str((p.out / "bundle").absolute()), "--task", run] + \
                (["--model", p.model] if p.model else [])
            with log.open("w") as f:
                proc = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
            running.append((run, number, log, proc, started))
            echo(f"start {run} a{number} (spent ${spent:.2f} of ${p.budget:.2f})")
        if not running:
            echo(f"stopped: the next run could take the spend past ${p.budget:.2f} (${spent:.2f} spent); "
                 f"not run: {', '.join(queue)}")
            break
        time.sleep(POLL_SECONDS)
        for item in [x for x in running if x[3].poll() is not None]:
            running.remove(item)
            run, number, log, proc, started = item
            row = outcome(p, run, number, log, proc.returncode, started)
            rows.append(row)
            spent += row["cost_usd"]
            with (p.out / "results.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
            echo(f"{row['outcome']} {run} a{number}: reward {row['reward']}, ${row['cost_usd']:.4f}, "
                 f"absent {row['absent'] or 'none'}")
            if not final([x for x in rows if x["run"] == run]):
                queue.insert(0, run)
    return 0 if all(final([x for x in rows if x["run"] == r]) for r in p.runs) else 1


def report(p: Pilot) -> dict:
    """Every scored attempt replayed and summed up."""
    pack = Pack(Path(p.pack).expanduser() if p.pack else None)
    rows = [r for r in results(p.out) if r["outcome"] == "scored"]
    out = {"pilot": asdict(p), "spent_usd": round(sum(r["cost_usd"] for r in results(p.out)), 4), "runs": {}}
    for r in rows:
        day = json.loads((p.out / r["day"]).read_text())
        again = replay(pack.world(day["task_id"]), day)
        g = day["grade"]
        out["runs"][r["run"]] = {
            **{k: r[k] for k in ("attempt", "reward", "reward_L", "cost", "best", "bound", "gap_pct", "B0",
                                 "over_best_pct", "over_bound_pct", "avoidable_share", "above_script", "below_best",
                                 "cost_usd", "wall_seconds", "absent")},
            "replays": again.exact, "mismatches": again.mismatches[:5], "audit": day["audit"],
            "requests": g["counts"]["requests"], "cancelled": g["counts"]["cancelled"],
            "crew_moves": g["counts"]["crew_moves"], "latency": dist(g["counts"]["latency"]),
            "costs": g["costs"], "desks": r["desks"]}
    pairs = {}
    for run, x in out["runs"].items():
        task, mode = run.rsplit("-", 1)
        pairs.setdefault(task, {})[mode] = x["cost"]
    out["joint_vs_one"] = {t: {**v, "joint_minus_one": round(v["joint"] - v["one"], 1)} for t, v in pairs.items()
                           if {"joint", "one"} <= set(v)}
    (p.out / "report.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    return out


def dist(values: list[float]) -> dict:
    v = sorted(values)
    return {"n": len(v), **({"median": statistics.median(v), "max": v[-1]} if v else {})}


def load(name: str) -> Pilot:
    path = RUNS / name / "pilot.json"
    if not path.is_file():
        raise click.UsageError(f"no pilot {name!r} under {RUNS}")
    return Pilot(**json.loads(path.read_text()))
