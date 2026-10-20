"""BIRD-compatible session endpoints. Run one Uvicorn worker on port 6000."""

import asyncio
import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.environment import Environment, GradingError
from agent.model import MODEL_ID, ModelClient, ModelError
from agent.session import InputError
from agent.tool_loop import run as run_tool_loop
from agent.tools import tool_metadata

AGENT_NAME = "deepseek-v4.1-flash-tool-selecting-two-phase"
SUPPORTED_MODE = "tools"


class InitRequest(BaseModel):
    task_id: str = Field(min_length=1)
    mode: Literal["a-interact"] = "a-interact"
    state: dict[str, Any] = Field(default_factory=dict)
    reset: bool = True


class RunRequest(BaseModel):
    task_id: str = Field(min_length=1)
    mode: Literal["a-interact"] = "a-interact"
    message: str


@dataclass
class Session:
    state: dict
    session_id: str = field(default_factory=lambda: str(uuid4()))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def create_app(
    environment: Environment | None = None,
    model: ModelClient | None = None,
    mode: str | None = None,
) -> FastAPI:
    # The tool-selecting loop is the only agent. An explicit request for a
    # removed mode must fail instead of silently running a different agent.
    selected = (mode if mode is not None else os.getenv("AGENT_MODE", "")).strip().lower()
    selected = selected or SUPPORTED_MODE
    if selected != SUPPORTED_MODE:
        raise ValueError(
            f"Unknown agent mode '{selected}'; only '{SUPPORTED_MODE}' is supported"
        )
    app = FastAPI(title=f"BIRD custom agent: {AGENT_NAME}")
    environment = environment or Environment()
    model = model or ModelClient()
    sessions: dict[tuple[str, str], Session] = {}

    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "service": "system_agent",
            "agent": AGENT_NAME,
            "mode": SUPPORTED_MODE,
            "model": MODEL_ID,
            # The real tool set identifies the service, so a stale server that
            # only shares the name cannot be reused by the evaluator.
            "tools": tool_metadata(),
            "execute_sql_timeout_seconds": environment.execute_sql_timeout,
            "ask_user_timeout_seconds": environment.ask_user_timeout,
        }

    @app.post("/init_session")
    async def init_session(req: InitRequest):
        key = (req.mode, req.task_id)
        if key not in sessions or req.reset:
            state = deepcopy(req.state)
            if state.get("task_id", req.task_id) != req.task_id:
                raise HTTPException(422, "state.task_id must match task_id")
            state["task_id"] = req.task_id
            for name, default in {
                "current_phase": 1, "initial_budget": 0.0,
                "budget_remaining": 0.0, "total_reward": 0.0,
                "phase1_completed": False, "phase2_completed": False,
                "task_done": False, "tool_trajectory": [],
                "dialogue_history": [], "adk_events": [],
            }.items():
                state.setdefault(name, default)
            sessions[key] = Session(state)
        return {"task_id": req.task_id, "mode": req.mode, "session_id": sessions[key].session_id}

    @app.post("/run_session")
    async def run_session(req: RunRequest):
        session = sessions.get((req.mode, req.task_id))
        if session is None:
            raise HTTPException(404, "Call /init_session first")
        async with session.lock:
            try:
                response = await run_tool_loop(session.state, req.message, environment, model)
            except InputError as exc:
                raise HTTPException(422, f"Invalid run input: {exc}") from exc
            except ModelError as exc:
                # Report the failure source explicitly instead of fabricating
                # a valid failed submission with reward=0.
                raise HTTPException(502, f"Model service error: {exc}") from exc
            except GradingError as exc:
                raise HTTPException(502, f"Benchmark service error: {exc}") from exc
            return {
                "task_id": req.task_id, "mode": req.mode,
                "session_id": session.session_id,
                "response": response, "state": deepcopy(session.state),
            }

    return app


app = create_app()
