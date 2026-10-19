"""The ops control center's zero-spend runs through the real harness (Z2): `agent-env run` on local Docker, the env
image's pack, the gateway, its triggers and clock, and airops-desk agents with policy "script", so no model is called.

    AGENT_ENV_CONFIG=<a config with local stores and no [model]> \\
    python scripts/occ_z2.py --out cache/<run> --z1 cache/<z1 run> [--only NAME,...] [--parallel 2]

Run `agent-env airops-occ setup` first, with data/b6-v2 the pack the checks read. The runs: joint and one on 2023-07-09,
2026-07-15 and both controls; wiring-noplay (no desk); the winter control's joint run again (two runs, one digest); a
joint run whose crew agent dies at watch 5 (its absence logged, end-day completes, the run unscored); and one whose ops
desk sleeps 300 s in a turn (the crew desk's wait_turn polls past 240 s and polls again), during which the gateway is
probed: each role lists only its desk's tools and is refused the other's (403), and get_time is hidden from both.
deploy_agent wants a model endpoint, so the runs get one nothing listens on, with a key no provider knows. Per run:
scored, the audit passed, the recorded calls replayed byte for byte through a fresh day, the digest the in-process
scripts gave in Z1, and each desk's spend $0. Writes runs/<name>.json and summary.json.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from agent_env.env.gateway import AGENT_ENV_GATEWAY_MCP_PORT
from agent_env.store.routing import namespace_routing
from agent_env.task.store import get_task_instance_store

from agentenv_airops.occ.pack import Pack
from agentenv_airops.occ.replay import replay
from agentenv_airops.occ.tasks import generate

ROOT = Path(__file__).resolve().parents[1]
DATES = ("2023-07-09", "2026-07-15", "2024-07-03", "2025-01-15")
SPARE = "2025-01-15"
INSTANCE = re.compile(r"(\d+(?:\.\d+)?)s, instance (\S+)")
VERIFIER = "airops-occ"
NO_MODEL = {"LITELLM_BASE_URL": "http://127.0.0.1:9", "LITELLM_API_KEY": "sk-no-model"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--z1", type=Path, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--parallel", type=int, default=2)
    a = ap.parse_args()
    pack = Pack(ROOT / "data" / "b6-v2")
    ids = {v["date"]: t for t, v in pack.tasks.items()}
    spare = ids[SPARE]
    bundle = a.out / "bundle"
    runs = generate(bundle, task_ids=[ids[d] for d in DATES], policy="script", noplay=True)
    runs += generate(bundle, task_ids=[spare], modes=("joint",), policy="script", suffix="-again")
    runs += generate(bundle, task_ids=[spare], modes=("joint",), policy="script", suffix="-killed", turn_timeout=120,
                     env_vars={"crew": {"AIROPS_DESK_DIE_AT": "5"}})
    runs += generate(bundle, task_ids=[spare], modes=("joint",), policy="script", suffix="-slow",
                     env_vars={"ops": {"AIROPS_DESK_SLEEP_AT": "2:300"}})
    only = {x for x in a.only.split(",") if x}
    runs = [r for r in dict.fromkeys(runs) if not only or r in only]
    (a.out / "runs").mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with ThreadPoolExecutor(a.parallel) as ex:
        for name, row in zip(runs, ex.map(lambda r: one(a.out, bundle, r, pack, a.z1), runs), strict=True):
            (a.out / "runs" / f"{name}.json").write_text(json.dumps(row, indent=1, default=str))
            print(f"[{time.monotonic() - t0:6.0f}s] {name}: {row.get('verdict')} {row.get('problems')}", flush=True)
    rows = {p.stem: json.loads(p.read_text()) for p in sorted((a.out / "runs").glob("*.json"))}
    report = {"runs": {n: {k: r.get(k) for k in ("verdict", "problems", "scored", "reward", "cost", "replays",
                                                 "digest_z1", "wall_seconds", "spend_usd", "audit_ok", "probe")}
                       for n, r in rows.items()},
              "passed": sorted(n for n, r in rows.items() if r.get("verdict") == "pass"),
              "failed": sorted(n for n, r in rows.items() if r.get("verdict") != "pass"),
              "seconds": round(time.monotonic() - t0, 1)}
    (a.out / "summary.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("passed", "failed", "seconds")}, indent=1))


def context(instance: str) -> dict:
    with namespace_routing():
        return get_task_instance_store().get(instance).context or {}


def one(out: Path, bundle: Path, name: str, pack: Pack, z1: Path) -> dict:
    row = {"name": name}
    try:
        row |= play(out, bundle, name, pack, z1)
    except Exception:
        row |= {"verdict": "error", "problems": [traceback.format_exc()[-2000:]]}
    return row


def play(out: Path, bundle: Path, name: str, pack: Pack, z1: Path) -> dict:
    log = out / "logs" / f"{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    probe = None
    with log.open("w") as f:
        proc = subprocess.Popen([sys.executable, "-m", "agent_env.cli", "run", str(bundle.absolute()), "--task", name],
                                stdout=f, stderr=subprocess.STDOUT, env=os.environ | NO_MODEL)
        if name.endswith("-slow"):
            probe = probing(proc, log)
        proc.wait()
    found = INSTANCE.findall(log.read_text(errors="replace"))
    if not found:
        return {"verdict": "fail", "problems": ["no instance"], "exit": proc.returncode}
    wall, instance = float(found[-1][0]), found[-1][1]
    ctx = context(instance)
    v = ((ctx.get("metadata") or {}).get("verifications") or {}).get(VERIFIER) or {}
    desks = {p.get("step_id"): (p.get("structured_output") or {}) for p in ctx.get("prompt_responses") or []}
    failed = (ctx.get("metadata") or {}).get("failed_steps") or []
    row = {"instance": instance, "wall_seconds": wall, "exit": proc.returncode, "scored": "score" in v,
           "reward": v.get("score"), "desks": {k: {x: s.get(x) for x in ("end_reason", "policy", "desk_turns",
                                                                         "tool_calls", "cost_usd", "seconds")}
                                               for k, s in desks.items()},
           "failed_steps": [(f["step_id"], (f.get("error") or "")[:300]) for f in failed], "probe": probe,
           "spend_usd": sum((s.get("cost_usd") or 0) for s in desks.values())}
    problems = []
    if name.endswith("-killed"):
        day = None
        if row["scored"]:
            problems.append("the killed run was scored")
        if not any(f[0] == "grade" for f in row["failed_steps"]):
            problems.append("the grade step did not refuse it")
    else:
        if not row["scored"]:
            problems.append("not scored")
        day = v["results"][0]["episode"] if row["scored"] else None
    if day is not None:
        row |= {"cost": day["grade"]["cost"], "audit_ok": day["audit"]["ok"], "absent": day["absent"],
                "turns": len(day["turns"]), "notices": [n["via"] for n in day["notices"]].count("trigger"),
                "pages": max((p["chars"] for p in day["pages"]), default=0)}
        world = pack.world(day["task_id"])
        again = replay(world, day)
        row["replays"] = again.exact
        if not again.exact:
            problems.append(f"replay mismatches {again.mismatches[:3]}")
        row["digest"] = day["digest"]
        row["digest_z1"] = z1_digest(z1, world.task.date, day["mode"], name)
        if row["digest_z1"] not in (None, day["digest"]):
            problems.append("the digest differs from Z1's")
        if not day["audit"]["ok"]:
            problems.append(f"audit {day['audit']['problems']}")
        if name == "wiring-noplay" and day["grade"]["cost"] != world.refs["idle"]:
            problems.append(f"noplay cost {day['grade']['cost']} is not idle {world.refs['idle']}")
    if row["spend_usd"]:
        problems.append(f"spent ${row['spend_usd']}")
    if probe is not None and not probe.get("ok"):
        problems.append(f"probe {probe}")
    row |= {"problems": problems, "verdict": "pass" if not problems else "fail"}
    return row


def z1_digest(z1: Path, date: str, mode: str, name: str) -> str | None:
    path = z1 / "dates" / f"{date}.json"
    if not path.is_file() or name.endswith(("-slow", "-killed")) or name == "wiring-noplay":
        return None
    r = json.loads(path.read_text())
    return r["Z1c"]["digest"] if mode == "joint" else r["one"]["digest"]


def probing(proc: subprocess.Popen, log: Path) -> dict:
    """While the slow run's ops desk sleeps (its plays started, so each role's tool access is set): each role's tools
    on the gateway's /step, and its refusals."""
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline and proc.poll() is None:
        if "play-ops-desk (prompt_agent)" not in log.read_text(errors="replace"):
            time.sleep(5)
            continue
        for url in gateways():
            try:
                with httpx.Client(base_url=url, timeout=30) as http:
                    listed = {r: {t["name"] for t in http.post("/step", json={"action": "list_tools"}, headers={
                        "AgentEnv-Role": r}).json()["tools"]} for r in ("ops", "crew")}
                    if "ops_confirm" not in listed["ops"]:
                        continue
                    refused = {f"{role} calls {tool}": http.post("/step", headers={"AgentEnv-Role": role}, json={
                        "action": "call_tool", "tool_name": tool, "arguments": {}}).status_code
                        for role, tool in (("crew", "ops_confirm"), ("ops", "crew_flights"), ("ops", "get_time"),
                                           ("crew", "airops_notice"))}
            except (httpx.HTTPError, KeyError, ValueError):
                continue
            ok = not ({t for t in listed["ops"] if t.startswith("crew_")} | {t for t in listed["crew"]
                                                                                if t.startswith("ops_")}) and \
                "get_time" not in listed["ops"] | listed["crew"] and "airops_notice" not in listed["ops"] | \
                listed["crew"] and set(refused.values()) == {403}
            return {"ok": ok, "gateway": url, "ops": sorted(listed["ops"]), "crew": sorted(listed["crew"]),
                    "refused": refused}
        time.sleep(10)
    return {"ok": False, "why": "no gateway with airops-occ's tools found"}


def gateways() -> list[str]:
    out = subprocess.run(["docker", "ps", "--format", "{{.Ports}}"], capture_output=True, text=True).stdout
    return sorted({f"http://127.0.0.1:{m}" for m in re.findall(
        rf"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]):(\d+)->{AGENT_ENV_GATEWAY_MCP_PORT}/tcp", out)})


if __name__ == "__main__":
    sys.exit(main())
