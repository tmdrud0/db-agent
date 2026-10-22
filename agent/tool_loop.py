"""Self-directed two-phase loop: the model picks one official tool action per
turn through a JSON-only chat protocol.

The model sees the question, the database name, its own actions, the official
observations and the coin budget. It never sees ground truth. All nine official
tools are offered: information lookups, execute_sql exploration, ask_user
questions and submit_sql. Phase transitions after a passing Phase 1 submission
stay under code control.
"""

import asyncio
import json
import time

from agent.environment import Environment, ExecuteResult, GradingError
from agent.harness import Harness
from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MODEL_ID,
    REQUEST_TIMEOUT_SECONDS,
    ChatReply,
    ModelClient,
    ModelError,
)
from agent.oracle_context import (
    ORACLE_ACTIONS,
    OracleContext,
    TaskContext,
    follow_up_section,
    initial_section,
)
from agent.session import (
    budget,
    database_line,
    finish,
    record_action,
    resolve_query,
    restore_failure,
)
from agent.subagent import build_user_message
from agent.tools import (
    ALLOWED_TOOLS,
    CONSULT_COST,
    CONSULT_SPEC,
    SUBMIT_COST,
    ActionError,
    ParsedAction,
    ToolSpec,
    catalog,
    consult_catalog,
    parse_response,
)
from agent.tracing import Generation, SessionTrace, Tracer

MAX_MODEL_ACTIONS = 24
SESSION_TIME_LIMIT_SECONDS = 1500.0


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


def system_prompt(harness: Harness, oracle: bool = False) -> str:
    """Assemble the harness guidance and the code-owned contract.

    Order: harness ``system.md``, code contract, harness ``memory.md``. The
    contract (response format, tools, prices, limits, rewards, phase rules) is
    always the text built here; harness files cannot replace it. The consult
    line and its rules appear only when the harness defines a sub-agent, so a
    harness without one builds the same prompt as before. In the oracle-context
    experiment the catalog lists only the actions that mode allows.
    """
    actions = catalog(harness.tool_notes, _oracle_tools() if oracle else None)
    rules = (
        "- Only the actions above exist. Any other action name is rejected without cost.\n"
        "- execute_sql explores with one read-only statement. Its result never changes the score\n"
        "  and never replaces submit_sql; an official SQL error is an observation, not a penalty.\n"
        "- ask_user receives the official answer of the task user. The answer is an observation;\n"
        "  decide yourself whether and when to ask.\n"
        "- A call is charged its coin cost when it starts; a failed or interrupted call is not\n"
        "  refunded.\n"
        "- A failed submission can be retried while the budget lasts. The official rewards are 0.7\n"
        "  for phase 1 and 0.3 for phase 2.\n"
        "- A passing phase 1 submission applies its SQL to the database and starts phase 2 with a\n"
        "  follow-up question.\n"
        f"- The session stops after {MAX_MODEL_ACTIONS} model actions or "
        f"{SESSION_TIME_LIMIT_SECONDS:.0f} seconds, and it stops when fewer than "
        f"{SUBMIT_COST:g} bird-coins remain because a submission would no longer be possible."
    )
    if harness.sub_agents:
        actions += "\n" + consult_catalog(
            (sub.name, sub.description, sub.fetch)
            for sub in harness.sub_agents.values()
        )
        rules += (
            "\n- consult calls one sub-agent defined by the harness. It is not an official tool\n"
            "  and costs no bird-coins, but the response that chooses it still counts as one model\n"
            "  action.\n"
            "- A sub-agent cannot use the database, the user simulator or the grader. It sees only\n"
            "  the context listed with its name and the request, and its answer is an observation\n"
            "  that never changes the score."
        )
        if any(sub.fetch for sub in harness.sub_agents.values()):
            rules += (
                "\n- A sub-agent listed with \"Fetches <tool> (cost <n>)\" receives that official\n"
                "  lookup when it is consulted. The lookup runs once, is charged its official coin\n"
                "  cost, and its raw output is shown only to the sub-agent; the main agent receives\n"
                "  only the sub-agent's answer."
            )
    contract = (
        "Reply with one JSON object and nothing else:\n"
        '{"action": "<tool name>", "args": {<tool arguments>}}\n'
        "\n"
        "Available actions:\n"
        f"{actions}\n"
        "\n"
        "Rules:\n"
        f"{rules}"
    )
    parts = []
    guidance = harness.system.strip("\n")
    if guidance.strip():
        parts.append(guidance)
    parts.append(contract)
    memory = harness.memory.strip()
    if memory:
        parts.append(f"Harness memory:\n{memory}")
    return "\n\n".join(parts) + "\n"


def _now() -> float:
    return time.monotonic()


def _initial_prompt(state: dict, query: str, context: TaskContext | None = None) -> str:
    initial = state.get("initial_budget")
    if isinstance(initial, (int, float)):
        # The oracle-context experiment starts with its offset already taken, so
        # the coins it announces are the ones the model can actually spend.
        offset = float(state["experiment"]["budget_offset"]) if context is not None else 0.0
        given = f"{float(initial) - offset:g}"
    else:
        given = f"{budget(state):g}"
    provided = f"{initial_section(context)}\n\n" if context is not None else ""
    return (
        f"{database_line(state)}User Query:\n{query}\n\n{provided}"
        f"You have {given} bird-coins for this task and {budget(state):g} remain. "
        "Send one JSON action."
    )


def _observation(index: int, phase: int, name: str, cost: float, state: dict, body: str) -> str:
    return (
        f"[action {index} | phase {phase} | {name} | cost {cost:g} | "
        f"budget {budget(state):g}]\n{body}"
    )


def _action_history(name: str, args: dict) -> dict:
    """The assistant message later calls see: only the action that was executed."""
    return {
        "role": "assistant",
        "content": json.dumps({"action": name, "args": args}, ensure_ascii=False),
    }


def _ignored_notice(parsed: ParsedAction) -> str:
    if not parsed.ignored_actions:
        return ""
    return (
        f"\nThe response contained {parsed.action_count} actions; only the first was executed. "
        "Send exactly one action per response."
    )


def _outcome(state: dict) -> str:
    if state.get("phase1_completed"):
        return "Phase 1 passed; the follow-up phase is incomplete."
    return "Phase 1 is incomplete."


def _model_actions(state: dict) -> int:
    return sum(1 for event in state.get("adk_events", []) if event.get("type") == "model_call")


def _oracle_tools() -> dict[str, ToolSpec]:
    return {name: ALLOWED_TOOLS[name] for name in ORACLE_ACTIONS}


def _allowed_actions(harness: Harness, oracle: bool = False) -> dict[str, ToolSpec]:
    """The official tools, plus consult only when the harness defines a sub-agent.

    The oracle-context experiment drops the six information lookups, so they
    are rejected as unknown actions without cost.
    """
    tools = _oracle_tools() if oracle else ALLOWED_TOOLS
    if not harness.sub_agents:
        return tools
    return {**tools, "consult": CONSULT_SPEC}


async def _model_call(
    state: dict,
    model: ModelClient,
    phase: int,
    messages: list[dict],
    deadline: float,
    trace: SessionTrace | None = None,
) -> tuple[ChatReply, dict, Generation | None]:
    generation = (
        trace.model_start(
            phase=phase, call_index=1 + _model_actions(state), messages=messages
        )
        if trace is not None
        else None
    )
    try:
        reply = await _bounded(model.chat(messages), _remaining(deadline), "the model response")
    except ModelError as exc:
        state["failure"] = {"stage": "model", "message": str(exc)}
        if generation is not None:
            generation.error(str(exc), attempts=getattr(exc, "attempts", None))
        raise
    except SessionExpired as exc:
        if generation is not None:
            generation.error(str(exc))
        raise
    events = state.setdefault("adk_events", [])
    event = {
        "type": "model_call",
        "harness": state.get("harness"),
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
    # The runner keeps adk_events, so the experiment marker reaches the result JSON.
    if "experiment" in state:
        event["experiment"] = dict(state["experiment"])
    events.append(event)
    return reply, event, generation


async def _information(
    state: dict,
    environment: Environment,
    name: str,
    args: dict,
    phase: int,
    deadline: float,
    trace: SessionTrace | None = None,
    requested_by: dict | None = None,
) -> str:
    spec = ALLOWED_TOOLS[name]
    budget_before = budget(state)
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before {name} could start")
    span = (
        trace.tool_start(
            name=name, args=args, phase=phase, cost=spec.cost, budget_before=budget_before
        )
        if trace is not None
        else None
    )
    state["budget_remaining"] = budget_before - spec.cost
    started = _now()
    # A lookup a consult requested keeps the official format and adds who asked
    # for it, so the trajectory shows the sub-agent that caused the charge.
    extra = {"requested_by": requested_by} if requested_by is not None else {}
    try:
        content = await _bounded(spec.run(environment, state["task_id"], args), remaining, name)
    except SessionExpired as exc:
        entry = record_action(
            state, name, args, {"error": str(exc)}, spec.cost, budget_before,
            phase=phase, timed_out=True, elapsed_seconds=round(_now() - started, 3), **extra,
        )
        if span is not None:
            span.finish(entry)
        raise
    except GradingError as exc:
        entry = record_action(
            state, name, args, {"error": str(exc)}, spec.cost, budget_before, phase=phase,
            elapsed_seconds=round(_now() - started, 3), **extra,
        )
        if span is not None:
            span.finish(entry)
        state["failure"] = {"stage": "information", "message": str(exc)}
        raise
    entry = record_action(
        state, name, args, content, spec.cost, budget_before, phase=phase,
        elapsed_seconds=round(_now() - started, 3), **extra,
    )
    if span is not None:
        span.finish(entry)
    return content


async def _submit_action(
    state: dict,
    environment: Environment,
    sql: str,
    phase: int,
    messages: list[dict],
    index: int,
    deadline: float,
    trace: SessionTrace | None = None,
    notice: str = "",
    follow_up_context: str = "",
) -> tuple[str, str] | None:
    """Submit to the official grader. Returns the stop reason when the task ends."""
    budget_before = budget(state)
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before the submission could start")
    span = (
        trace.tool_start(
            name="submit_sql", args={"sql": sql}, phase=phase, cost=SUBMIT_COST,
            budget_before=budget_before,
        )
        if trace is not None
        else None
    )
    state["budget_remaining"] = budget_before - SUBMIT_COST
    started = _now()
    try:
        result = await _bounded(
            environment.submit(state["task_id"], sql), remaining, "the submission"
        )
    except SessionExpired as exc:
        # The grader may already have received the statement, so this is recorded as an
        # unknown outcome instead of a failure or a pass.
        entry = record_action(
            state, "submit_sql", {"sql": sql}, {"error": str(exc)}, SUBMIT_COST, budget_before,
            phase=phase, timed_out=True, elapsed_seconds=round(_now() - started, 3),
        )
        if span is not None:
            span.finish(entry)
        raise
    except GradingError as exc:
        entry = record_action(
            state, "submit_sql", {"sql": sql}, {"error": str(exc)}, SUBMIT_COST, budget_before,
            phase=phase, elapsed_seconds=round(_now() - started, 3),
        )
        if span is not None:
            span.finish(entry)
        state["failure"] = {"stage": "grader", "message": str(exc)}
        raise
    entry = record_action(
        state, "submit_sql", {"sql": sql}, result.model_dump(), SUBMIT_COST, budget_before,
        phase=phase, elapsed_seconds=round(_now() - started, 3),
    )
    state["last_submit"] = result.model_dump()
    state.setdefault("submit_attempts", []).append({"phase": phase, "sql": sql})

    if not result.passed:
        if span is not None:
            span.finish(entry)
        messages.append({
            "role": "user",
            "content": _observation(
                index, phase, "submit_sql", SUBMIT_COST, state, result.message + notice
            ),
        })
        return None

    # Rewards come from the official responses; 0.7/0.3 are never awarded here.
    state["total_reward"] += result.reward
    if result.phase_completed == 1:
        state["phase1_completed"] = True
        if not result.has_follow_up:
            state["task_done"] = True
            if span is not None:
                span.finish(entry)
            return "phase1_completed", result.message
        state["follow_up_query"] = result.follow_up_query
        state["current_phase"] = 2
        # The official user simulator switches to the follow-up reference exactly once.
        transition_started = _now()
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
            if span is not None:
                span.transition(
                    completed=False, message=str(exc),
                    elapsed_seconds=round(_now() - transition_started, 3),
                )
                span.finish(entry, phase_transition=state["phase_transition"])
            raise
        except GradingError as exc:
            state["failure"] = {"stage": "transition", "message": str(exc)}
            if span is not None:
                span.transition(
                    completed=False, message=str(exc),
                    elapsed_seconds=round(_now() - transition_started, 3),
                )
                span.finish(entry, phase_transition={"completed": False, "message": str(exc)})
            raise
        state["phase_transition"] = {"completed": True}
        if span is not None:
            span.transition(
                completed=True, elapsed_seconds=round(_now() - transition_started, 3)
            )
            span.finish(entry, phase_transition=state["phase_transition"])
        body = (
            f"{result.message}\nThe phase 1 SQL is applied to the database.\n"
            f"Follow-up question:\n{result.follow_up_query or ''}"
            + (f"\n\n{follow_up_context}" if follow_up_context else "")
            + notice
        )
        messages.append({
            "role": "user",
            "content": _observation(index, phase, "submit_sql", SUBMIT_COST, state, body),
        })
        return None

    state["phase2_completed"] = True
    state["task_done"] = True
    if span is not None:
        span.finish(entry)
    return "phase2_completed", result.message


def _execute_observation(result: ExecuteResult) -> str:
    if result.success:
        return (
            "Execution result (exploration only; nothing was submitted and no points can be "
            f"won or lost here):\n{result.result}"
        )
    return (
        "The official SQL service reported an error for this statement; nothing was submitted "
        f"and no submission was charged:\n{result.error or 'Execution failed without details'}"
    )


async def _execute_action(
    state: dict,
    environment: Environment,
    sql: str,
    phase: int,
    deadline: float,
    trace: SessionTrace | None = None,
) -> str:
    """Run one execute_sql call; an official SQL error is an observation, not a failure."""
    spec = ALLOWED_TOOLS["execute_sql"]
    budget_before = budget(state)
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before execute_sql could start")
    span = (
        trace.tool_start(
            name=spec.name, args={"sql": sql}, phase=phase, cost=spec.cost,
            budget_before=budget_before,
        )
        if trace is not None
        else None
    )
    state["budget_remaining"] = budget_before - spec.cost
    started = _now()
    try:
        result = await _bounded(
            environment.execute_sql(state["task_id"], sql), remaining, "execute_sql"
        )
    except SessionExpired as exc:
        entry = record_action(
            state, spec.name, {"sql": sql}, {"kind": "cancelled", "error": str(exc)},
            spec.cost, budget_before, phase=phase, timed_out=True,
            elapsed_seconds=round(_now() - started, 3),
        )
        if span is not None:
            span.finish(entry)
        raise
    except GradingError as exc:
        entry = record_action(
            state, spec.name, {"sql": sql}, {"kind": "service_error", "error": str(exc)},
            spec.cost, budget_before, phase=phase, elapsed_seconds=round(_now() - started, 3),
        )
        if span is not None:
            span.finish(entry)
        state["failure"] = {"stage": "execute_sql", "message": str(exc)}
        raise
    entry = record_action(
        state, spec.name, {"sql": sql},
        {
            "kind": "result" if result.success else "sql_error",
            "success": result.success, "result": result.result, "error": result.error,
        },
        spec.cost, budget_before, phase=phase, elapsed_seconds=round(_now() - started, 3),
    )
    if span is not None:
        span.finish(entry)
    return _execute_observation(result)


async def _ask_action(
    state: dict,
    environment: Environment,
    question: str,
    phase: int,
    deadline: float,
    trace: SessionTrace | None = None,
) -> str:
    """Ask the official user simulator; only a valid answer enters the dialogue."""
    spec = ALLOWED_TOOLS["ask_user"]
    budget_before = budget(state)
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before ask_user could start")
    span = (
        trace.tool_start(
            name=spec.name, args={"question": question}, phase=phase, cost=spec.cost,
            budget_before=budget_before,
        )
        if trace is not None
        else None
    )
    state["budget_remaining"] = budget_before - spec.cost
    started = _now()
    try:
        answer = await _bounded(
            environment.ask_user(state["task_id"], question), remaining, "ask_user"
        )
    except SessionExpired as exc:
        # The simulator may still be calling its model; no answer is invented for the
        # interrupted question.
        entry = record_action(
            state, spec.name, {"question": question},
            {"kind": "cancelled", "answer": None, "error": str(exc)},
            spec.cost, budget_before, phase=phase, timed_out=True,
            elapsed_seconds=round(_now() - started, 3),
        )
        if span is not None:
            span.finish(entry)
        raise
    except GradingError as exc:
        entry = record_action(
            state, spec.name, {"question": question},
            {"kind": "service_error", "answer": None, "error": str(exc)},
            spec.cost, budget_before, phase=phase, elapsed_seconds=round(_now() - started, 3),
        )
        if span is not None:
            span.finish(entry)
        state["failure"] = {"stage": "ask_user", "message": str(exc)}
        raise
    entry = record_action(
        state, spec.name, {"question": question},
        {"kind": "answer", "answer": answer, "error": None},
        spec.cost, budget_before, phase=phase, elapsed_seconds=round(_now() - started, 3),
    )
    if span is not None:
        span.finish(entry)
    history = state.setdefault("dialogue_history", [])
    history.append({"role": "agent", "content": question, "phase": phase})
    history.append({"role": "user", "content": answer, "phase": phase})
    return f"User answer:\n{answer}"


async def _consult_action(
    state: dict,
    model: ModelClient,
    harness: Harness,
    environment: Environment,
    agent_name: str,
    request: str,
    query: str,
    messages: list[dict],
    phase: int,
    deadline: float,
    trace: SessionTrace | None = None,
) -> str:
    """Run one sub-agent call and return its answer as the observation body.

    When the definition names a ``fetch`` tool, the official lookup runs first
    with its official price and a tool span, and the raw output goes only into
    the sub-agent's user message. A model failure is an observation, not a
    session failure; the fetch charge stays. Only the session deadline stops
    the loop from here.
    """
    sub_agent = harness.sub_agents[agent_name]
    fetch = sub_agent.fetch
    fetch_cost = ALLOWED_TOOLS[fetch].cost if fetch else 0.0
    budget_before = budget(state)
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise SessionExpired(f"{_limit_text()} was reached before consult could start")
    # Defensive: the loop normally stops below the submission price, which is
    # above every fetch price, but a consult must never run a lookup it cannot pay.
    if budget_before < fetch_cost:
        return (
            f"The sub-agent '{agent_name}' was not consulted. Its {fetch} lookup costs "
            f"{fetch_cost:g} bird-coins, but only {budget_before:g} remain. Nothing was executed."
        )
    fetched: tuple[str, str] | None = None
    if fetch:
        fetched = (
            fetch,
            await _information(
                state, environment, fetch, {}, phase, deadline, trace,
                requested_by={"action": "consult", "agent": agent_name},
            ),
        )
    # The lookup was charged by its own entry; the consult entry starts after it.
    consult_budget = budget(state)
    span = (
        trace.tool_start(
            name="consult", args={"agent": agent_name, "request": request}, phase=phase,
            cost=CONSULT_COST, budget_before=consult_budget,
        )
        if trace is not None
        else None
    )
    # messages[-1] is the consult action being executed now; the sub-agent sees
    # the actions and observations that came before it.
    prompt = [
        {"role": "system", "content": sub_agent.system},
        {
            "role": "user",
            "content": build_user_message(
                sub_agent, state, query, messages[2:-1], request, fetched
            ),
        },
    ]
    generation = (
        trace.subagent_start(name=agent_name, messages=prompt) if trace is not None else None
    )
    event = {
        "type": "subagent_call",
        "harness": state.get("harness"),
        "agent": agent_name,
        "prompt": [dict(message) for message in prompt],
    }
    if fetch:
        event["fetch"] = {"tool": fetch, "cost": fetch_cost}
    extra = {"fetch": fetch} if fetch else {}
    started = _now()
    try:
        reply = await _bounded(
            model.chat(prompt), _remaining(deadline), f"the sub-agent '{agent_name}'"
        )
    except SessionExpired as exc:
        elapsed = round(_now() - started, 3)
        event.update({
            "content": "", "truncated": False, "usage": None,
            "elapsed_seconds": elapsed, "error": str(exc),
        })
        state.setdefault("adk_events", []).append(event)
        if generation is not None:
            generation.error(str(exc))
        entry = record_action(
            state, "consult", {"agent": agent_name, "request": request},
            {"kind": "cancelled", "content": None, "error": str(exc)},
            CONSULT_COST, consult_budget, phase=phase, timed_out=True,
            elapsed_seconds=elapsed, **extra,
        )
        if span is not None:
            span.finish(entry)
        raise
    except ModelError as exc:
        elapsed = round(_now() - started, 3)
        event.update({
            "content": "", "truncated": False, "usage": None,
            "elapsed_seconds": elapsed, "error": str(exc),
        })
        state.setdefault("adk_events", []).append(event)
        if generation is not None:
            generation.error(str(exc), attempts=getattr(exc, "attempts", None))
        entry = record_action(
            state, "consult", {"agent": agent_name, "request": request},
            {"kind": "model_error", "content": None, "error": str(exc)},
            CONSULT_COST, consult_budget, phase=phase, elapsed_seconds=elapsed, **extra,
        )
        if span is not None:
            span.finish(entry)
        if fetch:
            return (
                f"The sub-agent '{agent_name}' call failed and no answer is available. "
                f"The {fetch} lookup cost {fetch_cost:g} bird-coins and was not refunded.\n"
                f"Error: {exc}"
            )
        return (
            f"The sub-agent '{agent_name}' call failed and no answer is available. "
            "The failure does not affect the budget or the score.\n"
            f"Error: {exc}"
        )
    elapsed = round(_now() - started, 3)
    event.update({
        "content": reply.content,
        "truncated": reply.truncated,
        "usage": reply.usage,
        "elapsed_seconds": reply.elapsed_seconds,
    })
    state.setdefault("adk_events", []).append(event)
    if generation is not None:
        generation.finish(reply)
    entry = record_action(
        state, "consult", {"agent": agent_name, "request": request},
        {
            "kind": "answer", "content": reply.content, "error": None,
            "truncated": reply.truncated,
        },
        CONSULT_COST, consult_budget, phase=phase, elapsed_seconds=elapsed, **extra,
    )
    if span is not None:
        span.finish(entry)
    body = f"Sub-agent '{agent_name}' answer:\n{reply.content}"
    if reply.truncated:
        body += "\n[The sub-agent response was cut off by the output token limit.]"
    if fetch:
        body += f"\n{fetch} was fetched for the sub-agent (cost {fetch_cost:g})."
    return body


def _start_oracle_context(state: dict, oracle: OracleContext) -> TaskContext:
    """Load the task's prebuilt context and take the budget offset once.

    A missing or invalid file raises before anything is charged or recorded.
    The official ``initial_budget`` stays as the runner sent it; only the
    remaining budget starts lower, and ``experiment`` records why.
    """
    context = oracle.load(str(state.get("task_id") or ""))
    if "experiment" not in state:
        offset = oracle.descriptor()["budget_offset"]
        state["budget_remaining"] = budget(state) - offset
        state["experiment"] = {
            **oracle.descriptor(),
            "context_sha256": context.sha256,
            "context_chars": context.chars(),
        }
    return context


def _record_harness(state: dict, harness: Harness) -> None:
    """Keep the harness identity in the session state.

    The evaluation runner copies only a whitelist of state fields into the
    result JSON. Each model_call event also carries this descriptor, so the
    harness stays visible in every stored task result without changing the
    meaning or order of the model-call events.
    """
    state["harness"] = harness.descriptor()


async def run(
    state: dict,
    message: str,
    environment: Environment,
    model: ModelClient,
    tracer: Tracer | None = None,
    *,
    harness: Harness,
    oracle: OracleContext | None = None,
) -> str:
    _record_harness(state, harness)
    # Repeated requests return the cached outcome instead of calling out again.
    if state.get("loop_stopped"):
        return state["final_response"]
    # A failed attempt is never retried inside the same session.
    if state.get("failure"):
        raise restore_failure(state["failure"])

    query = resolve_query(state, message)
    context = _start_oracle_context(state, oracle) if oracle is not None else None
    trace = None
    if tracer is not None:
        try:
            trace = tracer.start_session(task_id=str(state.get("task_id") or ""), input=query)
        except Exception:
            trace = None
    try:
        response = await _session_loop(
            state, query, environment, model, trace, harness, context
        )
    except ModelError as exc:
        if trace is not None:
            trace.fail("model", str(exc))
        raise
    except GradingError as exc:
        failure = state.get("failure")
        stage = failure.get("stage") if isinstance(failure, dict) else None
        if trace is not None:
            trace.fail(str(stage or "grading"), str(exc))
        raise
    else:
        if trace is not None:
            trace.finish(state)
        return response
    finally:
        if trace is not None:
            trace.end()


async def _session_loop(
    state: dict,
    query: str,
    environment: Environment,
    model: ModelClient,
    trace: SessionTrace | None,
    harness: Harness,
    context: TaskContext | None = None,
) -> str:
    started = _now()
    deadline = started + SESSION_TIME_LIMIT_SECONDS
    oracle = context is not None
    allowed = _allowed_actions(harness, oracle)
    follow_up_context = follow_up_section(context) if oracle else ""
    messages = [
        {"role": "system", "content": system_prompt(harness, oracle)},
        {"role": "user", "content": _initial_prompt(state, query, context)},
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
            reply, event, generation = await _model_call(
                state, model, phase, messages, deadline, trace
            )
        except SessionExpired as exc:
            return stop("session_time_limit", f"Stopped: {exc}. {_outcome(state)}")
        messages.append({"role": "assistant", "content": reply.content})
        # The response may have arrived right at the limit; nothing is executed after it.
        if _remaining(deadline) <= 0:
            if generation is not None:
                generation.finish(reply)
            return stop(
                "session_time_limit",
                f"Stopped: {_limit_text()} was reached after the model response, so the action "
                f"was not executed. {_outcome(state)}",
            )
        try:
            if reply.truncated:
                raise ActionError(
                    "The response hit the output token limit, so no action was executed. "
                    "Reply with one complete JSON action."
                )
            parsed = parse_response(reply.content, allowed)
            if parsed.name == "consult" and parsed.args["agent"] not in harness.sub_agents:
                available = ", ".join(harness.sub_agents) or "none"
                raise ActionError(
                    f"Unknown sub-agent '{parsed.args['agent']}'. "
                    f"Available sub-agents: {available}."
                )
        except ActionError as exc:
            reason = str(exc)
            event["action_error"] = reason
            event["action"] = None
            if generation is not None:
                generation.finish(reply, action_error=reason)
            messages.append({
                "role": "user",
                "content": _observation(index, phase, "rejected", 0.0, state, reason),
            })
            continue
        name, args = parsed.name, parsed.args
        # The executed action replaces the raw response in the dialogue, so invented
        # observations and extra actions never look like real tool output to the model.
        messages[-1] = _action_history(name, args)
        event["action"] = {"name": name, "args": args}
        if parsed.action_count > 1:
            event["action_count"] = parsed.action_count
        if parsed.ignored_actions:
            event["ignored_actions"] = parsed.ignored_actions
        if generation is not None:
            generation.finish(
                reply,
                action={"name": name, "args": args},
                action_count=parsed.action_count if parsed.action_count > 1 else None,
                ignored_actions=parsed.ignored_actions or None,
            )
        notice = _ignored_notice(parsed)

        action_budget = budget(state)
        try:
            if name == "submit_sql":
                outcome = await _submit_action(
                    state, environment, args["sql"], phase, messages, index, deadline, trace,
                    notice=notice, follow_up_context=follow_up_context,
                )
                if outcome is not None:
                    return stop(*outcome)
                continue

            if name == "execute_sql":
                content = await _execute_action(
                    state, environment, args["sql"], phase, deadline, trace
                )
            elif name == "ask_user":
                content = await _ask_action(
                    state, environment, args["question"], phase, deadline, trace
                )
            elif name == "consult":
                content = await _consult_action(
                    state, model, harness, environment, args["agent"], args["request"], query,
                    messages, phase, deadline, trace,
                )
            else:
                content = await _information(
                    state, environment, name, args, phase, deadline, trace
                )
        except SessionExpired as exc:
            return stop("session_time_limit", f"Stopped: {exc}. {_outcome(state)}")
        # A consult observation shows the coins this call actually spent: the
        # fetch price when a lookup ran, zero otherwise. Other actions keep
        # their official price.
        if name == "consult":
            cost = action_budget - budget(state)
        else:
            cost = ALLOWED_TOOLS[name].cost
        messages.append({
            "role": "user",
            "content": _observation(index, phase, name, cost, state, content + notice),
        })
