"""Self-directed two-phase loop: the model picks one official tool action per
turn through a JSON-only chat protocol.

The model sees the question, the database name, its own actions, the official
observations and the coin budget. It never sees ground truth, and execute_sql
and ask_user are neither offered nor executed. Phase transitions after a
passing Phase 1 submission stay under code control.
"""

import asyncio
import re
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

SQL_WHITESPACE = " \t\n\r\f\v"
DOLLAR_QUOTE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


class SessionExpired(RuntimeError):
    """The session time limit was reached, so no new call may start."""


def _limit_text() -> str:
    return f"the {SESSION_TIME_LIMIT_SECONDS:.0f}-second session limit"


def _now() -> float:
    return time.monotonic()


def _remaining(deadline: float) -> float:
    return deadline - _now()


async def _bounded(awaitable, remaining: float, label: str):
    """Await one external call inside the session deadline.

    The wait is capped so an in-flight model or service request cannot push the
    session past its limit. Cancelling stops our wait only: a request the
    official service already received is not rolled back, so callers record the
    interrupted action as an unknown outcome instead of a result.
    """
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining)
    except asyncio.TimeoutError as exc:
        raise SessionExpired(
            f"{_limit_text()} was reached while waiting for {label}; the call was cancelled "
            "and its outcome is unknown"
        ) from exc


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


def _quoted_span(text: str, start: int, quote: str, allow_escapes: bool) -> tuple[str, int] | None:
    """Return one quoted region verbatim, or None when its extent is ambiguous."""
    index = start + 1
    while index < len(text):
        if text[index] == "\\" and allow_escapes:
            return None
        if text[index] == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                index += 2
                continue
            return text[start:index + 1], index + 1
        index += 1
    return None


def _dollar_span(text: str, start: int) -> tuple[str, int] | None:
    match = DOLLAR_QUOTE.match(text, start)
    if match is None:
        return None
    end = text.find(match.group(0), match.end())
    if end == -1:
        return None
    end += len(match.group(0))
    return text[start:end], end


def _block_comment_end(text: str, start: int) -> int | None:
    depth = 0
    index = start
    while index < len(text):
        if text.startswith("/*", index):
            depth += 1
            index += 2
        elif text.startswith("*/", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
        else:
            index += 1
    return None


def submission_key(sql: str) -> str:
    """The duplicate key of one submission.

    Whitespace between tokens is collapsed, but the text inside string literals,
    quoted identifiers, dollar-quoted bodies and comments is preserved exactly:
    SQL that differs only there means something different, and blocking it would
    keep a corrected statement away from the official grader. Regions whose
    extent cannot be decided safely (escape strings, unbalanced quotes or
    comments) fall back to the unchanged text, which only narrows the check.
    """
    text = sql.strip()
    parts: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in SQL_WHITESPACE:
            while index < len(text) and text[index] in SQL_WHITESPACE:
                index += 1
            parts.append(" ")
            continue
        if char in "'\"":
            span = _quoted_span(text, index, char, allow_escapes=char == "'")
            if span is None:
                return text
            parts.append(span[0])
            index = span[1]
            continue
        if char == "$":
            span = _dollar_span(text, index)
            if span is not None:
                parts.append(span[0])
                index = span[1]
                continue
        elif text.startswith("--", index):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end + 1
            parts.append(text[index:end])
            index = end
            continue
        elif text.startswith("/*", index):
            end = _block_comment_end(text, index)
            if end is None:
                return text
            parts.append(text[index:end])
            index = end
            continue
        parts.append(char)
        index += 1
    return "".join(parts).strip()


def _is_duplicate_submission(state: dict, sql: str, phase: int) -> bool:
    key = submission_key(sql)
    return any(
        attempt["phase"] == phase and submission_key(attempt["sql"]) == key
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
    state: dict, model: ModelClient, phase: int, messages: list[dict], deadline: float
) -> tuple[ChatReply, dict]:
    try:
        reply = await _bounded(model.chat(messages), _remaining(deadline), "the model response")
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
    state: dict, environment: Environment, name: str, args: dict, phase: int, deadline: float
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
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before {name} could start")
    state["budget_remaining"] = budget_before - spec.cost
    try:
        content = await _bounded(spec.run(environment, state["task_id"], args), remaining, name)
    except SessionExpired as exc:
        record_action(
            state, name, args, {"error": str(exc)}, spec.cost, budget_before,
            phase=phase, timed_out=True,
        )
        raise
    except GradingError as exc:
        record_action(
            state, name, args, {"error": str(exc)}, spec.cost, budget_before, phase=phase
        )
        state["failure"] = {"stage": "information", "message": str(exc)}
        raise
    record_action(state, name, args, content, spec.cost, budget_before, phase=phase)
    return content


async def _submit_action(
    state: dict,
    environment: Environment,
    sql: str,
    phase: int,
    messages: list[dict],
    index: int,
    deadline: float,
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

    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before the submission could start")
    state["budget_remaining"] = budget_before - SUBMIT_COST
    try:
        result = await _bounded(
            environment.submit(state["task_id"], sql), remaining, "the submission"
        )
    except SessionExpired as exc:
        # The grader may already have received the statement, so this is recorded as an
        # unknown outcome instead of a failure or a pass.
        record_action(
            state, "submit_sql", {"sql": sql}, {"error": str(exc)}, SUBMIT_COST, budget_before,
            phase=phase, timed_out=True,
        )
        raise
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
    state.setdefault("submit_attempts", []).append({"phase": phase, "sql": sql})

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
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise SessionExpired(
                    f"{_limit_text()} was reached before the phase transition could start"
                )
            await _bounded(
                environment.transition_to_phase2(state["task_id"]), remaining, "the phase transition"
            )
        except SessionExpired as exc:
            state["phase_transition"] = {"completed": False, "message": str(exc)}
            raise
        except GradingError as exc:
            state["failure"] = {"stage": "transition", "message": str(exc)}
            raise
        state["phase_transition"] = {"completed": True}
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
    deadline = started + SESSION_TIME_LIMIT_SECONDS
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
        if _remaining(deadline) <= 0:
            return stop(
                "session_time_limit",
                f"Stopped after {_limit_text()}. {_outcome(state)}",
            )
        # A submission is the only action that completes the task and it is the most
        # expensive one, so a budget below its price cannot end the session successfully.
        if budget(state) < SUBMIT_COST:
            return stop(
                "budget_exhausted_before_submit",
                f"Stopped: {budget(state):g} bird-coins remain and a submission costs "
                f"{SUBMIT_COST:g}. {_outcome(state)}",
            )

        try:
            reply, event = await _model_call(state, model, phase, messages, deadline)
        except SessionExpired as exc:
            return stop("session_time_limit", f"Stopped: {exc}. {_outcome(state)}")
        messages.append({"role": "assistant", "content": reply.content})
        # The response may have arrived right at the limit; nothing is executed after it.
        if _remaining(deadline) <= 0:
            return stop(
                "session_time_limit",
                f"Stopped: {_limit_text()} was reached after the model response, so the action "
                f"was not executed. {_outcome(state)}",
            )
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

        try:
            if name == "submit_sql":
                outcome = await _submit_action(
                    state, environment, args["sql"], phase, messages, index, deadline
                )
                if outcome is not None:
                    return stop(*outcome)
                continue

            content = await _information(state, environment, name, args, phase, deadline)
        except SessionExpired as exc:
            return stop("session_time_limit", f"Stopped: {exc}. {_outcome(state)}")
        messages.append({
            "role": "user",
            "content": _observation(index, phase, name, ALLOWED_TOOLS[name].cost, state, content),
        })
