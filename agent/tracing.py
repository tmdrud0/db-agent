"""Best-effort Langfuse tracing for one agent session.

Tracing is optional: without both Langfuse keys every call is a no-op and the
SDK is never imported. Once enabled, tracing errors are swallowed (with at
most one warning) so the agent's state, result and HTTP response never depend
on Langfuse.
"""

import asyncio
import logging
import os
import threading
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

LOGGER = logging.getLogger(__name__)

PUBLIC_KEY_ENV = "LANGFUSE_PUBLIC_KEY"
SECRET_KEY_ENV = "LANGFUSE_SECRET_KEY"
BASE_URL_ENV = "LANGFUSE_BASE_URL"
TRACE_SESSION_ENV = "AGENT_TRACE_SESSION_ID"
CODE_VERSION_ENV = "AGENT_CODE_VERSION"

# One session trace is flushed before /run_session answers, so the wait is
# bounded. The flush happens after the loop's elapsed time is recorded.
FLUSH_TIMEOUT_SECONDS = 5.0
SHUTDOWN_FLUSH_TIMEOUT_SECONDS = 10.0

_warned = False


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _warn_once(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        LOGGER.warning("%s", message)


def _trace_name(agent_name: str, task_id: str) -> str:
    name = f"{agent_name}:{task_id}" if task_id else agent_name
    # Langfuse trace names must be US-ASCII and at most 200 characters.
    return name.encode("ascii", "replace").decode("ascii")[:200]


def _cached_input_tokens(usage: dict, prompt_tokens: int) -> int:
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        return 0
    cached = details.get("cached_tokens")
    if not isinstance(cached, int) or isinstance(cached, bool):
        return 0
    if not 0 <= cached <= prompt_tokens:
        return 0
    return cached


def _usage_details(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    sources = (
        ("input", ("input_tokens", "prompt_tokens")),
        ("output", ("output_tokens", "completion_tokens")),
        ("total", ("total_tokens",)),
    )
    details = {}
    input_source = None
    for target, names in sources:
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                details[target] = value
                if target == "input":
                    input_source = name
                break
    # A cached prompt token is billed at a lower price, so keep it out of the
    # uncached input count Langfuse uses with the "input" price.
    if input_source == "prompt_tokens":
        cached = _cached_input_tokens(usage, details["input"])
        if cached:
            details["input"] -= cached
            details["input_cached_tokens"] = cached
    return details or None


class Generation:
    """One model call in a session trace."""

    def finish(
        self, reply: Any, *, action: dict | None = None, action_error: str | None = None
    ) -> None:
        raise NotImplementedError

    def error(self, message: str, attempts: int | None = None) -> None:
        raise NotImplementedError


class ToolSpan:
    """One tool execution in a session trace."""

    def finish(self, entry: dict, **extra: Any) -> None:
        raise NotImplementedError

    def transition(
        self,
        *,
        completed: bool,
        message: str | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        raise NotImplementedError


class SessionTrace:
    """The root trace of one loop execution."""

    def model_start(self, *, phase: int, call_index: int, messages: list[dict]) -> Generation:
        raise NotImplementedError

    def tool_start(
        self,
        *,
        name: str,
        args: dict,
        phase: int,
        cost: float,
        budget_before: float,
    ) -> ToolSpan:
        raise NotImplementedError

    def finish(self, state: dict) -> None:
        raise NotImplementedError

    def fail(self, stage: str, message: str) -> None:
        raise NotImplementedError

    def end(self) -> None:
        raise NotImplementedError


class Tracer:
    enabled = False

    def start_session(self, *, task_id: str, input: str) -> SessionTrace:
        raise NotImplementedError

    async def flush(self, timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
        raise NotImplementedError


class _NoopGeneration(Generation):
    def finish(self, reply, *, action=None, action_error=None) -> None:
        pass

    def error(self, message, attempts=None) -> None:
        pass


class _NoopToolSpan(ToolSpan):
    def finish(self, entry, **extra) -> None:
        pass

    def transition(self, *, completed, message=None, elapsed_seconds=None) -> None:
        pass


class _NoopSessionTrace(SessionTrace):
    def model_start(self, *, phase, call_index, messages):
        return _NOOP_GENERATION

    def tool_start(self, *, name, args, phase, cost, budget_before):
        return _NOOP_TOOL_SPAN

    def finish(self, state) -> None:
        pass

    def fail(self, stage, message) -> None:
        pass

    def end(self) -> None:
        pass


class NoopTracer(Tracer):
    enabled = False

    def start_session(self, *, task_id: str, input: str) -> SessionTrace:
        return _NOOP_SESSION

    async def flush(self, timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
        return None


_NOOP_GENERATION = _NoopGeneration()
_NOOP_TOOL_SPAN = _NoopToolSpan()
_NOOP_SESSION = _NoopSessionTrace()
_NOOP_TRACER = NoopTracer()


class _LangfuseGeneration(Generation):
    def __init__(self, span, *, phase: int, call_index: int, request_model: str):
        self._span = span
        self._phase = phase
        self._call_index = call_index
        self._request_model = request_model

    def _metadata(self, **extra: Any) -> dict:
        metadata = {
            "phase": self._phase,
            "call_index": self._call_index,
            "request_model": self._request_model,
        }
        metadata.update({key: value for key, value in extra.items() if value is not None})
        return metadata

    def finish(self, reply, *, action=None, action_error=None) -> None:
        self._span.update(
            output=reply.content,
            model=reply.response_model or self._request_model,
            usage_details=_usage_details(reply.usage),
            metadata=self._metadata(
                response_model=reply.response_model,
                elapsed_seconds=reply.elapsed_seconds,
                truncated=reply.truncated,
                attempts=getattr(reply, "attempts", 1),
                action=action,
                action_error=action_error,
            ),
        )
        self._span.end()

    def error(self, message: str, attempts: int | None = None) -> None:
        self._span.update(
            level="ERROR",
            status_message=message,
            metadata=self._metadata(attempts=attempts),
        )
        self._span.end()


class _LangfuseToolSpan(ToolSpan):
    def __init__(
        self,
        span,
        *,
        name: str,
        args: dict,
        phase: int,
        cost: float,
        budget_before: float,
    ):
        self._span = span
        self._name = name
        self._args = args
        self._phase = phase
        self._cost = cost
        self._budget_before = budget_before

    @staticmethod
    def _error_status(entry: dict) -> tuple[str, str | None]:
        result = entry.get("result")
        if entry.get("timed_out"):
            message = result.get("error") if isinstance(result, dict) else None
            return "ERROR", message or "The call was cancelled at the session time limit"
        if isinstance(result, dict) and result.get("kind") in ("service_error", "cancelled"):
            return "ERROR", result.get("error")
        if entry.get("tool") == "submit_sql" and isinstance(result, dict):
            if "passed" not in result:
                return "ERROR", result.get("error")
        if entry.get("blocked"):
            return "WARNING", f"blocked: {entry['blocked']}"
        return "DEFAULT", None

    def finish(self, entry: dict, **extra: Any) -> None:
        result = entry.get("result")
        metadata = {
            "phase": entry.get("phase", self._phase),
            "cost": entry.get("cost", self._cost),
            "budget_before": entry.get("budget_before", self._budget_before),
            "budget_after": entry.get("budget_after"),
            "cached": entry.get("cached", False),
            "blocked": entry.get("blocked"),
            "timed_out": entry.get("timed_out", False),
            "elapsed_seconds": entry.get("elapsed_seconds"),
        }
        if self._name == "submit_sql" and isinstance(result, dict):
            for key in ("passed", "reward", "phase_completed", "has_follow_up", "message"):
                if key in result:
                    metadata[key] = result[key]
        metadata.update({key: value for key, value in extra.items() if value is not None})
        level, status_message = self._error_status(entry)
        self._span.update(
            output=result,
            metadata={key: value for key, value in metadata.items() if value is not None},
            level=level,
            status_message=status_message,
        )
        self._span.end()

    def transition(
        self,
        *,
        completed: bool,
        message: str | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        metadata = {"completed": completed}
        if message is not None:
            metadata["message"] = message
        if elapsed_seconds is not None:
            metadata["elapsed_seconds"] = elapsed_seconds
        child = self._span.start_observation(
            name="phase_transition",
            as_type="span",
            input={"phase": 2},
            metadata=metadata,
            level="DEFAULT" if completed else "ERROR",
            status_message=None if completed else message,
        )
        child.end()


class _LangfuseSessionTrace(SessionTrace):
    def __init__(
        self,
        client,
        propagate,
        *,
        task_id: str,
        input: str,
        eval_run_id: str,
        code_version: str | None,
        agent_name: str,
        model_id: str,
    ):
        self._failed = False
        self._ended = False
        self._span = None
        self._propagation = None
        self._model_id = model_id

        name = _trace_name(agent_name, task_id)
        metadata = {
            "task_id": task_id,
            "agent": agent_name,
            "model": model_id,
            "eval_run_id": eval_run_id,
        }
        if code_version:
            metadata["code_version"] = code_version

        self._propagation = propagate(session_id=eval_run_id, trace_name=name)
        self._propagation.__enter__()
        try:
            self._span = client.start_observation(
                name=name,
                as_type="span",
                input=input,
                metadata=metadata,
                version=code_version or None,
            )
        except BaseException:
            self._propagation.__exit__(None, None, None)
            raise

    def _usable(self) -> bool:
        return not self._failed and not self._ended and self._span is not None

    def _disable(self, exc: Exception) -> None:
        self._failed = True
        _warn_once(f"Langfuse tracing was disabled ({type(exc).__name__})")

    def model_start(self, *, phase: int, call_index: int, messages: list[dict]) -> Generation:
        if not self._usable():
            return _NOOP_GENERATION
        try:
            span = self._span.start_observation(
                name="model_call",
                as_type="generation",
                input=[dict(message) for message in messages],
                model=self._model_id,
                metadata={
                    "phase": phase,
                    "call_index": call_index,
                    "request_model": self._model_id,
                },
            )
        except Exception as exc:
            self._disable(exc)
            return _NOOP_GENERATION
        return _LangfuseGeneration(
            span, phase=phase, call_index=call_index, request_model=self._model_id
        )

    def tool_start(
        self,
        *,
        name: str,
        args: dict,
        phase: int,
        cost: float,
        budget_before: float,
    ) -> ToolSpan:
        if not self._usable():
            return _NOOP_TOOL_SPAN
        try:
            span = self._span.start_observation(
                name=name,
                as_type="span",
                input=args,
                metadata={
                    "phase": phase,
                    "cost": cost,
                    "budget_before": budget_before,
                },
            )
        except Exception as exc:
            self._disable(exc)
            return _NOOP_TOOL_SPAN
        return _LangfuseToolSpan(
            span, name=name, args=args, phase=phase, cost=cost, budget_before=budget_before
        )

    def finish(self, state: dict) -> None:
        if not self._usable():
            return
        try:
            reward = float(state.get("total_reward") or 0.0)
            phase1 = bool(state.get("phase1_completed"))
            phase2 = bool(state.get("phase2_completed"))
            stop_reason = state.get("stop_reason")
            self._span.update(
                output=state.get("final_response"),
                metadata={
                    "total_reward": reward,
                    "phase1_completed": phase1,
                    "phase2_completed": phase2,
                    "stop_reason": stop_reason,
                    "session_elapsed_seconds": state.get("session_elapsed_seconds"),
                },
            )
            self._span.score_trace(name="total_reward", value=reward, data_type="NUMERIC")
            self._span.score_trace(
                name="phase1_completed", value=1.0 if phase1 else 0.0, data_type="BOOLEAN"
            )
            self._span.score_trace(
                name="phase2_completed", value=1.0 if phase2 else 0.0, data_type="BOOLEAN"
            )
            if stop_reason:
                self._span.score_trace(
                    name="stop_reason", value=str(stop_reason), data_type="CATEGORICAL"
                )
        except Exception as exc:
            self._disable(exc)

    def fail(self, stage: str, message: str) -> None:
        if not self._usable():
            return
        try:
            self._span.update(
                level="ERROR",
                status_message=f"{stage}: {message}",
                metadata={"failure.stage": stage, "failure.message": message},
            )
        except Exception as exc:
            self._disable(exc)

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        try:
            if self._span is not None:
                self._span.end()
        except Exception as exc:
            self._disable(exc)
        finally:
            propagation = self._propagation
            self._propagation = None
            if propagation is not None:
                try:
                    propagation.__exit__(None, None, None)
                except Exception:
                    pass


class LangfuseTracer(Tracer):
    enabled = True

    def __init__(
        self,
        client,
        propagate,
        *,
        eval_run_id: str,
        code_version: str | None,
        agent_name: str,
        model_id: str,
    ):
        self._client = client
        self._propagate = propagate
        self._eval_run_id = eval_run_id
        self._code_version = code_version
        self._agent_name = agent_name
        self._model_id = model_id

    def start_session(self, *, task_id: str, input: str) -> SessionTrace:
        try:
            return _LangfuseSessionTrace(
                self._client,
                self._propagate,
                task_id=task_id,
                input=input,
                eval_run_id=self._eval_run_id,
                code_version=self._code_version,
                agent_name=self._agent_name,
                model_id=self._model_id,
            )
        except Exception as exc:
            _warn_once(f"Langfuse tracing was disabled ({type(exc).__name__})")
            return _NOOP_SESSION

    async def flush(self, timeout: float = FLUSH_TIMEOUT_SECONDS) -> None:
        finished = threading.Event()

        def run_flush() -> None:
            try:
                self._client.flush()
            except Exception as exc:
                _warn_once(f"Langfuse flush failed ({type(exc).__name__})")
            finally:
                finished.set()

        # The SDK flush is synchronous and may retry, so a daemon thread keeps
        # a stuck request from delaying the response or process shutdown.
        threading.Thread(target=run_flush, daemon=True).start()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, finished.wait, max(0.0, timeout))
        if not finished.is_set():
            _warn_once("Langfuse flush timed out; tracing is best effort")


def _load_sdk() -> SimpleNamespace:
    from langfuse import Langfuse, propagate_attributes

    return SimpleNamespace(Langfuse=Langfuse, propagate_attributes=propagate_attributes)


def _load_tracer_provider():
    from opentelemetry.sdk.trace import TracerProvider

    # A private provider keeps Langfuse from replacing the process-wide
    # OpenTelemetry provider.
    return TracerProvider()


def create_tracer(*, agent_name: str, model_id: str) -> Tracer:
    public_key = _env(PUBLIC_KEY_ENV)
    secret_key = _env(SECRET_KEY_ENV)
    if not public_key or not secret_key:
        return _NOOP_TRACER
    try:
        sdk = _load_sdk()
        client = sdk.Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=_env(BASE_URL_ENV) or None,
            tracer_provider=_load_tracer_provider(),
        )
    except Exception as exc:
        _warn_once(f"Langfuse tracing is disabled ({type(exc).__name__})")
        return _NOOP_TRACER
    return LangfuseTracer(
        client,
        sdk.propagate_attributes,
        eval_run_id=_env(TRACE_SESSION_ENV) or f"agent-{uuid4().hex}",
        code_version=_env(CODE_VERSION_ENV) or None,
        agent_name=agent_name,
        model_id=model_id,
    )
