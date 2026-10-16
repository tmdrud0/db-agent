"""HTTP access to the official benchmark services; no ground truth access."""

import os

import httpx
from pydantic import BaseModel, ValidationError

# The agent waits at most this long for one execute_sql call; the session
# deadline can shorten the wait further.
EXECUTE_SQL_TIMEOUT_SECONDS = 120.0
# ask_user runs two model calls inside the official simulator, so its wait is
# longer by default; the session deadline can shorten the wait further.
ASK_USER_TIMEOUT_SECONDS = 660.0


class GradingError(RuntimeError):
    """An official service could not return a usable response."""


class SubmitResult(BaseModel):
    passed: bool
    message: str
    reward: float = 0.0
    phase_completed: int | None = None
    has_follow_up: bool = False
    follow_up_query: str | None = None


class ExecuteResult(BaseModel):
    success: bool
    result: str
    error: str | None = None


def _timeout_seconds(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds")
    return value


class Environment:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.db_url = os.getenv("DB_ENV_URL", "http://127.0.0.1:6002").rstrip("/")
        self.user_url = os.getenv("USER_SIM_URL", "http://127.0.0.1:6001").rstrip("/")
        self.transport = transport
        self.execute_sql_timeout = _timeout_seconds(
            "DB_EXECUTE_SQL_TIMEOUT_SECONDS", EXECUTE_SQL_TIMEOUT_SECONDS
        )
        self.ask_user_timeout = _timeout_seconds(
            "USER_SIM_ASK_TIMEOUT_SECONDS", ASK_USER_TIMEOUT_SECONDS
        )

    async def _post(
        self, base_url: str, path: str, payload: dict, label: str, timeout: float = 120.0
    ) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, trust_env=False, transport=self.transport
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

    async def execute_sql(self, task_id: str, sql: str) -> ExecuteResult:
        """Run one exploration statement; an SQL error is a valid official response."""
        data = await self._post(
            self.db_url, "/execute", {"task_id": task_id, "sql": sql}, "SQL execution",
            self.execute_sql_timeout,
        )
        try:
            return ExecuteResult.model_validate(data)
        except ValidationError as exc:
            raise GradingError(
                f"SQL execution response did not match the execute contract: {exc}"
            ) from exc

    async def ask_user(self, task_id: str, question: str) -> str:
        """Ask the official user simulator one question and return its answer."""
        data = await self._post(
            self.user_url, "/ask", {"task_id": task_id, "question": question}, "User question",
            self.ask_user_timeout,
        )
        answer = data.get("answer") if isinstance(data, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise GradingError("User question response did not contain a non-empty answer")
        return answer.strip()

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
