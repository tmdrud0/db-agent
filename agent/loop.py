"""Fixed-context two-phase baseline: one information fetch round, then one
model call and one official submission per phase."""

import json

from agent.environment import Environment, GradingError, SubmitResult
from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REQUEST_TIMEOUT_SECONDS,
    ModelClient,
    ModelError,
    ModelReply,
)

INFORMATION_COST = 1.0
SUBMIT_COST = 3.0

INFORMATION_FETCHES = (
    ("get_schema", Environment.fetch_schema),
    ("get_all_column_meanings", Environment.fetch_column_meanings),
    ("get_all_knowledge_definitions", Environment.fetch_knowledge),
)


class InputError(ValueError):
    """The runner did not provide a question to answer."""


def _budget(state: dict) -> float:
    return float(state.get("budget_remaining", 0))


def _resolve_query(state: dict, message: str) -> str:
    query = str(state.get("user_query") or "").strip() or message.strip()
    if not query:
        raise InputError("state.user_query and the run message are both empty")
    return query


def _database_line(state: dict) -> str:
    db_name = str(state.get("db_name") or "").strip()
    return f"Database: {db_name}\n\n" if db_name else ""


def _information_block(information: dict[str, str]) -> str:
    return (
        "Database schema (initial schema file, not a live dump of the task database):\n"
        f"{information['get_schema']}\n\n"
        "Meanings of all columns (official get_all_column_meanings response):\n"
        f"{information['get_all_column_meanings']}\n\n"
        "External knowledge definitions (official get_all_knowledge_definitions response):\n"
        f"{information['get_all_knowledge_definitions']}"
    )


def _phase1_prompt(state: dict, query: str, information: dict[str, str]) -> str:
    return f"{_database_line(state)}User Query:\n{query}\n\n{_information_block(information)}"


def _phase2_prompt(
    state: dict,
    query: str,
    information: dict[str, str],
    phase1_sql: str,
    phase1_result: SubmitResult,
) -> str:
    return (
        f"{_database_line(state)}Original user query:\n{query}\n\n"
        f"{_information_block(information)}\n\n"
        "Phase 1 SQL that is already applied to the database:\n"
        f"{phase1_sql}\n\n"
        "Phase 1 official result:\n"
        f"{json.dumps(phase1_result.model_dump(), ensure_ascii=False, sort_keys=True)}\n\n"
        "Follow-up question:\n"
        f"{phase1_result.follow_up_query or ''}\n\n"
        "The follow-up question continues after the Phase 1 SQL above has been applied. "
        "Return only the final PostgreSQL SQL for this follow-up step."
    )


def _restore_failure(failure: dict) -> Exception:
    message = str(failure.get("message", "The previous attempt failed"))
    if failure.get("stage") == "model":
        return ModelError(message)
    return GradingError(message)


def _finish(state: dict, stop_reason: str, response: str) -> str:
    state["stop_reason"] = stop_reason
    state["loop_stopped"] = True
    state["final_response"] = response
    return response


def _record_action(
    state: dict, tool: str, args: dict, result, cost: float, budget_before: float
) -> None:
    state.setdefault("tool_trajectory", []).append({
        "type": "tool",
        "tool": tool,
        "args": args,
        "result": result,
        "cost": cost,
        "budget_before": budget_before,
        "budget_after": state["budget_remaining"],
    })


async def _fetch_information(state: dict, environment: Environment, tool_name: str, fetch) -> str:
    budget_before = _budget(state)
    state["budget_remaining"] = budget_before - INFORMATION_COST
    try:
        content = await fetch(environment, state["task_id"])
    except GradingError as exc:
        _record_action(
            state, tool_name, {"task_id": state["task_id"]}, {"error": str(exc)},
            INFORMATION_COST, budget_before,
        )
        state["failure"] = {"stage": "information", "message": str(exc)}
        raise
    _record_action(
        state, tool_name, {"task_id": state["task_id"]}, content,
        INFORMATION_COST, budget_before,
    )
    return content


async def _model_call(state: dict, model: ModelClient, phase: int, prompt: str) -> ModelReply:
    try:
        reply = await model.generate_sql(prompt)
    except ModelError as exc:
        state["failure"] = {"stage": "model", "message": str(exc)}
        raise
    events = state.setdefault("adk_events", [])
    events.append({
        "type": "model_call",
        "call_index": 1 + sum(1 for event in events if event.get("type") == "model_call"),
        "phase": phase,
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
        "prompt": prompt,
    })
    return reply


async def _submit(state: dict, environment: Environment, sql: str) -> SubmitResult:
    budget_before = _budget(state)
    state["budget_remaining"] = budget_before - SUBMIT_COST
    try:
        result = await environment.submit(state["task_id"], sql)
    except GradingError as exc:
        _record_action(
            state, "submit_sql", {"sql": sql}, {"error": str(exc)}, SUBMIT_COST, budget_before
        )
        state["failure"] = {"stage": "grader", "message": str(exc)}
        raise
    _record_action(
        state, "submit_sql", {"sql": sql}, result.model_dump(), SUBMIT_COST, budget_before
    )
    state["last_submit"] = result.model_dump()
    return result


async def run(state: dict, message: str, environment: Environment, model: ModelClient) -> str:
    # Repeated requests return the cached outcome instead of calling out again.
    if state.get("loop_stopped"):
        return state["final_response"]
    # A failed attempt is never retried inside the same session.
    if state.get("failure"):
        raise _restore_failure(state["failure"])

    query = _resolve_query(state, message)

    # Fixed information round: each official fetch is tried once and costs one coin.
    information: dict[str, str] = {}
    for tool_name, fetch in INFORMATION_FETCHES:
        budget = _budget(state)
        if budget < INFORMATION_COST:
            return _finish(
                state, "budget_exhausted_before_information",
                f"Stopped before the fixed information fetches: {budget:g} bird-coins "
                f"remaining (each fetch costs {INFORMATION_COST:g} bird-coin).",
            )
        information[tool_name] = await _fetch_information(state, environment, tool_name, fetch)

    budget = _budget(state)
    if budget < SUBMIT_COST:
        return _finish(
            state, "budget_exhausted_before_phase1",
            f"Stopped before Phase 1: {budget:g} bird-coins remaining after the information "
            f"fetches (a submission costs {SUBMIT_COST:g} bird-coins).",
        )

    reply = await _model_call(state, model, 1, _phase1_prompt(state, query, information))
    result = await _submit(state, environment, reply.sql)

    if not result.passed:
        return _finish(state, "phase1_failed", result.message)

    # Rewards come from the official responses; 0.7/0.3 are never awarded here.
    state["total_reward"] += result.reward
    state["phase1_completed"] = True
    if not result.has_follow_up:
        state["task_done"] = True
        return _finish(state, "phase1_completed", result.message)

    state["follow_up_query"] = result.follow_up_query
    state["current_phase"] = 2

    budget = _budget(state)
    if budget < SUBMIT_COST:
        return _finish(
            state, "budget_exhausted_before_phase2",
            f"Phase 1 passed, but stopped before the follow-up: {budget:g} bird-coins "
            f"remaining (a submission costs {SUBMIT_COST:g} bird-coins).",
        )

    # The official user simulator switches to the follow-up reference once.
    try:
        await environment.transition_to_phase2(state["task_id"])
    except GradingError as exc:
        state["failure"] = {"stage": "transition", "message": str(exc)}
        raise

    reply2 = await _model_call(
        state, model, 2, _phase2_prompt(state, query, information, reply.sql, result)
    )
    result2 = await _submit(state, environment, reply2.sql)

    if result2.passed:
        state["total_reward"] += result2.reward
        state["phase2_completed"] = True
        state["task_done"] = True
        return _finish(state, "phase2_completed", result2.message)

    return _finish(state, "phase2_failed", result2.message)
