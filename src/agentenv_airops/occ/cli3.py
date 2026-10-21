"""`agent-env airops-occ3`: build and register the airops-occ3 env (the ops control center with passengers) and the
airops-desk agent, write its days as a bundle, and play and report a pilot."""

import json
import subprocess
from pathlib import Path

import click
from agent_env.a2a_agent import A2AAgent
from agent_env.artifact import DockerImageArtifact
from agent_env.env import MCPServerEnv

from ..cli import _build, _checkout, _context, _docker_platform
from .cli import MODEL
from .live3 import ENV3
from .pack import Pack3
from .sweep import Pilot3, load, play, prepare, report
from .tasks import AGENT, NOPLAY
from .tasks3 import CAPS, generate

IMAGE3 = "agentenv-airops-occ3-env"
AGENT_IMAGE = "agentenv-airops-desk"


@click.group("airops-occ3")
def occ3():
    """The AirOps ops control center with passengers: three desks, crew, customer and ops, on the storm day under cost
    P."""


@occ3.command()
@click.option("--platform", "build_platform", help="Platform to build for. Default: the Docker host's.")
@click.option("--source", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Checkout of this repo to build. Default: the one an editable install runs from, or the cwd.")
def setup(build_platform: str | None, source: Path | None):
    """Build the airops-occ3 env image (Dockerfile.occ3) and the airops-desk agent, and register both."""
    root = _checkout(source)
    build_platform = build_platform or _docker_platform()
    click.echo(f"Building {IMAGE3} for {build_platform} from {root}")
    if subprocess.run(["docker", "build", "--platform", build_platform, "-f", f"{root}/Dockerfile.occ3", "-t", IMAGE3,
                       root]).returncode:
        raise click.ClickException("docker build of the airops-occ3 env failed")
    click.echo("Storing the image (docker save, can take a minute)")
    artifact = DockerImageArtifact.put(id=IMAGE3, image_name=IMAGE3,
                                       description="AirOps ops control center with passengers env")
    env = MCPServerEnv.put(id=ENV3, docker_image_artifact=artifact, environment_name=ENV3, env_provider_type="gateway")
    click.echo(f"Registered env {env.id!r} version {env.version} (image {artifact.image_name})")
    image = _build(AGENT_IMAGE, _context(root, f"agents/{AGENT}"), build_platform,
                   f"{AGENT}: a desk of the AirOps ops control center")
    agent = A2AAgent.put(id=AGENT, docker_image_artifact=image, metadata={"default_model": MODEL})
    click.echo(f"Registered A2A agent {agent.id!r} version {agent.version} (image {image.image_name})")


@occ3.command("tasks")
@click.option("--split", type=click.Choice(("eval", "control", "train", "all")), default="eval", show_default=True)
@click.option("--task", "task_ids", multiple=True, metavar="ID", help="Write these days instead of a split.")
@click.option("--mode", "modes", multiple=True, type=click.Choice(("joint", "one")), help="Default: both.")
@click.option("--pack", type=click.Path(exists=True, path_type=Path), help="Default: the packaged b6-v3.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path), help="Default: results/bundles/airops-occ3.")
@click.option("--cap-joint", type=float, default=CAPS["joint"], show_default=True, help="USD per desk, three desks.")
@click.option("--cap-one", type=float, default=CAPS["one"], show_default=True, help="USD for one agent.")
@click.option("--noplay", is_flag=True, help=f"Also write {NOPLAY}: the first day with no desk.")
@click.option("--policy", type=click.Choice(("llm", "script")), help="The desks' policy (default: llm).")
def tasks(split, task_ids, modes, pack, out, cap_joint, cap_one, noplay, policy):
    """Write a bundle: per day, its three-desk and its one-agent task."""
    out = out or Path("results/bundles") / "airops-occ3"
    names = generate(out, task_ids=list(task_ids) or None, split=split, modes=modes or ("joint", "one"), pack=pack,
                     caps={"joint": cap_joint, "one": cap_one}, noplay=noplay, policy=policy)
    if not names:
        raise click.UsageError(f"no {split} days in the pack")
    click.echo(f"Wrote {len(names)} tasks into {out}; play one with: agent-env run {out} --task {names[0]} "
               "--model <litellm model id>")


@occ3.command()
@click.argument("name")
@click.option("--run", "runs", multiple=True, required=True, metavar="TASK-MODE",
              help="A run, as <task id>-joint or <task id>-one, in the order to play them.")
@click.option("--model", default=MODEL, show_default=True)
@click.option("--cap-joint", type=float, default=CAPS["joint"], show_default=True, help="USD per desk, three desks.")
@click.option("--cap-one", type=float, default=CAPS["one"], show_default=True, help="USD for one agent.")
@click.option("--budget", type=float, default=25.0, show_default=True,
              help="A run starts only if the spend so far plus its cap stays within this.")
@click.option("--pack", type=click.Path(exists=True, path_type=Path), help="Default: the packaged b6-v3.")
@click.option("--policy", type=click.Choice(("llm", "script")), help="The desks' policy (default: llm).")
@click.option("--parallel", type=int, default=1, show_default=True)
@click.option("--request-margin", type=float, default=1.5, show_default=True,
              help="USD of one worst request, kept free per desk in progress: a desk may pass its cap by one.")
def pilot(name, runs, model, cap_joint, cap_one, budget, pack, policy, parallel, request_margin):
    """Play the runs in order under the budget, retrying an unscored one at most twice, then report."""
    p = Pilot3(name, model, list(runs), {"joint": cap_joint, "one": cap_one}, budget,
               str(pack.absolute()) if pack else None, policy, request_margin)
    known = Pack3(pack).tasks
    if bad := [r for r in runs if r.rsplit("-", 1)[0] not in known or r.rsplit("-", 1)[-1] not in ("joint", "one")]:
        raise click.UsageError(f"not <task id>-joint or <task id>-one of the pack: {', '.join(bad)}")
    prepare(p)
    code = play(p, parallel)
    click.echo(json.dumps(report(p), indent=1, default=str))
    raise SystemExit(code)


@occ3.command("report")
@click.argument("name")
def report_command(name):
    """Replay and sum up a pilot's scored runs."""
    click.echo(json.dumps(report(load(name, Pilot3)), indent=1, default=str))
