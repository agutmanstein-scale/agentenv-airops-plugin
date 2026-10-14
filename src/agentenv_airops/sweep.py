"""`agent-env airops sweep`: the pack's days over models and reps under a spend cap, one `agent-env run` process per
attempt, retried and resumed from results.jsonl; and the report against the references of the same days: the
hindsight optimum the reward is anchored on, the follower and blind follower, naive (no plan) and the airline's own
day."""

import json
import random
import re
import signal
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

from . import tasks
from .core.model import Task
from .core.reward import reward
from .schedule import timetable

AGENT_ENV = [sys.executable, "-m", "agent_env.cli"]
RUNS = Path("results/runs")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
INSTANCE = re.compile(r"(\d+(?:\.\d+)?)s, instance (\S+)")
SPLITS = ("eval", "control", "train", "all")
OPTIONAL = ("pack", "hf_bill_to")
STOPS = ("model_endpoint", "unpriced_model")
FINAL = ("cost_cap", "provider_refused")
MAX_ATTEMPTS = 3
POLL_SECONDS = 2.0
VERIFIER = "airops"
POLICIES = ("follower", "blind", "naive", "airline")
COUNTS = ("end_reason", "turns", "tool_calls", "checks", "confirms", "input_tokens", "output_tokens", "cached_tokens",
          "cache_write_tokens")


@dataclass(frozen=True)
class Sweep:
    name: str
    models: list[str]
    tasks: list[str]
    k: int
    episode_cap_usd: float
    pack: str | None = None
    hf_bill_to: str | None = None

    @property
    def out(self) -> Path:
        return RUNS / self.name

    def runs(self) -> list[tuple[str, str, int]]:
        """(model, task, rep), model-major, then rep, then task."""
        return [(m, t, rep) for m in self.models for rep in range(1, self.k + 1) for t in self.tasks]

    def days(self) -> dict[str, Task]:
        return {t.task_id: t for t in tasks.pack_tasks(Path(self.pack).expanduser() if self.pack else None)}


@dataclass
class Attempt:
    run: tuple[str, str, int]
    number: int
    started_utc: str
    log: Path
    proc: subprocess.Popen

    @property
    def label(self) -> str:
        model, task, rep = self.run
        return f"{model} {task} r{rep} a{self.number}"


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model)


def results(out: Path) -> list[dict]:
    path = out / "results.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def of(rows: list[dict], run: tuple[str, str, int]) -> list[dict]:
    return [r for r in rows if (r["model"], r["task_id"], r["rep"]) == run]


def final(rows: list[dict]) -> bool:
    """A run is final once scored, stopped at its cost cap, refused by the provider, or after MAX_ATTEMPTS counted
    attempts; an interrupted attempt doesn't count."""
    counted = [r for r in rows if r["outcome"] != "interrupted"]
    return (any(r["outcome"] == "scored" or r["error_code"] in FINAL for r in counted)
            or len(counted) >= MAX_ATTEMPTS)


def cost(row: dict, cap: float) -> float:
    """What an attempt spent, or its episode cap when it recorded no spend: the budget counts the worst case."""
    return cap if row["cost_usd"] is None else row["cost_usd"]


def priced(row: dict) -> float | None:
    """What a run's tokens cost: its spend without the failed provider requests the cap books at their worst case."""
    return row["cost_usd"] if row.get("priced_cost_usd") is None else row["priced_cost_usd"]


def context(instance: str) -> dict:
    with namespace_routing():
        return get_task_instance_store().get(instance).context or {}


def trajectory(uri: str) -> bytes:
    with namespace_routing():
        return get_config().get_object_store_at(uri).get(uri)


def task_ids(spec: str, pack: Path | None = None) -> list[str]:
    """A split (eval, control, train or all), fewest watches first so a spend stop drops the longest days, or
    comma-separated task ids in their order."""
    days = tasks.pack_tasks(pack)
    if spec in SPLITS:
        chosen = sorted((t for t in days if spec in ("all", t.split)), key=lambda t: (len(timetable(t)), t.task_id))
        return [t.task_id for t in chosen]
    ids = spec.split(",")
    if unknown := [i for i in ids if i not in {t.task_id for t in days}]:
        raise click.UsageError(f"not task ids of the pack: {', '.join(unknown)}")
    return ids


def prepare(sweep: Sweep) -> None:
    """Writes the sweep's bundle and sweep.json on its first run; a later run must be the same sweep. A day without
    its reference can't be scored, so it is refused before any spend."""
    if not NAME.fullmatch(sweep.name):
        raise click.UsageError("a sweep's name is 1 to 41 lowercase letters, digits and dashes, starting with no dash")
    path = sweep.out / "sweep.json"
    if path.is_file():
        if {key: None for key in OPTIONAL} | json.loads(path.read_text()) != asdict(sweep):
            raise click.UsageError(f"{path} is another sweep; run it with its own models, tasks, k, episode cap and "
                                   f"pack: {path.read_text().strip()}")
        return
    days = sweep.days()
    if missing := [t for t in sweep.tasks if "anchor" not in days[t].reference]:
        raise click.UsageError(f"no reference yet for {', '.join(missing)}: the verifier can't score them (scripts/"
                               "references.py references writes them)")
    tasks.generate(sweep.out / "bundle", task_ids=sweep.tasks,
                   pack=Path(sweep.pack).expanduser() if sweep.pack else None, episode_cap_usd=sweep.episode_cap_usd,
                   hf_bill_to=sweep.hf_bill_to)
    spec = {key: value for key, value in asdict(sweep).items() if key not in OPTIONAL or value is not None}
    path.write_text(json.dumps(spec, indent=2) + "\n")


def preflight(sweep: Sweep, task: str) -> str | None:
    """The problem a dry run of ``task`` finds, as `agent-env run` would before its first step, or None."""
    log = sweep.out / "logs" / "preflight.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        code = subprocess.run([*AGENT_ENV, "run", str((sweep.out / "bundle").absolute()), "--task", task, "--dry-run"],
                              stdout=f, stderr=subprocess.STDOUT).returncode
    return f"the dry run of {task} exited {code}:\n{log.read_text(errors='replace').rstrip()}" if code else None


def start(sweep: Sweep, run: tuple[str, str, int], number: int) -> Attempt:
    model, task, rep = run
    log = sweep.out / "logs" / slug(model) / f"{task}-r{rep}-a{number}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with log.open("w") as f:
        proc = subprocess.Popen([*AGENT_ENV, "run", str((sweep.out / "bundle").absolute()), "--task", task,
                                 "--model", model], stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    return Attempt(run, number, started, log, proc)


def outcome(sweep: Sweep, attempt: Attempt, earlier: list[dict], interrupted: bool = False, read: bool = True) -> dict:
    """The attempt's row, from the instance its run stored: the verifier's score and the day it read from the env, and
    the play step's summary, failure and transcript. Without ``read`` (the sweep itself failed), from the log alone."""
    model, task, rep = attempt.run
    found = INSTANCE.findall(attempt.log.read_text(errors="replace"))
    wall, instance = (float(found[-1][0]), found[-1][1]) if found else (None, None)
    ctx = context(instance) if instance and read else {}
    m = ctx.get("metadata") or {}
    v = (m.get("verifications") or {}).get(VERIFIER) or {}
    pr = next((p for p in ctx.get("prompt_responses") or [] if p.get("step_id") == "play"), None)
    s = (pr or {}).get("structured_output") or {}
    failed = m.get("failed_steps") or []
    scored = "score" in v
    if s:
        spent = s.get("cost_usd")
    elif instance and not interrupted and pr is None and "play" not in {f["step_id"] for f in failed}:
        spent = 0.0
    else:
        spent = None
    transcript = None
    if pr and pr.get("agent_trajectory_object_url"):
        path = sweep.out / "transcripts" / slug(model) / f"{task}-r{rep}-a{attempt.number}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(trajectory(pr["agent_trajectory_object_url"]))
        transcript = path.relative_to(sweep.out).as_posix()
    day = v["results"][0]["episode"] if scored else {}
    grade = day.get("grade") or {}
    counts = grade.get("counts") or {}
    error_code = error = failed_step = None
    if not scored and not interrupted:
        error_code = (pr or {}).get("error_code") or (failed[-1]["error_type"] if failed else "no_instance")
        error = (pr or {}).get("error_message") or (failed[-1]["error"] if failed else None)
        failed_step = failed[-1]["step_id"] if failed else None
    row = {"sweep": sweep.name, "model": model, "task_id": task, "rep": rep, "attempt": attempt.number,
           "started_utc": attempt.started_utc, "wall_seconds": wall, "exit": attempt.proc.returncode,
           "instance": instance, "outcome": "scored" if scored else "interrupted" if interrupted else "failed",
           "failed_step": failed_step, "error_code": error_code, "error": error and error[:500], "retryable": False,
           "reward": v["score"] if scored else None, "finished": day["end_reason"] == "done" if scored else None,
           "day_cost": (grade.get("cost") or {}).get("total"), "watches": day.get("watches"),
           "planning_calls": sum(day["planning_calls"]) if scored else None,
           "refusals": len(day["refusals"]) if scored else None, "cancelled": counts.get("cancelled"),
           "auto_cancelled": counts.get("auto_cancelled"), **{key: s.get(key) for key in COUNTS},
           "cost_usd": spent, "priced_cost_usd": s.get("priced_cost_usd"), "failed_requests": s.get("failed_requests"),
           "agent_reward": s.get("reward"), "transcript": transcript}
    row["retryable"] = not final([*earlier, row])
    return row


def _money(row: dict, cap: float) -> str:
    return f"${row['cost_usd']:.4f}" if row["cost_usd"] is not None else f"no spend recorded, counted as ${cap:.2f}"


def _attempts(n: int) -> str:
    return f"{n} attempt{'' if n == 1 else 's'}"


def _spend(rows: list[dict], cap: float) -> str:
    unknown = sum(r["cost_usd"] is None for r in rows)
    known = sum(r["cost_usd"] or 0.0 for r in rows)
    return f"${known:.2f} spent" + (f", {_attempts(unknown)} with no spend recorded (${unknown * cap:.2f} at the cap)"
                                    if unknown else "")


def run(sweep: Sweep, cap_usd: float, parallel: int, echo=click.echo) -> int:
    """Plays the runs results.jsonl doesn't hold as final, `parallel` attempts at a time, while the spend so far plus
    the episode cap of every attempt running and the next stays within ``cap_usd``. Returns the exit code: 0 when
    every run is final, 1 when the sweep stopped early, 130 when a signal stopped it. If the sweep itself fails, it
    tears its attempts down and records them from their logs, at their caps, before the error propagates."""
    cap = sweep.episode_cap_usd
    rows = results(sweep.out)
    queue = [r for r in sweep.runs() if not final(of(rows, r))]
    spent = sum(cost(r, cap) for r in rows)
    running: list[Attempt] = []
    stopped = None
    caught: list[int] = []
    previous = {s: signal.signal(s, lambda signum, _: caught.append(signum)) for s in (signal.SIGINT, signal.SIGTERM)}

    def record(attempt: Attempt, interrupted: bool = False, read: bool = True) -> dict:
        nonlocal spent
        row = outcome(sweep, attempt, of(rows, attempt.run), interrupted, read)
        running.remove(attempt)
        rows.append(row)
        spent += cost(row, cap)
        with (sweep.out / "results.jsonl").open("a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    def finish(attempt: Attempt) -> dict:
        row = record(attempt)
        if row["outcome"] == "scored":
            echo(f"done {attempt.label}: reward {row['reward']} ({row['end_reason']}), {_money(row, cap)} "
                 f"(spent ${spent:.2f})")
        else:
            echo(f"failed {attempt.label}: {row['error_code']} {'retry' if row['retryable'] else 'final'} "
                 f"({_money(row, cap)})")
        return row

    def tear_down(read: bool) -> None:
        for attempt in running:
            attempt.proc.terminate()
        for attempt in list(running):
            attempt.proc.wait()
            echo(f"interrupted {attempt.label} ({_money(record(attempt, interrupted=True, read=read), cap)})")

    try:
        if queue:
            stopped = preflight(sweep, queue[0][1])
        while not caught and (running or (queue and not stopped)):
            while (queue and not stopped and len(running) < parallel
                   and spent + (len(running) + 1) * cap <= cap_usd + 1e-9):
                r = queue.pop(0)
                attempt = start(sweep, r, len(of(rows, r)) + 1)
                running.append(attempt)
                echo(f"start {attempt.label} (spent ${spent:.2f} of ${cap_usd:.2f})")
            if not running:
                stopped = (f"another attempt could take the spend past ${cap_usd:.2f} (${spent:.2f} spent, "
                           f"${cap:.2f} an attempt at most); {len(queue)} runs not final")
                break
            time.sleep(POLL_SECONDS)
            for attempt in [a for a in running if a.proc.poll() is not None]:
                row = finish(attempt)
                if row["error_code"] in STOPS:
                    stopped = f"{row['error_code']} from {attempt.label}: {row['error']}"
                elif row["retryable"]:
                    queue.append(attempt.run)
        if caught:
            for attempt in [a for a in running if a.proc.poll() is not None]:
                finish(attempt)
            tear_down(read=True)
            return 130
    except BaseException:
        tear_down(read=False)
        raise
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
    for model in sweep.models:
        planned = [r for r in sweep.runs() if r[0] == model]
        scored = sum(any(x["outcome"] == "scored" for x in of(rows, r)) for r in planned)
        unscored = sum(final(of(rows, r)) for r in planned) - scored
        echo(f"{model}: {scored} scored, {unscored} unscored, {len(planned) - scored - unscored} not final, of "
             f"{len(planned)} runs; {_spend([x for x in rows if x['model'] == model], cap)}")
    if stopped:
        echo(f"stopped: {stopped}")
    return 0 if all(final(of(rows, r)) for r in sweep.runs()) else 1


def ci(xs: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """The 95% bootstrap interval of the mean."""
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def load(name: str, root: Path = RUNS) -> Sweep:
    path = root / name / "sweep.json"
    if not path.is_file():
        raise click.UsageError(f"no sweep {name!r}: {path} doesn't exist")
    return Sweep(**json.loads(path.read_text()))


def policy_reward(task: Task, policy: str) -> float:
    """A reference policy's reward on the truth, graded as a run is."""
    ref = task.reference
    return reward(ref[policy], ref["anchor"], ref["floor"])


def _with_ci(xs: list[float]) -> str:
    if not xs:
        return "–"
    lo, hi = ci(xs)
    return f"{statistics.mean(xs):.3f} ({lo:.3f} to {hi:.3f})"


def _mean(xs: list[float], f: str = ".3f") -> str:
    return format(statistics.mean(xs), f) if xs else "–"


def _count(x: float) -> str:
    return f"{x:.1f}".removesuffix(".0")


def _tokens(rows: list[dict]) -> str:
    pairs = [(r["input_tokens"], r["output_tokens"]) for r in rows if r["input_tokens"] is not None]
    return (f"{statistics.mean(i for i, _ in pairs) / 1000:.1f}k/{statistics.mean(o for _, o in pairs) / 1000:.1f}k"
            if pairs else "–")


def _rep_labels(reps: list[tuple[str, int]]) -> list[str]:
    if len({name for name, _ in reps}) == 1:
        return [f"r{rep}" for _, rep in reps]
    return [f"{name} r{rep}" for name, rep in reps]


def _cost_reward(cost_: int, task: Task) -> str:
    ref = task.reference
    return f"{cost_:,} ({reward(cost_, ref['anchor'], ref['floor']):.3f})"


class Pooled:
    """The named sweeps' runs pooled per model and day, next to the references of the same days. A day's value is the
    mean of its scored reps across the sweeps."""

    def __init__(self, sweeps: list[Sweep]):
        self.sweeps = sweeps
        self.days = {t: day for s in sweeps for t, day in s.days().items() if t in s.tasks}
        rows = {s.name: results(s.out) for s in sweeps}
        self.caps = {s.name: s.episode_cap_usd for s in sweeps}
        self.runs = {(s.name, *run): of(rows[s.name], run) for s in sweeps for run in s.runs()}
        self.scored = {key: next((r for r in rs if r["outcome"] == "scored"), None) for key, rs in self.runs.items()}
        self.models = list(dict.fromkeys(m for s in sweeps for m in s.models))
        self.order = sorted(self.days, key=lambda t: (len(timetable(self.days[t])), t))

    def by_day(self, model: str) -> dict[str, list[dict]]:
        """Each day's scored runs, for the days with one, in task id order (the order the bootstrap draws from)."""
        found = {t: [row for key, row in self.scored.items() if key[1:3] == (model, t) and row]
                 for t in sorted(self.days)}
        return {t: rs for t, rs in found.items() if rs}

    def spend(self, rows: list[tuple[str, dict]]) -> tuple[float, int, float]:
        """Known spend, attempts with none recorded, and those at their cap."""
        unknown = [(name, r) for name, r in rows if r["cost_usd"] is None]
        return (sum(r["cost_usd"] or 0.0 for _, r in rows), len(unknown), sum(self.caps[name] for name, _ in unknown))

    def attempts(self, model: str | None = None) -> list[tuple[str, dict]]:
        return [(key[0], r) for key, rs in self.runs.items() for r in rs if model in (None, key[1])]

    def header(self) -> list[str]:
        known, unknown, at_cap = self.spend(self.attempts())
        attempts = len(self.attempts())
        unscored = sum(row is None for row in self.scored.values())
        return [
            "# AirOpsEnv: airops-llm on the storm days", "",
            "Sweeps: " + "; ".join(f"`{s.name}` ({', '.join(s.models)}; {len(s.tasks)} day{'s' * (len(s.tasks) > 1)}, "
                                   f"k={s.k}, episode cap ${s.episode_cap_usd:g})" for s in self.sweeps) + ".",
            "Harness: airops-llm. References, on the same days, as their reward on the truth: the follower (the "
            "time-line MIP re-solved at every watch on the day as known, persistence forecast), the blind follower "
            "(no forecast), naive (no plan: every flight as early as it can) and the airline (JetBlue's own "
            "cancellations and departure times). The reward is 1.0 at the hindsight optimum.", "",
            f"Spend: ${known:.2f} known; {_attempts(unknown)} with no spend recorded, ${at_cap:.2f} at the cap. "
            f"Attempts: {attempts}, retries: {attempts - sum(bool(rs) for rs in self.runs.values())}. "
            f"Unscored runs: {unscored} of {len(self.runs)}.",
        ]

    def model_line(self, model: str) -> str:
        by_day = self.by_day(model)
        ours = {t: statistics.mean(r["reward"] for r in rs) for t, rs in by_day.items()}
        rows = [r for rs in by_day.values() for r in rs]
        turns = [r["turns"] for r in rows if r["turns"] is not None]
        cached = [r["cached_tokens"] for r in rows if r["cached_tokens"] is not None]
        costs = [priced(r) for r in rows if priced(r) is not None]
        gaps = [100 * (r["day_cost"] - self.days[r["task_id"]].reference["anchor"])
                / self.days[r["task_id"]].reference["anchor"] for r in rows]
        known, unknown, _ = self.spend(self.attempts(model))
        return "| " + " | ".join([
            model, str(len(ours)), f"{len(rows)}/{sum(key[1] == model for key in self.runs)}",
            _with_ci(list(ours.values())),
            *(_mean([policy_reward(self.days[t], p) for t in ours]) for p in POLICIES),
            _count(sum(statistics.mean(bool(r["finished"]) for r in rs) for rs in by_day.values())),
            _mean(gaps, "+.1f") + ("%" if gaps else ""),
            f"{statistics.median(turns):g}" if turns else "–", _tokens(rows),
            f"{statistics.mean(cached) / 1000:.1f}k" if cached else "–",
            f"${statistics.mean(costs):.4f}" if costs else "–",
            f"${known:.2f}" + (f" + {unknown} unknown" if unknown else ""),
        ]) + " |"

    def days_table(self, model: str) -> list[str]:
        mine = [s for s in self.sweeps if model in s.models]
        reps = [(s.name, rep) for s in mine for rep in range(1, s.k + 1)]
        lines = ["", f"### {model}", "",
                 "| Day | Split | Flights | Watches | Hindsight | Follower | Naive | Airline | "
                 + " | ".join(_rep_labels(reps)) + " | Mean |",
                 "|---|---|---:|---:|---:|---|---|---|" + "---:|" * (len(reps) + 1)]
        for t in [t for t in self.order if any(t in s.tasks for s in mine)]:
            task = self.days[t]
            values = [self.scored.get((name, model, t, rep)) for name, rep in reps]
            rewards = [v["reward"] for v in values if v]
            lines.append(" | ".join([
                f"| {t}", task.split, str(len(task.flights)), str(len(timetable(task))),
                f"{task.reference['anchor']:,}", *(_cost_reward(task.reference[p], task)
                                                   for p in ("follower", "naive", "airline")),
                *(f"{v['reward']:.3f}" if v else "–" for v in values),
                f"{statistics.mean(rewards):.3f}" if rewards else "–",
            ]) + " |")
        return lines

    def unscored(self) -> list[str]:
        keys = [key for key, row in self.scored.items() if row is None]
        if not keys:
            return ["None."]
        lines = ["| Sweep | Model | Day | Rep | Attempts | Last outcome | Spend |", "|---|---|---|---:|---:|---|---:|"]
        for key in keys:
            rs = self.runs[key]
            last = (rs[-1]["error_code"] or rs[-1]["outcome"]) if rs else "not run"
            spent = sum(cost(r, self.caps[key[0]]) for r in rs)
            lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {len(rs)} | {last} | ${spent:.2f} |")
        return lines

    def differs(self) -> list[str]:
        """Only the runs whose agent saw the day end: one that stopped early has no reward of its own."""
        found = [(key, row) for key, row in self.scored.items()
                 if row is not None and row["end_reason"] == "done" and row["agent_reward"] != row["reward"]]
        if not found:
            return ["None."]
        return ["| Sweep | Model | Day | Rep | Agent reward | Verifier reward |", "|---|---|---|---:|---:|---:|"] + [
            f"| {key[0]} | {key[1]} | {key[2]} | {key[3]} | "
            f"{'–' if row['agent_reward'] is None else format(row['agent_reward'], '.3f')} | {row['reward']:.3f} |"
            for key, row in found]


def report(sweeps: list[Sweep]) -> str:
    """Each model's scored days next to the references of the same days; the CIs bootstrap over days."""
    pooled = Pooled(sweeps)
    lines = [*pooled.header(), "", "## Per model", "",
             "| Model | Days | Runs scored/planned | Mean reward (95% CI) | Follower | Blind follower | Naive "
             "| Airline | Finished per rep | Mean cost over hindsight | Median turns | Tokens in/out per episode "
             "| Cached tokens per episode | Cost per episode | Spend |",
             "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|",
             *(pooled.model_line(m) for m in pooled.models),
             "", "## Day by day", "", "The references as cost in passenger-minutes (reward).",
             *(line for m in pooled.models for line in pooled.days_table(m)),
             "", "## Unscored runs", "", *pooled.unscored(),
             "", "## Runs whose agent reward differs from the verifier's", "",
             "Only runs whose agent saw the day end.", "", *pooled.differs()]
    return "\n".join(lines) + "\n"


def home_relative(path: Path) -> str:
    """The path as sweep.json keeps it: absolute, with the home folder as ~, so a published sweep names no user."""
    path = path.resolve()
    return f"~/{path.relative_to(Path.home())}" if path.is_relative_to(Path.home()) else str(path)


@click.group("sweep")
def sweep_group():
    """Sweeps: the pack's days over models and reps under a spend cap, and their report."""


@sweep_group.command("run")
@click.option("--name", required=True, help="The sweep's name: its results go to results/runs/<name>/.")
@click.option("--models", required=True, help="Comma-separated LiteLLM model ids, e.g. anthropic/claude-sonnet-5-5.")
@click.option("--tasks", "task_spec", default="eval", show_default=True,
              help="eval, control, train, all, or comma-separated task ids of the pack.")
@click.option("--cap-usd", type=float, required=True,
              help="The model spend the sweep stays within: no attempt starts that could take it past this at the "
                   "episode cap.")
@click.option("--k", type=click.IntRange(min=1), default=1, show_default=True, help="Reps of each model and day.")
@click.option("--episode-cap-usd", type=float, default=5.0, show_default=True,
              help="An episode's cost cap, passed to the agent as AIROPS_MAX_COST_USD.")
@click.option("--parallel", type=click.IntRange(min=1), default=4, show_default=True, help="Attempts at a time.")
@click.option("--pack", type=click.Path(exists=True, path_type=Path),
              help="A pack folder or tasks .jsonl, the one the env image holds. Default: the packaged b6-v1 pack.")
@click.option("--hf-bill-to", metavar="ORG",
              help="Bill the agent's Hugging Face router calls to this organization (X-HF-Bill-To).")
def run_command(name: str, models: str, task_spec: str, cap_usd: float, k: int, episode_cap_usd: float,
                parallel: int, pack: Path | None, hf_bill_to: str | None):
    """Play each model on each day k times, one `agent-env run` per attempt, logged under results/runs/<name>/logs.
    Each attempt is a line of results.jsonl; a failed one is retried up to twice, and running the sweep again plays
    the runs that aren't final yet. Ctrl-C tears the running attempts down."""
    if "" in models.split(","):
        raise click.UsageError("--models takes comma-separated model ids, none empty")
    sweep = Sweep(name, models.split(","), task_ids(task_spec, pack), k, episode_cap_usd,
                  pack and home_relative(pack), hf_bill_to)
    prepare(sweep)
    raise SystemExit(run(sweep, cap_usd, parallel))


@sweep_group.command("report")
@click.argument("names", nargs=-1, required=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), default=Path("results/airops.md"),
              show_default=True, help="The Markdown file to write.")
def report_command(names: tuple[str, ...], out: Path):
    """Compare the sweeps NAMES with the references, per model and day."""
    text = report([load(name) for name in names])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    click.echo(f"Wrote {out}")
