"""Calibrated wrapper around the official BIRD-Interact DB environment service.

The official service never applies the dataset's public preparation fields, so
follow-up premises that depend on them ("I have dropped this constraint",
"I have recovered this table") are absent when Phase 2 is graded. This wrapper
leaves the vendor code untouched and adds the configured preparation statements
to a per-task database whenever the official service creates or resets it.

The original service stays the default: this module only runs when the
calibrated compose override selects it.
"""

import logging
import os
from typing import Any

import calibration_config
from calibration_config import CalibrationError
from db_environment import server as official
from shared.db_utils import _get_or_init_pool, execute_queries

logger = logging.getLogger(__name__)

CONFIG_ENV = "CALIBRATION_CONFIG"
SNAPSHOT_TASK_ID = "p1snap"

_state: dict[str, Any] = {
    "config": {}, "original_create": None, "original_reset": None,
    "patched": False, "summary": {},
}


def install(config: dict, original_create=None, original_reset=None) -> dict:
    """Patch the official service once, keeping the originals for delegation."""
    if _state["original_create"] is None:
        _state["original_create"] = original_create or official.create_task_db
    if _state["original_reset"] is None:
        _state["original_reset"] = original_reset or official.reset_task_db
    _state["config"] = config or {}
    _state["summary"] = calibration_config.summarize_config(_state["config"])
    if not _state["patched"]:
        official.create_task_db = _create_task_db
        official.reset_task_db = _reset_task_db
        _state["patched"] = True
    return _state


def run_statements(db_name: str, statements: list[str], task_id: str, phase: int) -> None:
    """Execute preparation statements on a task database."""
    pool = _get_or_init_pool(db_name)
    conn = pool.getconn()
    try:
        _, error, timeout, _ = execute_queries(statements, db_name, conn)
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass
    if error:
        raise CalibrationError(
            f"Calibration preparation failed for {task_id} phase {phase}: {error}"
        )
    if timeout:
        raise CalibrationError(f"Calibration preparation timed out for {task_id} phase {phase}")
    logger.warning(
        "Calibration: applied %d statement(s) to %s for %s phase %d",
        len(statements), db_name, task_id, phase,
    )


def _create_task_db(base_db: str, task_id: str, template: str = None) -> str:
    task_db = _state["original_create"](base_db, task_id, template=template)
    if task_id == SNAPSHOT_TASK_ID:
        # Phase 1 just ended; make the live database match the Phase 2 premise
        # as soon as the follow-up question is delivered.
        _apply_prep(base_db, 2)
    else:
        _apply_prep(task_db, 1)
    return task_db


def _reset_task_db(task_db: str, template_source: str) -> None:
    _state["original_reset"](task_db, template_source)
    _apply_prep(task_db, _current_phase(task_db))


def _apply_prep(db_name: str, phase: int) -> None:
    task_id, task_data = _task_for_db(db_name)
    if task_id is None:
        logger.warning("Calibration: no task registered for %s, skipping preparation", db_name)
        return
    entries = _state["config"].get("eval_prep", {}).get(task_id, {}).get(f"phase{phase}", [])
    statements = calibration_config.resolve_statements(entries, task_data)
    if statements:
        run_statements(db_name, statements, task_id, phase)


def _task_for_db(db_name: str) -> tuple[str | None, dict]:
    for task_id, task_data in official._task_data.items():
        if task_data.get("_task_db") == db_name:
            return task_id, task_data
    return None, {}


def _current_phase(db_name: str) -> int:
    _, task_data = _task_for_db(db_name)
    return task_data.get("_current_phase", 1)


def create_app():
    """Build the calibrated app. Used as a Uvicorn factory."""
    config_path = os.environ.get(CONFIG_ENV)
    if not config_path:
        raise RuntimeError(f"{CONFIG_ENV} must point at a calibration config file")
    config = calibration_config.load_config(config_path)
    install(config)
    logger.warning(
        "DB environment calibration active (%s): %s", os.path.basename(config_path), _state["summary"]
    )
    if not _state.get("route_added"):
        @official.app.get("/calibration")
        async def calibration_info():
            return {
                "service": "db_environment_calibration",
                "config": os.path.basename(config_path),
                **_state["summary"],
            }

        _state["route_added"] = True
    return official.app
