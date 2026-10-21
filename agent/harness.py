"""Load the harness files that carry the model-facing behavioral guidance.

A harness is a directory with one required file and three optional ones:

- ``system.md``: role and solution strategy for the model (required)
- ``memory.md``: notes shared across tasks (optional)
- ``tools/<tool name>.md``: usage guidance for one official tool (optional)
- ``sub_agents/<name>.md``: one sub-agent definition (optional); its ``fetch``
  header may name one argument-free official lookup to run on each consult

Everything else in the directory is rejected so a typo cannot silently drop a
file, and so are symbolic links and junctions, so only files that live in the
harness directory reach the model. The contract text (response format, tools, prices, limits, rewards and
phase rules) is built by code; harness files only add model input.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent.tools import ALLOWED_TOOLS, SUB_AGENT_FETCH_TOOLS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR_ENV = "AGENT_HARNESS_DIR"
DEFAULT_HARNESS_DIR = "harness/baseline"
SYSTEM_FILE = "system.md"
MEMORY_FILE = "memory.md"
TOOLS_DIR = "tools"
SUB_AGENTS_DIR = "sub_agents"
SUB_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SUB_AGENT_HEADER_KEYS = ("description", "context", "fetch")
SUB_AGENT_CONTEXT_ITEMS = ("task", "dialogue", "observations")
DEFAULT_SUB_AGENT_CONTEXT = ("task", "dialogue")


@dataclass(frozen=True)
class SubAgent:
    """One sub-agent definition from ``sub_agents/<name>.md``."""

    name: str
    description: str
    context: tuple[str, ...]
    system: str
    fetch: str | None = None


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
    sub_agents: dict[str, SubAgent]

    def descriptor(self) -> dict:
        return {"name": self.name, "sha256": self.sha256}

    def sub_agent_names(self) -> list[str]:
        return list(self.sub_agents)


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


def _check_inside(entry: Path, real_root: Path, relative: str) -> None:
    """Reject links so a harness file can never pull in content from elsewhere.

    ``is_file()`` and ``is_dir()`` follow symbolic links and Windows junctions,
    so without this check a link inside the harness could feed any file on the
    machine into the model prompt. The resolved path must also stay under the
    harness root. The root itself may be a link: it is the directory chosen.
    """
    if entry.is_symlink() or entry.is_junction():
        raise HarnessError(f"Harness entry {relative} is a link; harness files must be regular files")
    if not entry.resolve().is_relative_to(real_root):
        raise HarnessError(f"Harness entry {relative} resolves outside the harness directory")


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        raise HarnessError(f"Harness directory not found: {root}")
    real_root = root.resolve()
    system = root / SYSTEM_FILE
    if not system.is_file():
        raise HarnessError(f"Harness directory {root} is missing {SYSTEM_FILE}")
    _check_inside(system, real_root, SYSTEM_FILE)
    files = {SYSTEM_FILE: system}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name in (SYSTEM_FILE, TOOLS_DIR, SUB_AGENTS_DIR):
            continue
        if entry.name == MEMORY_FILE:
            _check_inside(entry, real_root, MEMORY_FILE)
            if not entry.is_file():
                raise HarnessError(f"Harness entry {MEMORY_FILE} is not a file")
            files[MEMORY_FILE] = entry
            continue
        raise HarnessError(f"Unrecognized harness entry: {entry.name}")
    tools = root / TOOLS_DIR
    if tools.exists() or tools.is_symlink() or tools.is_junction():
        _check_inside(tools, real_root, TOOLS_DIR)
        if not tools.is_dir():
            raise HarnessError(f"Harness entry {TOOLS_DIR} is not a directory")
        for entry in sorted(tools.iterdir(), key=lambda item: item.name):
            _check_inside(entry, real_root, f"{TOOLS_DIR}/{entry.name}")
            if not entry.name.endswith(".md") or not entry.is_file():
                raise HarnessError(f"Unrecognized tool guidance file: {TOOLS_DIR}/{entry.name}")
            tool = entry.name[: -len(".md")]
            if tool not in ALLOWED_TOOLS:
                raise HarnessError(
                    f"Tool guidance file {TOOLS_DIR}/{entry.name} does not name an "
                    f"official tool. Official tools: {', '.join(ALLOWED_TOOLS)}"
                )
            files[f"{TOOLS_DIR}/{entry.name}"] = entry
    files.update(_sub_agent_files(root, real_root))
    return files


def _sub_agent_files(root: Path, real_root: Path) -> dict[str, Path]:
    directory = root / SUB_AGENTS_DIR
    if not (directory.exists() or directory.is_symlink() or directory.is_junction()):
        return {}
    _check_inside(directory, real_root, SUB_AGENTS_DIR)
    if not directory.is_dir():
        raise HarnessError(f"Harness entry {SUB_AGENTS_DIR} is not a directory")
    files = {}
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        relative = f"{SUB_AGENTS_DIR}/{entry.name}"
        _check_inside(entry, real_root, relative)
        if not entry.name.endswith(".md") or not entry.is_file():
            raise HarnessError(f"Unrecognized sub-agent file: {relative}")
        name = entry.name[: -len(".md")]
        if SUB_AGENT_NAME.fullmatch(name) is None:
            raise HarnessError(
                f"Sub-agent file {relative} does not match the name rule "
                f"{SUB_AGENT_NAME.pattern} (lowercase letters, digits and _, starting with a letter)"
            )
        files[relative] = entry
    return files


def _parse_sub_agent(name: str, relative: str, text: str) -> SubAgent:
    """Parse one definition file: a ``---`` header block, then the system prompt."""
    lines = text.split("\n")
    if lines[0].strip() != "---":
        raise HarnessError(f"Sub-agent file {relative} must start with a '---' header block")
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise HarnessError(f"Sub-agent file {relative} has no closing '---' header line")
    header: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not key:
            raise HarnessError(
                f"Sub-agent header line in {relative} is not 'key: value': {line!r}"
            )
        if key not in SUB_AGENT_HEADER_KEYS:
            raise HarnessError(
                f"Sub-agent file {relative} has an unsupported header key '{key}'. "
                f"Supported keys: {', '.join(SUB_AGENT_HEADER_KEYS)}"
            )
        if key in header:
            raise HarnessError(f"Sub-agent file {relative} repeats the header key '{key}'")
        header[key] = value.strip()
    description = header.get("description", "")
    if not description:
        raise HarnessError(f"Sub-agent file {relative} needs a non-empty description")
    context = DEFAULT_SUB_AGENT_CONTEXT
    if "context" in header:
        items = []
        for item in header["context"].split(","):
            value = item.strip()
            if not value:
                raise HarnessError(f"Sub-agent file {relative} has an empty context item")
            if value not in SUB_AGENT_CONTEXT_ITEMS:
                raise HarnessError(
                    f"Sub-agent file {relative} has an unsupported context item '{value}'. "
                    f"Allowed items: {', '.join(SUB_AGENT_CONTEXT_ITEMS)}"
                )
            if value not in items:
                items.append(value)
        context = tuple(items)
    fetch = None
    if "fetch" in header:
        fetch = header["fetch"]
        if not fetch:
            raise HarnessError(f"Sub-agent file {relative} has an empty fetch value")
        if "," in fetch:
            raise HarnessError(
                f"Sub-agent file {relative} names more than one fetch tool '{fetch}'. "
                "fetch takes exactly one tool name"
            )
        if fetch not in SUB_AGENT_FETCH_TOOLS:
            raise HarnessError(
                f"Sub-agent file {relative} has an unsupported fetch tool '{fetch}'. "
                f"Allowed fetch tools: {', '.join(SUB_AGENT_FETCH_TOOLS)}"
            )
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise HarnessError(f"Sub-agent file {relative} needs a non-empty body")
    return SubAgent(
        name=name, description=description, context=context, system=body, fetch=fetch
    )


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
    sub_agents = {}
    for relative in sorted(texts):
        if relative.startswith(f"{SUB_AGENTS_DIR}/"):
            name = relative[len(SUB_AGENTS_DIR) + 1: -len(".md")]
            sub_agents[name] = _parse_sub_agent(name, relative, texts[relative])
    return Harness(
        name=root.name,
        path=root,
        sha256=_digest(texts),
        system=texts[SYSTEM_FILE],
        memory=texts.get(MEMORY_FILE, ""),
        tool_notes=tool_notes,
        sub_agents=sub_agents,
    )
