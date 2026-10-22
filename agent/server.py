"""BIRD-compatible session endpoints. Run one Uvicorn worker on port 6000."""

import asyncio
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.environment import Environment, GradingError
from agent.harness import Harness, load_harness
from agent.model import MODEL_ID, ModelClient, ModelError
from agent.oracle_context import OracleContext, OracleContextError, load_oracle_context
from agent.session import InputError
from agent.tool_loop import run as run_tool_loop
from agent.tools import tool_metadata
from agent.tracing import (
    FLUSH_TIMEOUT_SECONDS,
    SHUTDOWN_FLUSH_TIMEOUT_SECONDS,
    NoopTracer,
    Tracer,
    create_tracer,
)

AGENT_NAME = "deepseek-v4.1-flash-tool-selecting-two-phase"
SUPPORTED_MODE = "tools"
# Marks "read AGENT_ORACLE_CONTEXT_DIR" apart from an explicit None (mode off).
_FROM_ENVIRONMENT = object()


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
    tracer: Tracer | None = None,
    harness: Harness | None = None,
    oracle_context: OracleContext | None | object = _FROM_ENVIRONMENT,
) -> FastAPI:
    # The tool-selecting loop is the only agent. An explicit request for a
    # removed mode must fail instead of silently running a different agent.
    selected = (mode if mode is not None else os.getenv("AGENT_MODE", "")).strip().lower()
    selected = selected or SUPPORTED_MODE
    if selected != SUPPORTED_MODE:
        raise ValueError(
            f"Unknown agent mode '{selected}'; only '{SUPPORTED_MODE}' is supported"
        )
    environment = environment or Environment()
    model = model or ModelClient()
    # The harness is read once when the server is created. Changes to its files
    # do not affect a running server.
    harness = harness if harness is not None else load_harness()
    # The diagnostic oracle-context experiment (TASK-015) is on only when its
    # directory is configured; a configured but unusable directory stops here.
    oracle = (
        load_oracle_context() if oracle_context is _FROM_ENVIRONMENT else oracle_context
    )
    # The real client is created by the lifespan startup, not here: a test
    # app built without a lifespan must never reach Langfuse just because the
    # shell inherited keys from a previous evaluation run.
    fallback_tracer = NoopTracer()
    live_tracer: Tracer | None = tracer
    sessions: dict[tuple[str, str], Session] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal live_tracer
        if live_tracer is None:
            live_tracer = create_tracer(
                agent_name=AGENT_NAME,
                model_id=MODEL_ID,
                harness_name=harness.name,
                harness_sha256=harness.sha256,
            )
        try:
            yield
        finally:
            active = live_tracer
            if active is not None:
                # The runner normally stops the server with Stop-Process, so
                # each session also flushes before its response; this covers
                # clean exits.
                try:
                    await active.flush(SHUTDOWN_FLUSH_TIMEOUT_SECONDS)
                except Exception:
                    pass

    app = FastAPI(title=f"BIRD custom agent: {AGENT_NAME}", lifespan=lifespan)

    @app.get("/health")
    async def health():
        info = {
            "status": "healthy",
            "service": "system_agent",
            "agent": AGENT_NAME,
            "mode": SUPPORTED_MODE,
            "model": MODEL_ID,
            # The real tool set identifies the service, so a stale server that
            # only shares the name cannot be reused by the evaluator.
            "tools": tool_metadata(),
            # Sub-agents are not official tools: they are reported next to the
            # harness, so /health.tools keeps exactly the nine official entries.
            "harness": {
                **harness.descriptor(), "sub_agents": harness.sub_agent_names()
            },
            "execute_sql_timeout_seconds": environment.execute_sql_timeout,
            "ask_user_timeout_seconds": environment.ask_user_timeout,
        }
        # The experiment is reported next to the tools, which stay the nine
        # official entries; without the experiment /health is unchanged.
        if oracle is not None:
            info["experiment"] = oracle.descriptor()
        return info

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
                "harness": harness.descriptor(),
            }.items():
                state.setdefault(name, default)
            sessions[key] = Session(state)
        return {"task_id": req.task_id, "mode": req.mode, "session_id": sessions[key].session_id}

    @app.post("/run_session")
    async def run_session(req: RunRequest):
        session = sessions.get((req.mode, req.task_id))
        if session is None:
            raise HTTPException(404, "Call /init_session first")
        active_tracer = live_tracer or fallback_tracer
        async with session.lock:
            try:
                response = await run_tool_loop(
                    session.state, req.message, environment, model, active_tracer,
                    harness=harness, oracle=oracle,
                )
            except InputError as exc:
                raise HTTPException(422, f"Invalid run input: {exc}") from exc
            except OracleContextError as exc:
                raise HTTPException(500, f"Oracle context error: {exc}") from exc
            except ModelError as exc:
                # Report the failure source explicitly instead of fabricating
                # a valid failed submission with reward=0.
                raise HTTPException(502, f"Model service error: {exc}") from exc
            except GradingError as exc:
                raise HTTPException(502, f"Benchmark service error: {exc}") from exc
            else:
                return {
                    "task_id": req.task_id, "mode": req.mode,
                    "session_id": session.session_id,
                    "response": response, "state": deepcopy(session.state),
                }
            finally:
                # The runner force-stops the server, so the trace of this
                # session is sent before the response can leave. A slow or
                # failing Langfuse call never changes the response.
                try:
                    await active_tracer.flush(FLUSH_TIMEOUT_SECONDS)
                except Exception:
                    pass

    return app


app = create_app()
