"""Build the TASK-015 oracle-context files for the dev30 diagnostic experiment.

Diagnostic only: the knowledge (all groups) and the group A tables are chosen
with ground-truth data, so runs that use these files are not agent
performance. The outputs are derived from ground truth and stay under
``private/oracle_context/`` (never committed).

Groups (schema and column meanings; knowledge is the same in all groups):

- A: the tables whose names appear in the calibrated ``sol_sql``
- B: the tables a curator sub-agent selected from the question, the knowledge
  and the full schema and column meanings (``curation-inputs``, then
  ``record-selections``)
- C: the full schema and all column meanings

Every injected text is cut from the files the official DB service reads and
serialized the way its tools serialize them:

- schema: whole ``CREATE TABLE`` blocks of ``<db>_schema.txt`` (each block runs
  to the next ``CREATE TABLE`` line, so its sample rows stay with it), joined
  in schema order; C is the file text unchanged
- column meanings: the lowercased ``db|table|column`` entries of the selected
  tables, in file order, as ``json.dumps(..., indent=2)`` like
  ``/all_column_meanings``
- knowledge: the visible fields of the selected entries, in file order, as
  ``json.dumps([...], indent=2)`` like ``/knowledge`` without a name

Commands: ``curation-inputs``, ``record-selections``, ``check-curation-inputs``,
``build`` and ``summary``. Run with ``--help`` for the options.
"""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.oracle_context import MANIFEST_FILE, context_sha256  # noqa: E402

DEFAULT_DATA = "private/lite30.calibrated.jsonl"
DEFAULT_MANIFEST = "eval_sets/lite30.manifest.json"
DEFAULT_DB_DIR = "vendor/BIRD-Interact/BIRD-Interact-ADK/bird-interact-lite"
DEFAULT_OUT = "private/oracle_context"
CURATOR_PROMPT = ROOT / "scripts/oracle_context_curator.md"
GROUPS = ("A", "B", "C")
KNOWLEDGE_VISIBLE_FIELDS = ("id", "knowledge", "description", "definition")
CURATION_DIR = "curation"
RAW_DIR = "raw"
SELECTIONS_DIR = "selections"
DIAGNOSTIC_NOTE = (
    "Diagnostic oracle-context experiment (TASK-015): knowledge and group A tables are "
    "selected with ground truth. Results are not agent performance."
)
SERIALIZATION = {
    "schema": "whole CREATE TABLE blocks (to the next CREATE TABLE line), schema order",
    "column_meanings": "json.dumps(selected lowercased entries, indent=2), file order",
    "knowledge": "json.dumps([visible fields of selected entries], indent=2), file order",
    "empty": "a part with no selected item is an empty string",
}

CREATE_TABLE = re.compile(r'^CREATE TABLE\s+(?:"([^"]+)"|([A-Za-z_][\w$]*))', re.M)
# Dollar quotes in this data wrap function and DO bodies, which are SQL that
# names tables, so only their delimiters are dropped; string literals and
# comments are removed.
SQL_SKIP = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\$\w*\$", re.S)
SQL_IDENTIFIER = re.compile(r'"((?:[^"]|"")+)"|([A-Za-z_][\w$]*)')
SQL_RELATION = re.compile(
    r'\b(?:FROM|JOIN|UPDATE|INTO|TABLE|USING|ON|REFERENCES)\s+'
    r'((?:"[^"]+"|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][\w$]*))?)',
    re.I,
)


# --- official text ---------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_schema(path: Path) -> str:
    """The text ``/schema`` returns: the file read in text mode (CRLF -> LF)."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def schema_blocks(schema: str) -> list[tuple[str, str]]:
    """``(lowercased table name, block text)`` in schema order."""
    matches = list(CREATE_TABLE.finditer(schema))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(schema)
        name = (match.group(1) or match.group(2)).lower()
        blocks.append((name, schema[match.start():end]))
    return blocks


def read_column_meanings(path: Path) -> dict:
    """The dictionary ``/all_column_meanings`` serializes: keys lowercased."""
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    return {key.lower(): value for key, value in raw.items()}


def read_kb(path: Path) -> list[dict]:
    entries = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                entries.append(json.loads(line.strip()))
    return entries


@dataclass(frozen=True)
class Database:
    name: str
    schema: str
    blocks: tuple[tuple[str, str], ...]
    meanings: dict
    kb: tuple[dict, ...]
    files: dict[str, Path]

    @property
    def tables(self) -> list[str]:
        return [name for name, _ in self.blocks]

    def entry(self, knowledge_id: int) -> dict | None:
        return next((entry for entry in self.kb if entry.get("id") == knowledge_id), None)


def load_database(db_dir: Path, name: str) -> Database:
    folder = db_dir / name
    files = {
        "schema": folder / f"{name}_schema.txt",
        "column_meanings": folder / f"{name}_column_meaning_base.json",
        "kb": folder / f"{name}_kb.jsonl",
    }
    schema = read_schema(files["schema"])
    return Database(
        name=name,
        schema=schema,
        blocks=tuple(schema_blocks(schema)),
        meanings=read_column_meanings(files["column_meanings"]),
        kb=tuple(read_kb(files["kb"])),
        files=files,
    )


def schema_text(database: Database, tables) -> str:
    selected = set(tables)
    return "".join(block for name, block in database.blocks if name in selected).rstrip()


def meanings_text(database: Database, tables) -> str:
    selected = set(tables)
    entries = {
        key: value for key, value in database.meanings.items()
        if len(key.split("|")) >= 3 and key.split("|")[1] in selected
    }
    return json.dumps(entries, indent=2) if entries else ""


def knowledge_text(database: Database, ids) -> str:
    selected = set(ids)
    visible = [
        {field: entry[field] for field in KNOWLEDGE_VISIBLE_FIELDS if field in entry}
        for entry in database.kb if entry.get("id") in selected
    ]
    return json.dumps(visible, indent=2) if visible else ""


# --- selection rules -------------------------------------------------------


def deleted_ids(record: dict) -> set[int]:
    """Knowledge the environment hides for this task (``_filter_knowledge``)."""
    return {
        item["deleted_knowledge"] for item in record.get("knowledge_ambiguity") or []
        if item.get("deleted_knowledge") is not None
    }


def _children(entry: dict) -> list[int]:
    value = entry.get("children_knowledge")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, int) and item >= 0]
    if isinstance(value, int) and value >= 0:
        return [value]
    return []


def knowledge_ids(database: Database, roots, deleted: set[int]) -> list[int]:
    """The roots and all their descendants, minus deleted entries, in file order.

    The walk passes through a deleted entry, so its children stay visible like
    they are through the official tools; only the deleted entry itself is left out.
    """
    seen: set[int] = set()
    stack = [item for item in roots if isinstance(item, int)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        entry = database.entry(current)
        if entry is not None:
            stack.extend(_children(entry))
    return [
        entry["id"] for entry in database.kb
        if entry.get("id") in seen and entry["id"] not in deleted
    ]


def _sql_texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _identifier(text: str) -> str:
    return text[1:-1].replace('""', '"').lower() if text.startswith('"') else text.lower()


def sql_tables(sql, tables) -> tuple[list[str], list[str]]:
    """Schema tables named in the SQL, and those seen in a relation position.

    Comments and string literals are removed first; dollar-quoted bodies stay.
    Every identifier part is compared, so ``public.t``, ``"T"`` and aliased
    tables are found through the table name itself. The second list (names
    right after FROM/JOIN/UPDATE/INTO/TABLE/USING/ON/REFERENCES) is only used to report
    token matches that may be columns sharing a table name.
    """
    known = set(tables)
    found: set[str] = set()
    relations: set[str] = set()
    for text in _sql_texts(sql):
        clean = SQL_SKIP.sub(" ", text)
        for match in SQL_IDENTIFIER.finditer(clean):
            name = (match.group(1).replace('""', '"') if match.group(1) else match.group(2)).lower()
            if name in known:
                found.add(name)
        for match in SQL_RELATION.finditer(clean):
            parts = re.findall(r'"[^"]+"|[A-Za-z_][\w$]*', match.group(1))
            name = _identifier(parts[-1])
            if name in known:
                relations.add(name)
    order = [name for name in tables if name in found]
    return order, [name for name in order if name in relations]


def parse_curator_output(raw: str, tables) -> tuple[list[str], list[str], str | None]:
    """Valid tables in schema order, names not in the schema, and the reason."""
    decoder = json.JSONDecoder()
    index = 0
    data = None
    while True:
        start = raw.find("{", index)
        if start == -1:
            break
        try:
            candidate, end = decoder.raw_decode(raw, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("tables"), list):
            data = candidate
            break
        index = end
    if data is None:
        raise ValueError('The curator output has no JSON object with a "tables" list')
    known = set(tables)
    chosen: set[str] = set()
    excluded: list[str] = []
    for item in data["tables"]:
        if not isinstance(item, str) or not item.strip():
            excluded.append(str(item))
            continue
        name = _identifier(item.strip().split(".")[-1].strip())
        if name in known:
            chosen.add(name)
        elif item not in excluded:
            excluded.append(item)
    reason = data.get("reason") if isinstance(data.get("reason"), str) else None
    return [name for name in tables if name in chosen], excluded, reason


# --- context files ---------------------------------------------------------


def _block(database: Database, tables, ids, full: bool = False) -> dict:
    if full:
        schema, meanings = database.schema, json.dumps(database.meanings, indent=2)
    else:
        schema, meanings = schema_text(database, tables), meanings_text(database, tables)
    return {
        "schema": schema,
        "column_meanings": meanings,
        "knowledge": knowledge_text(database, ids),
        "tables": list(tables),
        "knowledge_ids": list(ids),
    }


def phase_knowledge(database: Database, record: dict) -> tuple[list[int], list[int]]:
    deleted = deleted_ids(record)
    first = knowledge_ids(database, record.get("external_knowledge") or [], deleted)
    follow_up = record.get("follow_up") or {}
    second = knowledge_ids(database, follow_up.get("external_knowledge") or [], deleted)
    return first, [item for item in second if item not in first]


def group_tables(group: str, record: dict, database: Database, selection: dict | None):
    """Phase 1 tables and the phase 2 additions for groups A and B."""
    if group == "A":
        first, _ = sql_tables(record.get("sol_sql"), database.tables)
        second, _ = sql_tables((record.get("follow_up") or {}).get("sol_sql"), database.tables)
    elif group == "B":
        if selection is None:
            raise ValueError(f"Group B needs the selection record of {record['instance_id']}")
        first = list(selection["phase1"]["tables"])
        second = list((selection.get("phase2") or {}).get("tables") or [])
    else:
        raise ValueError(f"Group {group} has no table selection")
    return first, [name for name in second if name not in first]


def build_task(
    group: str, record: dict, database: Database, selection: dict | None = None
) -> dict:
    """The context of one task without its generation record."""
    first_ids, second_ids = phase_knowledge(database, record)
    if group == "C":
        phase1 = _block(database, database.tables, first_ids, full=True)
        phase2 = _block(database, [], second_ids)
    else:
        first, second = group_tables(group, record, database, selection)
        phase1 = _block(database, first, first_ids)
        phase2 = _block(database, second, second_ids)
    parts = ("schema", "column_meanings", "knowledge")
    digest = context_sha256(
        {name: phase1[name] for name in parts}, {name: phase2[name] for name in parts}
    )
    return {
        "experiment": group,
        "diagnostic": DIAGNOSTIC_NOTE,
        "task_id": record["instance_id"],
        "db_name": record["selected_database"],
        "phase1": phase1,
        "phase2": phase2,
        "context_sha256": digest,
    }


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# --- B curation ------------------------------------------------------------


def curation_bundle(
    record: dict, database: Database, phase: int, prompt: str,
    phase1_tables: list[str] | None = None,
) -> str:
    """The only input a curator sees: no SQL, test case or ambiguity snippet."""
    first_ids, second_ids = phase_knowledge(database, record)
    if phase == 1:
        question_title, question = "Question", record.get("amb_user_query") or ""
        ids = first_ids
    else:
        question_title = "Follow-up question"
        question = (record.get("follow_up") or {}).get("query") or ""
        ids = first_ids + second_ids
    sections = [
        "# Table curation input",
        f"Task: {record['instance_id']}\nPhase: {phase}\nDatabase: {database.name}",
        f"## Instructions\n\n{prompt.strip()}",
        f"## {question_title}\n\n{question.strip()}",
    ]
    if phase == 2:
        sections.append(
            "## Tables selected for the previous question\n\n"
            + json.dumps(list(phase1_tables or []))
        )
    sections += [
        f"## External knowledge\n\n{knowledge_text(database, ids) or '(none)'}",
        f"## Schema\n\n{database.schema.strip()}",
        f"## Column meanings\n\n{json.dumps(database.meanings, indent=2)}",
    ]
    return "\n\n".join(sections) + "\n"


def forbidden_strings(record: dict) -> list[tuple[str, str]]:
    """Ground-truth text that must never reach a curator or the agent."""
    items: list[tuple[str, str]] = []
    follow_up = record.get("follow_up") or {}
    for label, value in (
        ("sol_sql", record.get("sol_sql")), ("follow_up.sol_sql", follow_up.get("sol_sql")),
        ("test_cases", record.get("test_cases")),
        ("follow_up.test_cases", follow_up.get("test_cases")),
    ):
        items += [(label, text) for text in _sql_texts(value)]
    ambiguity = record.get("user_query_ambiguity") or {}
    for kind in ("critical_ambiguity", "non_critical_ambiguity"):
        for item in ambiguity.get(kind) or []:
            if isinstance(item.get("sql_snippet"), str):
                items.append((f"user_query_ambiguity.{kind}.sql_snippet", item["sql_snippet"]))
    for item in record.get("knowledge_ambiguity") or []:
        if isinstance(item.get("sql_snippet"), str):
            items.append(("knowledge_ambiguity.sql_snippet", item["sql_snippet"]))
    return [(label, text.strip()) for label, text in items if text.strip()]


# --- command plumbing ------------------------------------------------------


@dataclass
class Paths:
    root: Path
    data: Path
    manifest: Path
    db_dir: Path
    out: Path

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def _paths(args) -> Paths:
    root = Path(args.project_root).resolve()

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    return Paths(root, resolve(args.data), resolve(args.manifest), resolve(args.db_dir),
                 resolve(args.out))


def load_records(paths: Paths) -> list[dict]:
    ids = json.loads(paths.manifest.read_text(encoding="utf-8"))["selected_ids"]
    records = {}
    with open(paths.data, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                records[record["instance_id"]] = record
    missing = [task for task in ids if task not in records]
    if missing:
        raise SystemExit(f"Tasks missing from {paths.data}: {', '.join(missing)}")
    return [records[task] for task in sorted(ids)]


class Databases(dict):
    def __init__(self, db_dir: Path):
        super().__init__()
        self.db_dir = db_dir

    def __missing__(self, name: str) -> Database:
        self[name] = load_database(self.db_dir, name)
        return self[name]


def _selection_path(paths: Paths, task_id: str) -> Path:
    return paths.out / "B" / SELECTIONS_DIR / f"{task_id}.json"


def _read_selection(paths: Paths, task_id: str) -> dict | None:
    path = _selection_path(paths, task_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _bundle_path(paths: Paths, task_id: str, phase: int) -> Path:
    return paths.out / "B" / CURATION_DIR / f"{task_id}.phase{phase}.md"


def _raw_path(paths: Paths, task_id: str, phase: int) -> Path:
    return paths.out / "B" / RAW_DIR / f"{task_id}.phase{phase}.txt"


def _phase1_tables(paths: Paths, task_id: str) -> list[str]:
    selection = _read_selection(paths, task_id)
    if not selection or "phase1" not in selection:
        raise SystemExit(f"Record the phase 1 selection of {task_id} before phase 2")
    return list(selection["phase1"]["tables"])


def expected_bundle(paths: Paths, record: dict, database: Database, phase: int) -> str:
    prompt = CURATOR_PROMPT.read_text(encoding="utf-8")
    first = _phase1_tables(paths, record["instance_id"]) if phase == 2 else None
    return curation_bundle(record, database, phase, prompt, first)


def command_curation_inputs(args) -> int:
    paths = _paths(args)
    databases = Databases(paths.db_dir)
    for record in load_records(paths):
        if args.phase == 2 and not (record.get("follow_up") or {}).get("query"):
            continue
        bundle = expected_bundle(paths, record, databases[record["selected_database"]], args.phase)
        target = _bundle_path(paths, record["instance_id"], args.phase)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(bundle)
        print(f"{paths.rel(target)}  sha256={hashlib.sha256(bundle.encode('utf-8')).hexdigest()}")
    return 0


def command_record_selections(args) -> int:
    paths = _paths(args)
    databases = Databases(paths.db_dir)
    missing = []
    for record in load_records(paths):
        task_id = record["instance_id"]
        bundle_path = _bundle_path(paths, task_id, args.phase)
        if not bundle_path.is_file():
            continue
        raw_path = _raw_path(paths, task_id, args.phase)
        if not raw_path.is_file():
            missing.append(task_id)
            continue
        database = databases[record["selected_database"]]
        bundle = bundle_path.read_text(encoding="utf-8")
        if bundle != expected_bundle(paths, record, database, args.phase):
            raise SystemExit(f"{paths.rel(bundle_path)} differs from its rebuilt input; rerun curation-inputs")
        raw = raw_path.read_text(encoding="utf-8")
        tables, excluded, reason = parse_curator_output(raw, database.tables)
        selection = _read_selection(paths, task_id) or {
            "task_id": task_id, "db_name": database.name,
        }
        selection[f"phase{args.phase}"] = {
            "input_file": paths.rel(bundle_path),
            "input_sha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest(),
            "model": args.model,
            "effort": args.effort,
            "raw_output_file": paths.rel(raw_path),
            "raw_output": raw,
            "tables": tables,
            "excluded_tables": excluded,
            "reason": reason,
        }
        write_json(_selection_path(paths, task_id), selection)
        note = f" excluded={excluded}" if excluded else ""
        print(f"{task_id} phase{args.phase}: {len(tables)} tables{note}")
    if missing:
        print(f"No raw curator output for: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


def command_check_curation_inputs(args) -> int:
    """AC-3: every bundle equals its rebuild and holds no ground-truth SQL text."""
    paths = _paths(args)
    databases = Databases(paths.db_dir)
    problems = 0
    checked = 0
    for record in load_records(paths):
        database = databases[record["selected_database"]]
        # Text the agent receives anyway: a hit inside it is not a leak.
        public = "\n".join([
            record.get("amb_user_query") or "",
            (record.get("follow_up") or {}).get("query") or "",
            database.schema, json.dumps(database.meanings, indent=2),
            knowledge_text(database, [entry.get("id") for entry in database.kb]),
            CURATOR_PROMPT.read_text(encoding="utf-8"),
        ])
        for phase in (1, 2):
            path = _bundle_path(paths, record["instance_id"], phase)
            if not path.is_file():
                continue
            checked += 1
            bundle = path.read_text(encoding="utf-8")
            if bundle != expected_bundle(paths, record, database, phase):
                print(f"MISMATCH {paths.rel(path)}: not the rebuilt input")
                problems += 1
            for label, text in forbidden_strings(record):
                if text not in bundle:
                    continue
                if text in public:
                    print(f"public   {paths.rel(path)}: {label} text also occurs in the questions "
                          f"or the public schema/meanings/knowledge/prompt ({len(text)} chars)")
                    continue
                print(f"LEAK     {paths.rel(path)}: {label}")
                problems += 1
            selection = (_read_selection(paths, record["instance_id"]) or {}).get(f"phase{phase}")
            if selection and selection["input_sha256"] != hashlib.sha256(
                bundle.encode("utf-8")
            ).hexdigest():
                print(f"HASH     {paths.rel(path)}: differs from the recorded input_sha256")
                problems += 1
    print(f"checked {checked} curation inputs; problems: {problems}")
    return 1 if problems or not checked else 0


def _generated_at(args) -> str:
    return args.generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def command_build(args) -> int:
    paths = _paths(args)
    databases = Databases(paths.db_dir)
    records = load_records(paths)
    stamp = _generated_at(args)
    for group in args.groups:
        directory = paths.out / group
        shared_inputs = {
            paths.rel(paths.data): sha256_file(paths.data),
            paths.rel(paths.manifest): sha256_file(paths.manifest),
            paths.rel(Path(__file__)): sha256_file(Path(__file__)),
        }
        for record in records:
            database = databases[record["selected_database"]]
            selection = _read_selection(paths, record["instance_id"]) if group == "B" else None
            data = build_task(group, record, database, selection)
            inputs = dict(shared_inputs)
            inputs.update({paths.rel(path): sha256_file(path) for path in database.files.values()})
            if group == "B":
                selection_path = _selection_path(paths, record["instance_id"])
                inputs[paths.rel(selection_path)] = sha256_file(selection_path)
            data["generation"] = {
                "experiment": group, "inputs": inputs, "generated_at": stamp,
                "serialization": SERIALIZATION,
            }
            write_json(directory / f"{record['instance_id']}.json", data)
        write_json(directory / MANIFEST_FILE, {
            "experiment": group,
            "diagnostic": DIAGNOSTIC_NOTE,
            "tasks": [record["instance_id"] for record in records],
            "generation": {"inputs": shared_inputs, "generated_at": stamp},
        })
        print(f"{paths.rel(directory)}: {len(records)} task files")
    return 0


def command_summary(args) -> int:
    """Per-task table, knowledge and length counts plus the AC-2 checks."""
    paths = _paths(args)
    databases = Databases(paths.db_dir)
    contexts = {
        group: {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((paths.out / group).glob("*.json")) if path.name != MANIFEST_FILE
        }
        for group in GROUPS
    }
    print("| task | db | A p1/p2 | B p1/p2 | C p1 | knowledge p1/p2 | chars A | chars B | chars C |")
    print("|---|---|---|---|---|---|---|---|---|")
    suspicious = []
    same_knowledge = True
    for record in load_records(paths):
        task = record["instance_id"]
        database = databases[record["selected_database"]]
        row = {group: contexts[group].get(task) for group in GROUPS}
        if any(item is None for item in row.values()):
            print(f"| {task} | missing in {[g for g, v in row.items() if v is None]} |")
            continue

        def counts(item):
            return f"{len(item['phase1']['tables'])}/{len(item['phase2']['tables'])}"

        def chars(item):
            p1 = sum(len(item["phase1"][k]) for k in ("schema", "column_meanings", "knowledge"))
            p2 = sum(len(item["phase2"][k]) for k in ("schema", "column_meanings", "knowledge"))
            return f"{p1}+{p2}"

        knowledge = {
            group: (item["phase1"]["knowledge"], item["phase2"]["knowledge"],
                    item["phase1"]["knowledge_ids"], item["phase2"]["knowledge_ids"])
            for group, item in row.items()
        }
        if len(set(json.dumps(value) for value in knowledge.values())) != 1:
            same_knowledge = False
            print(f"<!-- knowledge differs across groups for {task} -->")
        ids = knowledge["A"]
        print(f"| {task} | {database.name} | {counts(row['A'])} | {counts(row['B'])} | "
              f"{len(row['C']['phase1']['tables'])} | {len(ids[2])}/{len(ids[3])} | "
              f"{chars(row['A'])} | {chars(row['B'])} | {chars(row['C'])} |")
        for phase, sql in (("phase1", record.get("sol_sql")),
                           ("phase2", (record.get("follow_up") or {}).get("sol_sql"))):
            found, relations = sql_tables(sql, database.tables)
            token_only = [name for name in found if name not in relations]
            if (phase == "phase1" and not found) or token_only:
                suspicious.append((task, phase, len(found), token_only))
    print()
    print(f"knowledge identical across A/B/C for every task: {same_knowledge}")
    print("A tasks with no table or with token-only matches (not after FROM/JOIN/...):")
    for task, phase, found, token_only in suspicious:
        print(f"- {task} {phase}: {found} tables; token-only: {token_only}")
    if not suspicious:
        print("- none")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--data", default=DEFAULT_DATA)
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--db-dir", default=DEFAULT_DB_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    commands = parser.add_subparsers(dest="command", required=True)

    inputs = commands.add_parser("curation-inputs", help="write the B curator input bundles")
    inputs.add_argument("--phase", type=int, choices=(1, 2), required=True)
    inputs.set_defaults(run=command_curation_inputs)

    record = commands.add_parser("record-selections", help="store parsed B curator outputs")
    record.add_argument("--phase", type=int, choices=(1, 2), required=True)
    record.add_argument("--model", default="opus")
    record.add_argument("--effort", default="high")
    record.set_defaults(run=command_record_selections)

    check = commands.add_parser("check-curation-inputs", help="AC-3 leak check of the bundles")
    check.set_defaults(run=command_check_curation_inputs)

    build = commands.add_parser("build", help="write the context files of the groups")
    build.add_argument("--groups", nargs="+", choices=GROUPS, default=list(GROUPS))
    build.add_argument("--generated-at", default="")
    build.set_defaults(run=command_build)

    summary = commands.add_parser("summary", help="print the AC-2 summary table")
    summary.set_defaults(run=command_summary)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
