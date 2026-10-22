"""A played day as the viewer's Episode JSON (DESIGN.md; web/episode.schema.json): the schedule, the final plan flown on
the truth, each watch as the desk knew it (its news, the caps, the changes, the plan run on the day as known), the
transcript's steps, the truth's caps, and the radar, hub twin and 3D airport stage found under the cache; and the folder
the viewer reads, episodes/ (with each episode's traffic sidecar) beside radar/, twin/ and stage/."""

import gzip
import json
import re
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from . import stage as hub_stage
from .core.known import known_caps, known_lb
from .core.model import Caps, Flight, Task
from .core.sim import AUTO_CANCELLED, CANCELLED, FLOWN, execute
from .occ import crewstage, paxstage
from .schedule import Watch, timetable

WEB = Path(str(files("agentenv_airops") / "web"))
SCHEMA = json.loads((WEB / "episode.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
CACHE = Path("cache")
LATE = 15
FLY = {"cancel": False, "not_before": None}
TASK_KEYS = ("task_id", "carrier", "hub", "date", "tz", "window", "start", "turn", "turn_all", "dmax", "airports",
             "tails")
REFERENCES = ("floor", "hindsight", "anchor", "follower", "blind", "naive", "airline", "gap_pct", "qualifies")
SOURCES = {"flights": "BTS On-Time Reporting Carrier On-Time Performance and T-100 Segment data",
           "advisories": "FAA ATCSCC advisories database (fly.faa.gov)",
           "weather": "NWS METARs, TAFs and area forecast discussions via the Iowa Environmental Mesonet",
           "aircraft": "FAA aircraft registry and BTS Form 41 Schedule B-43"}
NAMES = {
    "anthropic/claude-opus-5-5": "Claude Opus 5.5", "anthropic/claude-sonnet-5-5": "Claude Sonnet 5.5",
    "anthropic/claude-haiku-5-5": "Claude Haiku 5.5", "openai/gpt-6-astra": "GPT-6 Astra",
    "openai/gpt-6.1-sol": "GPT-6.1 Sol", "openai/gpt-6-luna": "GPT-6 Luna", "openai/gpt-5.6-terra": "GPT-5.6 Terra",
    "fireworks_ai/kimi-k3": "Kimi K3", "fireworks_ai/glm-5p3": "GLM-5.3", "fireworks_ai/glm-5p3-flash": "GLM-5.3-Flash",
    "fireworks_ai/qwen3p8-2p4t-a95b": "Qwen3.8-2.4T", "groq/qwen/qwen3.8-27b": "Qwen3.8-27B",
    "fireworks_ai/deepseek-v4p1-flash": "DeepSeek V4.1 Flash",
    "Qwen/Qwen3.8-2.4T-A95B:together": "Qwen3.8-2.4T", "Qwen/Qwen3.8-27B:ovhcloud": "Qwen3.8-27B",
    "zai-org/GLM-5.3-Flash:baseten": "GLM-5.3-Flash", "zai-org/GLM-5.3:together": "GLM-5.3",
    "follower": "Reference follower (the MIP re-solved each watch on the day as known)",
}


def episode(task: Task, day: Mapping, steps: list[dict], *, model: str, run: str, cache: Path = CACHE,
            record: Mapping | None = None) -> dict:
    """``day`` is the env's data/get once the day ended (Day.data()); ``steps`` the tool calls that reached the env."""
    watches = timetable(task)
    items = {n.id: n.to_dict() for n in task.news}
    plans = plans_by_watch(day)
    decided = {c["flight"]: c["t"] for c in day["changes"]}
    grade = day["grade"]
    raw = task.to_dict()
    return {
        "version": 1,
        "task": {k: raw[k] for k in TASK_KEYS},
        "model": model, "model_label": NAMES.get(model, model), "run": run,
        "reward": day["reward"], "cost": grade["cost"],
        "reference": {k: task.reference[k] for k in REFERENCES if k in task.reference},
        "schedule": [schedule_row(f) for f in task.flights],
        "executed": [outcome(task, r, plans[-1].get(r["id"]), task.lb, decided) for r in grade["executed"]],
        "ferries": grade["ferries"],
        "frozen": [dict(f) for f in task.frozen],
        "watches": [watch(task, w, plans[w.index], day, items) for w in watches],
        "steps": steps,
        "news": [items[n] for w in watches for n in w.news],
        "caps": raw["caps"], "nominal": raw["nominal"],
        "radar": radar(task, cache), "twin": twin(task, cache),
        "stage": stage(task, cache, f"{slug(run)}.{slug(model)}.{task.task_id}"),
        "end_reason": day["end_reason"], "audit": day["audit"], "record": dict(record or {}),
        "sources": SOURCES,
    }


def plans_by_watch(day: Mapping) -> list[dict[int, dict]]:
    """The plan as each watch left it, from the env's log of every change it confirmed or held."""
    plan: dict[int, dict] = {}
    out = []
    for k in range(day["watches"]):
        for c in day["changes"]:
            if c["watch"] == k:
                plan[c["flight"]] = c["now"]
        out.append({i: e for i, e in sorted(plan.items()) if e != FLY})
    return out


def watch(task: Task, w: Watch, plan: dict[int, dict], day: Mapping, items: Mapping[str, dict]) -> dict:
    lb, caps = known_lb(task, w.t), known_caps(task, w.t)
    run = execute(task, plan, lb, caps)
    rows = [outcome(task, r.to_dict(), plan.get(i), lb) for i, r in run.flights.items()]
    return {"t": w.t, "news": [items[n] for n in w.news], "caps_known": _caps(caps),
            "changes": changes(task, day["changes"], w.index), "est_cost": run.cost.total,
            "cost_known": run.cost.to_dict(),
            "counters": counters(task, rows) | {"planning_calls": day["planning_calls"][w.index]},
            "frozen": len(day["freezes"][w.index]["frozen"]), "projected": rows}


def changes(task: Task, log: list[dict], k: int) -> list[dict]:
    """A watch's changes, one per flight: what it was when the watch opened and what the watch left it."""
    was, last = {}, {}
    for c in log:
        if c["watch"] == k:
            was.setdefault(c["flight"], c["was"])
            last[c["flight"]] = c
    out = []
    for i, c in sorted(last.items()):
        if was[i] != c["now"]:
            f = task.flight(i)
            out.append({"id": i, "flight": f.flight, "orig": f.orig, "dest": f.dest, "action": action(was[i], c["now"]),
                        "cancel": c["now"]["cancel"], "not_before": c["now"]["not_before"], "was": was[i],
                        "by": c["by"]})
    return out


def action(was: Mapping, now: Mapping) -> str:
    if now["cancel"]:
        return "cancel"
    if was["cancel"]:
        return "restore"
    return "release" if now["not_before"] is None else "hold"


def outcome(task: Task, r: Mapping, entry: Mapping | None, lb: Mapping[int, int],
            decided: Mapping[int, int] | None = None) -> dict:
    """A flight's run with its hold, which is ``held`` when it binds past the flight's own delay; on the truth, also the
    minute its fate was decided: the planner's last change to it, or its deadline when it was cancelled for want of
    a slot or an aircraft."""
    f = task.flight(r["id"])
    nb = None if not entry or entry["cancel"] else entry["not_before"]
    row = {**r, "not_before": nb, "held": r["status"] == FLOWN and nb is not None and nb > f.sched_dep + lb[f.id]}
    if decided is not None:
        row["decided"] = f.sched_dep + f.dmax if r["status"] == AUTO_CANCELLED else decided.get(f.id)
    return row


def counters(task: Task, rows: list[dict]) -> dict[str, int]:
    flown = [r for r in rows if r["status"] == FLOWN]
    pax = {f.id: f.pax for f in task.flights}
    return {"cancelled": sum(r["status"] == CANCELLED for r in rows),
            "auto_cancelled": sum(r["status"] == AUTO_CANCELLED for r in rows),
            "held": sum(r["held"] for r in rows), "delayed": sum(r["arr_delay"] >= LATE for r in flown),
            "pax_delay": sum(pax[r["id"]] * r["arr_delay"] for r in flown),
            "pax_disrupted": sum(pax[r["id"]] for r in rows if r["status"] != FLOWN or r["arr_delay"] >= LATE)}


def schedule_row(f: Flight) -> dict:
    a = f.actual
    return {"id": f.id, "flight": f.flight, "orig": f.orig, "dest": f.dest, "sched_dep": f.sched_dep,
            "sched_arr": f.sched_arr, "family": f.family, "pax": f.pax, "tail": a.tail,
            "airline": {"cancelled": a.cancelled, "dep": a.dep, "arr": a.arr, "cancel_code": a.cancel_code}}


def radar(task: Task, cache: Path) -> dict | None:
    """The NEXRAD frames scripts/prebake_radar.py baked for the day, if it did."""
    path = cache / "radar" / task.task_id / "index.json"
    if not path.is_file():
        return None
    r = json.loads(path.read_text())
    return {"bounds": r["bounds"], "projection": r["projection"], "step": r["step"], "attribution": r["attribution"],
            "frames": [{"t": f["t"], "url": f"../radar/{task.task_id}/{f['url']}"} for f in r["frames"]]}


def twin(task: Task, cache: Path) -> dict | None:
    """The hub's OpenStreetMap twin scripts/build_twin.py built, if it did."""
    path = cache / "twin" / "index.json"
    hubs = json.loads(path.read_text()) if path.is_file() else {}
    if task.hub not in hubs:
        return None
    t = hubs[task.hub]
    return {"hub": task.hub, "url": f"../twin/{t['url']}", "sha256": t["sha256"],
            "attribution": f"Hub twin {t['attribution']}"}


def stage(task: Task, cache: Path, stem: str) -> dict | None:
    """The hub's 3D stage scripts/build_stage.py built and the episode's traffic sidecar beside the episode, when
    scripts/prebake_traffic.py baked the task's traffic."""
    if not ((cache / "stage" / task.hub / "stage.json.gz").is_file()
            and (cache / "traffic" / f"{task.task_id}.json.gz").is_file()):
        return None
    return {"hub": task.hub, "url": f"../stage/{task.hub}/stage.json.gz", "traffic": f"{stem}.stage.json.gz"}


def sidecar(ep: Mapping, cache: Path, stages: dict[str, dict] | None = None) -> bytes:
    """The episode's traffic sidecar, gzipped: its run laid over the task's baked traffic at the hub's stage."""
    hub, task_id = ep["task"]["hub"], ep["task"]["task_id"]
    stages = {} if stages is None else stages
    if hub not in stages:
        stages[hub] = json.loads(gzip.decompress((cache / "stage" / hub / "stage.json.gz").read_bytes()))
    traffic = json.loads(gzip.decompress((cache / "traffic" / f"{task_id}.json.gz").read_bytes()))
    crew = {int(i): rows for i, rows in (ep.get("occ") or {}).get("crew", {}).items()}
    doc = hub_stage.sidecar(ep["task"], ep["executed"], traffic, stages[hub], crew)
    if ep.get("occ"):
        doc["crews"] = crewstage.crews(ep, doc, stages[hub])
    if ep.get("passengers") and (pax := paxstage.passengers(ep, doc, stages[hub])):
        doc["passengers"] = pax
    return gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)


def validate(ep: Mapping) -> None:
    errors = sorted(VALIDATOR.iter_errors(ep), key=lambda e: list(e.absolute_path))
    if errors:
        raise ValueError("not an episode the viewer reads: " + "; ".join(
            f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:10]))


def file_name(ep: Mapping) -> str:
    return f"{slug(ep['run'])}.{slug(ep['model'])}.{ep['task']['task_id']}.json"


def entry(ep: Mapping, url: str, cache: Path = CACHE) -> dict:
    """The episode's row in the index, with the list thumbnail scripts/record.py --thumb made for it, if it did."""
    t, thumb = ep["task"], f"thumbs/{file_name(ep)[:-5]}.jpg"
    return {"url": url, "task_id": t["task_id"], "hub": t["hub"], "date": t["date"], "model": ep["model"],
            "model_label": ep["model_label"], "run": ep["run"], "reward": ep["reward"], "cost": ep["cost"]["total"],
            "anchor": ep["reference"]["anchor"]} | ({"thumb": f"../{thumb}"} if (cache / thumb).is_file() else {}) \
        | ({"occ": ep["occ"]["label"], "reward_L": ep["occ"]["grade"].get("reward_L"),
            "gap_pct": ep["reference"].get("gap_pct"), "open": opening(ep)} if ep.get("occ") else {}) \
        | ({"passengers": True} if ep.get("passengers") else {})


def opening(ep: Mapping) -> int:
    """The minute a control-center day's link opens on: just before the room shows its first typed request, else the
    day's start."""
    shown = [r["shown"] for r in ep["occ"]["requests"] if r["type"] != "TEXT"]
    return max(ep["task"]["start"], round(min(shown)) - 6) if shown else ep["task"]["start"]


def folder(episodes: list[dict], cache: Path = CACHE, index: Mapping | None = None) -> dict[str, bytes]:
    """The viewer's files for ``episodes``, by path: episodes/<name>.json and its traffic sidecar, episodes/index.json
    listing them after the entries of ``index`` it keeps, and the radar frames, hub twins, stages and list thumbnails
    they point at, read from ``cache``."""
    files: dict[str, bytes] = {}
    stages: dict[str, dict] = {}
    entries = list((index or {}).get("episodes", []))
    for ep in episodes:
        validate(ep)
        name = file_name(ep)
        files[f"episodes/{name}"] = json.dumps(ep, ensure_ascii=False, separators=(",", ":")).encode()
        if ep.get("stage"):
            files[f"episodes/{ep['stage']['traffic']}"] = sidecar(ep, cache, stages)
        entries = [e for e in entries if e["url"] != name] + [entry(ep, name, cache)]
        for rel in assets(ep, cache):
            files[rel] = (cache / rel).read_bytes()
    files["episodes/index.json"] = (json.dumps({"version": 1, "episodes": entries}, indent=1, ensure_ascii=False)
                                    + "\n").encode()
    return files


def assets(ep: Mapping, cache: Path = CACHE) -> list[str]:
    task_id, thumb = ep["task"]["task_id"], f"thumbs/{file_name(ep)[:-5]}.jpg"
    found = [f"radar/{task_id}/index.json", *(f"radar/{task_id}/{Path(f['url']).name}" for f in ep["radar"]["frames"])
             ] if ep["radar"] else []
    found += [f"twin/{Path(ep['twin']['url']).name}"] if ep["twin"] else []
    found += [thumb] if (cache / thumb).is_file() else []
    hub = (ep.get("stage") or {}).get("hub")
    return found + ([f"stage/{hub}/stage.json.gz", f"stage/{hub}/ortho.jpg"] if hub else [])


def write(out: Path, episodes: list[dict], cache: Path = CACHE) -> None:
    """The episodes into the viewer folder ``out``, added to the index there, with the assets they point at."""
    index = out / "episodes" / "index.json"
    for rel, content in folder(episodes, cache, json.loads(index.read_text()) if index.is_file() else None).items():
        if rel.startswith("episodes/") or out.resolve() != cache.resolve():
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_bytes(content)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)


def _caps(caps: Caps) -> dict:
    return {k: {str(h): v for h, v in hours.items()} for k, hours in caps.items()}
