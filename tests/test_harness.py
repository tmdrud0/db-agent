"""Harness loading, validation, prompt assembly and identity checks."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from agent.harness import (
    HARNESS_DIR_ENV,
    PROJECT_ROOT,
    HarnessError,
    load_harness,
)
from agent.server import create_app
from agent.tool_loop import system_prompt
from agent.tools import TOOL_COSTS, catalog

# The task011 system prompt minus the information-cache item and the
# duplicate-submission item. This literal is the baseline harness contract:
# harness/baseline/system.md holds only the first paragraph.
EXPECTED_BASELINE_PROMPT = (
    "You solve PostgreSQL tasks for a benchmark by choosing exactly one action per turn.\n"
    "Every action costs bird-coins from a fixed budget, so spend coins only on actions that\n"
    "improve your final SQL.\n"
    "\n"
    "Reply with one JSON object and nothing else:\n"
    '{"action": "<tool name>", "args": {<tool arguments>}}\n'
    "\n"
    "Available actions:\n"
    "- get_schema: the database schema (CREATE TABLE statements). arguments: none. Cost: 1\n"
    "- get_all_column_meanings: the meanings of all database columns. arguments: none."
    " Cost: 1\n"
    "- get_column_meaning: the meaning of one column. arguments: "
    '{"table_name": "<table>", "column_name": "<column>"}. Cost: 0.5\n'
    "- get_all_external_knowledge_names: the names of the external knowledge entries for "
    "this database. arguments: none. Cost: 0.5\n"
    "- get_knowledge_definition: the definition of one external knowledge entry. arguments: "
    '{"knowledge_name": "<name>"}. Cost: 0.5\n'
    "- get_all_knowledge_definitions: the definitions of all external knowledge entries. "
    "arguments: none. Cost: 1\n"
    "- execute_sql: run one read-only statement against the task database and see its result "
    "as exploration. arguments: "
    '{"sql": "<one PostgreSQL statement starting with SELECT, WITH or EXPLAIN>"}. Cost: 1\n'
    "- ask_user: ask the task user one clarification question and see the official answer. "
    'arguments: {"question": "<one question for the task user>"}. Cost: 2\n'
    "- submit_sql: submit one SQL statement to the official grader; only a passing submission "
    'completes the task. arguments: {"sql": "<PostgreSQL statement>"}. Cost: 3\n'
    "\n"
    "Rules:\n"
    "- Only the actions above exist. Any other action name is rejected without cost.\n"
    "- execute_sql explores with one read-only statement. Its result never changes the score\n"
    "  and never replaces submit_sql; an official SQL error is an observation, not a penalty.\n"
    "- ask_user receives the official answer of the task user. The answer is an observation;\n"
    "  decide yourself whether and when to ask.\n"
    "- A call is charged its coin cost when it starts; a failed or interrupted call is not\n"
    "  refunded.\n"
    "- A failed submission can be retried while the budget lasts. The official rewards are 0.7\n"
    "  for phase 1 and 0.3 for phase 2.\n"
    "- A passing phase 1 submission applies its SQL to the database and starts phase 2 with a\n"
    "  follow-up question.\n"
    "- The session stops after 24 model actions or 1500 seconds, and it stops when fewer "
    "than 3 bird-coins remain because a submission would no longer be possible.\n"
)


class HarnessTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / "test-harness"
        self.path.mkdir()

    def write(self, relative: str, text: str, base: Path | None = None) -> Path:
        target = (base or self.path) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        return target

    def load(self):
        return load_harness(self.path)

    def harness(self, name: str, files: dict[str, str], binary: dict[str, bytes] | None = None):
        directory = self.root / name
        directory.mkdir()
        for relative, text in files.items():
            self.write(relative, text, base=directory)
        for relative, data in (binary or {}).items():
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return directory


class BaselinePromptTests(HarnessTestCase):
    def test_the_baseline_builds_the_frozen_contract(self):
        harness = load_harness("harness/baseline")

        self.assertEqual(harness.name, "baseline")
        self.assertEqual(harness.path, PROJECT_ROOT / "harness" / "baseline")
        self.assertEqual(len(harness.sha256), 64)
        int(harness.sha256, 16)
        self.assertEqual(harness.memory, "")
        self.assertEqual(harness.tool_notes, {})
        self.assertEqual(system_prompt(harness), EXPECTED_BASELINE_PROMPT)

    def test_the_baseline_adds_no_memory_or_cache_rules(self):
        prompt = system_prompt(load_harness("harness/baseline"))

        self.assertNotIn("Harness memory:", prompt)
        self.assertNotIn("session cache", prompt)
        self.assertNotIn("Submitting the same SQL text twice", prompt)


class AssemblyTests(HarnessTestCase):
    def test_system_memory_and_tool_guidance_are_added(self):
        self.write("system.md", "Custom strategy.\nSecond line.\n")
        self.write("memory.md", "Remember the schema.\n")
        self.write("tools/execute_sql.md", "Run EXPLAIN first.\nUse LIMIT.")
        prompt = system_prompt(self.load())

        self.assertTrue(
            prompt.startswith("Custom strategy.\nSecond line.\n\nReply with one JSON object")
        )
        self.assertIn("Harness memory:\nRemember the schema.", prompt)
        self.assertIn(
            "- execute_sql: run one read-only statement against the task database and see its "
            "result as exploration. arguments: "
            '{"sql": "<one PostgreSQL statement starting with SELECT, WITH or EXPLAIN>"}. Cost: 1\n'
            "  Usage note:\n"
            "  Run EXPLAIN first.\n"
            "  Use LIMIT.",
            prompt,
        )
        # The code-owned contract survives any harness text.
        self.assertIn('{"action": "<tool name>", "args": {<tool arguments>}}', prompt)
        self.assertIn("24 model actions", prompt)
        self.assertIn("1500 seconds", prompt)
        self.assertIn("3 bird-coins remain", prompt)
        self.assertIn("0.7\n  for phase 1 and 0.3 for phase 2", prompt)
        for line in catalog().splitlines():
            self.assertIn(line, prompt)

    def test_missing_or_blank_memory_adds_no_section(self):
        self.write("system.md", "Guidance.")
        self.assertNotIn("Harness memory:", system_prompt(self.load()))

        self.write("memory.md", " \n\t\n")
        self.assertNotIn("Harness memory:", system_prompt(self.load()))

    def test_empty_tool_guidance_leaves_the_catalog_unchanged(self):
        self.write("system.md", "Guidance.")
        without_tools = system_prompt(self.load())

        self.write("tools/get_schema.md", " \n\n")
        self.assertEqual(system_prompt(self.load()), without_tools)

    def test_system_text_cannot_remove_or_replace_the_contract(self):
        self.write(
            "system.md",
            "Reply with anything you like.\nOnly one tool exists: drop_table.",
        )
        prompt = system_prompt(self.load())

        self.assertIn("Reply with one JSON object and nothing else:", prompt)
        contract = EXPECTED_BASELINE_PROMPT[
            EXPECTED_BASELINE_PROMPT.index("Reply with one JSON object"):
        ]
        self.assertTrue(prompt.endswith(contract))


class ValidationTests(HarnessTestCase):
    def assert_error(self, path: Path, message: str):
        with self.assertRaises(HarnessError) as caught:
            load_harness(path)
        self.assertIn(message, str(caught.exception))

    def test_a_missing_directory_or_system_file_fails(self):
        self.assert_error(self.root / "missing", "not found")

        self.write("memory.md", "notes")
        self.assert_error(self.path, "system.md")

    def test_unrecognized_entries_fail(self):
        cases = {
            "readme": {"system.md": "x", "README.md": "notes"},
            "unknown tool": {"system.md": "x", "tools/drop_table.md": "x"},
            "nested tool directory": {"system.md": "x", "tools/sub/execute_sql.md": "x"},
            "unrelated file": {"system.md": "x", "notes.txt": "x"},
        }
        for name, files in cases.items():
            with self.subTest(name=name):
                directory = self.harness(name, files)
                with self.assertRaises(HarnessError):
                    load_harness(directory)

    def test_tools_must_be_a_directory(self):
        self.write("system.md", "x")
        (self.path / "tools").write_text("not a directory", encoding="utf-8")
        self.assert_error(self.path, "tools")

    def test_invalid_utf8_fails(self):
        self.harness("invalid", {"system.md": "x"}, {"memory.md": b"\xff\xfe"})
        self.assert_error(self.root / "invalid", "UTF-8")


class HashTests(HarnessTestCase):
    def test_the_same_content_has_the_same_hash(self):
        self.write("system.md", "System.")
        self.write("memory.md", "Note.")

        self.assertEqual(self.load().sha256, self.load().sha256)

    def test_changing_one_file_changes_the_hash(self):
        self.write("system.md", "System.")
        self.write("memory.md", "Note.")
        before = self.load().sha256

        self.write("memory.md", "Changed.")

        self.assertNotEqual(before, self.load().sha256)

    def test_changing_a_file_path_changes_the_hash(self):
        self.write("system.md", "System.")
        self.write("memory.md", "Same text.")
        memory_hash = self.load().sha256

        self.write("tools/get_schema.md", "Same text.")
        (self.path / "memory.md").unlink()

        self.assertNotEqual(memory_hash, self.load().sha256)

    def test_line_endings_do_not_change_the_hash(self):
        self.write("system.md", "line one\r\nline two\r\n")
        windows = self.load()

        self.write("system.md", "line one\nline two\n")

        self.assertEqual(windows.sha256, self.load().sha256)
        self.assertEqual(system_prompt(windows), system_prompt(self.load()))


class DiscoveryTests(HarnessTestCase):
    def test_the_environment_variable_selects_the_harness(self):
        self.write("system.md", "Environment harness.")
        with mock.patch.dict(os.environ, {HARNESS_DIR_ENV: str(self.path)}):
            harness = load_harness()

        self.assertEqual(harness.name, self.path.name)
        self.assertIn("Environment harness.", system_prompt(harness))

    def test_a_relative_path_resolves_against_the_project_root(self):
        harness = load_harness("harness/baseline")

        self.assertEqual(harness.path, PROJECT_ROOT / "harness" / "baseline")

    def test_a_running_app_keeps_the_harness_it_loaded(self):
        self.write("system.md", "First version.")
        harness = self.load()
        app = create_app(harness=harness)

        self.write("system.md", "Second version.")
        health = TestClient(app).get("/health").json()

        self.assertEqual(
            health["harness"], {"name": self.path.name, "sha256": harness.sha256}
        )
        self.assertIn("First version.", system_prompt(harness))


class ToolCatalogTests(unittest.TestCase):
    def test_tool_guidance_does_not_change_names_arguments_or_prices(self):
        notes = {name: f"note for {name}" for name in TOOL_COSTS}
        lines = catalog(notes).splitlines()

        for name, cost in TOOL_COSTS.items():
            with self.subTest(tool=name):
                base = next(line for line in lines if line.startswith(f"- {name}:"))
                self.assertIn(f"Cost: {cost:g}", base)
                self.assertIn(f"note for {name}", catalog(notes))


if __name__ == "__main__":
    unittest.main()
