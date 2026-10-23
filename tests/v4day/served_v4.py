"""A real airops-v4-day env serving programs tasks from a tasks.jsonl on a free port, with a stand-in for the
gateway's clock routes on the same server that records each re-arm, and a client calling its tools over MCP."""

import json
from contextlib import contextmanager
from pathlib import Path

from fastmcp import Client
from served import serve
from starlette.responses import JSONResponse

from agentenv_airops.programs.model import Task, write_tasks
from agentenv_airops.v4day.clock import virtual_time
from agentenv_airops.v4day.live import TASKS_VAR, AirOpsV4LiveEnv


@contextmanager
def served_v4(monkeypatch, tmp_path: Path, tasks: list[Task], task_id: str | None = None, synced: bool = True,
              refuse: int = 0, **refs):
    """A fresh env on `tasks`, the day `task_id` (default the first) loaded with `refs` (anchor, follower): (base
    url, env, the clock's PUTs). The clock answers the first `refuse` PUTs with a 503, as a gateway hiccup."""
    monkeypatch.setenv(TASKS_VAR, str(write_tasks(tasks, tmp_path / "tasks.jsonl")))
    env = AirOpsV4LiveEnv()
    mcp = env.create_app()
    puts: list[dict] = []
    refused: list[dict] = []
    task = next(t for t in tasks if t.task_id == (task_id or tasks[0].task_id))

    @mcp.custom_route("/clock/time", methods=["GET"])
    async def time(request):
        return JSONResponse({"virtual_time": puts[-1]["virtual_time"] if puts else virtual_time(task, task.start)})

    @mcp.custom_route("/clock/set-time", methods=["PUT"])
    async def set_time(request):
        if len(refused) < refuse:
            refused.append(await request.json())
            return JSONResponse({"error": "gateway hiccup"}, status_code=503)
        puts.append(await request.json())
        return JSONResponse({"armed": True})

    env.live_load(task.task_id, **refs)
    with serve(mcp.http_app()) as url:
        if synced:
            env.set_time_url = f"{url}/clock/set-time"
        yield url, env, puts


class Tools:
    """Calls tools as an MCP client does, through argument validation and the env's middleware; results are JSON."""

    def __init__(self, client: Client):
        self.client = client

    async def __call__(self, tool: str, /, **arguments) -> dict:
        result = await self.client.call_tool_mcp(tool, arguments)
        assert not result.isError, result.content[0].text
        return json.loads(result.content[0].text)

    async def error(self, tool: str, /, **arguments) -> str:
        result = await self.client.call_tool_mcp(tool, arguments)
        assert result.isError
        return result.content[0].text
