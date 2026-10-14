"""`agent-env airops`: build and register the airops-day env and the airops-llm agent, write the tasks, sweep models
over them, and watch and film the recorded runs."""

import json
import subprocess
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import click
from agent_env.a2a_agent import A2AAgent
from agent_env.artifact import DockerImageArtifact
from agent_env.env import MCPServerEnv

from .record import record_command
from .schedule import AGENT, ENV
from .sweep import sweep_group
from .tasks import tasks_group
from .view import view_command

IMAGE = "agentenv-airops-env"
AGENT_IMAGE = "agentenv-airops-agent"
AGENT_MODEL = "anthropic/claude-sonnet-5-5"
REPO = "https://github.com/earakely-scale/agentenv-airops-plugin"


@click.group()
def airops():
    """AirOps: build and register the env and agent, write the tasks, sweep models over them, and watch and film the
    recorded runs."""


def _installed_commit() -> str | None:
    """The git commit a `pip install git+...` or uv git source installed this package from (PEP 610), if it did."""
    try:
        direct = json.loads(distribution("agentenv-airops").read_text("direct_url.json") or "{}")
    except PackageNotFoundError:
        return None
    return direct.get("vcs_info", {}).get("commit_id")


def _checkout(source: Path | None) -> str:
    """What to build from: ``source``, else the checkout an editable install runs from, else the cwd, else the commit
    of this repo a git install came from, which Docker fetches itself as a git build context."""
    for root in [source] if source else [Path(__file__).resolve().parents[2], Path.cwd()]:
        if (root / "Dockerfile").is_file() and (root / "src/agentenv_airops").is_dir():
            return str(root)
    if source:
        raise click.UsageError(f"{source} is not a checkout of agentenv-airops-plugin (no Dockerfile and src/)")
    if commit := _installed_commit():
        return f"{REPO}.git#{commit}"
    raise click.UsageError(f"no checkout of agentenv-airops-plugin found; clone {REPO} and pass --source")


def _context(root: str, subdir: str) -> str:
    return f"{root}:{subdir}" if "#" in root else str(Path(root) / subdir)


def _docker_platform() -> str:
    """The Docker host's own platform: the local providers pull images without emulation."""
    try:
        out = subprocess.run(["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise click.ClickException(f"docker is not available: {e}") from e
    if out.returncode:
        raise click.ClickException(f"docker is not running: {out.stderr.strip()}")
    return out.stdout.strip()


def _build(image: str, context: str, build_platform: str, what: str) -> DockerImageArtifact:
    click.echo(f"Building {image} for {build_platform} from {context}")
    if subprocess.run(["docker", "build", "--platform", build_platform, "-t", image, context]).returncode:
        raise click.ClickException(f"docker build of {what} failed")
    click.echo("Storing the image (docker save, can take a minute)")
    return DockerImageArtifact.put(id=image, image_name=image, description=what)


@airops.command()
@click.option("--platform", "build_platform",
              help="Platform to build for. Default: the Docker host's (linux/arm64 on Apple Silicon); "
                   "remote sandboxes need linux/amd64.")
@click.option("--source", type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Checkout of this repo to build. Default: the one an editable install runs from, the cwd, or the "
                   "GitHub commit a git install came from.")
@click.option("--agent", is_flag=True, help=f"Also build the {AGENT} agent for the same platform and register it.")
def setup(build_platform: str | None, source: Path | None, agent: bool):
    """Build the env image and register it as the MCP server env `airops-day` on the gateway provider; with --agent,
    the airops-llm agent too."""
    root = _checkout(source)
    build_platform = build_platform or _docker_platform()
    artifact = _build(IMAGE, root, build_platform, "AirOps env: the airops-day MCP server and the b6-v1 pack")
    env = MCPServerEnv.put(id=ENV, docker_image_artifact=artifact, environment_name=ENV, env_provider_type="gateway")
    click.echo(f"Registered env {env.id!r} version {env.version} (image {artifact.image_name})")
    if agent:
        image = _build(AGENT_IMAGE, _context(root, f"agents/{AGENT}"), build_platform,
                       f"{AGENT}: an LLM planner for AirOps days on agent-env's model endpoint")
        registered = A2AAgent.put(id=AGENT, docker_image_artifact=image, metadata={"default_model": AGENT_MODEL})
        click.echo(f"Registered A2A agent {registered.id!r} version {registered.version} (image {image.image_name})")
    click.echo("Next: agent-env run airops --task <task id> --model <litellm model id>")


airops.add_command(tasks_group)
airops.add_command(sweep_group)
airops.add_command(view_command)
airops.add_command(record_command)
