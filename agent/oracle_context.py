"""Diagnostic oracle-context mode (TASK-015, branch exp/oracle-context only).

When ``AGENT_ORACLE_CONTEXT_DIR`` names a directory built by
``scripts/build_oracle_context.py``, every session reads one prebuilt context
file for its task, shows that context in the prompt, offers only execute_sql,
ask_user and submit_sql, and starts with ``BUDGET_OFFSET`` fewer coins.

The context files are selected with ground-truth data, so results of this mode
are diagnostic and are never agent performance. Without the variable nothing
here is used and the loop behaves exactly as before.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ORACLE_CONTEXT_ENV = "AGENT_ORACLE_CONTEXT_DIR"
MANIFEST_FILE = "manifest.json"
BUDGET_OFFSET = 3.0
ORACLE_ACTIONS = ("execute_sql", "ask_user", "submit_sql")
CONTEXT_PARTS = ("schema", "column_meanings", "knowledge")
PART_TITLES = {
    "schema": "Schema",
    "column_meanings": "Column meanings",
    "knowledge": "External knowledge",
}
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent

INITIAL_HEADING = "Provided database context:"
INITIAL_NOTE = (
    "The schema, column meanings and external knowledge below are the parts provided as "
    "relevant to this task."
)
FOLLOW_UP_HEADING = "Additional database context for the follow-up question:"
FOLLOW_UP_NOTE = (
    "The parts below are provided as relevant to the follow-up question, in addition to the "
    "context given at the start."
)
EMPTY_PART = "(none provided)"


class OracleContextError(RuntimeError):
    """The oracle-context directory or one task's context file is unusable."""


def context_sha256(phase1: dict, phase2: dict) -> str:
    """Hash of the injected text only, so the generation record does not change it."""
    canonical = json.dumps(
        {"phase1": phase1, "phase2": phase2}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parts(block, label: str) -> dict[str, str]:
    if not isinstance(block, dict):
        raise OracleContextError(f"{label} is not an object")
    parts = {}
    for name in CONTEXT_PARTS:
        value = block.get(name)
        if not isinstance(value, str):
            raise OracleContextError(f"{label}.{name} is not a string")
        parts[name] = value
    return parts


@dataclass(frozen=True)
class TaskContext:
    """The text one session receives: phase 1 parts and the phase 2 additions."""

    phase1: dict[str, str]
    phase2: dict[str, str]
    sha256: str

    def chars(self) -> dict[str, int]:
        return {
            "phase1": sum(len(text) for text in self.phase1.values()),
            "phase2": sum(len(text) for text in self.phase2.values()),
        }


@dataclass(frozen=True)
class OracleContext:
    """One experiment group's directory, checked when the server starts."""

    path: Path
    group: str

    def descriptor(self) -> dict:
        return {"oracle_context": self.group, "budget_offset": BUDGET_OFFSET}

    def load(self, task_id: str) -> TaskContext:
        if TASK_ID.fullmatch(task_id) is None:
            raise OracleContextError(f"task_id '{task_id}' cannot name a context file")
        path = self.path / f"{task_id}.json"
        if not path.is_file():
            raise OracleContextError(
                f"No oracle context file for task '{task_id}' in {self.path} "
                f"(group {self.group}); the session was not started"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OracleContextError(f"Could not read oracle context file {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise OracleContextError(f"Oracle context file {path} is not a JSON object")
        if data.get("experiment") != self.group or data.get("task_id") != task_id:
            raise OracleContextError(
                f"Oracle context file {path} belongs to group {data.get('experiment')!r} and "
                f"task {data.get('task_id')!r}, not group {self.group!r} and task {task_id!r}"
            )
        phase1 = _parts(data.get("phase1"), f"{path.name} phase1")
        phase2 = _parts(data.get("phase2"), f"{path.name} phase2")
        digest = context_sha256(phase1, phase2)
        if data.get("context_sha256") != digest:
            raise OracleContextError(
                f"Oracle context file {path} does not match its context_sha256; rebuild it"
            )
        return TaskContext(phase1=phase1, phase2=phase2, sha256=digest)


def load_oracle_context(directory: str | os.PathLike | None = None) -> OracleContext | None:
    """Read the experiment directory from the argument or the environment.

    No directory means the mode is off. A directory that is missing or has no
    manifest stops the server instead of silently running the normal loop.
    """
    if directory is None:
        directory = os.getenv(ORACLE_CONTEXT_ENV, "").strip()
        if not directory:
            return None
    path = Path(directory).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_dir():
        raise OracleContextError(f"{ORACLE_CONTEXT_ENV} directory not found: {path}")
    manifest = path / MANIFEST_FILE
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OracleContextError(
            f"{ORACLE_CONTEXT_ENV} directory {path} has no readable {MANIFEST_FILE}: {exc}"
        ) from exc
    group = data.get("experiment") if isinstance(data, dict) else None
    if not isinstance(group, str) or not group:
        raise OracleContextError(f"{manifest} does not name an experiment group")
    return OracleContext(path=path, group=group)


def _section(heading: str, note: str, parts: dict[str, str], keep_empty: bool) -> str:
    lines = [heading, note]
    for name in CONTEXT_PARTS:
        text = parts.get(name, "").strip("\n")
        if not text and not keep_empty:
            continue
        lines.append("")
        lines.append(f"{PART_TITLES[name]}:")
        lines.append(text or EMPTY_PART)
    return "\n".join(lines)


def initial_section(context: TaskContext) -> str:
    """The context block of the first user message; every part is always listed."""
    return _section(INITIAL_HEADING, INITIAL_NOTE, context.phase1, keep_empty=True)


def follow_up_section(context: TaskContext) -> str:
    """The phase 2 additions, or an empty string when there are none."""
    if not any(text.strip() for text in context.phase2.values()):
        return ""
    return _section(FOLLOW_UP_HEADING, FOLLOW_UP_NOTE, context.phase2, keep_empty=False)
