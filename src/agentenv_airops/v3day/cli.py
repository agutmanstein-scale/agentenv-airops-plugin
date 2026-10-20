"""`agent-env airops-v3`: build and register the airops-v3-day env (and the airops-llm agent its tasks play), and write
the b6-v3 pack's days as the bundle airops-v3."""

import subprocess
from pathlib import Path

import click
from agent_env.a2a_agent import A2AAgent
from agent_env.artifact import DockerImageArtifact
from agent_env.env import MCPServerEnv

from ..cli import AGENT_IMAGE, AGENT_MODEL, _build, _checkout, _context, _docker_platform
from ..programs.model import load_tasks
from ..schedule import AGENT
from .live import ENV
from .tasks import BUNDLE_NAME, NOPLAY, PACK, SPLITS, generate

IMAGE = "agentenv-airops-v3-env"


@click.group("airops-v3")
def airops_v3():
    """AirOps v3: the storm day across JetBlue's network with v3's realism layers, played live by airops-llm."""


@airops_v3.command()
@click.option("--platform", "build_platform", help="Platform to build for. Default: the Docker host's.")
@click.option("--source", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Checkout of this repo to build. Default: the one an editable install runs from, or the cwd.")
@click.option("--agent", is_flag=True, help=f"Also build the {AGENT} agent for the same platform and register it.")
def setup(build_platform: str | None, source: Path | None, agent: bool):
    """Build the airops-v3-day env image (Dockerfile.v3, with the b6-v3 pack) and register it on the gateway provider;
    with --agent, the airops-llm agent too (as `agent-env airops setup --agent` does)."""
    root = _checkout(source)
    build_platform = build_platform or _docker_platform()
    click.echo(f"Building {IMAGE} for {build_platform} from {root}")
    if subprocess.run(["docker", "build", "--platform", build_platform, "-f", f"{root}/Dockerfile.v3", "-t", IMAGE,
                       root]).returncode:
        raise click.ClickException("docker build of the airops-v3-day env failed")
    click.echo("Storing the image (docker save, can take a minute)")
    artifact = DockerImageArtifact.put(id=IMAGE, image_name=IMAGE, description="AirOps v3 env: airops-v3-day and b6-v3")
    env = MCPServerEnv.put(id=ENV, docker_image_artifact=artifact, environment_name=ENV, env_provider_type="gateway")
    click.echo(f"Registered env {env.id!r} version {env.version} (image {artifact.image_name})")
    if agent:
        image = _build(AGENT_IMAGE, _context(root, f"agents/{AGENT}"), build_platform,
                       f"{AGENT}: an LLM planner for AirOps days on agent-env's model endpoint")
        registered = A2AAgent.put(id=AGENT, docker_image_artifact=image, metadata={"default_model": AGENT_MODEL})
        click.echo(f"Registered A2A agent {registered.id!r} version {registered.version} (image {image.image_name})")
    click.echo(f"Next: agent-env run {BUNDLE_NAME} --task {NOPLAY}, then --task <task id> --model <litellm model id>")


@airops_v3.command("tasks")
@click.option("--split", type=click.Choice(SPLITS), default="eval", show_default=True, help="The days to write.")
@click.option("--task", "task_ids", multiple=True, metavar="ID", help="Write these days instead of a split.")
@click.option("--pack", type=click.Path(exists=True, path_type=Path),
              help="A v3 pack folder (tasks.jsonl or tasks.jsonl.gz, and refs.json) or tasks file. Default: b6-v3.")
@click.option("--out", type=click.Path(file_okay=False, path_type=Path),
              help=f"The bundle folder to write. Default: results/bundles/{BUNDLE_NAME}.")
@click.option("--noplay", is_flag=True, help=f"Also write {NOPLAY}: the first day's steps without the agent.")
def tasks(split: str, task_ids: tuple[str, ...], pack: Path | None, out: Path | None, noplay: bool):
    """Write a bundle with one task per day: deploy airops-v3-day, load the day with its stored reference costs, play
    it with airops-llm on the gateway's clock, end the day and grade it with airops-verifier."""
    if task_ids and (unknown := sorted(set(task_ids) - {t.task_id for t in load_tasks(Path(str(pack or PACK)))})):
        raise click.UsageError(f"unknown task ids: {', '.join(unknown)}")
    out = out or Path("results/bundles") / BUNDLE_NAME
    names = generate(out, task_ids=list(task_ids) or None, split=split, pack=pack, noplay=noplay)
    if not names:
        raise click.UsageError(f"no {split} days in the pack")
    click.echo(f"Wrote {len(names)} tasks into {out}; play one with: agent-env run {out} --task {names[0]} "
               "--model <litellm model id>")
