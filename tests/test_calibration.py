"""Calibration contract checks: config validation, patch application, service wiring."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "calibration"))
sys.path.insert(0, str(ROOT / "vendor/BIRD-Interact/BIRD-Interact-ADK"))

import calibration_config  # noqa: E402
import db_env_calibration  # noqa: E402


def config_file(payload: dict) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(payload, handle, ensure_ascii=False)
    return handle.name


MINIMAL_CONFIG = {
    "task": "TEST",
    "eval_prep": {"task_a": {"phase2": [{"field": "clean_up_sqls"}]}},
    "dataset_patches": {},
    "verification": [],
}


class ConfigValidationTests(unittest.TestCase):
    def load(self, payload: dict) -> dict:
        path = config_file(payload)
        self.addCleanup(Path(path).unlink)
        return calibration_config.load_config(path)

    def test_minimal_config_loads(self):
        config = self.load(MINIMAL_CONFIG)
        self.assertEqual(config["eval_prep"], MINIMAL_CONFIG["eval_prep"])

    def test_missing_file_is_reported(self):
        with self.assertRaises(calibration_config.CalibrationError):
            calibration_config.load_config(str(ROOT / "does-not-exist.json"))

    def test_unknown_phase_key_is_rejected(self):
        payload = {"eval_prep": {"task_a": {"phase3": [{"field": "clean_up_sqls"}]}}}
        with self.assertRaises(calibration_config.CalibrationError):
            self.load(payload)

    def test_empty_preparation_entry_is_rejected(self):
        payload = {"eval_prep": {"task_a": {"phase1": ["  "]}}}
        with self.assertRaises(calibration_config.CalibrationError):
            self.load(payload)

    def test_patch_with_two_actions_is_rejected(self):
        payload = {"dataset_patches": {"task_a": {"sol_sql": {"set": "x", "replace": []}}}}
        with self.assertRaises(calibration_config.CalibrationError):
            self.load(payload)

    def test_check_cannot_reference_an_unknown_submission(self):
        payload = {"verification": [{
            "task_id": "task_a",
            "submissions": [{"id": "phase1", "phase": 1, "sql": None, "expect_passed": True}],
            "checks": [{"id": "c1", "after": "phase9", "sql": "SELECT 1;", "expect_rows": []}],
        }]}
        with self.assertRaises(calibration_config.CalibrationError):
            self.load(payload)

    def test_expect_rows_must_be_text_cells(self):
        payload = {"verification": [{
            "task_id": "task_a",
            "submissions": [{"id": "phase1", "phase": 1, "sql": None, "expect_passed": True}],
            "checks": [{"id": "c1", "sql": "SELECT 1;", "expect_rows": [[1]]}],
        }]}
        with self.assertRaises(calibration_config.CalibrationError):
            self.load(payload)

    def test_summary_exposes_no_sql_or_expected_values(self):
        payload = dict(MINIMAL_CONFIG)
        payload["dataset_patches"] = {"task_b": {"sol_sql": {"set": "SECRET ANSWER"}}}
        payload["verification"] = [{
            "task_id": "task_b",
            "submissions": [{"id": "s1", "phase": 2, "sql": "SECRET COUNTEREXAMPLE", "expect_passed": False}],
            "checks": [{"id": "c1", "sql": "SELECT 1;", "expect_rows": [["1"]]}],
        }]
        summary = calibration_config.summarize_config(self.load(payload))
        self.assertEqual(summary["dataset_patches"], ["task_b"])
        self.assertEqual(summary["verification_tasks"], ["task_b"])
        self.assertNotIn("SECRET", json.dumps(summary))


class ResolveStatementsTests(unittest.TestCase):
    def test_field_reference_uses_the_public_preparation_sql(self):
        task_data = {"clean_up_sqls": ["DROP INDEX i;"], "preprocess_sql": ["CREATE TABLE b AS SELECT * FROM t;"]}
        entries = [{"field": "preprocess_sql"}, {"field": "clean_up_sqls"}]
        self.assertEqual(calibration_config.resolve_statements(entries, task_data),
                         ["CREATE TABLE b AS SELECT * FROM t;", "DROP INDEX i;"])

    def test_inline_sql_and_blank_entries(self):
        entries = ["ALTER TABLE t DROP CONSTRAINT c;", "   "]
        self.assertEqual(calibration_config.resolve_statements(entries, {}),
                         ["ALTER TABLE t DROP CONSTRAINT c;"])

    def test_missing_field_is_reported(self):
        with self.assertRaises(calibration_config.CalibrationError):
            calibration_config.resolve_statements([{"field": "clean_up_sqls"}], {})

    def test_non_string_field_value_is_reported(self):
        with self.assertRaises(calibration_config.CalibrationError):
            calibration_config.resolve_statements([{"field": "conditions"}], {"conditions": {"order": True}})

    def test_absent_entries_produce_no_statements(self):
        self.assertEqual(calibration_config.resolve_statements(None, {}), [])


class DatasetPatchTests(unittest.TestCase):
    def rows(self):
        return [
            {"instance_id": "task_a", "sol_sql": ["SELECT 1;"], "follow_up": {"sol_sql": "SELECT x / (t + 1, 0);"}},
            {"instance_id": "task_b", "sol_sql": ["SELECT 2;"]},
        ]

    def test_replace_changes_one_occurrence_and_leaves_the_input_untouched(self):
        rows = self.rows()
        patches = {"task_a": {"follow_up.sol_sql": {"replace": [{"old": "(t + 1, 0)", "new": "(t + 1)"}]}}}
        patched, summary = calibration_config.apply_dataset_patches(rows, patches)

        self.assertEqual(patched[0]["follow_up"]["sol_sql"], "SELECT x / (t + 1);")
        self.assertEqual(rows[0]["follow_up"]["sol_sql"], "SELECT x / (t + 1, 0);")
        self.assertIs(patched[1], rows[1])
        self.assertEqual([entry["action"] for entry in summary], ["replace"])
        self.assertEqual(summary[0]["task_id"], "task_a")
        self.assertGreater(summary[0]["before_length"], summary[0]["after_length"])

    def test_replace_requires_exactly_one_occurrence(self):
        rows = self.rows()
        for old in ["(absent)", "(t + 1, 0) OR (t + 1, 0)"]:
            with self.subTest(old=old):
                patches = {"task_a": {"follow_up.sol_sql": {"replace": [{"old": old, "new": "x"}]}}}
                with self.assertRaises(calibration_config.CalibrationError):
                    calibration_config.apply_dataset_patches(rows, patches)

    def test_replace_works_on_a_list_of_sql_strings(self):
        rows = [{"instance_id": "task_a", "sol_sql": ["SELECT bad;"]}]
        patches = {"task_a": {"sol_sql": {"replace": [{"old": "bad", "new": "good"}]}}}
        patched, summary = calibration_config.apply_dataset_patches(rows, patches)
        self.assertEqual(patched[0]["sol_sql"], ["SELECT good;"])
        self.assertEqual(summary[0]["before_length"], 11)
        self.assertEqual(summary[0]["after_length"], 12)

    def test_set_action_replaces_the_value(self):
        rows = self.rows()
        patches = {"task_a": {"follow_up.sol_sql": {"set": "SELECT 1;"}}}
        patched, summary = calibration_config.apply_dataset_patches(rows, patches)
        self.assertEqual(patched[0]["follow_up"]["sol_sql"], "SELECT 1;")
        self.assertEqual(summary[0]["action"], "set")

    def test_unknown_task_or_field_is_reported(self):
        with self.assertRaises(calibration_config.CalibrationError):
            calibration_config.apply_dataset_patches(self.rows(), {"task_z": {"sol_sql": {"set": "x"}}})
        with self.assertRaises(calibration_config.CalibrationError):
            calibration_config.apply_dataset_patches(self.rows(), {"task_a": {"missing.path": {"set": "x"}}})

    def test_no_patches_keeps_every_row_identical(self):
        rows = self.rows()
        patched, summary = calibration_config.apply_dataset_patches(rows, {})
        self.assertEqual(patched, rows)
        self.assertEqual(summary, [])


class ExecuteResultParsingTests(unittest.TestCase):
    def test_single_value_table(self):
        self.assertEqual(calibration_config.parse_execute_result("count\n-----\n194"), [["194"]])

    def test_multiple_columns(self):
        self.assertEqual(calibration_config.parse_execute_result("a | b\n--\n1 | 2\n3 | 4"), [["1", "2"], ["3", "4"]])

    def test_empty_result_is_an_empty_list(self):
        self.assertEqual(calibration_config.parse_execute_result("Query executed, empty result set."), [])
        self.assertEqual(calibration_config.parse_execute_result(""), [])


class ServiceWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_create = mock.Mock(side_effect=lambda base_db, task_id, template=None: f"{base_db}__{task_id}")
        cls.original_reset = mock.Mock()
        db_env_calibration.install({}, original_create=cls.original_create, original_reset=cls.original_reset)

    def setUp(self):
        self.config = {
            "eval_prep": {
                "task_a": {"phase1": [{"field": "preprocess_sql"}], "phase2": [{"field": "clean_up_sqls"}]},
                "task_b": {"phase2": ["ALTER TABLE t DROP CONSTRAINT IF EXISTS c;"]},
            },
            "dataset_patches": {}, "verification": [],
        }
        db_env_calibration.install(self.config)
        db_env_calibration.official._task_data.clear()
        self.addCleanup(db_env_calibration.official._task_data.clear)
        self.original_create.reset_mock()
        self.original_reset.reset_mock()
        self.statements = mock.patch.object(db_env_calibration, "run_statements")
        self.run_statements = self.statements.start()
        self.addCleanup(self.statements.stop)

    def register(self, task_id: str, **extra) -> None:
        db_env_calibration.official._task_data[task_id] = {
            "_task_db": f"db__{task_id}", "_current_phase": 1, **extra,
        }

    def test_phase1_reset_applies_phase1_preparation(self):
        self.register("task_a", preprocess_sql=["CREATE TABLE b AS SELECT * FROM t;"])
        db_env_calibration._reset_task_db("db__task_a", "db_template")

        self.original_reset.assert_called_once_with("db__task_a", "db_template")
        self.run_statements.assert_called_once_with(
            "db__task_a", ["CREATE TABLE b AS SELECT * FROM t;"], "task_a", 1)

    def test_phase2_reset_applies_phase2_preparation(self):
        self.register("task_a", _current_phase=2, clean_up_sqls=["INSERT INTO t SELECT * FROM b;"])
        db_env_calibration._reset_task_db("db__task_a", "db__task_a__p1snap")
        self.run_statements.assert_called_once_with(
            "db__task_a", ["INSERT INTO t SELECT * FROM b;"], "task_a", 2)

    def test_inline_preparation_sql_is_used(self):
        self.register("task_b", _current_phase=2)
        db_env_calibration._reset_task_db("db__task_b", "db__task_b__p1snap")
        self.run_statements.assert_called_once_with(
            "db__task_b", ["ALTER TABLE t DROP CONSTRAINT IF EXISTS c;"], "task_b", 2)

    def test_tasks_without_preparation_run_nothing(self):
        self.register("task_c")
        db_env_calibration._reset_task_db("db__task_c", "db_template")
        self.run_statements.assert_not_called()

    def test_unregistered_database_runs_nothing(self):
        db_env_calibration._reset_task_db("db__unknown", "db_template")
        self.run_statements.assert_not_called()

    def test_snapshot_creation_prepares_the_live_phase2_database(self):
        self.register("task_a", preprocess_sql=["CREATE TABLE b AS SELECT * FROM t;"],
                      clean_up_sqls=["INSERT INTO t SELECT * FROM b;"])
        created = db_env_calibration._create_task_db("db__task_a", "p1snap", template="db__task_a")

        self.assertEqual(created, "db__task_a__p1snap")
        self.run_statements.assert_called_once_with(
            "db__task_a", ["INSERT INTO t SELECT * FROM b;"], "task_a", 2)

    def test_initial_task_database_applies_phase1_preparation(self):
        self.register("task_a", preprocess_sql=["CREATE TABLE b AS SELECT * FROM t;"])
        db_env_calibration._create_task_db("db", "task_a")
        self.run_statements.assert_called_once_with(
            "db__task_a", ["CREATE TABLE b AS SELECT * FROM t;"], "task_a", 1)

    def test_preparation_failure_is_raised_not_swallowed(self):
        self.statements.stop()
        pool = mock.Mock()
        conn = pool.getconn.return_value
        with mock.patch.object(db_env_calibration, "_get_or_init_pool", return_value=pool), \
             mock.patch.object(db_env_calibration, "execute_queries", return_value=(None, "boom", False, None)):
            with self.assertRaises(calibration_config.CalibrationError):
                db_env_calibration.run_statements("db__task_a", ["SELECT 1;"], "task_a", 2)
        pool.putconn.assert_called_once_with(conn)


class DefaultStackTests(unittest.TestCase):
    def test_default_compose_keeps_the_original_service(self):
        default = (ROOT / "compose.benchmark.yml").read_text(encoding="utf-8")
        calibrated = (ROOT / "compose.benchmark.calibrated.yml").read_text(encoding="utf-8")

        self.assertNotIn("calibration", default.lower())
        self.assertIn("db_env_calibration:create_app", calibrated)
        self.assertIn("CALIBRATION_CONFIG", calibrated)


if __name__ == "__main__":
    unittest.main()
