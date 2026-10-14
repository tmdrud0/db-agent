"""HTTP access to the official benchmark services; no ground truth access."""

import os

import httpx
from pydantic import BaseModel


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
        self.user_url = os.getenv("USER_SIM_URL", "http://127.0.0.1:6001")
        self.transport = transport

    async def _post(self, base: str, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(
            timeout=120, trust_env=False, transport=self.transport
        ) as client:
            response = await client.post(base.rstrip("/") + path, json=payload)
            response.raise_for_status()
            return response.json()

    async def submit(self, task_id: str, sql: str) -> SubmitResult:
        data = await self._post(self.db_url, "/submit", {"task_id": task_id, "sql": sql})
        result = SubmitResult.model_validate(data)
        if result.passed and result.phase_completed not in (1, 2):
            raise ValueError("Successful submission must identify phase 1 or 2")
        return result

    async def phase_transition(self, task_id: str) -> None:
        await self._post(self.user_url, "/phase_transition", {"task_id": task_id})
