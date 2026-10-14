"""The instance contexts an AirOps run stores, as `Task.run()` stores them (`TaskStepContext.to_safe_dict()`): the
verifier's score and the day it read from the env, and the play step's PromptResponse and failures."""

from pathlib import Path

from agent_env.task_step.context import PromptResponse, TaskStepContext

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tasks.jsonl"


def summary(model: str, end_reason: str = "done", reward: float | None = 0.840588, cost: float = 0.224018,
            **fields) -> dict:
    """airops-llm's structured output."""
    return {"agent": "airops-llm", "model": model, "route": "messages", "end_reason": end_reason, "reward": reward,
            "turns": 9, "tool_calls": 43, "checks": 2, "confirms": 9, "input_tokens": 251050, "output_tokens": 17392,
            "cached_tokens": 200000, "cache_write_tokens": 30000, "cost_usd": cost, "max_cost_usd": 5.0,
            "priced_cost_usd": cost, "failed_requests": 0, "seconds": 124.5, "error": None} | fields


def day(task: str, reward: float, end_reason: str = "done", cost: int = 691780) -> dict:
    """data/get once the day ended, as the verifier reads it."""
    return {"task_id": task, "watches": 9, "watch": 8, "t": 1500, "done": True, "end_reason": end_reason,
            "calls_used": 26, "planning_calls": [1, 1, 1, 1, 1, 1, 1, 1, 1], "plan": [], "frozen": [], "notices": [],
            "freezes": [], "changes": [], "refusals": [{"watch": 1, "flight": "f12", "from": "Duty manager",
                                                        "reason": "f12 is frozen"}],
            "estimates": [], "audit": {"ok": True, "problems": []},
            "grade": {"cost": {"pax_delay": cost - 273435, "cancel": 230120, "ferry": 43315, "total": cost},
                      "counts": {"flights": 45, "flown": 40, "cancelled": 4, "auto_cancelled": 1, "ferries": 3},
                      "reward": reward, "executed": [], "ferries": []},
            "reward": reward}


def play(task: str, model: str, structured: dict | None, code: str | None = None, message: str | None = None,
         trajectory: str | None = "file:///state/agent-env/object_store/trajectories/play.json") -> PromptResponse:
    return PromptResponse(prompt_id=task, response=f"{code or 'done'}: ...", agent_trajectory_object_url=trajectory,
                          model=model, error_type="infra_error" if code else None, error_code=code,
                          error_message=message, agent_session_id="a2a-1", agent_name="airops-llm", step_id="play",
                          structured_output=structured)


def failure(step_id: str, step_type: str, error: str, error_type: str = "RuntimeError") -> dict:
    return {"step_id": step_id, "step_type": step_type, "error": error, "error_type": error_type,
            "started_at_utc": "2026-10-13T12:00:00+00:00", "duration_seconds": 3.0, "is_fatal": True}


def stored(prompt: PromptResponse | None = None, **metadata) -> dict:
    context = TaskStepContext(prompt_responses=[prompt] if prompt else [], metadata=metadata,
                              instance_id="@local/fake/tasks/t-1")
    return context.to_safe_dict()


def scored(task: str, model: str, reward: float = 0.840588, cost: float = 0.224018, end_reason: str = "done",
           day_cost: int = 691780, **fields) -> dict:
    agent = reward if end_reason == "done" else None
    return stored(play(task, model, summary(model, end_reason=end_reason, reward=agent, cost=cost, **fields)),
                  verifications={"airops": {"results": [
                      {"criterion": "the final plan's reward on the day as it happened", "result": reward >= 1.0,
                       "score": reward, "episode": day(task, reward, "done" if end_reason == "done" else "end_day",
                                                       day_cost)},
                      {"criterion": "every watch got its news", "result": True, "score": 1.0, "weight": 0}],
                      "score": reward}})


def agent_failed(task: str, model: str, code: str, cost: float = 0.05) -> dict:
    return stored(play(task, model, summary(model, end_reason=code, reward=None, cost=cost,
                                            error=f"APIStatusError: {code}"),
                       code=code, message=f"{code}: the episode stopped"),
                  failed_steps=[failure("play", "prompt_agent",
                                        f"A2A task failed (error_type=infra_error, error_code={code}): {code}")])


def step_failed(step_id: str, step_type: str) -> dict:
    return stored(failed_steps=[failure(step_id, step_type, f"{step_id} failed: the sandbox did not start")])


def crashed(task: str, model: str) -> dict:
    """The agent failed without a structured output: prompt_agent recorded its response and raised."""
    return stored(play(task, model, None), failed_steps=[
        failure("play", "prompt_agent", "A2A task failed (exception=KeyError): the agent crashed")])


def timed_out() -> dict:
    return stored(failed_steps=[failure("play", "prompt_agent", "no answer in 7200s", "TimeoutError")])
