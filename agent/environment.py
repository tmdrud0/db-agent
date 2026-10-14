"""HTTP access to the official benchmark services; no ground truth access."""

import os

import httpx
from pydantic import BaseModel, ValidationError


class GradingError(RuntimeError):
    """The official grader could not return a valid submission result."""


class SubmitResult(BaseModel):
    passed: bool
    message: str
    reward: float = 0.0
    phase_completed: int | None = None
    has_follow_up: bool = False
    follow_up_query: str | None = None


class Environment:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.db_url = os.getenv("DB_ENV_URL", "http://127.0.0.1:6002")
        self.transport = transport

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=120, trust_env=False, transport=self.transport
            ) as client:
                response = await client.post(self.db_url.rstrip("/") + path, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise GradingError(f"Grader request failed: {exc}") from exc
        except ValueError as exc:
            raise GradingError(f"Grader response was not valid JSON: {exc}") from exc

    async def submit(self, task_id: str, sql: str) -> SubmitResult:
        data = await self._post("/submit", {"task_id": task_id, "sql": sql})
        try:
            result = SubmitResult.model_validate(data)
        except ValidationError as exc:
            raise GradingError(f"Grader response did not match the submit contract: {exc}") from exc
        if result.passed and result.phase_completed not in (1, 2):
            raise GradingError("A passing submission must identify phase 1 or 2")
        return result
