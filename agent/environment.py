"""HTTP access to the official benchmark services; no ground truth access."""

import os

import httpx
from pydantic import BaseModel, ValidationError


class GradingError(RuntimeError):
    """An official service could not return a usable response."""


class SubmitResult(BaseModel):
    passed: bool
    message: str
    reward: float = 0.0
    phase_completed: int | None = None
    has_follow_up: bool = False
    follow_up_query: str | None = None


class Environment:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.db_url = os.getenv("DB_ENV_URL", "http://127.0.0.1:6002").rstrip("/")
        self.user_url = os.getenv("USER_SIM_URL", "http://127.0.0.1:6001").rstrip("/")
        self.transport = transport

    async def _post(self, base_url: str, path: str, payload: dict, label: str) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=120, trust_env=False, transport=self.transport
            ) as client:
                response = await client.post(base_url + path, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise GradingError(f"{label} request failed: {exc}") from exc
        except ValueError as exc:
            raise GradingError(f"{label} response was not valid JSON: {exc}") from exc

    async def _fetch_information(
        self, path: str, field: str, label: str, task_id: str, **extra
    ) -> str:
        data = await self._post(self.db_url, path, {"task_id": task_id, **extra}, label)
        content = data.get(field)
        if not isinstance(content, str):
            raise GradingError(f"{label} response did not contain a {field} string")
        return content

    async def fetch_schema(self, task_id: str) -> str:
        return await self._fetch_information("/schema", "schema", "Schema fetch", task_id)

    async def fetch_column_meanings(self, task_id: str) -> str:
        return await self._fetch_information(
            "/all_column_meanings", "column_meanings", "Column meanings fetch", task_id
        )

    async def fetch_column_meaning(self, task_id: str, table_name: str, column_name: str) -> str:
        return await self._fetch_information(
            "/column_meaning", "meaning", "Column meaning fetch", task_id,
            table_name=table_name, column_name=column_name,
        )

    async def fetch_knowledge(self, task_id: str) -> str:
        return await self._fetch_information("/knowledge", "knowledge", "Knowledge fetch", task_id)

    async def fetch_knowledge_names(self, task_id: str) -> list[str]:
        data = await self._post(
            self.db_url, "/knowledge_names", {"task_id": task_id}, "Knowledge names fetch"
        )
        names = data.get("names")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise GradingError("Knowledge names fetch response did not contain a list of names")
        return names

    async def fetch_knowledge_entry(self, task_id: str, knowledge_name: str) -> str:
        return await self._fetch_information(
            "/knowledge", "knowledge", "Knowledge entry fetch", task_id,
            knowledge_name=knowledge_name,
        )

    async def transition_to_phase2(self, task_id: str) -> None:
        data = await self._post(
            self.user_url, "/phase_transition", {"task_id": task_id}, "Phase transition"
        )
        if data.get("status") != "ok":
            raise GradingError("Phase transition response did not report status ok")

    async def submit(self, task_id: str, sql: str) -> SubmitResult:
        data = await self._post(
            self.db_url, "/submit", {"task_id": task_id, "sql": sql}, "Grader"
        )
        try:
            result = SubmitResult.model_validate(data)
        except ValidationError as exc:
            raise GradingError(f"Grader response did not match the submit contract: {exc}") from exc
        if result.passed and result.phase_completed not in (1, 2):
            raise GradingError("A passing submission must identify phase 1 or 2")
        return result
