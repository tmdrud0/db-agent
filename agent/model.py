"""One non-streaming Command Code model call; no SDK, streaming, or retries."""

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


class ModelError(RuntimeError):
    """The model call did not return a usable response."""


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
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False, transport=self.transport
            ) as client:
                response = await client.post(API_URL, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ModelError(f"Model request failed: {exc}") from exc
        except ValueError as exc:
            raise ModelError(f"Model response was not valid JSON: {exc}") from exc
        elapsed = time.monotonic() - started

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ModelError("Model response contained no choices")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ModelError("Model response contained no choices")
        content = (choice.get("message") or {}).get("content")
        return ChatReply(
            content=content if isinstance(content, str) else "",
            response_model=data.get("model"),
            usage=data.get("usage"),
            elapsed_seconds=elapsed,
            truncated=choice.get("finish_reason") == "length",
        )

    async def chat(self, messages: list[dict]) -> ChatReply:
        """One non-streaming turn of a multi-message conversation."""
        return await self._complete(messages)
