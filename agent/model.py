"""One non-streaming model call with up to three retries.

The default provider is Command Code chat completions. ``AGENT_MODEL_PROVIDER=gms``
sends the same messages to the OpenAI Responses API through the SSAFY GMS proxy
with ``GMS_KEY`` and maps the reply back to the chat fields the loop records.
"""

import os
import time
from dataclasses import dataclass

import httpx

API_URL = "https://api.commandcode.ai/provider/v1/chat/completions"
GMS_API_URL = "https://gms.ssafy.io/gmsapi/api.openai.com/v1/responses"
PROVIDER_ENV = "AGENT_MODEL_PROVIDER"
PROVIDERS = ("commandcode", "gms")
# The environment variable that holds each provider's key.
PROVIDER_KEYS = {"commandcode": "CMD_API_KEY", "gms": "GMS_KEY"}
DEFAULT_MODEL_ID = "deepseek/deepseek-v4.1-flash"
MODEL_ENV = "AGENT_MODEL_ID"
REASONING_EFFORT_ENV = "AGENT_REASONING_EFFORT"
# The values Command Code accepts for reasoning_effort; the Responses API
# receives the same value as reasoning.effort.
REASONING_EFFORTS = ("off", "low", "medium", "high", "xhigh", "max")
REQUEST_TIMEOUT_SECONDS = 300.0
# The model spends reasoning tokens before writing the action, so a small cap
# can truncate the response before any action is produced.
MAX_OUTPUT_TOKENS = 16384
# The initial call plus three retries. Only transient failures are retried.
MAX_RETRIES = 3


def model_settings(environ) -> tuple[str, str | None]:
    """The model ID and reasoning effort chosen by the environment.

    Without the variables the model is the default and no reasoning_effort is
    sent, so the request is the same as before they existed.
    """
    model_id = str(environ.get(MODEL_ENV, "")).strip() or DEFAULT_MODEL_ID
    effort = str(environ.get(REASONING_EFFORT_ENV, "")).strip().lower() or None
    if effort is not None and effort not in REASONING_EFFORTS:
        raise ValueError(
            f"{REASONING_EFFORT_ENV}='{effort}' is not one of {', '.join(REASONING_EFFORTS)}"
        )
    return model_id, effort


def model_provider(environ) -> str:
    """The provider chosen by the environment; Command Code without the variable."""
    provider = str(environ.get(PROVIDER_ENV, "")).strip().lower() or "commandcode"
    if provider not in PROVIDERS:
        raise ValueError(f"{PROVIDER_ENV}='{provider}' is not one of {', '.join(PROVIDERS)}")
    return provider


MODEL_ID, REASONING_EFFORT = model_settings(os.environ)
PROVIDER = model_provider(os.environ)


class ModelError(RuntimeError):
    """The model call did not return a usable response."""

    def __init__(self, message: str, attempts: int | None = None):
        super().__init__(message)
        self.attempts = attempts


class _RetryableModelError(ModelError):
    """A model call failure that may succeed on another attempt."""


@dataclass
class ChatReply:
    content: str
    response_model: str | None
    usage: dict | None
    elapsed_seconds: float
    truncated: bool
    attempts: int = 1


class ModelClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str | None = None,
        model_id: str = MODEL_ID,
        reasoning_effort: str | None = REASONING_EFFORT,
        provider: str = PROVIDER,
    ):
        if provider not in PROVIDERS:
            raise ValueError(f"provider '{provider}' is not one of {', '.join(PROVIDERS)}")
        self.transport = transport
        self.api_key = api_key
        self.model_id = model_id
        self.reasoning_effort = reasoning_effort
        self.provider = provider
        self.endpoint = GMS_API_URL if provider == "gms" else API_URL

    def _key(self) -> str:
        name = PROVIDER_KEYS[self.provider]
        key = self.api_key if self.api_key is not None else os.getenv(name, "")
        key = key.strip()
        if not key:
            raise ModelError(f"{name} is not set")
        return key

    def _payload(self, messages: list[dict]) -> dict:
        if self.provider == "gms":
            # The Responses API takes the chat messages as input items as they are.
            payload = {
                "model": self.model_id,
                "input": messages,
                "stream": False,
                "store": False,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
            if self.reasoning_effort is not None:
                payload["reasoning"] = {"effort": self.reasoning_effort}
            return payload
        payload = {
            "model": self.model_id,
            "messages": messages,
            "stream": False,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    async def _complete(self, messages: list[dict]) -> ChatReply:
        payload = self._payload(messages)
        headers = {"Authorization": f"Bearer {self._key()}"}
        started = time.monotonic()
        attempts = MAX_RETRIES + 1
        # Retries are immediate; the tool loop's session deadline bounds the
        # whole sequence.
        for attempt in range(attempts):
            try:
                reply = await self._request(payload, headers, started)
                reply.attempts = attempt + 1
                return reply
            except _RetryableModelError as exc:
                if attempt + 1 == attempts:
                    raise ModelError(
                        f"{exc} (after {attempts} attempts)", attempts=attempts
                    ) from exc
            except ModelError as exc:
                exc.attempts = attempt + 1
                raise
        raise AssertionError("unreachable")

    async def _request(self, payload: dict, headers: dict, started: float) -> ChatReply:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False, transport=self.transport
            ) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            error_type = (
                _RetryableModelError
                if status in (408, 429) or status >= 500
                else ModelError
            )
            raise error_type(f"Model request failed: {exc}") from exc
        except httpx.TransportError as exc:
            raise _RetryableModelError(f"Model request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"Model request failed: {exc}") from exc
        except ValueError as exc:
            raise _RetryableModelError(f"Model response was not valid JSON: {exc}") from exc

        if self.provider == "gms":
            return _responses_reply(data, started)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise _RetryableModelError("Model response contained no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise _RetryableModelError("Model response contained no choices")
        content = (choice.get("message") or {}).get("content")
        return ChatReply(
            content=content if isinstance(content, str) else "",
            response_model=data.get("model"),
            usage=data.get("usage"),
            elapsed_seconds=time.monotonic() - started,
            truncated=choice.get("finish_reason") == "length",
        )

    async def chat(self, messages: list[dict]) -> ChatReply:
        """One non-streaming turn of a multi-message conversation."""
        return await self._complete(messages)


def _responses_usage(usage) -> dict | None:
    """Responses API usage under the chat key names the records already use."""
    if not isinstance(usage, dict):
        return None
    mapped = {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    if cached is not None:
        mapped["prompt_tokens_details"] = {"cached_tokens": cached}
    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        mapped["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return mapped


def _responses_reply(data, started: float) -> ChatReply:
    """The text of the message items in a Responses API reply."""
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, list):
        raise _RetryableModelError("Model response contained no output")
    texts = [
        part.get("text")
        for item in output
        if isinstance(item, dict) and item.get("type") == "message"
        for part in item.get("content") or []
        if isinstance(part, dict) and part.get("type") == "output_text"
        and isinstance(part.get("text"), str)
    ]
    incomplete = data.get("incomplete_details") or {}
    return ChatReply(
        content="".join(texts),
        response_model=data.get("model"),
        usage=_responses_usage(data.get("usage")),
        elapsed_seconds=time.monotonic() - started,
        truncated=data.get("status") == "incomplete"
        and incomplete.get("reason") == "max_output_tokens",
    )
