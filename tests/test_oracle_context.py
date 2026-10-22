"""TASK-015 oracle-context experiment: generation rules on synthetic fixtures and
the loop's experiment mode. No ground truth, private/ or results/ file is read."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from agent.environment import Environment
from agent.harness import load_harness
from agent.model import ModelClient
from agent.oracle_context import (
    ORACLE_CONTEXT_ENV,
    OracleContextError,
    load_oracle_context,
)
from agent.server import create_app
from agent.tool_loop import _initial_prompt, system_prompt
from agent.tools import tool_metadata
from test_tool_loop import (
    PHASE1_WITH_FOLLOW_UP,
    PHASE2_PASS,
    ToolLoopTestCase,
    action,
    model_events,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_oracle_context as builder  # noqa: E402

# A synthetic database in the official file layout. The schema file uses CRLF
# like the dataset files, and the column meanings use mixed-case keys.
SCHEMA_FILE = (
    'CREATE TABLE "customers" (\r\n'
    "cust_id integer NOT NULL,\r\n"
    "region text NULL,\r\n"
    "    PRIMARY KEY (cust_id)\r\n"
    ");\r\n"
    "\r\n"
    "First 3 rows:\r\n"
    "cust_id    region\r\n"
    "---------  --------\r\n"
    "1          North\r\n"
    "\r\n"
    "\r\n"
    'CREATE TABLE "orders" (\r\n'
    "order_id integer NOT NULL,\r\n"
    "cust_ref integer NULL,\r\n"
    "amount real NULL,\r\n"
    "    FOREIGN KEY (cust_ref) REFERENCES customers(cust_id)\r\n"
    ");\r\n"
    "\r\n"
    "First 3 rows:\r\n"
    "order_id    cust_ref    amount\r\n"
    "\r\n"
    "\r\n"
    'CREATE TABLE "regions" (\r\n'
    "region text NOT NULL\r\n"
    ");\r\n"
    "\r\n"
    "First 3 rows:\r\n"
    "region\r\n"
)
MEANINGS = {
    "shop|Customers|cust_id": "Customer key.",
    "shop|customers|region": "Sales region – one of North, South.",
    "shop|orders|order_id": "Order key.",
    "shop|orders|cust_ref": "References customers.cust_id.",
    "shop|orders|amount": {"column_meaning": "Order amount.", "unit": "USD"},
    "shop|regions|region": "Region name.",
}
KB = [
    {"id": 0, "knowledge": "Big order", "description": "Large order.",
     "definition": "amount > 100", "type": "domain_knowledge", "children_knowledge": -1},
    {"id": 1, "knowledge": "Loyal customer", "description": "Repeat buyer.",
     "definition": "uses Big order", "type": "domain_knowledge", "children_knowledge": [0, 3]},
    {"id": 2, "knowledge": "Region share", "description": "Share per region.",
     "definition": "count / total", "type": "calculation_knowledge", "children_knowledge": 4},
    {"id": 3, "knowledge": "Hidden rule", "description": "Deleted for the task.",
     "definition": "secret", "type": "domain_knowledge", "children_knowledge": 5},
    {"id": 4, "knowledge": "Total", "description": "All orders.",
     "definition": "count(*)", "type": "calculation_knowledge", "children_knowledge": -1},
    {"id": 5, "knowledge": "Child of hidden", "description": "Visible child.",
     "definition": "x", "type": "domain_knowledge", "children_knowledge": -1},
    {"id": 6, "knowledge": "Unused", "description": "Not selected.",
     "definition": "y", "type": "domain_knowledge", "children_knowledge": -1},
]
SOL_SQL = (
    "SELECT c.region, SUM(o.amount) FROM public.\"Customers\" AS c "
    "JOIN orders o ON o.cust_ref = c.cust_id WHERE c.region <> 'regions' -- regions\n"
    "GROUP BY c.region"
)
FOLLOW_UP_SQL = "SELECT r.region FROM regions r JOIN customers c ON c.region = r.region"
SNIPPET = "SUM(o.amount) FROM public"
RECORD = {
    "instance_id": "shop_1",
    "selected_database": "shop",
    "amb_user_query": "Which regions have loyal customers with big orders?",
    "sol_sql": [SOL_SQL],
    "external_knowledge": [1],
    "test_cases": ["assert result == expected  # synthetic"],
    "user_query_ambiguity": {
        "critical_ambiguity": [{"term": "big", "sql_snippet": SNIPPET}],
        "non_critical_ambiguity": [],
    },
    "knowledge_ambiguity": [
        {"term": "hidden", "sql_snippet": "o.amount > 100", "deleted_knowledge": 3},
    ],
    "follow_up": {
        "query": "Now show each region's share.",
        "sol_sql": FOLLOW_UP_SQL,
        "external_knowledge": [1, 2],
        "test_cases": [],
    },
}


class FixtureMixin:
    """A temporary project root with the synthetic database and dev set."""

    def make_root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        db = root / "db" / "shop"
        db.mkdir(parents=True)
        (db / "shop_schema.txt").write_bytes(SCHEMA_FILE.encode("utf-8"))
        (db / "shop_column_meaning_base.json").write_text(
            json.dumps(MEANINGS, indent=2), encoding="utf-8"
        )
        (db / "shop_kb.jsonl").write_text(
            "".join(json.dumps(entry) + "\n" for entry in KB), encoding="utf-8"
        )
        (root / "data.jsonl").write_text(json.dumps(RECORD) + "\n", encoding="utf-8")
        (root / "manifest.json").write_text(
            json.dumps({"selected_ids": ["shop_1"]}), encoding="utf-8"
        )
        return root

    def run_builder(self, root: Path, *args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return builder.main([
                "--project-root", str(root), "--data", "data.jsonl",
                "--manifest", "manifest.json", "--db-dir", "db", "--out", "out", *args,
            ])

    def database(self, root: Path):
        return builder.load_database(root / "db", "shop")


def official_schema(root: Path) -> str:
    # The official service reads the file in text mode on Linux.
    with open(root / "db/shop/shop_schema.txt", encoding="utf-8") as handle:
        return handle.read()


def official_meanings(root: Path) -> dict:
    raw = json.loads((root / "db/shop/shop_column_meaning_base.json").read_text(encoding="utf-8"))
    return {key.lower(): value for key, value in raw.items()}


class GenerationRuleTests(FixtureMixin, unittest.TestCase):
    def test_knowledge_adds_children_recursively_and_drops_deleted_entries(self):
        database = self.database(self.make_root())
        first, second = builder.phase_knowledge(database, RECORD)
        # 1 -> 0 and 3; 3 is deleted but its child 5 stays visible.
        self.assertEqual(first, [0, 1, 5])
        # 2 -> 4 are new; 1 and its children are already in phase 1.
        self.assertEqual(second, [2, 4])

    def test_phase_two_blocks_hold_only_new_items(self):
        database = self.database(self.make_root())
        data = builder.build_task("A", RECORD, database)
        self.assertEqual(data["phase1"]["tables"], ["customers", "orders"])
        self.assertEqual(data["phase2"]["tables"], ["regions"])
        self.assertEqual(data["phase2"]["knowledge_ids"], [2, 4])
        self.assertNotIn('"orders"', data["phase2"]["schema"])
        self.assertNotIn('"id": 1,', data["phase2"]["knowledge"])

    def test_a_uses_whole_blocks_of_the_solution_tables_and_c_the_full_text(self):
        root = self.make_root()
        database = self.database(root)
        schema = official_schema(root)
        a = builder.build_task("A", RECORD, database)
        blocks = dict(builder.schema_blocks(schema))
        self.assertEqual(a["phase1"]["schema"], (blocks["customers"] + blocks["orders"]).rstrip())
        self.assertIn("First 3 rows:\ncust_id    region", a["phase1"]["schema"])
        self.assertNotIn("\r", a["phase1"]["schema"])
        self.assertEqual(
            json.loads(a["phase1"]["column_meanings"]),
            {key: value for key, value in official_meanings(root).items()
             if key.split("|")[1] in ("customers", "orders")},
        )
        c = builder.build_task("C", RECORD, database)
        self.assertEqual(c["phase1"]["schema"], schema)
        self.assertEqual(
            c["phase1"]["column_meanings"], json.dumps(official_meanings(root), indent=2)
        )
        self.assertEqual(c["phase2"]["schema"], "")
        self.assertEqual(c["phase2"]["column_meanings"], "")
        self.assertEqual(c["phase1"]["knowledge"], a["phase1"]["knowledge"])
        self.assertEqual(c["phase2"]["knowledge"], a["phase2"]["knowledge"])

    def test_table_detection_ignores_strings_and_comments_and_reads_prefixes(self):
        tables = ["customers", "orders", "regions"]
        found, relations = builder.sql_tables([SOL_SQL], tables)
        self.assertEqual(found, ["customers", "orders"])
        self.assertEqual(relations, ["customers", "orders"])
        found, relations = builder.sql_tables(
            "SELECT orders FROM customers WHERE name = 'regions' /* regions */", tables
        )
        self.assertEqual(found, ["customers", "orders"])
        self.assertEqual(relations, ["customers"])
        # Function bodies are dollar-quoted SQL and name tables too.
        found, relations = builder.sql_tables(
            "CREATE FUNCTION f() RETURNS int AS $body$ SELECT 1 FROM regions $body$ "
            "LANGUAGE sql; CREATE INDEX i ON orders (amount)", tables
        )
        self.assertEqual(found, ["orders", "regions"])
        self.assertEqual(relations, ["orders", "regions"])

    def test_b_blocks_come_from_the_selection_record(self):
        database = self.database(self.make_root())
        selection = {
            "phase1": {"tables": ["orders"]},
            "phase2": {"tables": ["orders", "regions"]},
        }
        data = builder.build_task("B", RECORD, database, selection)
        self.assertEqual(data["phase1"]["tables"], ["orders"])
        self.assertEqual(data["phase2"]["tables"], ["regions"])
        self.assertTrue(data["phase1"]["schema"].startswith('CREATE TABLE "orders"'))
        with self.assertRaises(ValueError):
            builder.build_task("B", RECORD, database, None)

    def test_curator_output_keeps_schema_tables_and_reports_the_rest(self):
        tables, excluded, reason = builder.parse_curator_output(
            'Here: {"tables": ["Orders", "public.customers", "invoices"], "reason": "join"}',
            ["customers", "orders", "regions"],
        )
        self.assertEqual(tables, ["customers", "orders"])
        self.assertEqual(excluded, ["invoices"])
        self.assertEqual(reason, "join")
        with self.assertRaises(ValueError):
            builder.parse_curator_output("no json", ["customers"])

    def test_injected_text_matches_the_official_response_format(self):
        root = self.make_root()
        database = self.database(root)
        a = builder.build_task("A", RECORD, database)
        schema = official_schema(root)
        for name, block in builder.schema_blocks(schema):
            if name in a["phase1"]["tables"]:
                self.assertIn(block.rstrip(), schema)
        # /all_column_meanings: json.dumps(dict, indent=2) with ASCII escapes.
        self.assertIn("\\u2013", a["phase1"]["column_meanings"])
        self.assertIn('"shop|customers|cust_id"', a["phase1"]["column_meanings"])
        # /knowledge without a name: visible fields only, indent=2.
        visible = [
            {field: entry[field] for field in ("id", "knowledge", "description", "definition")}
            for entry in KB if entry["id"] in (0, 1, 5)
        ]
        self.assertEqual(a["phase1"]["knowledge"], json.dumps(visible, indent=2))
        self.assertNotIn("children_knowledge", a["phase1"]["knowledge"])
        self.assertNotIn("Hidden rule", a["phase1"]["knowledge"] + a["phase2"]["knowledge"])

    def test_build_is_deterministic_and_loadable(self):
        root = self.make_root()
        selections = root / "out/B/selections"
        selections.mkdir(parents=True)
        (selections / "shop_1.json").write_text(json.dumps({
            "phase1": {"tables": ["customers"]}, "phase2": {"tables": ["regions"]},
        }), encoding="utf-8")
        stamp = "2026-01-01T00:00:00Z"
        self.assertEqual(self.run_builder(root, "build", "--generated-at", stamp), 0)
        first = {path: path.read_bytes() for path in sorted((root / "out").rglob("*.json"))}
        self.assertEqual(self.run_builder(root, "build", "--generated-at", stamp), 0)
        second = {path: path.read_bytes() for path in sorted((root / "out").rglob("*.json"))}
        self.assertEqual(first, second)
        for group in ("A", "B", "C"):
            context = load_oracle_context(root / "out" / group)
            self.assertEqual(context.group, group)
            loaded = context.load("shop_1")
            data = json.loads((root / "out" / group / "shop_1.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded.sha256, data["context_sha256"])
            self.assertEqual(data["generation"]["generated_at"], stamp)
            self.assertIn("data.jsonl", data["generation"]["inputs"])
            self.assertNotIn(SOL_SQL, json.dumps(data))
            self.assertNotIn(SNIPPET, json.dumps(data))
        # The record of the generation does not change the injected-text hash.
        self.assertEqual(self.run_builder(root, "build", "--generated-at", "later"), 0)
        self.assertEqual(
            load_oracle_context(root / "out/A").load("shop_1").sha256,
            json.loads(first[root / "out/A/shop_1.json"])["context_sha256"],
        )

    def test_curation_inputs_hold_no_ground_truth_and_selections_are_recorded(self):
        root = self.make_root()
        self.assertEqual(self.run_builder(root, "curation-inputs", "--phase", "1"), 0)
        bundle = (root / "out/B/curation/shop_1.phase1.md").read_text(encoding="utf-8")
        self.assertIn(RECORD["amb_user_query"], bundle)
        self.assertIn(official_schema(root).strip(), bundle)
        self.assertIn(json.dumps(official_meanings(root), indent=2), bundle)
        for _, text in builder.forbidden_strings(RECORD):
            self.assertNotIn(text, bundle)
        self.assertEqual(self.run_builder(root, "check-curation-inputs"), 0)

        raw = root / "out/B/raw/shop_1.phase1.txt"
        raw.parent.mkdir(parents=True)
        raw.write_text('{"tables": ["orders", "ghost"], "reason": "r"}', encoding="utf-8")
        self.assertEqual(self.run_builder(root, "record-selections", "--phase", "1"), 0)
        record = json.loads((root / "out/B/selections/shop_1.json").read_text(encoding="utf-8"))
        self.assertEqual(record["phase1"]["tables"], ["orders"])
        self.assertEqual(record["phase1"]["excluded_tables"], ["ghost"])
        self.assertEqual((record["phase1"]["model"], record["phase1"]["effort"]), ("opus", "high"))
        self.assertEqual(len(record["phase1"]["input_sha256"]), 64)

        self.assertEqual(self.run_builder(root, "curation-inputs", "--phase", "2"), 0)
        bundle2 = (root / "out/B/curation/shop_1.phase2.md").read_text(encoding="utf-8")
        self.assertIn(RECORD["follow_up"]["query"], bundle2)
        self.assertIn('["orders"]', bundle2)
        self.assertNotIn(RECORD["amb_user_query"], bundle2)
        self.assertNotIn(FOLLOW_UP_SQL, bundle2)
        self.assertEqual(self.run_builder(root, "check-curation-inputs"), 0)

    def test_the_leak_check_fails_on_a_tampered_bundle(self):
        root = self.make_root()
        self.run_builder(root, "curation-inputs", "--phase", "1")
        path = root / "out/B/curation/shop_1.phase1.md"
        path.write_text(path.read_text(encoding="utf-8") + SOL_SQL, encoding="utf-8")
        self.assertEqual(self.run_builder(root, "check-curation-inputs"), 1)


def write_context(directory: Path, group: str = "A", task_id: str = "test-task",
                  phase2: dict | None = None) -> dict:
    from agent.oracle_context import context_sha256

    phase1 = {
        "schema": 'CREATE TABLE "customers" (\nid integer\n);',
        "column_meanings": '{\n  "demo|customers|id": "Customer key."\n}',
        "knowledge": '[\n  {\n    "id": 7,\n    "knowledge": "Active customer"\n  }\n]',
    }
    phase2 = phase2 if phase2 is not None else {
        "schema": 'CREATE TABLE "regions" (\nname text\n);',
        "column_meanings": "",
        "knowledge": "",
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({"experiment": group}), encoding="utf-8")
    data = {
        "experiment": group, "task_id": task_id,
        "phase1": {**phase1, "tables": ["customers"]},
        "phase2": {**phase2, "tables": ["regions"]},
        "context_sha256": context_sha256(phase1, phase2),
    }
    (directory / f"{task_id}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


class OracleModeTestCase(ToolLoopTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.context_dir = Path(directory.name) / "A"
        self.context_file = write_context(self.context_dir)
        self.oracle = load_oracle_context(self.context_dir)
        self.client = TestClient(create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(transport=httpx.MockTransport(self.model_handler), api_key="test-key"),
            mode="tools", oracle_context=self.oracle,
        ))


class OracleModeTests(OracleModeTestCase):
    def test_only_the_allowed_actions_are_listed_and_lookups_are_rejected_free(self):
        self.actions = [
            action("get_schema"),
            action("get_all_column_meanings"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_WITH_FOLLOW_UP)
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        self.grader_payloads.append(dict(PHASE2_PASS))
        state = self.run_ok()

        system = self.model_requests[0]["messages"][0]["content"]
        self.assertIn("- execute_sql:", system)
        self.assertIn("- ask_user:", system)
        self.assertIn("- submit_sql:", system)
        for name in ("get_schema", "get_all_column_meanings", "get_column_meaning",
                     "get_all_external_knowledge_names", "get_knowledge_definition",
                     "get_all_knowledge_definitions", "consult"):
            self.assertNotIn(name, system)
        self.assertNotIn("/schema", self.paths())
        self.assertNotIn("/all_column_meanings", self.paths())
        events = model_events(state)
        self.assertIn("Unknown action 'get_schema'", events[0]["action_error"])
        self.assertIn("Available actions: execute_sql, ask_user, submit_sql.", events[0]["action_error"])
        self.assertIn("| cost 0 |", self.model_requests[1]["messages"][-1]["content"])
        self.assertEqual(
            [entry["tool"] for entry in state["tool_trajectory"]], ["submit_sql", "submit_sql"]
        )
        self.assertEqual(state["budget_remaining"], 18 - 3 - 3 - 3)

    def test_health_keeps_the_nine_tools_and_reports_the_experiment(self):
        health = self.client.get("/health").json()
        self.assertEqual(health["tools"], tool_metadata())
        self.assertEqual(len(health["tools"]), 9)
        self.assertEqual(health["experiment"], {"oracle_context": "A", "budget_offset": 3.0})
        plain = TestClient(create_app(oracle_context=None)).get("/health").json()
        self.assertNotIn("experiment", plain)

    def test_context_is_injected_in_the_first_message_and_the_follow_up(self):
        self.actions = [
            action("submit_sql", sql="SELECT 1;"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        self.run_ok()

        first = self.model_requests[0]["messages"][1]["content"]
        self.assertEqual(first, (
            "Database: demo\n\n"
            "User Query:\nHow many customers?\n\n"
            "Provided database context:\n"
            "The schema, column meanings and external knowledge below are the parts provided "
            "as relevant to this task.\n\n"
            "Schema:\n" + self.context_file["phase1"]["schema"] + "\n\n"
            "Column meanings:\n" + self.context_file["phase1"]["column_meanings"] + "\n\n"
            "External knowledge:\n" + self.context_file["phase1"]["knowledge"] + "\n\n"
            "You have 15 bird-coins for this task and 15 remain. Send one JSON action."
        ))
        for word in ("ground truth", "oracle", "correct answer", "GT"):
            self.assertNotIn(word, first)
        transition = self.model_requests[1]["messages"][-1]["content"]
        self.assertTrue(transition.endswith(
            "Follow-up question:\nAnd by region?\n\n"
            "Additional database context for the follow-up question:\n"
            "The parts below are provided as relevant to the follow-up question, in addition "
            "to the context given at the start.\n\n"
            "Schema:\n" + self.context_file["phase2"]["schema"]
        ))
        self.assertNotIn("Column meanings:", transition)

    def test_an_empty_follow_up_block_adds_nothing(self):
        write_context(self.context_dir, phase2={"schema": "", "column_meanings": "", "knowledge": ""})
        self.actions = [
            action("submit_sql", sql="SELECT 1;"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        self.run_ok()
        transition = self.model_requests[1]["messages"][-1]["content"]
        self.assertTrue(transition.endswith("Follow-up question:\nAnd by region?"))

    def test_budget_starts_three_coins_lower_and_the_experiment_is_recorded(self):
        self.actions = [action("execute_sql", sql="SELECT 1"), action("submit_sql", sql="SELECT 1;")]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        self.grader_payloads.append(dict(PHASE2_PASS))
        state = self.run_ok()

        self.assertEqual(state["initial_budget"], 18)
        self.assertEqual(state["tool_trajectory"][0]["budget_before"], 15)
        self.assertEqual(state["budget_remaining"], 15 - 1 - 3 - 3)
        expected = {
            "oracle_context": "A", "budget_offset": 3.0,
            "context_sha256": self.context_file["context_sha256"],
            "context_chars": {
                "phase1": sum(len(self.context_file["phase1"][k])
                              for k in ("schema", "column_meanings", "knowledge")),
                "phase2": len(self.context_file["phase2"]["schema"]),
            },
        }
        self.assertEqual(state["experiment"], expected)
        events = model_events(state)
        self.assertEqual(len(events), 3)
        for event in events:
            self.assertEqual(event["experiment"], expected)

    def test_the_offset_is_taken_once_per_session(self):
        self.actions = [action("submit_sql", sql="SELECT 1;"), action("submit_sql", sql="SELECT 2;")]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        self.initialize()
        self.assertEqual(self.execute().status_code, 200)
        state = self.execute().json()["state"]
        self.assertEqual(state["budget_remaining"], 18 - 3 - 3 - 3)
        self.assertEqual(len(self.model_requests), 2)

    def test_a_missing_context_file_fails_the_session_clearly(self):
        (self.context_dir / "test-task.json").unlink()
        self.initialize()
        response = self.execute()
        self.assertEqual(response.status_code, 500)
        self.assertIn("Oracle context error", response.text)
        self.assertIn("No oracle context file for task 'test-task'", response.text)
        self.assertEqual(self.model_requests, [])
        self.assertEqual(self.service_requests, [])

    def test_a_tampered_context_file_is_rejected(self):
        data = json.loads((self.context_dir / "test-task.json").read_text(encoding="utf-8"))
        data["phase1"]["schema"] += "\nextra"
        (self.context_dir / "test-task.json").write_text(json.dumps(data), encoding="utf-8")
        self.initialize()
        response = self.execute()
        self.assertEqual(response.status_code, 500)
        self.assertIn("context_sha256", response.text)


class OracleSetupTests(unittest.TestCase):
    def test_the_mode_is_off_without_the_variable(self):
        with mock.patch.dict("os.environ", {ORACLE_CONTEXT_ENV: ""}):
            self.assertIsNone(load_oracle_context())

    def test_a_bad_directory_stops_the_server_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with mock.patch.dict("os.environ", {ORACLE_CONTEXT_ENV: str(missing)}):
                with self.assertRaises(OracleContextError):
                    create_app(mode="tools")
            with self.assertRaises(OracleContextError) as caught:
                load_oracle_context(directory)
            self.assertIn("manifest.json", str(caught.exception))
            write_context(Path(directory) / "B", group="B")
            with mock.patch.dict(
                "os.environ", {ORACLE_CONTEXT_ENV: str(Path(directory) / "B")}
            ):
                health = TestClient(create_app(mode="tools")).get("/health").json()
            self.assertEqual(health["experiment"]["oracle_context"], "B")

    def test_a_context_file_of_another_group_or_task_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            write_context(Path(directory), group="A", task_id="other")
            data = json.loads((Path(directory) / "other.json").read_text(encoding="utf-8"))
            data["task_id"] = "test-task"
            (Path(directory) / "test-task.json").write_text(json.dumps(data), encoding="utf-8")
            context = load_oracle_context(directory)
            context.load("test-task")
            data["experiment"] = "C"
            (Path(directory) / "test-task.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(OracleContextError):
                context.load("test-task")
            with self.assertRaises(OracleContextError):
                context.load("../test-task")

    def test_without_the_mode_prompt_and_actions_are_the_base_ones(self):
        harness = load_harness()
        state = {"db_name": "demo", "initial_budget": 18, "budget_remaining": 18}
        self.assertEqual(
            _initial_prompt(state, "Q?"),
            "Database: demo\n\nUser Query:\nQ?\n\nYou have 18 bird-coins for this task and "
            "18 remain. Send one JSON action.",
        )
        self.assertEqual(system_prompt(harness), system_prompt(harness, False))
        self.assertIn("get_schema", system_prompt(harness))
        self.assertNotIn("get_schema", system_prompt(harness, True))


class AnalysisTests(unittest.TestCase):
    def test_the_report_reads_result_files_and_separates_the_offset(self):
        import analyze_oracle_context as analysis

        experiment = {"oracle_context": "A", "budget_offset": 3.0,
                      "context_chars": {"phase1": 100, "phase2": 10}}
        call = {"type": "model_call", "usage": {"prompt_tokens": 50, "completion_tokens": 5},
                "experiment": experiment}
        oracle = [
            {"task_id": "t1", "total_reward": 1.0, "phase1_passed": True, "phase2_passed": True,
             "budget_used": 10.0, "elapsed_seconds": 20.0,
             "final_response": "Phase 2 correct! (Reward: 0.3). Task finished.",
             "adk_events": [call, call],
             "tool_trajectory": [
                 {"tool": "ask_user", "phase": 1},
                 {"tool": "submit_sql", "phase": 1, "result": {"passed": True}},
                 {"tool": "submit_sql", "phase": 2, "result": {"passed": True}},
             ]},
            {"task_id": "t2", "error": "boom"},
        ]
        baseline = [{"task_id": "t1", "total_reward": 0.0, "budget_used": 4.0,
                     "final_response": "Stopped after 24 model actions.",
                     "adk_events": [{"type": "model_call", "usage": {}}],
                     "tool_trajectory": [{"tool": "submit_sql", "phase": 1,
                                          "result": {"passed": False}}]}]
        stats = analysis.summarize(oracle)
        self.assertEqual(stats["oracle_context"], "A")
        self.assertEqual(stats["phase1_passed"], 1)
        self.assertEqual(stats["first_submit_pass_rate"], 1.0)
        self.assertEqual(stats["coins_spent"], (10.0 - 3.0 + 0.0) / 2)
        self.assertEqual(stats["input_tokens"], 50.0)
        self.assertEqual(stats["stop_reasons"], {"phase2_completed": 1, "error": 1})
        text = analysis.report({"baseline": baseline, "A": oracle})
        self.assertIn("not agent performance", text)
        self.assertIn("0.327-0.463", text)
        self.assertIn("| t1 | 0.0 (N/N) | 1.0 (Y/Y) |", text)
        self.assertIn("| t2 | not run | error |", text)
        self.assertIn("| stop: action_limit_reached | 1 | 0 |", text)


class OracleHarnessTests(unittest.TestCase):
    def test_the_experiment_harness_loads_without_sub_agents(self):
        harness = load_harness(ROOT / "harness/oracle_context")
        self.assertEqual(harness.name, "oracle_context")
        self.assertEqual(harness.sub_agents, {})
        baseline = load_harness(ROOT / "harness/baseline").system.strip()
        self.assertTrue(harness.system.startswith(baseline))
        self.assertIn("ask_user", harness.system)
        self.assertIn("execute_sql", harness.system)


if __name__ == "__main__":
    unittest.main()
