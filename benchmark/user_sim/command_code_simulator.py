"""Command Code model connection for the official BIRD-Interact user simulator.

The vendor code is not modified. This project-owned adapter is started instead
of ``user_simulator.server:app``:

- It provides the official provider extension point (``shared._local_provider``)
  with a Command Code client, so ``shared.llm.call_llm`` reaches the Command
  Code API instead of LiteLlm/Anthropic.
- It replaces the official model-call seam so a failed model call raises
  instead of returning an empty string that the pipeline turns into a
  plausible sentence. The /ask route reports such a failure as an HTTP 502
  service error, and no answer is invented.
- It replaces /init_task only to fetch the task schema from the real DB
  environment service (``DB_ENV_URL``) instead of the container-local
  ``localhost`` address; the official state and response contract stay.
- It replaces /health with non-secret connection metadata: model, endpoint,
  timeout, output cap, retry count, prompt version and call counters.

The official v2 prompts, action parsing, answer generation and
/phase_transition stay untouched. Uvicorn starts the factory with:

    python -m uvicorn command_code_simulator:create_app --factory ...
"""

import asyncio
import contextvars
import logging
import os
import sys
import threading
import time
import types

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.commandcode.ai/provider/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
REQUEST_TIMEOUT_SECONDS = 300.0
# One cap for both official stages: the stage defaults (500/1024) can be
# consumed by reasoning tokens before any answer text is produced.
MAX_OUTPUT_TOKENS = 16384
# One request is sent per model call; a failed call is reported, never retried.
MAX_RETRIES = 0
TEMPERATURE = 0.0
SCHEMA_TIMEOUT_SECONDS = 30.0


class SimulatorModelError(RuntimeError):
    """The Command Code model did not return a usable completion."""


# One list per /ask request. asyncio.to_thread copies the context into the
# worker thread, so the adapter records into that request's list only and
# concurrent tasks never share model-call state.
_request_calls: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "simulator_request_model_calls", default=None
)

_lock = threading.Lock()
_totals = {
    "model_calls": 0,
    "model_failures": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "schema_failures": 0,
}


def _env_number(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def model_settings() -> dict:
    """The Command Code connection settings; no secret values are included."""
    return {
        "model": os.getenv("USER_SIM_MODEL", "").strip() or DEFAULT_MODEL,
        "endpoint": API_URL,
        "timeout_seconds": _env_number("USER_SIM_TIMEOUT_SECONDS", REQUEST_TIMEOUT_SECONDS),
        "max_output_tokens": int(_env_number("USER_SIM_MAX_OUTPUT_TOKENS", MAX_OUTPUT_TOKENS)),
        "temperature": TEMPERATURE,
        "max_retries": MAX_RETRIES,
    }


def _completion_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, trust_env=False)


def _async_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def _token_count(value) -> int:
    return value if isinstance(value, int) else 0


def _record(entry: dict) -> None:
    calls = _request_calls.get()
    if calls is not None:
        calls.append(entry)
    usage = entry.get("usage") or {}
    with _lock:
        _totals["model_calls"] += 1
        _totals["prompt_tokens"] += _token_count(usage.get("prompt_tokens"))
        _totals["completion_tokens"] += _token_count(usage.get("completion_tokens"))
        if entry.get("error"):
            _totals["model_failures"] += 1


def _completion_text(data) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) else ""


def call_llm(
    messages: list,
    model_name: str | None = None,
    temperature: float = 0,
    max_tokens: int = 1024,
) -> str:
    """The official provider extension point, backed by the Command Code API.

    The requested model name is ignored: the official default is an Anthropic
    id this endpoint cannot serve, so ``USER_SIM_MODEL`` is the single source of
    the model id. The official per-stage output caps are replaced by one cap.
    """
    settings = model_settings()
    key = os.getenv("CMD_API_KEY", "").strip()
    if not key:
        _record({"error": "CMD_API_KEY is not set"})
        raise SimulatorModelError("CMD_API_KEY is not set for the simulator")
    payload = {
        "model": settings["model"],
        "messages": messages,
        "stream": False,
        "max_tokens": settings["max_output_tokens"],
        "temperature": settings["temperature"],
    }
    started = time.monotonic()
    try:
        with _completion_client(settings["timeout_seconds"]) as client:
            response = client.post(
                API_URL, headers={"Authorization": f"Bearer {key}"}, json=payload
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        _record({
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        raise SimulatorModelError(f"Command Code request failed: {exc}") from exc
    except ValueError as exc:
        _record({
            "error": f"invalid JSON: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
        raise SimulatorModelError(f"Command Code response was not valid JSON: {exc}") from exc

    elapsed = round(time.monotonic() - started, 3)
    usage = data.get("usage") if isinstance(data, dict) else None
    content = _completion_text(data)
    if not content.strip():
        _record({"usage": usage, "elapsed_seconds": elapsed, "error": "empty completion"})
        raise SimulatorModelError("Command Code response contained no answer text")
    _record({"usage": usage, "elapsed_seconds": elapsed})
    return content.strip()


def _build_adk_model(model_name: str | None = None):
    raise NotImplementedError(
        "The Command Code adapter only provides call_llm for the user simulator"
    )


def install_provider() -> None:
    """Register this adapter at the official provider extension point."""
    module = types.ModuleType("shared._local_provider")
    module.call_llm = call_llm
    module.build_adk_model = _build_adk_model
    sys.modules["shared._local_provider"] = module


def install_failing_seam(official) -> None:
    """Make a failed model call raise instead of becoming an empty string.

    The official seam returns "" on any failure, which the pipeline turns into
    a plausible sentence ("I'm not sure I understand your question."). It is
    replaced once at startup — never per request — and calls the same provider
    with the same arguments, so normal answers and prompts stay unchanged.
    """

    def _call_llm(prompt: str, max_tokens: int = 200) -> str:
        from shared.llm import call_llm as provider_call_llm

        return provider_call_llm(
            [{"role": "user", "content": prompt}],
            model_name=official.settings.user_sim_model,
            temperature=TEMPERATURE,
            max_tokens=max_tokens,
        )

    official._call_llm = _call_llm


def _db_env_url() -> str:
    return os.getenv("DB_ENV_URL", "http://127.0.0.1:6002").rstrip("/")


async def fetch_task_schema(task_id: str) -> str:
    """Load the task schema from the real DB environment service."""
    async with _async_client(SCHEMA_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{_db_env_url()}/schema", json={"task_id": task_id})
        response.raise_for_status()
        data = response.json()
    schema = data.get("schema") if isinstance(data, dict) else None
    if not isinstance(schema, str) or not schema.strip():
        raise ValueError("the schema response did not contain a non-empty schema")
    return schema


def _summarize(calls: list[dict], started: float) -> dict:
    prompt_tokens = completion_tokens = 0
    for entry in calls:
        usage = entry.get("usage") or {}
        prompt_tokens += _token_count(usage.get("prompt_tokens"))
        completion_tokens += _token_count(usage.get("completion_tokens"))
    return {
        "model_calls": len(calls),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "errors": [entry["error"] for entry in calls if entry.get("error")],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _configure_logging() -> None:
    """Show the per-request model metrics in the container log (INFO).

    Uvicorn configures only its own loggers, so without this handler these
    lines would be dropped while the official warnings still reached the log.
    """
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app():
    """Build the wrapped official app. Used as a Uvicorn factory."""
    from fastapi import HTTPException
    from user_simulator import server as official

    _configure_logging()
    install_provider()
    install_failing_seam(official)
    settings = model_settings()

    def replace_route(path: str) -> None:
        official.app.router.routes = [
            route for route in official.app.router.routes if getattr(route, "path", None) != path
        ]

    replace_route("/health")
    replace_route("/ask")
    replace_route("/init_task")

    @official.app.get("/health")
    async def health():
        with _lock:
            totals = dict(_totals)
        return {
            "status": "healthy",
            "service": "user_simulator_command_code",
            "prompt_version": official.PROMPT_VERSION,
            "api_key_present": bool(os.getenv("CMD_API_KEY", "").strip()),
            **settings,
            **totals,
        }

    @official.app.post("/init_task")
    async def init_task(req: official.InitTaskRequest):
        result = await official.init_task(req)
        state = official._task_states.get(req.task_id)
        if state is None:
            return result
        try:
            state.db_schema = await fetch_task_schema(req.task_id)
        except (httpx.HTTPError, ValueError) as exc:
            with _lock:
                _totals["schema_failures"] += 1
            logger.error("Schema load failed for %s: %s", req.task_id, exc)
            raise HTTPException(
                502, f"Could not load the task schema from the DB environment: {exc}"
            ) from exc
        logger.info("Loaded %d characters of schema for %s", len(state.db_schema), req.task_id)
        return result

    @official.app.post("/ask", response_model=official.AskUserResponse)
    async def ask_user(req: official.AskUserRequest):
        state = official._task_states.get(req.task_id)
        if not state:
            raise HTTPException(404, f"Task {req.task_id} not initialized")
        calls: list[dict] = []
        token = _request_calls.set(calls)
        started = time.monotonic()
        error: str | None = None
        try:
            answer = await asyncio.to_thread(official._ask_sync, state, req.question)
        except SimulatorModelError as exc:
            error = str(exc)
            answer = ""
        finally:
            _request_calls.reset(token)
        summary = _summarize(calls, started)
        if error is not None:
            logger.error("ask_user failed for %s: %s (%s)", req.task_id, error, summary)
            raise HTTPException(502, f"Command Code model call failed: {error}")
        if not answer.strip():
            logger.error("ask_user produced an empty answer for %s (%s)", req.task_id, summary)
            raise HTTPException(502, "The user simulator produced an empty answer")
        logger.info("ask_user served for %s (%s)", req.task_id, summary)
        return official.AskUserResponse(answer=answer)

    logger.warning("Command Code simulator adapter active: %s", settings)
    return official.app
