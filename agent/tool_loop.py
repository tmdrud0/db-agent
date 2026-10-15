"""Self-directed two-phase loop: the model picks one official tool action per
turn through a JSON-only chat protocol.

The model sees the question, the database name, its own actions, the official
observations and the coin budget. It never sees ground truth, and execute_sql
and ask_user are neither offered nor executed. Phase transitions after a
passing Phase 1 submission stay under code control.
"""

import time

from agent.environment import Environment, GradingError
from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REQUEST_TIMEOUT_SECONDS,
    ChatReply,
    ModelClient,
    ModelError,
)
from agent.session import (
    budget,
    database_line,
    finish,
    record_action,
    resolve_query,
    restore_failure,
)
from agent.tools import ALLOWED_TOOLS, SUBMIT_COST, ActionError, catalog, parse_action

MAX_MODEL_ACTIONS = 24
SESSION_TIME_LIMIT_SECONDS = 1500.0

SYSTEM_PROMPT = (
    "You solve PostgreSQL tasks for a benchmark by choosing exactly one action per turn.\n"
    "Every action costs bird-coins from a fixed budget, so spend coins only on actions that\n"
    "improve your final SQL.\n"
    "\n"
    "Reply with one JSON object and nothing else:\n"
    '{"action": "<tool name>", "args": {<tool arguments>}}\n'
    "\n"
    "Available actions:\n"
    f"{catalog()}\n"
    "\n"
    "Rules:\n"
    "- Only the actions above exist. Any other action name is rejected without cost.\n"
    "- An information request that repeats an earlier one is answered from the session cache;\n"
    "  its coin cost still applies.\n"
    "- Submitting the same SQL text twice in one phase is rejected without cost; revise the SQL\n"
    "  before submitting again.\n"
    "- A failed submission can be retried while the budget lasts. The official rewards are 0.7\n"
    "  for phase 1 and 0.3 for phase 2.\n"
    "- A passing phase 1 submission applies its SQL to the database and starts phase 2 with a\n"
    "  follow-up question.\n"
    f"- The session stops after {MAX_MODEL_ACTIONS} model actions or "
    f"{SESSION_TIME_LIMIT_SECONDS:.0f} seconds, and it stops when fewer than "
    f"{SUBMIT_COST:g} bird-coins remain because a submission would no longer be possible.\n"
)


def _now() -> float:
    return time.monotonic()


def _initial_prompt(state: dict, query: str) -> str:
    initial = state.get("initial_budget")
    given = f"{float(initial):g}" if isinstance(initial, (int, float)) else f"{budget(state):g}"
    return (
        f"{database_line(state)}User Query:\n{query}\n\n"
        f"You have {given} bird-coins for this task and {budget(state):g} remain. "
        "Send one JSON action."
    )


def _observation(index: int, phase: int, name: str, cost: float, state: dict, body: str) -> str:
    return (
        f"[action {index} | phase {phase} | {name} | cost {cost:g} | "
        f"budget {budget(state):g}]\n{body}"
    )


def _outcome(state: dict) -> str:
    if state.get("phase1_completed"):
        return "Phase 1 passed; the follow-up phase is incomplete."
    return "Phase 1 is incomplete."


def _model_actions(state: dict) -> int:
    return sum(1 for event in state.get("adk_events", []) if event.get("type") == "model_call")


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _is_duplicate_submission(state: dict, sql: str, phase: int) -> bool:
    normalized = _normalize_sql(sql)
    return any(
        attempt["phase"] == phase and attempt["sql"] == normalized
        for attempt in state.get("submit_attempts", [])
    )


def _cached_result(state: dict, name: str, args: dict) -> str | None:
    for entry in state.get("tool_trajectory", []):
        if (
            entry.get("tool") == name
            and entry.get("args") == args
            and isinstance(entry.get("result"), str)
        ):
            return entry["result"]
    return None


async def _model_call(
    state: dict, model: ModelClient, phase: int, messages: list[dict]
) -> tuple[ChatReply, dict]:
    try:
        reply = await model.chat(messages)
    except ModelError as exc:
        state["failure"] = {"stage": "model", "message": str(exc)}
        raise
    events = state.setdefault("adk_events", [])
    event = {
        "type": "model_call",
        "call_index": 1 + _model_actions(state),
        "phase": phase,
        "request_model": MODEL_ID,
        "response_model": reply.response_model,
        "content": reply.content,
        "truncated": reply.truncated,
        "usage": reply.usage,
        "settings": {
            "endpoint": API_URL,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
        "elapsed_seconds": reply.elapsed_seconds,
        "prompt": [dict(message) for message in messages],
    }
    events.append(event)
    return reply, event


async def _information(
    state: dict, environment: Environment, name: str, args: dict, phase: int
) -> str:
    spec = ALLOWED_TOOLS[name]
    budget_before = budget(state)
    cached = _cached_result(state, name, args)
    if cached is not None:
        # The repeated request is not sent again, but the official price still applies.
        state["budget_remaining"] = budget_before - spec.cost
        record_action(
            state, name, args, {"cache_hit": True}, spec.cost, budget_before,
            phase=phase, cached=True,
        )
        return f"Served from the session cache; the same request was already made.\n{cached}"
    state["budget_remaining"] = budget_before - spec.cost
    try:
        content = await spec.run(environment, state["task_id"], args)
    except GradingError as exc:
        record_action(
            state, name, args, {"error": str(exc)}, spec.cost, budget_before, phase=phase
        )
        state["failure"] = {"stage": "information", "message": str(exc)}
        raise
    record_action(state, name, args, content, spec.cost, budget_before, phase=phase)
    return content


async def _submit_action(
    state: dict, environment: Environment, sql: str, phase: int, messages: list[dict], index: int
) -> tuple[str, str] | None:
    """Submit, or report a blocked duplicate. Returns the stop reason when the task ends."""
    budget_before = budget(state)
    if _is_duplicate_submission(state, sql, phase):
        record_action(
            state, "submit_sql", {"sql": sql}, {"duplicate_sql": True}, 0.0, budget_before,
            phase=phase, blocked="duplicate_sql",
        )
        body = (
            f"The same SQL text was already submitted in phase {phase} and was not sent again. "
            "Revise the SQL before submitting."
        )
        messages.append({
            "role": "user",
            "content": _observation(index, phase, "submit_sql", 0.0, state, body),
        })
        return None

    state["budget_remaining"] = budget_before - SUBMIT_COST
    try:
        result = await environment.submit(state["task_id"], sql)
    except GradingError as exc:
        record_action(
            state, "submit_sql", {"sql": sql}, {"error": str(exc)}, SUBMIT_COST, budget_before,
            phase=phase,
        )
        state["failure"] = {"stage": "grader", "message": str(exc)}
        raise
    record_action(
        state, "submit_sql", {"sql": sql}, result.model_dump(), SUBMIT_COST, budget_before,
        phase=phase,
    )
    state["last_submit"] = result.model_dump()
    state.setdefault("submit_attempts", []).append({"phase": phase, "sql": _normalize_sql(sql)})

    if not result.passed:
        messages.append({
            "role": "user",
            "content": _observation(index, phase, "submit_sql", SUBMIT_COST, state, result.message),
        })
        return None

    # Rewards come from the official responses; 0.7/0.3 are never awarded here.
    state["total_reward"] += result.reward
    if result.phase_completed == 1:
        state["phase1_completed"] = True
        if not result.has_follow_up:
            state["task_done"] = True
            return "phase1_completed", result.message
        state["follow_up_query"] = result.follow_up_query
        state["current_phase"] = 2
        # The official user simulator switches to the follow-up reference exactly once.
        try:
            await environment.transition_to_phase2(state["task_id"])
        except GradingError as exc:
            state["failure"] = {"stage": "transition", "message": str(exc)}
            raise
        body = (
            f"{result.message}\nThe phase 1 SQL is applied to the database.\n"
            f"Follow-up question:\n{result.follow_up_query or ''}"
        )
        messages.append({
            "role": "user",
            "content": _observation(index, phase, "submit_sql", SUBMIT_COST, state, body),
        })
        return None

    state["phase2_completed"] = True
    state["task_done"] = True
    return "phase2_completed", result.message


async def run(state: dict, message: str, environment: Environment, model: ModelClient) -> str:
    # Repeated requests return the cached outcome instead of calling out again.
    if state.get("loop_stopped"):
        return state["final_response"]
    # A failed attempt is never retried inside the same session.
    if state.get("failure"):
        raise restore_failure(state["failure"])

    query = resolve_query(state, message)
    started = _now()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _initial_prompt(state, query)},
    ]

    def stop(reason: str, response: str) -> str:
        state["session_elapsed_seconds"] = round(_now() - started, 3)
        return finish(state, reason, response)

    while True:
        phase = int(state.get("current_phase", 1))
        index = _model_actions(state) + 1
        if index > MAX_MODEL_ACTIONS:
            return stop(
                "action_limit_reached",
                f"Stopped after {MAX_MODEL_ACTIONS} model actions. {_outcome(state)}",
            )
        if _now() - started >= SESSION_TIME_LIMIT_SECONDS:
            return stop(
                "session_time_limit",
                f"Stopped after the {SESSION_TIME_LIMIT_SECONDS:.0f}-second session limit. "
                f"{_outcome(state)}",
            )
        # A submission is the only action that completes the task and it is the most
        # expensive one, so a budget below its price cannot end the session successfully.
        if budget(state) < SUBMIT_COST:
            return stop(
                "budget_exhausted_before_submit",
                f"Stopped: {budget(state):g} bird-coins remain and a submission costs "
                f"{SUBMIT_COST:g}. {_outcome(state)}",
            )

        reply, event = await _model_call(state, model, phase, messages)
        messages.append({"role": "assistant", "content": reply.content})
        try:
            name, args = parse_action(reply.content)
        except ActionError as exc:
            reason = str(exc)
            if reply.truncated:
                reason = "The response hit the output token limit before a complete action. " + reason
            event["action_error"] = reason
            event["action"] = None
            messages.append({
                "role": "user",
                "content": _observation(index, phase, "rejected", 0.0, state, reason),
            })
            continue
        event["action"] = {"name": name, "args": args}

        if name == "submit_sql":
            outcome = await _submit_action(state, environment, args["sql"], phase, messages, index)
            if outcome is not None:
                return stop(*outcome)
            continue

        content = await _information(state, environment, name, args, phase)
        messages.append({
            "role": "user",
            "content": _observation(index, phase, name, ALLOWED_TOOLS[name].cost, state, content),
        })
