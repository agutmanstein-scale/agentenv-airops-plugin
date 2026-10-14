"""`agent-env airops setup` with a stand-in docker: what it builds, and the env and agent it registers."""

from pathlib import Path

import pytest
from agent_env.a2a_agent import A2AAgent
from agent_env.artifact import DockerImageArtifact
from agent_env.env import Env
from click.testing import CliRunner

from agentenv_airops import cli
from agentenv_airops.cli import airops

ROOT = Path(__file__).resolve().parents[2]


def fake_docker(tmp_path: Path, monkeypatch, build_exit: int = 0) -> Path:
    """A docker on PATH that logs its arguments and reports an arm64 host."""
    log, bin_dir = tmp_path / "docker.log", tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f'#!/bin/sh\necho "$@" >> {log}\ncase "$1" in\n  version) echo linux/arm64 ;;\n'
                      f"  build) exit {build_exit} ;;\nesac\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    return log


def builds(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines() if line.startswith("build ")]


@pytest.fixture
def stored(monkeypatch):
    """DockerImageArtifact.put without docker save and the registry push: the arguments setup stores the image with."""
    calls = []

    def put(id, *, description, image_name):
        calls.append({"id": id, "image_name": image_name})
        return DockerImageArtifact.put_tar(id, description=description, image_name=f"localhost:5000/{id}:v1",
                                           tar_gz_object_url="file:///dev/null")

    monkeypatch.setattr(cli.DockerImageArtifact, "put", put)
    return calls


@pytest.mark.parametrize(("args", "platform"), [([], "linux/arm64"), (["--platform", "linux/amd64"], "linux/amd64")])
def test_setup_builds_the_image_and_registers_airops_day_on_the_gateway(local_stores, tmp_path, monkeypatch, stored,
                                                                        args, platform):
    log = fake_docker(tmp_path, monkeypatch)
    result = CliRunner().invoke(airops, ["setup", "--source", str(ROOT), *args])
    assert result.exit_code == 0, result.output
    assert builds(log) == [f"build --platform {platform} -t agentenv-airops-env {ROOT}"]
    assert stored == [{"id": "agentenv-airops-env", "image_name": "agentenv-airops-env"}]
    env = Env.get("airops-day")
    assert (env.type, env.environment_name, env.env_provider_type) == ("mcp_server", "airops-day", "gateway")
    assert env.docker_image_artifact.image_name == "localhost:5000/agentenv-airops-env:v1"
    assert "Registered env 'airops-day' version 1" in result.output


def test_setup_with_agent_also_builds_and_registers_airops_llm(local_stores, tmp_path, monkeypatch, stored):
    log = fake_docker(tmp_path, monkeypatch)
    result = CliRunner().invoke(airops, ["setup", "--source", str(ROOT), "--agent"])
    assert result.exit_code == 0, result.output
    assert builds(log) == [f"build --platform linux/arm64 -t agentenv-airops-env {ROOT}",
                           f"build --platform linux/arm64 -t agentenv-airops-agent {ROOT / 'agents/airops-llm'}"]
    assert stored[1:] == [{"id": "agentenv-airops-agent", "image_name": "agentenv-airops-agent"}]
    agent = A2AAgent.get("airops-llm")
    assert agent.metadata["default_model"] == "anthropic/claude-sonnet-5-5"
    assert agent.docker_image_artifact.image_name == "localhost:5000/agentenv-airops-agent:v1"
    assert "Registered A2A agent 'airops-llm' version 1" in result.output


def test_setup_stops_when_the_build_fails(local_stores, tmp_path, monkeypatch, stored):
    fake_docker(tmp_path, monkeypatch, build_exit=1)
    result = CliRunner().invoke(airops, ["setup", "--source", str(ROOT)])
    assert result.exit_code == 1 and "docker build of AirOps env" in result.output
    assert stored == []


def test_setup_from_a_git_install_builds_the_commit_it_came_from(local_stores, tmp_path, monkeypatch, stored):
    log = fake_docker(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "site-packages/agentenv_airops/cli.py"))
    monkeypatch.setattr(cli, "_installed_commit", lambda: "4f79d0a4662eed1dba66d58cab18cc16895448dd")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(airops, ["setup", "--agent"])
    assert result.exit_code == 0, result.output
    repo = "https://github.com/earakely-scale/agentenv-airops-plugin.git#4f79d0a4662eed1dba66d58cab18cc16895448dd"
    assert builds(log) == [f"build --platform linux/arm64 -t agentenv-airops-env {repo}",
                           f"build --platform linux/arm64 -t agentenv-airops-agent {repo}:agents/airops-llm"]


def test_setup_needs_a_checkout_to_build(tmp_path, monkeypatch):
    result = CliRunner().invoke(airops, ["setup", "--source", str(tmp_path)])
    assert result.exit_code == 2 and "is not a checkout of agentenv-airops-plugin" in result.output
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "site-packages/agentenv_airops/cli.py"))
    monkeypatch.setattr(cli, "_installed_commit", lambda: None)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(airops, ["setup"])
    assert result.exit_code == 2 and "no checkout of agentenv-airops-plugin found" in result.output
