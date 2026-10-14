"""One non-streaming Command Code model call; no SDK, streaming, or retries."""

import os
import time
from dataclasses import dataclass

import httpx

API_URL = "https://api.commandcode.ai/provider/v1/chat/completions"
MODEL_ID = "deepseek/deepseek-v4.1-flash"
REQUEST_TIMEOUT_SECONDS = 300.0
# The model spends reasoning tokens before writing SQL, so a small cap can
# truncate the answer before any SQL is produced.
MAX_OUTPUT_TOKENS = 16384

SYSTEM_PROMPT = (
    "You answer PostgreSQL questions for a benchmark. "
    "Output only the final SQL statement: no explanation, no markdown, no code fences."
)


class ModelError(RuntimeError):
    """The model did not return a usable SQL statement."""


@dataclass
class ModelReply:
    sql: str
    response_model: str | None
    usage: dict | None
    elapsed_seconds: float


def extract_sql(content: str) -> str:
    text = content.strip()
    if text.startswith("```") and text.endswith("```") and text.count("```") == 2:
        opening_end = text.find("\n")
        text = "" if opening_end == -1 else text[opening_end + 1 : -3].strip()
    if not text:
        raise ModelError("Model response contained no SQL")
    return text


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

    async def generate_sql(self, user_prompt: str) -> ModelReply:
        payload = {
            "model": MODEL_ID,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
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
        if choice.get("finish_reason") == "length":
            raise ModelError("Model output was truncated")
        content = (choice.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelError("Model response contained no SQL")
        return ModelReply(
            sql=extract_sql(content),
            response_model=data.get("model"),
            usage=data.get("usage"),
            elapsed_seconds=elapsed,
        )
