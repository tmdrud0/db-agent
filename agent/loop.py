"""One empty submission to verify the runner -> agent -> grader connection."""

from agent.environment import Environment


async def choose_sql(state: dict, message: str) -> str:
    """Replace this placeholder with your agent's tool-use loop later."""
    return ""


async def run(state: dict, message: str, environment: Environment) -> str:
    # Repeated requests return the cached outcome instead of submitting again.
    if state.get("loop_stopped"):
        return state["final_response"]

    sql = await choose_sql(state, message)
    budget_before = float(state.get("budget_remaining", 0))
    # Match the official submit callback: a final submission is allowed even
    # with less than 3 coins, and -1 prevents further actions.
    state["budget_remaining"] = budget_before - 3 if budget_before > 3 else -1.0
    result = await environment.submit(state["task_id"], sql)
    state.setdefault("tool_trajectory", []).append({
        "type": "tool",
        "tool": "submit_sql",
        "args": {"sql": sql},
        "result": result.model_dump(),
        "cost": 3.0,
        "budget_before": budget_before,
        "budget_after": state["budget_remaining"],
    })
    state["last_submit"] = result.model_dump()

    # Never infer success from the SQL or from an empty response.
    if result.passed:
        state["total_reward"] += result.reward
        if result.phase_completed == 1:
            state["phase1_completed"] = True
            if result.has_follow_up:
                state["current_phase"] = 2
                state["follow_up_query"] = result.follow_up_query
                await environment.phase_transition(state["task_id"])
            else:
                state["task_done"] = True
        elif result.phase_completed == 2:
            state["phase2_completed"] = True
            state["task_done"] = True

    # This connection stub intentionally stops after one submission. Stopping
    # the program is distinct from successfully completing the benchmark task.
    state["loop_stopped"] = True
    state["stop_reason"] = "empty_submission_stub"
    state["final_response"] = result.message
    return result.message
