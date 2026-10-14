"""Phase 1 baseline: one model call and one official submission per session."""

from agent.environment import Environment, GradingError
from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REQUEST_TIMEOUT_SECONDS,
    ModelClient,
    ModelError,
)


class InputError(ValueError):
    """The runner did not provide a question to answer."""


def _user_prompt(state: dict, message: str) -> str:
    query = str(state.get("user_query") or "").strip() or message.strip()
    if not query:
        raise InputError("state.user_query and the run message are both empty")
    db_name = str(state.get("db_name") or "").strip()
    if not db_name:
        return f"User Query:\n{query}"
    return f"Database: {db_name}\n\nUser Query:\n{query}"


def _restore_failure(failure: dict) -> Exception:
    message = str(failure.get("message", "The previous attempt failed"))
    if failure.get("stage") == "model":
        return ModelError(message)
    return GradingError(message)


def _record_model_call(state: dict, reply) -> None:
    events = state.setdefault("adk_events", [])
    events.append({
        "type": "model_call",
        "call_index": 1 + sum(1 for event in events if event.get("type") == "model_call"),
        "request_model": MODEL_ID,
        "response_model": reply.response_model,
        "sql": reply.sql,
        "usage": reply.usage,
        "settings": {
            "endpoint": API_URL,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
        "elapsed_seconds": reply.elapsed_seconds,
    })


async def run(state: dict, message: str, environment: Environment, model: ModelClient) -> str:
    # Repeated requests return the cached outcome instead of calling out again.
    if state.get("loop_stopped"):
        return state["final_response"]
    # A failed attempt is never retried inside the same session.
    if state.get("failure"):
        raise _restore_failure(state["failure"])

    prompt = _user_prompt(state, message)
    try:
        reply = await model.generate_sql(prompt)
    except ModelError as exc:
        state["failure"] = {"stage": "model", "message": str(exc)}
        raise
    _record_model_call(state, reply)

    budget_before = float(state.get("budget_remaining", 0))
    # Match the official submit callback: a final submission is allowed even
    # with less than 3 coins, and -1 prevents further actions.
    state["budget_remaining"] = budget_before - 3 if budget_before > 3 else -1.0
    try:
        result = await environment.submit(state["task_id"], reply.sql)
    except GradingError as exc:
        state["failure"] = {"stage": "grader", "message": str(exc)}
        raise
    state.setdefault("tool_trajectory", []).append({
        "type": "tool",
        "tool": "submit_sql",
        "args": {"sql": reply.sql},
        "result": result.model_dump(),
        "cost": 3.0,
        "budget_before": budget_before,
        "budget_after": state["budget_remaining"],
    })
    state["last_submit"] = result.model_dump()

    # Never infer success from the SQL or from the model response.
    if result.passed:
        state["total_reward"] += result.reward
        if result.phase_completed == 1:
            state["phase1_completed"] = True
            if result.has_follow_up:
                # Phase 2 stays out of scope for this baseline; its follow-up is
                # recorded but never answered or transitioned to.
                state["follow_up_query"] = result.follow_up_query
                state["stop_reason"] = "phase1_baseline_phase2_skipped"
            else:
                state["task_done"] = True
                state["stop_reason"] = "phase1_baseline_completed"
        else:
            state["phase2_completed"] = True
            state["task_done"] = True
            state["stop_reason"] = "phase1_baseline_completed"
    else:
        state["stop_reason"] = "phase1_baseline_failed"

    state["loop_stopped"] = True
    state["final_response"] = result.message
    return result.message
