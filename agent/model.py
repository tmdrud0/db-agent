"""One non-streaming Command Code chat call with up to three retries."""

import os
import time
from dataclasses import dataclass

import httpx

API_URL = "https://api.commandcode.ai/provider/v1/chat/completions"
MODEL_ID = "deepseek/deepseek-v4.1-flash"
REQUEST_TIMEOUT_SECONDS = 300.0
# The model spends reasoning tokens before writing the action, so a small cap
# can truncate the response before any action is produced.
MAX_OUTPUT_TOKENS = 16384
# The initial call plus three retries. Only transient failures are retried.
MAX_RETRIES = 3


class ModelError(RuntimeError):
    """The model call did not return a usable response."""


class _RetryableModelError(ModelError):
    """A model call failure that may succeed on another attempt."""


@dataclass
class ChatReply:
    content: str
    response_model: str | None
    usage: dict | None
    elapsed_seconds: float
    truncated: bool


class ModelClient:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str | None = None,
    ):
        self.transport = transport
        self.api_key = api_key

    def _key(self) -> str:
        key = self.api_key if self.api_key is not None else os.getenv("CMD_API_KEY", "")
        key = key.strip()
        if not key:
            raise ModelError("CMD_API_KEY is not set")
        return key

    async def _complete(self, messages: list[dict]) -> ChatReply:
        payload = {
            "model": MODEL_ID,
            "messages": messages,
            "stream": False,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        headers = {"Authorization": f"Bearer {self._key()}"}
        started = time.monotonic()
        attempts = MAX_RETRIES + 1
        # Retries are immediate; the tool loop's session deadline bounds the
        # whole sequence.
        for attempt in range(attempts):
            try:
                return await self._request(payload, headers, started)
            except _RetryableModelError as exc:
                if attempt + 1 == attempts:
                    raise ModelError(f"{exc} (after {attempts} attempts)") from exc
        raise AssertionError("unreachable")

    async def _request(self, payload: dict, headers: dict, started: float) -> ChatReply:
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False, transport=self.transport
            ) as client:
                response = await client.post(API_URL, headers=headers, json=payload)
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
