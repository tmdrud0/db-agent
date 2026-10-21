"""Assemble the input of one sub-agent call.

A sub-agent sees only its definition body and one user message built here. The
message holds the context items its definition asks for, in the declared order,
followed by the requesting agent's request. Nothing else from the session,
the environment or the local filesystem is added.
"""

from agent.harness import SubAgent
from agent.session import database_line


def _task(state: dict, query: str) -> str:
    lines = []
    database = database_line(state).strip()
    if database:
        lines.append(database)
    phase = int(state.get("current_phase", 1))
    lines.append(f"Phase: {phase}")
    lines.append(f"Phase 1 question:\n{query}")
    follow_up = str(state.get("follow_up_query") or "").strip()
    if phase == 2 and follow_up:
        lines.append(f"Follow-up question:\n{follow_up}")
    return "Task:\n" + "\n".join(lines)


def _dialogue(state: dict) -> str:
    history = state.get("dialogue_history") or []
    if not history:
        return "Dialogue:\nNo questions or answers yet."
    lines = []
    for entry in history:
        role = "Agent" if entry.get("role") == "agent" else "User"
        lines.append(f"{role}: {entry.get('content', '')}")
    return "Dialogue:\n" + "\n".join(lines)


def _observations(history: list[dict]) -> str:
    """Copy the executed actions and the observations the main agent received.

    ``history`` is the loop transcript without the system message, the initial
    prompt and the consult action currently being executed.
    """
    if not history:
        return "Observations:\nNo actions yet."
    lines = []
    for message in history:
        content = str(message.get("content", ""))
        if message.get("role") == "assistant":
            lines.append(f"Assistant action:\n{content}")
        else:
            lines.append(f"Observation:\n{content}")
    return "Observations:\n" + "\n\n".join(lines)


def build_user_message(
    sub_agent: SubAgent,
    state: dict,
    query: str,
    history: list[dict],
    request: str,
) -> str:
    """The one user message of a sub-agent call."""
    builders = {
        "task": lambda: _task(state, query),
        "dialogue": lambda: _dialogue(state),
        "observations": lambda: _observations(history),
    }
    parts = [builders[item]() for item in sub_agent.context]
    parts.append(f"Request:\n{request}")
    return "\n\n".join(parts) + "\n"
