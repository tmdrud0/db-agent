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


def sub_agent_text(
    description: str = "Reviews SQL.",
    context: str | None = None,
    body: str = "Review the SQL.",
    fetch: str | None = None,
) -> str:
    lines = ["---", f"description: {description}"]
    if context is not None:
        lines.append(f"context: {context}")
    if fetch is not None:
        lines.append(f"fetch: {fetch}")
    lines.extend(["---", body, ""])
    return "\n".join(lines)


# The prompt harness/sql_review produced at the TASK-013 code SHA. The fetch
# work must not change a harness without fetch definitions.
EXPECTED_SQL_REVIEW_PROMPT = """You solve PostgreSQL tasks for a benchmark by choosing exactly one action per turn.
Every action costs bird-coins from a fixed budget, so spend coins only on actions that
improve your final SQL.

Before you submit, consult the sql_reviewer sub-agent with the candidate SQL and the
conditions you believe the question requires. It compares the SQL with the question, the
user's answers and what you have observed, and its review costs no bird-coins.

Reply with one JSON object and nothing else:
{"action": "<tool name>", "args": {<tool arguments>}}

Available actions:
- get_schema: the database schema (CREATE TABLE statements). arguments: none. Cost: 1
- get_all_column_meanings: the meanings of all database columns. arguments: none. Cost: 1
- get_column_meaning: the meaning of one column. arguments: {"table_name": "<table>", "column_name": "<column>"}. Cost: 0.5
- get_all_external_knowledge_names: the names of the external knowledge entries for this database. arguments: none. Cost: 0.5
- get_knowledge_definition: the definition of one external knowledge entry. arguments: {"knowledge_name": "<name>"}. Cost: 0.5
- get_all_knowledge_definitions: the definitions of all external knowledge entries. arguments: none. Cost: 1
- execute_sql: run one read-only statement against the task database and see its result as exploration. arguments: {"sql": "<one PostgreSQL statement starting with SELECT, WITH or EXPLAIN>"}. Cost: 1
- ask_user: ask the task user one clarification question and see the official answer. arguments: {"question": "<one question for the task user>"}. Cost: 2
- submit_sql: submit one SQL statement to the official grader; only a passing submission completes the task. arguments: {"sql": "<PostgreSQL statement>"}. Cost: 3
- consult: ask one harness sub-agent for an answer; the answer is an observation and never changes the score. arguments: {"agent": "<sub-agent name>", "request": "<what the sub-agent should do>"}. Cost: 0
  Available sub-agents:
  - sql_reviewer: Checks a candidate PostgreSQL query against the question, the user answers and the agent's observations.

Rules:
- Only the actions above exist. Any other action name is rejected without cost.
- execute_sql explores with one read-only statement. Its result never changes the score
  and never replaces submit_sql; an official SQL error is an observation, not a penalty.
- ask_user receives the official answer of the task user. The answer is an observation;
  decide yourself whether and when to ask.
- A call is charged its coin cost when it starts; a failed or interrupted call is not
  refunded.
- A failed submission can be retried while the budget lasts. The official rewards are 0.7
  for phase 1 and 0.3 for phase 2.
- A passing phase 1 submission applies its SQL to the database and starts phase 2 with a
  follow-up question.
- The session stops after 24 model actions or 1500 seconds, and it stops when fewer than 3 bird-coins remain because a submission would no longer be possible.
- consult calls one sub-agent defined by the harness. It is not an official tool
  and costs no bird-coins, but the response that chooses it still counts as one model
  action.
- A sub-agent cannot use the database, the user simulator or the grader. It sees only
  the context listed with its name and the request, and its answer is an observation
  that never changes the score.
"""


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

    def test_sub_agents_add_the_consult_line_rules_and_agent_list(self):
        self.write("system.md", "Guidance.")
        self.write(
            "sub_agents/reviewer.md",
            sub_agent_text(description="Reviews candidate SQL.", context="task, dialogue"),
        )
        prompt = system_prompt(self.load())

        self.assertIn(
            "- consult: ask one harness sub-agent for an answer; the answer is an observation "
            "and never changes the score. arguments: "
            '{"agent": "<sub-agent name>", "request": "<what the sub-agent should do>"}. Cost: 0',
            prompt,
        )
        self.assertIn(
            "  Available sub-agents:\n  - reviewer: Reviews candidate SQL.", prompt
        )
        self.assertIn("- consult calls one sub-agent defined by the harness.", prompt)
        self.assertIn("It is not an official tool", prompt)
        self.assertIn("costs no bird-coins", prompt)
        self.assertIn("counts as one model\n  action", prompt)
        self.assertIn("cannot use the database, the user simulator or the grader", prompt)
        self.assertIn("never changes the score", prompt)
        # The official catalog lines stay exactly as they are.
        for line in catalog().splitlines():
            self.assertIn(line, prompt)

    def test_a_harness_without_sub_agents_has_no_consult_text(self):
        self.write("system.md", "Guidance.")
        prompt = system_prompt(self.load())

        self.assertNotIn("consult", prompt)
        self.assertNotIn("sub-agent", prompt)

    def test_a_fetch_sub_agent_shows_its_lookup_and_the_fetch_contract(self):
        self.write("system.md", "Guidance.")
        self.write(
            "sub_agents/knowledge_finder.md",
            sub_agent_text(fetch="get_all_knowledge_definitions"),
        )
        prompt = system_prompt(self.load())

        self.assertIn(
            "  - knowledge_finder: Reviews SQL. Fetches get_all_knowledge_definitions (cost 1).",
            prompt,
        )
        self.assertIn(
            '- A sub-agent listed with "Fetches <tool> (cost <n>)" receives that official\n'
            "  lookup when it is consulted. The lookup runs once, is charged its official coin\n"
            "  cost, and its raw output is shown only to the sub-agent; the main agent receives\n"
            "  only the sub-agent's answer.",
            prompt,
        )
        for line in catalog().splitlines():
            self.assertIn(line, prompt)

    def test_sub_agents_without_fetch_keep_the_task_013_prompt(self):
        self.write("system.md", "Guidance.")
        self.write("sub_agents/reviewer.md", sub_agent_text())
        prompt = system_prompt(self.load())

        self.assertNotIn("Fetches", prompt)
        self.assertNotIn("receives that official", prompt)
        self.assertIn("  - reviewer: Reviews SQL.", prompt)


class SubAgentTests(HarnessTestCase):
    def test_a_definition_loads_with_its_description_context_and_body(self):
        self.write("system.md", "Guidance.")
        self.write("sub_agents/reviewer.md", sub_agent_text(context="task, observations"))
        harness = self.load()

        self.assertEqual(list(harness.sub_agents), ["reviewer"])
        sub = harness.sub_agents["reviewer"]
        self.assertEqual(sub.name, "reviewer")
        self.assertEqual(sub.description, "Reviews SQL.")
        self.assertEqual(sub.context, ("task", "observations"))
        self.assertEqual(sub.system, "Review the SQL.")

    def test_the_default_context_is_task_and_dialogue(self):
        self.write("system.md", "Guidance.")
        self.write("sub_agents/reviewer.md", sub_agent_text())

        self.assertEqual(
            self.load().sub_agents["reviewer"].context, ("task", "dialogue")
        )
        self.assertIsNone(self.load().sub_agents["reviewer"].fetch)

    def test_the_four_argument_free_lookups_load_as_fetch(self):
        self.write("system.md", "Guidance.")
        for tool in (
            "get_schema",
            "get_all_column_meanings",
            "get_all_external_knowledge_names",
            "get_all_knowledge_definitions",
        ):
            with self.subTest(tool=tool):
                self.write("sub_agents/finder.md", sub_agent_text(fetch=tool))
                self.assertEqual(self.load().sub_agents["finder"].fetch, tool)

    def test_invalid_fetch_values_fail(self):
        cases = {
            "empty": sub_agent_text(fetch=""),
            "multiple tools": sub_agent_text(fetch="get_schema, get_all_column_meanings"),
            "argument lookup": sub_agent_text(fetch="get_column_meaning"),
            "single knowledge lookup": sub_agent_text(fetch="get_knowledge_definition"),
            "execute_sql": sub_agent_text(fetch="execute_sql"),
            "ask_user": sub_agent_text(fetch="ask_user"),
            "submit_sql": sub_agent_text(fetch="submit_sql"),
            "unknown tool": sub_agent_text(fetch="get_everything"),
            "duplicate key": (
                "---\ndescription: Reviews SQL.\n"
                "fetch: get_schema\nfetch: get_all_knowledge_definitions\n"
                "---\nReview the SQL.\n"
            ),
        }
        for index, (case, text) in enumerate(cases.items()):
            with self.subTest(case=case):
                directory = self.harness(f"invalid-fetch-{index}", {
                    "system.md": "x", "sub_agents/reviewer.md": text,
                })
                with self.assertRaises(HarnessError):
                    load_harness(directory)

    def test_context_items_keep_their_declared_order_and_drop_duplicates(self):
        self.write("system.md", "Guidance.")
        self.write(
            "sub_agents/reviewer.md",
            sub_agent_text(context="observations, task, observations"),
        )

        self.assertEqual(
            self.load().sub_agents["reviewer"].context, ("observations", "task")
        )

    def test_names_that_match_the_rule_load(self):
        self.write("system.md", "Guidance.")
        for name in ("a", "reviewer2", "a" * 40):
            self.write(f"sub_agents/{name}.md", sub_agent_text())

        self.assertEqual(list(self.load().sub_agents), ["a", "a" * 40, "reviewer2"])

    def test_names_that_violate_the_rule_fail(self):
        for index, name in enumerate(
            ("Reviewer", "_reviewer", "review-er", "1reviewer", "a" * 41)
        ):
            with self.subTest(name=name):
                directory = self.harness(f"invalid-name-{index}", {
                    "system.md": "x", f"sub_agents/{name}.md": sub_agent_text(),
                })
                with self.assertRaises(HarnessError) as caught:
                    load_harness(directory)
                self.assertIn("name rule", str(caught.exception))

    def test_invalid_definitions_fail(self):
        cases = {
            "missing header": "Review the SQL.\n",
            "unclosed header": "---\ndescription: Reviews SQL.\nReview the SQL.\n",
            "missing description": "---\ncontext: task\n---\nReview.\n",
            "empty description": "---\ndescription:   \n---\nReview.\n",
            "missing body": "---\ndescription: Reviews SQL.\n---\n\n",
            "unknown header key": (
                "---\ndescription: Reviews SQL.\nmodel: fast\n---\nReview.\n"
            ),
            "unsupported context item": (
                "---\ndescription: Reviews SQL.\ncontext: task, files\n---\nReview.\n"
            ),
            "empty context item": (
                "---\ndescription: Reviews SQL.\ncontext: task,\n---\nReview.\n"
            ),
            "repeated header key": (
                "---\ndescription: A\ndescription: B\n---\nReview.\n"
            ),
            "malformed header line": "---\ndescription Reviews SQL.\n---\nReview.\n",
        }
        for index, (name, text) in enumerate(cases.items()):
            with self.subTest(case=name):
                directory = self.harness(f"invalid-{index}", {
                    "system.md": "x", "sub_agents/reviewer.md": text,
                })
                with self.assertRaises(HarnessError):
                    load_harness(directory)

    def test_unrecognized_sub_agent_entries_fail(self):
        cases = {
            "nested directory": {
                "system.md": "x", "sub_agents/nested/reviewer.md": sub_agent_text(),
            },
            "not markdown": {"system.md": "x", "sub_agents/notes.txt": "x"},
        }
        for index, (name, files) in enumerate(cases.items()):
            with self.subTest(case=name):
                directory = self.harness(f"unrecognized-sub-agent-{index}", files)
                with self.assertRaises(HarnessError):
                    load_harness(directory)

        self.write("system.md", "x")
        (self.path / "sub_agents").write_text("not a directory", encoding="utf-8")
        with self.assertRaises(HarnessError) as caught:
            self.load()
        self.assertIn("sub_agents", str(caught.exception))


class ExampleHarnessTests(unittest.TestCase):
    def test_the_sql_review_prompt_is_the_task_013_text(self):
        self.assertEqual(
            system_prompt(load_harness("harness/sql_review")), EXPECTED_SQL_REVIEW_PROMPT
        )

    def test_the_sql_review_example_loads_and_reaches_the_prompt(self):
        harness = load_harness("harness/sql_review")
        self.assertEqual(harness.name, "sql_review")
        self.assertEqual(list(harness.sub_agents), ["sql_reviewer"])
        sub = harness.sub_agents["sql_reviewer"]
        self.assertEqual(sub.context, ("task", "dialogue", "observations"))
        self.assertIsNone(sub.fetch)
        self.assertTrue(sub.description)
        prompt = system_prompt(harness)
        self.assertIn("- consult:", prompt)
        self.assertIn("  - sql_reviewer:", prompt)
        self.assertIn(sub.description, prompt)
        self.assertIn("Before you submit, consult the sql_reviewer sub-agent", prompt)
        self.assertNotIn("Fetches", prompt)

    def test_the_context_finder_example_loads_with_its_three_fetches(self):
        harness = load_harness("harness/context_finder")
        self.assertEqual(harness.name, "context_finder")
        self.assertEqual(
            {name: sub.fetch for name, sub in harness.sub_agents.items()},
            {
                "column_finder": "get_all_column_meanings",
                "knowledge_finder": "get_all_knowledge_definitions",
                "schema_finder": "get_schema",
            },
        )
        prompt = system_prompt(harness)
        for name, sub in harness.sub_agents.items():
            self.assertEqual(sub.context, ("task", "dialogue"))
            self.assertIn(
                f"  - {name}: {sub.description} "
                f"Fetches {sub.fetch} (cost {TOOL_COSTS[sub.fetch]:g}).",
                prompt,
            )
        self.assertIn("ask a\nfinder sub-agent", prompt)
        self.assertIn("- consult:", prompt)
        self.assertNotIn("sql_reviewer", prompt)

    def test_all_three_harnesses_expose_only_the_official_tools(self):
        for directory in ("harness/baseline", "harness/sql_review", "harness/context_finder"):
            with self.subTest(directory=directory):
                client = TestClient(create_app(harness=load_harness(directory)))
                health = client.get("/health").json()
                self.assertEqual(
                    [tool["name"] for tool in health["tools"]], list(TOOL_COSTS)
                )
                self.assertEqual(
                    health["tools"],
                    [{"name": name, "cost": cost} for name, cost in TOOL_COSTS.items()],
                )

    def test_health_reports_sub_agents_without_changing_the_tool_set(self):
        client = TestClient(create_app(harness=load_harness("harness/sql_review")))
        health = client.get("/health").json()
        self.assertEqual([tool["name"] for tool in health["tools"]], list(TOOL_COSTS))
        self.assertEqual(health["harness"]["sub_agents"], ["sql_reviewer"])

        baseline = TestClient(create_app(harness=load_harness("harness/baseline")))
        self.assertEqual(baseline.get("/health").json()["harness"]["sub_agents"], [])

        finder = TestClient(create_app(harness=load_harness("harness/context_finder")))
        self.assertEqual(
            finder.get("/health").json()["harness"]["sub_agents"],
            ["column_finder", "knowledge_finder", "schema_finder"],
        )


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


def _directory_link(link: Path, target: Path) -> None:
    """Create a directory link without extra privileges where the OS allows it."""
    if os.name == "nt":
        import _winapi

        # A junction needs no administrator rights or developer mode on Windows.
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


class LinkTests(HarnessTestCase):
    def setUp(self):
        super().setUp()
        self.write("system.md", "role")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.write("ask_user.md", "OUTSIDE-CONTENT", base=self.outside)
        self.write("secret.md", "OUTSIDE-CONTENT", base=self.outside)

    def assert_link_rejected(self, relative: str):
        with self.assertRaises(HarnessError) as caught:
            self.load()
        self.assertIn(relative, str(caught.exception))
        self.assertIn("link", str(caught.exception))

    def file_link(self, relative: str):
        link = self.path / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(self.outside / "secret.md", link)
        except OSError as exc:
            self.skipTest(f"file symbolic links are not available here: {exc}")

    def test_a_tools_directory_link_is_rejected(self):
        _directory_link(self.path / "tools", self.outside)
        self.assert_link_rejected("tools")

    def test_a_link_inside_tools_is_rejected(self):
        (self.path / "tools").mkdir()
        _directory_link(self.path / "tools" / "execute_sql.md", self.outside)
        self.assert_link_rejected("tools/execute_sql.md")

    def test_a_sub_agents_directory_link_is_rejected(self):
        _directory_link(self.path / "sub_agents", self.outside)
        self.assert_link_rejected("sub_agents")

    def test_a_link_inside_sub_agents_is_rejected(self):
        (self.path / "sub_agents").mkdir()
        _directory_link(self.path / "sub_agents" / "reviewer.md", self.outside)
        self.assert_link_rejected("sub_agents/reviewer.md")

    def test_file_links_are_rejected(self):
        for relative in ("tools/ask_user.md", "memory.md"):
            with self.subTest(relative=relative):
                self.file_link(relative)
                self.assert_link_rejected(relative)
                (self.path / relative).unlink()

    def test_a_linked_system_file_is_rejected(self):
        (self.path / "system.md").unlink()
        self.file_link("system.md")
        self.assert_link_rejected("system.md")

    def test_a_file_reported_as_a_link_is_rejected_without_link_privileges(self):
        # Creating file symlinks needs extra rights on Windows, so the file-link
        # branch is also checked by reporting one regular file as a link.
        self.write("memory.md", "notes")
        target = self.path / "memory.md"
        original = Path.is_symlink

        def is_symlink(path):
            return path == target or original(path)

        with mock.patch.object(Path, "is_symlink", is_symlink):
            self.assert_link_rejected("memory.md")

    def test_the_harness_root_itself_may_be_a_link(self):
        self.write("tools/ask_user.md", "local note")
        alias = self.root / "alias"
        _directory_link(alias, self.path)
        harness = load_harness(alias)
        self.assertEqual(harness.tool_notes, {"ask_user": "local note"})
        self.assertEqual(harness.sha256, load_harness(self.path).sha256)


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

    def test_changing_a_sub_agent_file_changes_the_hash(self):
        self.write("system.md", "System.")
        self.write("sub_agents/reviewer.md", sub_agent_text())
        before = self.load().sha256

        self.write("sub_agents/reviewer.md", sub_agent_text(body="Review it carefully."))

        self.assertNotEqual(before, self.load().sha256)

    def test_changing_a_sub_agent_name_changes_the_hash(self):
        self.write("system.md", "System.")
        self.write("sub_agents/reviewer.md", sub_agent_text())
        before = self.load().sha256

        (self.path / "sub_agents" / "reviewer.md").rename(
            self.path / "sub_agents" / "checker.md"
        )

        self.assertNotEqual(before, self.load().sha256)

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

        self.assertEqual(health["harness"], {
            "name": self.path.name, "sha256": harness.sha256, "sub_agents": [],
        })
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
