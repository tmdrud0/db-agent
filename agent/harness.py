"""Load the harness files that carry the model-facing behavioral guidance.

A harness is a directory with one required file and two optional ones:

- ``system.md``: role and solution strategy for the model (required)
- ``memory.md``: notes shared across tasks (optional)
- ``tools/<tool name>.md``: usage guidance for one official tool (optional)

Everything else in the directory is rejected so a typo cannot silently drop a
file. The contract text (response format, tools, prices, limits, rewards and
phase rules) is built by code; harness files only add model input.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from agent.tools import ALLOWED_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR_ENV = "AGENT_HARNESS_DIR"
DEFAULT_HARNESS_DIR = "harness/baseline"
SYSTEM_FILE = "system.md"
MEMORY_FILE = "memory.md"
TOOLS_DIR = "tools"


class HarnessError(ValueError):
    """The harness directory is missing or contains something unrecognized."""


@dataclass(frozen=True)
class Harness:
    """One loaded harness. Files are read once; the content never changes."""

    name: str
    path: Path
    sha256: str
    system: str
    memory: str
    tool_notes: dict[str, str]

    def descriptor(self) -> dict:
        return {"name": self.name, "sha256": self.sha256}


def _resolve(directory: str | os.PathLike | None) -> Path:
    if directory is None:
        directory = os.getenv(HARNESS_DIR_ENV, "").strip() or DEFAULT_HARNESS_DIR
    path = Path(directory).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _read_text(path: Path, relative: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HarnessError(f"Could not read harness file {relative}: {exc}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HarnessError(f"Harness file {relative} is not valid UTF-8") from exc
    # Windows editors may write CRLF. Normalizing keeps the hash and the prompt
    # identical for the same text on every platform.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        raise HarnessError(f"Harness directory not found: {root}")
    system = root / SYSTEM_FILE
    if not system.is_file():
        raise HarnessError(f"Harness directory {root} is missing {SYSTEM_FILE}")
    files = {SYSTEM_FILE: system}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name in (SYSTEM_FILE, TOOLS_DIR):
            continue
        if entry.name == MEMORY_FILE:
            if not entry.is_file():
                raise HarnessError(f"Harness entry {MEMORY_FILE} is not a file")
            files[MEMORY_FILE] = entry
            continue
        raise HarnessError(f"Unrecognized harness entry: {entry.name}")
    tools = root / TOOLS_DIR
    if tools.exists():
        if not tools.is_dir():
            raise HarnessError(f"Harness entry {TOOLS_DIR} is not a directory")
        for entry in sorted(tools.iterdir(), key=lambda item: item.name):
            if not entry.name.endswith(".md") or not entry.is_file():
                raise HarnessError(f"Unrecognized tool guidance file: {TOOLS_DIR}/{entry.name}")
            tool = entry.name[: -len(".md")]
            if tool not in ALLOWED_TOOLS:
                raise HarnessError(
                    f"Tool guidance file {TOOLS_DIR}/{entry.name} does not name an "
                    f"official tool. Official tools: {', '.join(ALLOWED_TOOLS)}"
                )
            files[f"{TOOLS_DIR}/{entry.name}"] = entry
    return files


def _digest(files: dict[str, str]) -> str:
    """SHA-256 over sorted relative paths and normalized file content."""
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def load_harness(directory: str | os.PathLike | None = None) -> Harness:
    """Read one harness directory, or the default one when none is given."""
    root = _resolve(directory)
    paths = _files(root)
    texts = {relative: _read_text(path, relative) for relative, path in paths.items()}
    tool_notes = {
        relative[len(TOOLS_DIR) + 1: -len(".md")]: texts[relative]
        for relative in texts
        if relative.startswith(f"{TOOLS_DIR}/")
    }
    return Harness(
        name=root.name,
        path=root,
        sha256=_digest(texts),
        system=texts[SYSTEM_FILE],
        memory=texts.get(MEMORY_FILE, ""),
        tool_notes=tool_notes,
    )
