"""Official a-interact tools this loop exposes, with the official coin prices.

execute_sql and ask_user are part of the official a-interact tool set, but they
are deliberately absent here: the model can neither call nor execute them, and
they never appear in a model request.
"""

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from agent.environment import Environment

SUBMIT_COST = 3.0
UNAVAILABLE_TOOLS = ("execute_sql", "ask_user")


class ActionError(ValueError):
    """The model did not produce one allowed tool action."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    cost: float
    arguments: str
    summary: str
    required: tuple[str, ...]
    run: Callable[[Environment, str, dict], Awaitable[str]] | None


async def _schema(environment: Environment, task_id: str, args: dict) -> str:
    return await environment.fetch_schema(task_id)


async def _all_column_meanings(environment: Environment, task_id: str, args: dict) -> str:
    return await environment.fetch_column_meanings(task_id)


async def _column_meaning(environment: Environment, task_id: str, args: dict) -> str:
    return await environment.fetch_column_meaning(
        task_id, args["table_name"], args["column_name"]
    )


async def _knowledge_names(environment: Environment, task_id: str, args: dict) -> str:
    return json.dumps(await environment.fetch_knowledge_names(task_id), ensure_ascii=False)


async def _knowledge_entry(environment: Environment, task_id: str, args: dict) -> str:
    return await environment.fetch_knowledge_entry(task_id, args["knowledge_name"])


async def _all_knowledge(environment: Environment, task_id: str, args: dict) -> str:
    return await environment.fetch_knowledge(task_id)


INFORMATION_TOOLS = {
    "get_schema": ToolSpec(
        name="get_schema", cost=1.0, arguments="none",
        summary="the database schema (CREATE TABLE statements)",
        required=(), run=_schema,
    ),
    "get_all_column_meanings": ToolSpec(
        name="get_all_column_meanings", cost=1.0, arguments="none",
        summary="the meanings of all database columns",
        required=(), run=_all_column_meanings,
    ),
    "get_column_meaning": ToolSpec(
        name="get_column_meaning", cost=0.5,
        arguments='{"table_name": "<table>", "column_name": "<column>"}',
        summary="the meaning of one column",
        required=("table_name", "column_name"), run=_column_meaning,
    ),
    "get_all_external_knowledge_names": ToolSpec(
        name="get_all_external_knowledge_names", cost=0.5, arguments="none",
        summary="the names of the external knowledge entries for this database",
        required=(), run=_knowledge_names,
    ),
    "get_knowledge_definition": ToolSpec(
        name="get_knowledge_definition", cost=0.5,
        arguments='{"knowledge_name": "<name>"}',
        summary="the definition of one external knowledge entry",
        required=("knowledge_name",), run=_knowledge_entry,
    ),
    "get_all_knowledge_definitions": ToolSpec(
        name="get_all_knowledge_definitions", cost=1.0, arguments="none",
        summary="the definitions of all external knowledge entries",
        required=(), run=_all_knowledge,
    ),
}

SUBMIT_SPEC = ToolSpec(
    name="submit_sql", cost=SUBMIT_COST, arguments='{"sql": "<PostgreSQL statement>"}',
    summary="submit one SQL statement to the official grader; only a passing submission completes the task",
    required=("sql",), run=None,
)

ALLOWED_TOOLS = {**INFORMATION_TOOLS, "submit_sql": SUBMIT_SPEC}
TOOL_COSTS = {name: spec.cost for name, spec in ALLOWED_TOOLS.items()}


def catalog() -> str:
    """The tool list shown to the model, one action per line."""
    return "\n".join(
        f"- {spec.name}: {spec.summary}. arguments: {spec.arguments}. Cost: {spec.cost:g}"
        for spec in ALLOWED_TOOLS.values()
    )


def validate_args(spec: ToolSpec, args) -> dict:
    if not isinstance(args, dict):
        raise ActionError(f"{spec.name} arguments must be a JSON object.")
    missing = [name for name in spec.required if name not in args]
    if missing:
        raise ActionError(f"{spec.name} is missing the argument(s): {', '.join(missing)}.")
    unknown = sorted(name for name in args if name not in spec.required)
    if unknown:
        raise ActionError(f"{spec.name} does not take the argument(s): {', '.join(unknown)}.")
    values = {}
    for name in spec.required:
        value = args[name]
        if not isinstance(value, str) or not value.strip():
            raise ActionError(f"{spec.name} argument '{name}' must be a non-empty string.")
        values[name] = value.strip()
    return values


def parse_action(content: str) -> tuple[str, dict]:
    """Turn one model response into an allowed (tool name, validated arguments)."""
    data = _json_object(content)
    name = data.get("action")
    if not isinstance(name, str) or not name.strip():
        raise ActionError('The JSON object needs an "action" member naming one tool.')
    name = name.strip()
    if name in UNAVAILABLE_TOOLS:
        raise ActionError(
            f"'{name}' is not available in this loop. Available actions: {', '.join(ALLOWED_TOOLS)}."
        )
    spec = ALLOWED_TOOLS.get(name)
    if spec is None:
        raise ActionError(
            f"Unknown action '{name}'. Available actions: {', '.join(ALLOWED_TOOLS)}."
        )
    args = data.get("args")
    if args is None:
        args = {key: value for key, value in data.items() if key != "action"}
    return name, validate_args(spec, args)


def _json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = "" if first_newline == -1 else text[first_newline + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    if not text:
        raise ActionError("The response was empty; reply with one JSON action.")
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ActionError('The response was not a JSON object like {"action": "...", "args": {...}}.')
