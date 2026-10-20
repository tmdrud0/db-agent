"""The connection check must not claim a commit it did not run."""

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "verify_tool_connection.py"


def load_script():
    spec = importlib.util.spec_from_file_location("verify_tool_connection", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorktreeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script()

    def test_clean_tracked_worktree_has_no_changes(self):
        with mock.patch.object(self.module.subprocess, "check_output", return_value=""):
            self.assertEqual(self.module.tracked_changes(ROOT), [])
            self.assertEqual(self.module.revision(ROOT), "unknown")

    def test_modified_staged_renamed_and_quoted_paths_are_reported(self):
        porcelain = (
            " M agent/tools.py\n"
            "M  tests/test_tool_loop.py\n"
            "R  scripts/old.py -> scripts/new.py\n"
            ' M "docs/a b.md"\n'
        )
        with mock.patch.object(self.module.subprocess, "check_output", return_value=porcelain):
            self.assertEqual(
                self.module.tracked_changes(ROOT),
                ["agent/tools.py", "tests/test_tool_loop.py", "scripts/new.py", "docs/a b.md"],
            )

    def test_revision_and_dirty_state_come_from_the_same_worktree(self):
        outputs = ["deadbeef\n", " M scripts/verify_tool_connection.py\n"]
        with mock.patch.object(self.module.subprocess, "check_output", side_effect=outputs):
            self.assertEqual(self.module.revision(ROOT), "deadbeef")
            changes = self.module.tracked_changes(ROOT)
        self.assertEqual(changes, ["scripts/verify_tool_connection.py"])
        self.assertTrue(changes)

    def test_health_counter_deltas_are_recorded_or_left_unknown(self):
        before = {"model_calls": 3, "prompt_tokens": 100, "completion_tokens": 20}
        after = {"model_calls": 5, "prompt_tokens": 180, "completion_tokens": 45}
        self.assertEqual(self.module.counter_delta(after, before, "model_calls"), 2)
        self.assertEqual(self.module.counter_delta(after, before, "prompt_tokens"), 80)
        self.assertIsNone(self.module.counter_delta(after, {}, "model_calls"))
        self.assertIsNone(self.module.counter_delta({"model_calls": "5"}, before, "model_calls"))

    def test_missing_git_is_unknown_and_not_reported_as_dirty(self):
        with mock.patch.object(
            self.module.subprocess, "check_output", side_effect=FileNotFoundError
        ):
            self.assertEqual(self.module.revision(ROOT), "unknown")
            self.assertEqual(self.module.tracked_changes(ROOT), [])


if __name__ == "__main__":
    unittest.main()
