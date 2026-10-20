"""Official a-interact tools this loop exposes, with the official coin prices.

All nine official tools are offered to the model in tools mode. execute_sql and
ask_user reach the official DB service and user simulator through HTTP; the
loop charges their official prices and records their observations.
"""

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from agent.environment import Environment

SUBMIT_COST = 3.0
EXECUTE_SQL_COST = 1.0
ASK_USER_COST = 2.0

SQL_WHITESPACE = " \t\n\r\f\v"
# Dollar-quote tags follow the unquoted-identifier rules, so non-ASCII
# characters are valid tag characters.
DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_\u0080-\U0010FFFF][A-Za-z0-9_\u0080-\U0010FFFF]*)?\$")

DSML_CHAR = r"[|｜]"
DSML_MARKER = rf"{DSML_CHAR}+DSML{DSML_CHAR}+"
DSML_INVOKE_OPEN = re.compile(rf"<\s*{DSML_MARKER}\s*invoke\b([^<>\n]*)>")
DSML_INVOKE_ATTRS = re.compile(r'^\s*name\s*=\s*"([^"]+)"\s*$')
DSML_INVOKE_ATTEMPT = re.compile(rf"<\s*{DSML_MARKER}\s*invoke\b")
DSML_INVOKE_CLOSE = re.compile(rf"</\s*{DSML_MARKER}\s*invoke\s*>")
DSML_PARAMETER_OPEN = re.compile(rf"<\s*{DSML_MARKER}\s*parameter\b([^<>\n]*)>")
DSML_PARAMETER_CLOSE = re.compile(rf"</\s*{DSML_MARKER}\s*parameter\s*>")
DSML_PARAMETER_NAME = re.compile(r'\bname\s*=\s*"([^"]+)"')

DSML_GUIDANCE = (
    'The DSML tool call could not be parsed. Reply with one JSON object like '
    '{"action": "...", "args": {...}}.'
)


class ActionError(ValueError):
    """The model did not produce one allowed tool action."""


@dataclass(frozen=True)
class ParsedAction:
    """The executed action plus how many actions the response contained."""

    name: str
    args: dict
    action_count: int = 1
    ignored_actions: int = 0


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

EXECUTE_SQL_SPEC = ToolSpec(
    name="execute_sql", cost=EXECUTE_SQL_COST,
    arguments='{"sql": "<one PostgreSQL statement starting with SELECT, WITH or EXPLAIN>"}',
    summary="run one read-only statement against the task database and see its result as exploration",
    required=("sql",), run=None,
)

ASK_USER_SPEC = ToolSpec(
    name="ask_user", cost=ASK_USER_COST,
    arguments='{"question": "<one question for the task user>"}',
    summary="ask the task user one clarification question and see the official answer",
    required=("question",), run=None,
)

ALLOWED_TOOLS = {
    **INFORMATION_TOOLS,
    "execute_sql": EXECUTE_SQL_SPEC,
    "ask_user": ASK_USER_SPEC,
    "submit_sql": SUBMIT_SPEC,
}
TOOL_COSTS = {name: spec.cost for name, spec in ALLOWED_TOOLS.items()}


def catalog() -> str:
    """The tool list shown to the model, one action per line."""
    return "\n".join(
        f"- {spec.name}: {spec.summary}. arguments: {spec.arguments}. Cost: {spec.cost:g}"
        for spec in ALLOWED_TOOLS.values()
    )


def tool_metadata() -> list[dict]:
    """The tool set and official prices, as non-secret runtime metadata."""
    return [{"name": spec.name, "cost": spec.cost} for spec in ALLOWED_TOOLS.values()]


def _quoted_span(text: str, start: int, quote: str, allow_escapes: bool) -> tuple[str, int] | None:
    """Return one quoted region verbatim, or None when its extent is ambiguous."""
    index = start + 1
    while index < len(text):
        if text[index] == "\\" and allow_escapes:
            return None
        if text[index] == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                index += 2
                continue
            return text[start:index + 1], index + 1
        index += 1
    return None


def _dollar_span(text: str, start: int) -> tuple[str, int] | None:
    match = DOLLAR_QUOTE.match(text, start)
    if match is None:
        return None
    end = text.find(match.group(0), match.end())
    if end == -1:
        return None
    end += len(match.group(0))
    return text[start:end], end


def _block_comment_end(text: str, start: int) -> int | None:
    depth = 0
    index = start
    while index < len(text):
        if text.startswith("/*", index):
            depth += 1
            index += 2
        elif text.startswith("*/", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
        else:
            index += 1
    return None


def _starts_string_constant(text: str, index: int) -> bool:
    """Whether a string constant token starts at index.

    Erring towards yes only narrows the duplicate check, so prefixed forms are
    included too.
    """
    if index >= len(text):
        return False
    char = text[index]
    if char == "'":
        return True
    if char == "$":
        return _dollar_span(text, index) is not None
    if char in "eEbBxXnN":
        return index + 1 < len(text) and text[index + 1] == "'"
    return char in "uU" and index + 1 < len(text) and text[index + 1] == "&"


def submission_key(sql: str) -> str:
    """The duplicate key of one submission.

    Whitespace between tokens is collapsed, but the text inside string literals,
    quoted identifiers, dollar-quoted bodies and comments is preserved exactly:
    SQL that differs only there means something different, and blocking it would
    keep a corrected statement away from the official grader. The whitespace
    between two adjacent string constants is kept as written as well, because a
    newline there concatenates them while any other spacing is a syntax error;
    collapsing it would hide the fix for that error. Regions whose extent cannot
    be decided safely (escape strings, unbalanced quotes or comments) fall back
    to the unchanged text, which only narrows the check.
    """
    text = sql.strip()
    parts: list[str] = []
    index = 0
    after_string = False
    while index < len(text):
        char = text[index]
        if char in SQL_WHITESPACE:
            start = index
            while index < len(text) and text[index] in SQL_WHITESPACE:
                index += 1
            if after_string and _starts_string_constant(text, index):
                parts.append(text[start:index])
            else:
                parts.append(" ")
            after_string = False
            continue
        if char in "'\"":
            span = _quoted_span(text, index, char, allow_escapes=char == "'")
            if span is None:
                return text
            parts.append(span[0])
            index = span[1]
            after_string = char == "'"
            continue
        if char == "$":
            span = _dollar_span(text, index)
            if span is not None:
                parts.append(span[0])
                index = span[1]
                after_string = True
                continue
        elif text.startswith("--", index):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end + 1
            parts.append(text[index:end])
            index = end
            after_string = False
            continue
        elif text.startswith("/*", index):
            end = _block_comment_end(text, index)
            if end is None:
                return text
            parts.append(text[index:end])
            index = end
            after_string = False
            continue
        parts.append(char)
        index += 1
        after_string = False
    return "".join(parts).strip()


def statement_count(sql: str) -> int | None:
    """Count top-level SQL statements, or None when the text cannot be decided safely.

    Semicolons inside string literals, quoted identifiers, dollar-quoted bodies and
    comments do not separate statements. A trailing semicolon or comment-only tail
    still counts as one statement. Unbalanced regions fall back to None so callers
    keep the previous behavior instead of rejecting SQL they cannot inspect.
    """
    text = sql
    index = 0
    count = 0
    has_content = False
    while index < len(text):
        char = text[index]
        if char in SQL_WHITESPACE:
            index += 1
            continue
        if text.startswith("--", index):
            end = text.find("\n", index)
            index = len(text) if end == -1 else end + 1
            continue
        if text.startswith("/*", index):
            end = _block_comment_end(text, index)
            if end is None:
                return None
            index = end
            continue
        if char in "'\"":
            span = _quoted_span(text, index, char, allow_escapes=char == "'")
            if span is None:
                return None
            index = span[1]
            has_content = True
            continue
        if char == "$" and DOLLAR_QUOTE.match(text, index) is not None:
            span = _dollar_span(text, index)
            if span is None:
                return None
            index = span[1]
            has_content = True
            continue
        if char in "eEbBxXnN" and index + 1 < len(text) and text[index + 1] == "'":
            span = _quoted_span(text, index + 1, "'", allow_escapes=True)
            if span is None:
                return None
            index = span[1]
            has_content = True
            continue
        if char in "uU" and text.startswith("&'", index + 1):
            span = _quoted_span(text, index + 2, "'", allow_escapes=True)
            if span is None:
                return None
            index = span[1]
            has_content = True
            continue
        if char == ";":
            if has_content:
                count += 1
                has_content = False
            index += 1
            continue
        has_content = True
        index += 1
    if has_content:
        count += 1
    return count


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
    if spec.name == "execute_sql":
        count = statement_count(values["sql"])
        if count is not None and count > 1:
            raise ActionError(
                f"execute_sql takes exactly one SQL statement, but the sql argument contains "
                f"{count} statements. Send one statement, or combine them with a subquery or "
                "UNION ALL."
            )
    return values


def _json_objects(content: str) -> list[dict]:
    """Every complete JSON object in text order.

    Objects are parsed by extent rather than by joining the first and last braces,
    so braces inside string values never split an object.
    """
    decoder = json.JSONDecoder()
    objects = []
    index = 0
    while True:
        starts = [
            position
            for position in (content.find("{", index), content.find("[", index))
            if position != -1
        ]
        if not starts:
            return objects
        start = min(starts)
        try:
            data, end = decoder.raw_decode(content, start)
        except ValueError:
            index = start + 1
            continue
        index = end
        if isinstance(data, dict):
            objects.append(data)


def _arguments(data: dict):
    args = data.get("args")
    if args is None:
        return {key: value for key, value in data.items() if key != "action"}
    return args


def _dsml_scope(content: str, start: int, next_start: int | None) -> str:
    close = DSML_INVOKE_CLOSE.search(content, start)
    if close is not None and (next_start is None or close.start() < next_start):
        return content[start:close.start()]
    if next_start is not None:
        return content[start:next_start]
    return content[start:]


def _dsml_arguments(scope: str) -> tuple[dict, bool]:
    """Read a DSML invoke's arguments, preferring a JSON ``args`` parameter.

    Native tool calls either wrap the whole argument object in ``parameter name="args"``
    or name each argument directly. Named parameters are the fallback so a call like
    ``parameter name="sql"`` is still understood.
    """
    named: dict[str, str] = {}
    args: str | None = None
    for match in DSML_PARAMETER_OPEN.finditer(scope):
        name = DSML_PARAMETER_NAME.search(match.group(1))
        if name is None:
            return {}, True
        close = DSML_PARAMETER_CLOSE.search(scope, match.end())
        if close is None:
            return {}, True
        body = scope[match.end():close.start()].strip()
        parameter = name.group(1).strip()
        if parameter == "args":
            if args is None:
                args = body
        else:
            named[parameter] = body
    if args is not None:
        try:
            value = json.loads(args)
        except ValueError:
            return {}, True
        if not isinstance(value, dict):
            return {}, True
        return value, False
    return named, False


@dataclass(frozen=True)
class _DsmlAction:
    name: str
    args: dict
    malformed: bool = False


def _dsml_actions(content: str) -> list[_DsmlAction]:
    matches = list(DSML_INVOKE_OPEN.finditer(content))
    actions = []
    for position, match in enumerate(matches):
        attrs = DSML_INVOKE_ATTRS.match(match.group(1))
        if attrs is None:
            actions.append(_DsmlAction("", {}, malformed=True))
            continue
        next_start = matches[position + 1].start() if position + 1 < len(matches) else None
        args, malformed = _dsml_arguments(_dsml_scope(content, match.end(), next_start))
        actions.append(_DsmlAction(attrs.group(1).strip(), args, malformed))
    return actions


def _first_dsml_action(content: str, actions: list[_DsmlAction]) -> _DsmlAction:
    attempt = DSML_INVOKE_ATTEMPT.search(content)
    strict = DSML_INVOKE_OPEN.search(content)
    if attempt is not None and (strict is None or attempt.start() < strict.start()):
        raise ActionError(DSML_GUIDANCE)
    if not actions or actions[0].malformed:
        raise ActionError(DSML_GUIDANCE)
    return actions[0]


def parse_response(content: str) -> ParsedAction:
    """Turn one model response into its first allowed action and ignored-action count."""
    text = content.strip()
    if not text:
        raise ActionError("The response was empty; reply with one JSON action.")
    json_objects = _json_objects(text)
    json_actions = [
        item for item in json_objects if isinstance(item.get("action"), str)
    ]
    dsml_actions = _dsml_actions(text)
    if json_actions:
        if json_objects[0] is not json_actions[0]:
            raise ActionError('The first JSON object needs an "action" member naming one tool.')
        first = json_actions[0]
        name = first["action"].strip()
        if not name:
            raise ActionError('The JSON object needs an "action" member naming one tool.')
        spec = ALLOWED_TOOLS.get(name)
        if spec is None:
            raise ActionError(
                f"Unknown action '{name}'. Available actions: {', '.join(ALLOWED_TOOLS)}."
            )
        args = validate_args(spec, _arguments(first))
        first_key = (name, _arguments(first))
        others = [(item["action"].strip(), _arguments(item)) for item in json_actions[1:]]
        others += [(item.name, item.args) for item in dsml_actions if not item.malformed]
    elif dsml_actions or DSML_INVOKE_ATTEMPT.search(text):
        first = _first_dsml_action(text, dsml_actions)
        name = first.name
        if not name:
            raise ActionError('The JSON object needs an "action" member naming one tool.')
        spec = ALLOWED_TOOLS.get(name)
        if spec is None:
            raise ActionError(
                f"Unknown action '{name}'. Available actions: {', '.join(ALLOWED_TOOLS)}."
            )
        args = validate_args(spec, first.args)
        first_key = (name, first.args)
        others = [
            (item.name, item.args) for item in dsml_actions[1:] if not item.malformed
        ]
    else:
        raise ActionError(
            'The response did not contain a JSON object like {"action": "...", "args": {...}}.'
        )
    ignored = sum(1 for candidate in others if candidate != first_key)
    return ParsedAction(name, args, action_count=1 + len(others), ignored_actions=ignored)


def parse_action(content: str) -> tuple[str, dict]:
    """Turn one model response into an allowed (tool name, validated arguments)."""
    parsed = parse_response(content)
    return parsed.name, parsed.args
