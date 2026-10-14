"""BIRD-compatible session endpoints. Run one Uvicorn worker on port 6000."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

from agent.environment import Environment
from agent.loop import run


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


def create_app(environment: Environment | None = None) -> FastAPI:
    app = FastAPI(title="BIRD custom agent: empty submission stub")
    environment = environment or Environment()
    sessions: dict[tuple[str, str], Session] = {}

    @app.get("/health")
    async def health():
        return {"status": "healthy", "service": "system_agent", "agent": "empty-submit"}

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
                response = await run(session.state, req.message, environment)
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                # Report infrastructure errors explicitly instead of fabricating
                # a valid failed submission with reward=0.
                raise HTTPException(502, f"Benchmark service error: {exc}") from exc
            return {
                "task_id": req.task_id, "mode": req.mode,
                "session_id": session.session_id,
                "response": response, "state": deepcopy(session.state),
            }

    return app


app = create_app()
