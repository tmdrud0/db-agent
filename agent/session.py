"""Session state helpers shared by the fixed and the tool-selecting loops."""

from agent.environment import GradingError
from agent.model import ModelError


class InputError(ValueError):
    """The runner did not provide a question to answer."""


def budget(state: dict) -> float:
    return float(state.get("budget_remaining", 0))


def resolve_query(state: dict, message: str) -> str:
    query = str(state.get("user_query") or "").strip() or message.strip()
    if not query:
        raise InputError("state.user_query and the run message are both empty")
    return query


def database_line(state: dict) -> str:
    db_name = str(state.get("db_name") or "").strip()
    return f"Database: {db_name}\n\n" if db_name else ""


def finish(state: dict, stop_reason: str, response: str) -> str:
    state["stop_reason"] = stop_reason
    state["loop_stopped"] = True
    state["final_response"] = response
    return response


def record_action(
    state: dict, tool: str, args: dict, result, cost: float, budget_before: float, **extra
) -> None:
    entry = {
        "type": "tool",
        "tool": tool,
        "args": args,
        "result": result,
        "cost": cost,
        "budget_before": budget_before,
        "budget_after": state["budget_remaining"],
    }
    entry.update(extra)
    state.setdefault("tool_trajectory", []).append(entry)


def restore_failure(failure: dict) -> Exception:
    message = str(failure.get("message", "The previous attempt failed"))
    if failure.get("stage") == "model":
        return ModelError(message)
    return GradingError(message)
