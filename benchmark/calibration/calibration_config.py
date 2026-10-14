"""Validation and patch helpers shared by the calibration wrapper and scripts.

The calibration inputs (SQL corrections, preparation references, verification
cases) stay in `private/`; this module contains only the generic processing code
that loads and applies them.
"""

import copy
import hashlib
import json
from typing import Any

PHASE_KEYS = ("phase1", "phase2")
PATCH_ACTIONS = ("set", "replace")


class CalibrationError(Exception):
    """Raised when a calibration file is missing, malformed, or cannot be applied."""


def load_config(path: str) -> dict:
    """Read and validate a calibration config file."""
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except OSError as exc:
        raise CalibrationError(f"Cannot read calibration config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"Calibration config {path} is not valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise CalibrationError("Calibration config must be a JSON object")
    _validate_eval_prep(config.get("eval_prep", {}))
    _validate_dataset_patches(config.get("dataset_patches", {}))
    _validate_verification(config.get("verification", []))
    return config


def summarize_config(config: dict) -> dict:
    """Describe the active calibration without exposing any SQL or expected values."""
    eval_prep = config.get("eval_prep", {})
    return {
        "eval_prep": {task_id: sorted(phases) for task_id, phases in eval_prep.items()},
        "dataset_patches": sorted(config.get("dataset_patches", {})),
        "verification_tasks": sorted(
            scenario["task_id"] for scenario in config.get("verification", [])
        ),
    }


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows_canonical_sha256(rows: list[dict]) -> str:
    """Hash parsed rows regardless of file encoding or line endings."""
    content = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def resolve_statements(entries: list, task_data: dict) -> list[str]:
    """Turn preparation entries into SQL statements.

    An entry is either a SQL string or ``{"field": "<task data field>"}``, which
    resolves to the public preparation statements of that task.
    """
    statements: list[str] = []
    for entry in entries or []:
        if isinstance(entry, str):
            if entry.strip():
                statements.append(entry)
            continue
        field = entry["field"]
        if field not in task_data:
            raise CalibrationError(f"Preparation field {field!r} is missing from the task data")
        value = task_data[field]
        if isinstance(value, str):
            if value.strip():
                statements.append(value)
        elif isinstance(value, list):
            statements.extend(item for item in value if isinstance(item, str) and item.strip())
        elif value is not None:
            raise CalibrationError(
                f"Preparation field {field!r} must be a SQL string or a list of SQL strings"
            )
    return statements


def apply_dataset_patches(rows: list[dict], patches: dict) -> tuple[list[dict], list[dict]]:
    """Return patched dataset rows and a summary that contains no answer text."""
    known = {row["instance_id"] for row in rows}
    for task_id in patches:
        if task_id not in known:
            raise CalibrationError(f"dataset_patches target {task_id!r} is not in the dataset")
    patched: list[dict] = []
    summary: list[dict] = []
    for row in rows:
        fields = patches.get(row["instance_id"])
        if not fields:
            patched.append(row)
            continue
        updated = copy.deepcopy(row)
        for path, patch in fields.items():
            before, after = _apply_patch(updated, path, patch)
            action = next(name for name in PATCH_ACTIONS if name in patch)
            summary.append({
                "task_id": row["instance_id"], "field": path, "action": action,
                "before_length": _text_size(before), "after_length": _text_size(after),
            })
        patched.append(updated)
    return patched, summary


def parse_execute_result(text: str) -> list[list[str]]:
    """Parse the text table returned by the official `/execute` endpoint."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or lines[0].startswith("Query executed"):
        return []
    return [line.split(" | ") for line in lines[2:]]


def _text_size(value: Any) -> int | None:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(len(item) for item in value if isinstance(item, str))
    return None


def _apply_patch(row: dict, path: str, patch: dict) -> tuple[Any, Any]:
    parts = path.split(".")
    target: Any = row
    for part in parts[:-1]:
        target = _child(target, part, row["instance_id"], path)
    leaf = parts[-1]
    before = _child(target, leaf, row["instance_id"], path)
    after = _apply_action(before, patch, row["instance_id"], path)
    target[leaf] = after
    return before, after


def _child(target: Any, key: str, task_id: str, path: str) -> Any:
    if not isinstance(target, dict) or key not in target:
        raise CalibrationError(f"Patch path {path!r} does not exist for {task_id}")
    return target[key]


def _apply_action(value: Any, patch: dict, task_id: str, path: str) -> Any:
    if "set" in patch:
        return copy.deepcopy(patch["set"])
    for step in patch["replace"]:
        value = _replace_once(value, step["old"], step["new"], task_id, path)
    return value


def _replace_once(value: Any, old: str, new: str, task_id: str, path: str) -> Any:
    if isinstance(value, str):
        if value.count(old) != 1:
            raise CalibrationError(
                f"Replacement text occurs {value.count(old)} times in {path!r} for {task_id}"
            )
        return value.replace(old, new)
    if isinstance(value, list):
        occurrences = sum(item.count(old) for item in value if isinstance(item, str))
        if occurrences != 1:
            raise CalibrationError(
                f"Replacement text occurs {occurrences} times in {path!r} for {task_id}"
            )
        return [item.replace(old, new) if isinstance(item, str) else item for item in value]
    raise CalibrationError(f"Cannot replace text in non-string field {path!r} for {task_id}")


def _validate_eval_prep(eval_prep: Any) -> None:
    if not isinstance(eval_prep, dict):
        raise CalibrationError("eval_prep must map task IDs to phase entries")
    for task_id, phases in eval_prep.items():
        if not isinstance(phases, dict) or not phases:
            raise CalibrationError(f"eval_prep[{task_id}] must map phase keys to entries")
        for key, entries in phases.items():
            if key not in PHASE_KEYS:
                raise CalibrationError(f"eval_prep[{task_id}] has unknown phase key {key!r}")
            if not isinstance(entries, list) or not entries:
                raise CalibrationError(f"eval_prep[{task_id}][{key}] must be a non-empty list")
            for entry in entries:
                if isinstance(entry, str) and entry.strip():
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("field"), str) and entry["field"]:
                    continue
                raise CalibrationError(
                    f"eval_prep[{task_id}][{key}] entries must be SQL strings or field references"
                )


def _validate_dataset_patches(patches: Any) -> None:
    if not isinstance(patches, dict):
        raise CalibrationError("dataset_patches must map task IDs to field patches")
    for task_id, fields in patches.items():
        if not isinstance(fields, dict) or not fields:
            raise CalibrationError(f"dataset_patches[{task_id}] must map field paths to patches")
        for path, patch in fields.items():
            if not isinstance(path, str) or not path:
                raise CalibrationError(f"dataset_patches[{task_id}] has an empty field path")
            _validate_patch_action(task_id, path, patch)


def _validate_patch_action(task_id: str, path: str, patch: Any) -> None:
    if not isinstance(patch, dict) or len(patch) != 1:
        raise CalibrationError(f"dataset_patches[{task_id}][{path}] must have exactly one action")
    action = next(iter(patch))
    if action not in PATCH_ACTIONS:
        raise CalibrationError(f"dataset_patches[{task_id}][{path}] has unknown action {action!r}")
    if action == "set":
        return
    steps = patch["replace"]
    if not isinstance(steps, list) or not steps:
        raise CalibrationError(f"dataset_patches[{task_id}][{path}] needs a non-empty replace list")
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get("old"), str)
                or not isinstance(step.get("new"), str)):
            raise CalibrationError(
                f"dataset_patches[{task_id}][{path}] replace steps need string 'old' and 'new'"
            )


def _validate_verification(scenarios: Any) -> None:
    if not isinstance(scenarios, list):
        raise CalibrationError("verification must be a list of scenarios")
    for scenario in scenarios:
        task_id = scenario.get("task_id") if isinstance(scenario, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise CalibrationError("Each verification scenario needs a task_id")
        submissions = scenario.get("submissions", [])
        if not isinstance(submissions, list) or not submissions:
            raise CalibrationError(f"verification[{task_id}] needs at least one submission")
        for submission in submissions:
            _validate_submission(task_id, submission)
        checks = scenario.get("checks", [])
        if not isinstance(checks, list):
            raise CalibrationError(f"verification[{task_id}] checks must be a list")
        submission_ids = {submission["id"] for submission in submissions}
        for check in checks:
            _validate_check(task_id, check, submission_ids)


def _validate_submission(task_id: str, submission: Any) -> None:
    if not isinstance(submission, dict):
        raise CalibrationError(f"verification[{task_id}] submissions must be objects")
    if not isinstance(submission.get("id"), str) or not submission["id"]:
        raise CalibrationError(f"verification[{task_id}] submissions need an id")
    if submission.get("phase") not in (1, 2):
        raise CalibrationError(f"verification[{task_id}][{submission.get('id')}] needs phase 1 or 2")
    if not isinstance(submission.get("expect_passed"), bool):
        raise CalibrationError(
            f"verification[{task_id}][{submission['id']}] needs an expect_passed flag"
        )
    sql = submission.get("sql")
    if sql is not None and not isinstance(sql, str):
        raise CalibrationError(f"verification[{task_id}][{submission['id']}] sql must be text")


def _validate_check(task_id: str, check: Any, submission_ids: set) -> None:
    if not isinstance(check, dict):
        raise CalibrationError(f"verification[{task_id}] checks must be objects")
    if not isinstance(check.get("id"), str) or not check["id"]:
        raise CalibrationError(f"verification[{task_id}] checks need an id")
    if not isinstance(check.get("sql"), str) or not check["sql"].strip():
        raise CalibrationError(f"verification[{task_id}][{check['id']}] needs a read-only SQL text")
    after = check.get("after")
    if after is not None and after not in submission_ids:
        raise CalibrationError(
            f"verification[{task_id}][{check['id']}] references unknown submission {after!r}"
        )
    rows = check.get("expect_rows")
    if (not isinstance(rows, list)
            or not all(isinstance(row, list) and all(isinstance(cell, str) for cell in row)
                       for row in rows)):
        raise CalibrationError(
            f"verification[{task_id}][{check['id']}] expect_rows must be lists of strings"
        )
