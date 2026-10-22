"""Tool-selecting loop contracts with a mock model and grader; not BIRD scores."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from agent import tool_loop
from agent.environment import (
    ASK_USER_TIMEOUT_SECONDS,
    EXECUTE_SQL_TIMEOUT_SECONDS,
    Environment,
    GradingError,
)
from agent.harness import load_harness
from agent.model import (
    API_URL,
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    MODEL_ID,
    ModelClient,
    ModelError,
)
from agent.server import AGENT_NAME, create_app
from agent.tool_loop import MAX_MODEL_ACTIONS
from agent.tools import (
    ALLOWED_TOOLS,
    ASK_USER_COST,
    EXECUTE_SQL_COST,
    SUBMIT_COST,
    TOOL_COSTS,
    ActionError,
    parse_action,
    parse_response,
    tool_metadata,
)

SCHEMA_TEXT = "CREATE TABLE customers (\n  id integer,\n  name text\n);"
MEANINGS_TEXT = '{\n  "customers|id": "identifier",\n  "customers|name": "full name"\n}'
COLUMN_MEANING = "identifier of a customer"
KNOWLEDGE_NAMES = ["Active customer"]
KNOWLEDGE_ENTRY = '{\n  "id": 7,\n  "knowledge": "Active customer",\n  "definition": "Has orders."\n}'
KNOWLEDGE_TEXT = '[{"id": 7, "knowledge": "Active customer", "definition": "Has orders."}]'
EXECUTE_RESULT = "table_count\n3"
EXECUTE_ERROR = "SQL error: relation \"customers\" does not exist"
USER_ANSWER = "The result should list the customer counts per region."

GRADER_FAILURE = {
    "passed": False,
    "reward": 0.0,
    "message": "SQL failed Phase 1. Your SQL is not correct.",
}
PHASE1_WITH_FOLLOW_UP = {
    "passed": True,
    "reward": 0.7,
    "message": "Phase 1 correct! (Reward: 0.7). Moving to Phase 2.",
    "phase_completed": 1,
    "has_follow_up": True,
    "follow_up_query": "And by region?",
}
PHASE1_FINAL = {
    "passed": True,
    "reward": 0.7,
    "message": "Phase 1 correct! (Reward: 0.7). Task finished.",
    "phase_completed": 1,
    "has_follow_up": False,
}
PHASE2_PASS = {
    "passed": True,
    "reward": 0.3,
    "message": "Phase 2 correct! (Reward: 0.3). Task finished.",
    "phase_completed": 2,
}


def action(tool: str, **args) -> str:
    return json.dumps({"action": tool, "args": args})


DSML = "<\uff5c\uff5cDSML\uff5c\uff5c"
DSML_END = "</\uff5c\uff5cDSML\uff5c\uff5c"


def dsml_invoke(name: str, arguments: str | None = None) -> str:
    lines = [f'{DSML} invoke name="{name}">']
    if arguments is not None:
        lines.append(
            f'{DSML} parameter name="args" string="false">{arguments}{DSML_END} parameter>'
        )
    lines.append(f"{DSML_END} invoke>")
    return "\n".join(lines)


def dsml_calls(*invokes: str) -> str:
    return "\n".join([f"{DSML} calls>", *invokes, f"{DSML_END} calls>"])


def mimo_call(name: str, **parameters) -> str:
    body = "".join(
        f"<parameter={key}>{value}</parameter>" for key, value in parameters.items()
    )
    return f"<tool_call><function={name}>{body}</function></tool_call>"


def mimo_calls(*calls: str) -> str:
    return "\n".join(calls)


def model_reply(content: str, finish_reason: str = "stop") -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


def model_events(state: dict) -> list[dict]:
    return [event for event in state.get("adk_events", [])
            if event.get("type") == "model_call"]


class FakeClock:
    """A monotonic clock the tests move to check the session deadline."""

    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class ActionParsingTests(unittest.TestCase):
    def test_plain_json_action_is_parsed_with_typed_argument_names(self):
        self.assertEqual(
            parse_action('{"action": "get_column_meaning", '
                         '"args": {"table_name": "customers", "column_name": "id"}}'),
            ("get_column_meaning", {"table_name": "customers", "column_name": "id"}),
        )
        self.assertEqual(parse_action(action("get_schema")), ("get_schema", {}))
        self.assertEqual(
            parse_action(action("submit_sql", sql="SELECT 1;")), ("submit_sql", {"sql": "SELECT 1;"})
        )

    def test_fences_prose_and_bare_arguments_are_accepted(self):
        cases = {
            "fenced": '```json\n{"action": "get_schema", "args": {}}\n```',
            "prose": 'Here is my next action: {"action": "get_schema"} — thanks.',
            "bare arguments": '{"action": "get_column_meaning", "table_name": "t", "column_name": "c"}',
            "bare sql": '{"action": "submit_sql", "sql": "SELECT 1;"}',
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                self.assertIsInstance(parse_action(content), tuple)

    def test_several_actions_in_one_response_use_the_first(self):
        first = action("get_schema")
        other = action("get_all_column_meanings")
        cases = {
            "same fenced json": (f'{first}\n```json\n{first}\n```', 2, 0),
            "same json twice": (f"{first}\n\n{first}", 2, 0),
            "different json actions": (f"{first}\n{other}", 2, 1),
            "json and the same dsml": (
                f"{first}\n{dsml_calls(dsml_invoke('get_schema'))}", 2, 0
            ),
            "json and another dsml": (
                f"{first}\n{dsml_calls(dsml_invoke('get_all_column_meanings'))}", 2, 1
            ),
            "json and the same mimo": (
                f"{first}\n{mimo_call('get_schema')}", 2, 0
            ),
            "json and another mimo": (
                f"{first}\n{mimo_call('get_all_column_meanings')}", 2, 1
            ),
        }
        for name, (content, action_count, ignored) in cases.items():
            with self.subTest(name=name):
                parsed = parse_response(content)
                self.assertEqual((parsed.name, parsed.args), ("get_schema", {}))
                self.assertEqual(
                    (parsed.action_count, parsed.ignored_actions), (action_count, ignored)
                )
                self.assertEqual(parse_action(content), ("get_schema", {}))

    def test_braces_and_action_words_inside_string_values_are_one_action(self):
        sql = "SELECT '{\"action\": \"get_schema\", \"args\": {}}' AS payload;"
        parsed = parse_response(action("execute_sql", sql=sql))
        self.assertEqual((parsed.name, parsed.args), ("execute_sql", {"sql": sql}))
        self.assertEqual((parsed.action_count, parsed.ignored_actions), (1, 0))

    def test_dsml_only_responses_are_read_from_the_first_invoke(self):
        cases = {
            "arguments": (
                dsml_calls(dsml_invoke("execute_sql", '{"sql": "SELECT 1;"}')),
                ("execute_sql", {"sql": "SELECT 1;"}),
                1,
                0,
            ),
            "no arguments": (
                dsml_calls(dsml_invoke("get_schema")),
                ("get_schema", {}),
                1,
                0,
            ),
            "named arguments": (
                dsml_calls(
                    f'{DSML} invoke name="execute_sql">\n'
                    f'{DSML} parameter name="sql">SELECT 1;{DSML_END} parameter>\n'
                    f"{DSML_END} invoke>"
                ),
                ("execute_sql", {"sql": "SELECT 1;"}),
                1,
                0,
            ),
            "multiple invokes": (
                dsml_calls(
                    dsml_invoke("get_schema"), dsml_invoke("get_all_column_meanings")
                ),
                ("get_schema", {}),
                2,
                1,
            ),
        }
        for name, (content, expected, action_count, ignored) in cases.items():
            with self.subTest(name=name):
                parsed = parse_response(content)
                self.assertEqual((parsed.name, parsed.args), expected)
                self.assertEqual(
                    (parsed.action_count, parsed.ignored_actions), (action_count, ignored)
                )

    def test_malformed_dsml_only_responses_are_rejected_with_json_guidance(self):
        cases = {
            "unclosed name attribute": (
                f'{DSML} calls>\n{DSML} invoke name="get_all_knowledge_definitions"}}\n\n'
            ),
            "arguments in the opening tag": (
                f'{DSML} calls>\n{DSML} invoke name="get_all_external_knowledge_names", '
                '"args": {}}'
            ),
            "arguments are not a JSON object": dsml_calls(
                dsml_invoke("execute_sql", '"SELECT 1;"')
            ),
            "arguments are a list": dsml_calls(dsml_invoke("execute_sql", "[1]")),
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(ActionError) as caught:
                parse_action(content)
            self.assertIn('{"action"', str(caught.exception))
            self.assertIn("JSON", str(caught.exception))

    def test_mimo_tool_calls_are_read_from_the_first_call(self):
        cases = {
            "named argument": (
                mimo_call("execute_sql", sql="SELECT 1;"),
                ("execute_sql", {"sql": "SELECT 1;"}),
                1,
                0,
            ),
            "json arguments": (
                mimo_call("execute_sql", args='{"sql": "SELECT 1;"}'),
                ("execute_sql", {"sql": "SELECT 1;"}),
                1,
                0,
            ),
            "no arguments": (
                mimo_call("get_schema"),
                ("get_schema", {}),
                1,
                0,
            ),
            "several named arguments": (
                mimo_call("get_column_meaning", table_name="customers", column_name="id"),
                ("get_column_meaning", {"table_name": "customers", "column_name": "id"}),
                1,
                0,
            ),
            "sql with comparison operators": (
                mimo_call("execute_sql", sql="SELECT 1 WHERE 1 < 2 AND 3 > 2;"),
                ("execute_sql", {"sql": "SELECT 1 WHERE 1 < 2 AND 3 > 2;"}),
                1,
                0,
            ),
            "multiple calls": (
                mimo_calls(
                    mimo_call("get_schema"), mimo_call("get_all_column_meanings")
                ),
                ("get_schema", {}),
                2,
                1,
            ),
            "same call twice": (
                mimo_calls(mimo_call("get_schema"), mimo_call("get_schema")),
                ("get_schema", {}),
                2,
                0,
            ),
            "newlines and spaces": (
                "<tool_call>\n<function=execute_sql>\n"
                "<parameter=sql>SELECT 1;</parameter>\n"
                "</function>\n</tool_call>",
                ("execute_sql", {"sql": "SELECT 1;"}),
                1,
                0,
            ),
        }
        for name, (content, expected, action_count, ignored) in cases.items():
            with self.subTest(name=name):
                parsed = parse_response(content)
                self.assertEqual((parsed.name, parsed.args), expected)
                self.assertEqual(
                    (parsed.action_count, parsed.ignored_actions), (action_count, ignored)
                )
                self.assertEqual(parse_action(content), expected)

    def test_malformed_mimo_responses_are_rejected(self):
        cases = {
            "no function": "<tool_call></tool_call>",
            "invalid json arguments": mimo_call("execute_sql", args='{"sql": '),
            "arguments are not an object": mimo_call("execute_sql", args="[1]"),
            "unclosed parameter": (
                "<tool_call><function=execute_sql><parameter=sql>SELECT 1;"
                "</function></tool_call>"
            ),
            "unknown action": mimo_call("drop_table"),
            "missing sql": mimo_call("execute_sql"),
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(ActionError):
                parse_action(content)
        with self.assertRaises(ActionError) as caught:
            parse_action("<tool_call></tool_call>")
        self.assertIn('{"action"', str(caught.exception))
        self.assertIn("JSON", str(caught.exception))

    def test_an_invalid_first_action_is_not_replaced_by_a_later_valid_one(self):
        cases = {
            "json without an action": (
                '{"args": {"sql": "SELECT 1;"}}\n' + action("get_schema")
            ),
            "unknown first action": action("drop_table") + "\n" + action("get_schema"),
            "invalid first arguments": (
                '{"action": "get_column_meaning", "args": {"table_name": "t"}}\n'
                + action("get_schema")
            ),
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(ActionError):
                parse_action(content)

    def test_the_two_new_tools_are_accepted_and_unknown_actions_are_rejected(self):
        self.assertEqual(
            parse_action(action("execute_sql", sql="SELECT 1;")),
            ("execute_sql", {"sql": "SELECT 1;"}),
        )
        self.assertEqual(
            parse_action(action("ask_user", question="Which columns?")),
            ("ask_user", {"question": "Which columns?"}),
        )
        with self.assertRaises(ActionError) as caught:
            parse_action(action("drop_table"))
        self.assertIn("drop_table", str(caught.exception))

    def test_invalid_new_tool_arguments_are_rejected_before_any_call(self):
        cases = {
            "blank sql": '{"action": "execute_sql", "args": {"sql": "  "}}',
            "missing sql": '{"action": "execute_sql", "args": {}}',
            "blank question": '{"action": "ask_user", "args": {"question": ""}}',
            "missing question": '{"action": "ask_user"}',
            "extra argument": (
                '{"action": "execute_sql", "args": {"sql": "SELECT 1;", "task_id": "x"}}'
            ),
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(ActionError):
                parse_action(content)

    def test_malformed_responses_are_rejected(self):
        cases = {
            "empty": "",
            "whitespace": "   ",
            "not json": "I will now look at the schema.",
            "json list": "[1, 2]",
            "no action": '{"args": {"sql": "SELECT 1;"}}',
            "action not a string": '{"action": 7}',
            "args not an object": '{"action": "get_schema", "args": "none"}',
            "missing argument": '{"action": "get_column_meaning", "args": {"table_name": "t"}}',
            "unknown argument": '{"action": "get_schema", "args": {"db": "demo"}}',
            "empty argument": '{"action": "submit_sql", "args": {"sql": "  "}}',
        }
        for name, content in cases.items():
            with self.subTest(name=name), self.assertRaises(ActionError):
                parse_action(content)

    def test_only_the_official_prices_of_the_nine_tools_are_registered(self):
        self.assertEqual(
            TOOL_COSTS,
            {
                "get_schema": 1.0,
                "get_all_column_meanings": 1.0,
                "get_column_meaning": 0.5,
                "get_all_external_knowledge_names": 0.5,
                "get_knowledge_definition": 0.5,
                "get_all_knowledge_definitions": 1.0,
                "execute_sql": 1.0,
                "ask_user": 2.0,
                "submit_sql": 3.0,
            },
        )
        self.assertEqual(EXECUTE_SQL_COST, 1.0)
        self.assertEqual(ASK_USER_COST, 2.0)
        self.assertEqual(max(TOOL_COSTS.values()), SUBMIT_COST)
        self.assertEqual(len(ALLOWED_TOOLS), 9)
        self.assertEqual(
            tool_metadata(),
            [{"name": name, "cost": cost} for name, cost in TOOL_COSTS.items()],
        )


class ExecuteSqlStatementTests(unittest.TestCase):
    """execute_sql accepts one statement and rejects several before any call."""

    def test_multiple_statements_are_rejected_with_the_count_and_guidance(self):
        for sql in ("SELECT 1; SELECT 2", "SELECT 1; SELECT 2;"):
            with self.subTest(sql=sql), self.assertRaises(ActionError) as caught:
                parse_action(action("execute_sql", sql=sql))
            message = str(caught.exception)
            self.assertIn("2 statements", message)
            self.assertIn("UNION ALL", message)

    def test_semicolons_inside_sql_tokens_do_not_split_statements(self):
        allowed = (
            "SELECT 1",
            "SELECT 1;",
            "SELECT 1; -- note",
            "SELECT 1; /* note */",
            "SELECT ';'",
            "SELECT E'a;b'",
            'SELECT "a;b" FROM t',
            "SELECT $$;$$",
            "SELECT $x$;$x$",
            "SELECT 1 /* ; /* ; */ */",
        )
        for sql in allowed:
            with self.subTest(sql=sql):
                self.assertEqual(
                    parse_action(action("execute_sql", sql=sql)),
                    ("execute_sql", {"sql": sql}),
                )

    def test_ambiguous_regions_keep_the_current_behavior(self):
        for sql in (
            "SELECT 'unterminated",
            "SELECT 1; /* unterminated",
            "SELECT $$unterminated",
            "SELECT E'a\\'b; SELECT 2",
        ):
            with self.subTest(sql=sql):
                self.assertEqual(
                    parse_action(action("execute_sql", sql=sql)),
                    ("execute_sql", {"sql": sql}),
                )

    def test_submit_sql_is_not_limited_to_one_statement(self):
        sql = "SELECT 1; SELECT 2;"
        self.assertEqual(
            parse_action(action("submit_sql", sql=sql)), ("submit_sql", {"sql": sql})
        )


class EnvironmentContractTests(unittest.IsolatedAsyncioTestCase):
    """The official HTTP contract of execute_sql and ask_user."""

    async def test_execute_sql_accepts_only_the_official_response_shape(self):
        cases = {
            "success": ({"success": True, "result": EXECUTE_RESULT, "error": None}, True),
            "official SQL error": ({"success": False, "result": "", "error": EXECUTE_ERROR}, False),
            "missing result": ({"success": True}, None),
            "wrong success type": ({"success": "maybe", "result": "1"}, None),
        }
        for name, (payload, expected_success) in cases.items():
            with self.subTest(name=name):
                environment = Environment(
                    httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
                )
                if expected_success is None:
                    with self.assertRaises(GradingError):
                        await environment.execute_sql("test-task", "SELECT 1;")
                else:
                    result = await environment.execute_sql("test-task", "SELECT 1;")
                    self.assertEqual(result.success, expected_success)

    async def test_timeouts_can_be_set_explicitly_and_default_to_120_and_660(self):
        environment = Environment()
        self.assertEqual(environment.execute_sql_timeout, EXECUTE_SQL_TIMEOUT_SECONDS)
        self.assertEqual(environment.ask_user_timeout, ASK_USER_TIMEOUT_SECONDS)
        with mock.patch.dict(os.environ, {
            "DB_EXECUTE_SQL_TIMEOUT_SECONDS": "7", "USER_SIM_ASK_TIMEOUT_SECONDS": "42.5",
        }):
            configured = Environment()
        self.assertEqual(configured.execute_sql_timeout, 7.0)
        self.assertEqual(configured.ask_user_timeout, 42.5)
        for value in ("0", "-3", "soon"):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"USER_SIM_ASK_TIMEOUT_SECONDS": value}
            ):
                with self.assertRaises(ValueError):
                    Environment()

    async def test_ask_user_requires_a_non_empty_string_answer(self):
        for payload in ({"answer": "   "}, {"answer": 7}, {"answer": None}, {}, [], "text"):
            with self.subTest(payload=payload):
                environment = Environment(
                    httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    )
                )
                with self.assertRaises(GradingError):
                    await environment.ask_user("test-task", "Which?")

    async def test_http_failures_and_broken_responses_are_service_errors(self):
        failed = Environment(httpx.MockTransport(
            lambda request: httpx.Response(500, json={"error": {"message": "boom"}})
        ))
        with self.assertRaises(GradingError):
            await failed.execute_sql("test-task", "SELECT 1;")
        with self.assertRaises(GradingError):
            await failed.ask_user("test-task", "Which?")
        broken = Environment(
            httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json"))
        )
        with self.assertRaises(GradingError):
            await broken.execute_sql("test-task", "SELECT 1;")
        with self.assertRaises(GradingError):
            await broken.ask_user("test-task", "Which?")


class ToolLoopTestCase(unittest.TestCase):
    """Shared harness: scripted model actions and a mock official DB service."""

    def setUp(self):
        self.service_requests = []
        self.model_requests = []
        self.actions = []
        self.grader_payloads = []
        self.grader_payload = dict(GRADER_FAILURE)
        self.grader_status = 200
        self.transition_status = 200
        self.execute_payload = {"success": True, "result": EXECUTE_RESULT, "error": None}
        self.execute_status = 200
        self.ask_payload = {"answer": USER_ANSWER}
        self.ask_status = 200
        self.info_payloads = {}
        self.on_model_call = None
        self.on_service_call = None
        self.state = {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": 18, "initial_budget": 18,
        }
        self.client = TestClient(create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(transport=httpx.MockTransport(self.model_handler), api_key="test-key"),
            mode="tools",
            harness=getattr(self, "harness", None),
        ))

    def service_handler(self, request):
        path = request.url.path
        payload = json.loads(request.content)
        self.service_requests.append((path, payload))
        if self.on_service_call is not None:
            self.on_service_call(path)
        if path == "/submit":
            if self.grader_status != 200:
                return httpx.Response(self.grader_status, json={"error": {"message": "boom"}})
            replies = self.grader_payloads
            return httpx.Response(200, json=replies.pop(0) if replies else self.grader_payload)
        if path == "/phase_transition":
            if self.transition_status != 200:
                return httpx.Response(self.transition_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json={"status": "ok", "phase": 2})
        if path == "/execute":
            if self.execute_status != 200:
                return httpx.Response(self.execute_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=dict(self.execute_payload))
        if path == "/ask":
            if self.ask_status != 200:
                return httpx.Response(self.ask_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=dict(self.ask_payload))
        if path in self.info_payloads:
            return httpx.Response(200, json=self.info_payloads[path])
        official = {
            "/schema": {"schema": SCHEMA_TEXT},
            "/all_column_meanings": {"column_meanings": MEANINGS_TEXT},
            "/column_meaning": {"meaning": COLUMN_MEANING},
            "/knowledge_names": {"names": KNOWLEDGE_NAMES},
            "/knowledge": (
                {"knowledge": KNOWLEDGE_ENTRY} if payload.get("knowledge_name")
                else {"knowledge": KNOWLEDGE_TEXT}
            ),
        }
        return httpx.Response(200, json=official[path])

    def model_handler(self, request):
        self.model_requests.append(json.loads(request.content))
        if self.on_model_call is not None:
            self.on_model_call()
        if not self.actions:
            raise AssertionError("The loop asked for a model action the script does not provide")
        scripted = self.actions.pop(0)
        if isinstance(scripted, dict):
            return httpx.Response(200, json=model_reply(**scripted))
        return httpx.Response(200, json=model_reply(scripted))

    def initialize(self, reset: bool = True, state: dict | None = None):
        response = self.client.post("/init_session", json={
            "task_id": "test-task", "mode": "a-interact", "reset": reset,
            "state": dict(self.state if state is None else state),
        })
        self.assertEqual(response.status_code, 200)
        return response.json()

    def execute(self, message: str = "Answer the question."):
        return self.client.post("/run_session", json={
            "task_id": "test-task", "mode": "a-interact", "message": message,
        })

    def run_ok(self):
        self.initialize()
        response = self.execute()
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["state"]

    def paths(self):
        return [path for path, _ in self.service_requests]

    def trajectory(self, state):
        return [(entry["tool"], entry["cost"]) for entry in state["tool_trajectory"]]


class ToolLoopContractTests(ToolLoopTestCase):
    def test_health_reports_the_tool_selecting_agent(self):
        health = TestClient(create_app()).get("/health").json()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["service"], "system_agent")
        self.assertEqual(health["agent"], AGENT_NAME)
        self.assertEqual(health["mode"], "tools")
        self.assertEqual(health["model"], MODEL_ID)
        self.assertEqual(health["tools"], tool_metadata())
        self.assertEqual(
            health["harness"],
            {**load_harness().descriptor(), "sub_agents": []},
        )
        self.assertEqual(health["execute_sql_timeout_seconds"], EXECUTE_SQL_TIMEOUT_SECONDS)
        self.assertEqual(health["ask_user_timeout_seconds"], ASK_USER_TIMEOUT_SECONDS)

        with mock.patch.dict(os.environ, {"AGENT_MODE": "tools"}):
            self.assertEqual(TestClient(create_app()).get("/health").json()["mode"], "tools")
        with mock.patch.dict(os.environ, {"AGENT_MODE": ""}):
            self.assertEqual(TestClient(create_app()).get("/health").json()["mode"], "tools")

    def test_the_harness_is_recorded_in_every_session(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        descriptor = load_harness().descriptor()
        self.assertEqual(state["harness"], descriptor)
        # The runner copies adk_events into the result JSON, so each model
        # call carries the same identity without changing the event order.
        self.assertEqual(model_events(state)[0]["harness"], descriptor)

    def test_removed_modes_are_rejected_instead_of_silently_running_tools(self):
        for removed in ("fixed", "unknown"):
            with self.subTest(mode=removed), self.assertRaises(ValueError) as caught:
                create_app(mode=removed)
            self.assertIn(removed, str(caught.exception))
            self.assertIn("tools", str(caught.exception))
            with self.subTest(env=removed):
                with mock.patch.dict(os.environ, {"AGENT_MODE": removed}):
                    with self.assertRaises(ValueError):
                        create_app()

    def test_each_allowed_tool_uses_its_official_endpoint_and_price(self):
        execute_sql = "SELECT count(*) FROM customers;"
        question = "Which customers?"
        self.actions = [
            action("get_schema"),
            action("get_all_column_meanings"),
            action("get_column_meaning", table_name="customers", column_name="id"),
            action("get_all_external_knowledge_names"),
            action("get_knowledge_definition", knowledge_name="Active customer"),
            action("get_all_knowledge_definitions"),
            action("execute_sql", sql=execute_sql),
            action("ask_user", question=question),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), [
            "/schema", "/all_column_meanings", "/column_meaning", "/knowledge_names",
            "/knowledge", "/knowledge", "/execute", "/ask", "/submit",
        ])
        self.assertEqual(self.service_requests, [
            ("/schema", {"task_id": "test-task"}),
            ("/all_column_meanings", {"task_id": "test-task"}),
            ("/column_meaning", {"task_id": "test-task", "table_name": "customers",
                                 "column_name": "id"}),
            ("/knowledge_names", {"task_id": "test-task"}),
            ("/knowledge", {"task_id": "test-task", "knowledge_name": "Active customer"}),
            ("/knowledge", {"task_id": "test-task"}),
            ("/execute", {"task_id": "test-task", "sql": execute_sql}),
            ("/ask", {"task_id": "test-task", "question": question}),
            ("/submit", {"task_id": "test-task", "sql": "SELECT 1;"}),
        ])
        self.assertEqual(self.trajectory(state), [
            ("get_schema", 1.0), ("get_all_column_meanings", 1.0), ("get_column_meaning", 0.5),
            ("get_all_external_knowledge_names", 0.5), ("get_knowledge_definition", 0.5),
            ("get_all_knowledge_definitions", 1.0), ("execute_sql", 1.0), ("ask_user", 2.0),
            ("submit_sql", 3.0),
        ])
        budgets = [
            (entry["budget_before"], entry["budget_after"]) for entry in state["tool_trajectory"]
        ]
        self.assertEqual(budgets, [
            (18, 17), (17, 16), (16, 15.5), (15.5, 15), (15, 14.5), (14.5, 13.5),
            (13.5, 12.5), (12.5, 10.5), (10.5, 7.5),
        ])
        self.assertEqual(state["budget_remaining"], 7.5)
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertTrue(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["stop_reason"], "phase1_completed")
        self.assertEqual(state["last_submit"]["passed"], True)
        self.assertEqual(state["dialogue_history"], [
            {"role": "agent", "content": question, "phase": 1},
            {"role": "user", "content": USER_ANSWER, "phase": 1},
        ])
        self.assertEqual(state["tool_trajectory"][6]["result"], {
            "kind": "result", "success": True, "result": EXECUTE_RESULT, "error": None,
        })
        self.assertEqual(state["tool_trajectory"][7]["result"], {
            "kind": "answer", "answer": USER_ANSWER, "error": None,
        })

        self.assertEqual(len(self.model_requests), 9)
        for request in self.model_requests:
            self.assertEqual(request["model"], MODEL_ID)
            self.assertFalse(request["stream"])
            self.assertEqual(request["max_tokens"], MAX_OUTPUT_TOKENS)
            self.assertEqual([m["role"] for m in request["messages"]][:2], ["system", "user"])
        system = self.model_requests[0]["messages"][0]["content"]
        for name in TOOL_COSTS:
            self.assertIn(name, system)
        self.assertIn('{"action": "<tool name>", "args": {<tool arguments>}}', system)
        self.assertIn(str(MAX_MODEL_ACTIONS), system)
        self.assertIn("1500", system)
        self.assertIn("How many customers?", self.model_requests[0]["messages"][1]["content"])
        self.assertIn("Database: demo", self.model_requests[0]["messages"][1]["content"])

        observations = [m["content"] for m in self.model_requests[1]["messages"][-1:]]
        self.assertIn(SCHEMA_TEXT, observations[0])
        self.assertIn("[action 1 | phase 1 | get_schema | cost 1 | budget 17]", observations[0])
        execute_feedback = self.model_requests[7]["messages"][-1]["content"]
        self.assertIn(EXECUTE_RESULT, execute_feedback)
        self.assertIn(
            "[action 7 | phase 1 | execute_sql | cost 1 | budget 12.5]", execute_feedback
        )
        ask_feedback = self.model_requests[8]["messages"][-1]["content"]
        self.assertIn(USER_ANSWER, ask_feedback)
        self.assertIn("[action 8 | phase 1 | ask_user | cost 2 | budget 10.5]", ask_feedback)
        events = model_events(state)
        self.assertEqual(events[-1]["prompt"], self.model_requests[-1]["messages"])
        self.assertEqual([e["call_index"] for e in events], list(range(1, 10)))
        self.assertEqual([e["action"]["name"] for e in events], list(TOOL_COSTS))
        self.assertEqual(events[0]["settings"]["endpoint"], API_URL)
        self.assertEqual(events[0]["usage"]["total_tokens"], 14)
        self.assertNotIn("test-key", json.dumps(state))

    def test_consult_is_unknown_in_a_harness_without_sub_agents(self):
        self.actions = [
            action("consult", agent="reviewer", request="Check this."),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(self.paths(), ["/submit"])
        self.assertEqual(self.trajectory(state), [("submit_sql", 3.0)])
        self.assertEqual(state["budget_remaining"], 15)
        event = model_events(state)[0]
        self.assertIsNone(event["action"])
        self.assertIn("Unknown action 'consult'", event["action_error"])
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn("rejected", feedback)
        self.assertIn("| cost 0 |", feedback)
        self.assertNotIn(
            "subagent_call", [item["type"] for item in state["adk_events"]]
        )
        self.assertNotIn("consult", self.model_requests[0]["messages"][0]["content"])

    def test_invalid_new_tool_arguments_cost_nothing_and_call_nothing(self):
        self.actions = [
            '{"action": "execute_sql", "args": {"sql": "   "}}',
            '{"action": "ask_user", "args": {"question": " "}}',
            '{"action": "ask_user", "args": {"question": "ok", "task_id": "other"}}',
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/submit"])
        self.assertEqual(self.trajectory(state), [("submit_sql", 3.0)])
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual(state["dialogue_history"], [])
        for index in range(3):
            with self.subTest(index=index):
                event = model_events(state)[index]
                self.assertIsNone(event["action"])
                self.assertTrue(event["action_error"])
                observation = self.model_requests[index + 1]["messages"][-1]["content"]
                self.assertIn("rejected", observation)
                self.assertIn("| cost 0 |", observation)

    def test_failed_submission_feedback_retry_and_single_transition(self):
        self.actions = [
            action("submit_sql", sql="SELECT count(*) FROM customers;"),
            action("submit_sql", sql="SELECT count(id) FROM customers;"),
            action("submit_sql", sql="SELECT region, count(*) FROM customers GROUP BY region;"),
        ]
        self.grader_payloads = [
            dict(GRADER_FAILURE), dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS),
        ]
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/submit", "/submit", "/phase_transition", "/submit"])
        self.assertEqual(state["total_reward"], 1.0)
        self.assertTrue(state["phase1_completed"])
        self.assertTrue(state["phase2_completed"])
        self.assertTrue(state["task_done"])
        self.assertEqual(state["stop_reason"], "phase2_completed")
        self.assertEqual(state["follow_up_query"], "And by region?")
        self.assertEqual(state["final_response"], PHASE2_PASS["message"])
        self.assertEqual(
            [entry["phase"] for entry in state["tool_trajectory"]], [1, 1, 2]
        )
        self.assertEqual(state["budget_remaining"], 9)
        self.assertGreaterEqual(state["session_elapsed_seconds"], 0)

        failure_feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn("SQL failed Phase 1", failure_feedback)
        phase2_prompt = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn("Phase 1 correct!", phase2_prompt)
        self.assertIn("And by region?", phase2_prompt)
        self.assertIn("applied to the database", phase2_prompt)
        self.assertEqual([e["phase"] for e in model_events(state)], [1, 1, 2])
        self.assertEqual(model_events(state)[1]["action"]["args"]["sql"],
                         "SELECT count(id) FROM customers;")

    def test_same_sql_is_submitted_and_charged_every_time(self):
        first = "SELECT count(*) FROM customers;"
        second = "SELECT count(id) FROM customers;"
        self.actions = [
            action("submit_sql", sql=first),
            action("submit_sql", sql=f"  {first}  "),
            action("submit_sql", sql=second),
        ]
        self.grader_payloads = [
            dict(GRADER_FAILURE), dict(GRADER_FAILURE), dict(PHASE1_FINAL),
        ]
        state = self.run_ok()

        self.assertEqual(
            [payload["sql"] for path, payload in self.service_requests if path == "/submit"],
            [first, first, second],
        )
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["task_done"])
        self.assertEqual(state["stop_reason"], "phase1_completed")
        self.assertEqual(
            self.trajectory(state),
            [("submit_sql", 3.0), ("submit_sql", 3.0), ("submit_sql", 3.0)],
        )
        self.assertEqual(
            [entry["budget_after"] for entry in state["tool_trajectory"]], [15, 12, 9]
        )
        for entry in state["tool_trajectory"]:
            self.assertNotIn("blocked", entry)
            self.assertNotIn("duplicate_sql", entry)
        self.assertEqual(state["submit_attempts"], [
            {"phase": 1, "sql": first},
            {"phase": 1, "sql": first},
            {"phase": 1, "sql": second},
        ])

    def test_the_same_sql_is_submitted_again_in_phase_two(self):
        sql = "SELECT 1;"
        self.actions = [
            action("submit_sql", sql=sql),
            action("submit_sql", sql=sql),
        ]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        state = self.run_ok()

        self.assertEqual(
            [payload["sql"] for path, payload in self.service_requests if path == "/submit"],
            [sql, sql],
        )
        self.assertEqual(state["total_reward"], 1.0)
        self.assertEqual(state["stop_reason"], "phase2_completed")
        self.assertEqual(
            [entry["phase"] for entry in state["tool_trajectory"]], [1, 2]
        )
        self.assertEqual(
            [entry["cost"] for entry in state["tool_trajectory"]], [3.0, 3.0]
        )
        self.assertEqual(state["budget_remaining"], 12)
        self.assertNotIn("blocked", state["tool_trajectory"][1])
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertNotIn("already submitted", feedback)

    def test_repeated_information_lookup_is_sent_and_charged_again(self):
        self.actions = [action("get_schema"), action("get_schema"),
                        action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/schema", "/submit"])
        self.assertEqual(
            self.trajectory(state), [("get_schema", 1.0), ("get_schema", 1.0), ("submit_sql", 3.0)]
        )
        repeated = state["tool_trajectory"][1]
        self.assertNotIn("cached", repeated)
        self.assertEqual(repeated["result"], SCHEMA_TEXT)
        self.assertEqual(state["budget_remaining"], 13)
        feedback = self.model_requests[2]["messages"][-1]["content"]
        self.assertNotIn("Served from the session cache", feedback)
        self.assertIn(SCHEMA_TEXT, feedback)

    def test_unusable_model_responses_are_fed_back_without_charging(self):
        cases = {
            "prose": "I will look at the schema now.",
            "empty": {"content": "", "finish_reason": "stop"},
            "truncated": {"content": '{"action": "get_sche', "finish_reason": "length"},
            "no action member": '{"args": {"sql": "SELECT 1;"}}',
            "missing argument": '{"action": "get_column_meaning", "args": {"table_name": "t"}}',
            "unknown argument": '{"action": "get_schema", "args": {"limit": 3}}',
            "unknown action": '{"action": "run_sql", "args": {"sql": "SELECT 1;"}}',
        }
        for name, scripted in cases.items():
            with self.subTest(name=name):
                self.setUp()
                self.actions = [scripted, action("submit_sql", sql="SELECT 1;")]
                self.grader_payload = dict(PHASE1_FINAL)
                state = self.run_ok()

                self.assertEqual(self.paths(), ["/submit"])
                self.assertEqual(self.trajectory(state), [("submit_sql", 3.0)])
                self.assertEqual(state["budget_remaining"], 15)
                event = model_events(state)[0]
                self.assertIsNone(event["action"])
                self.assertTrue(event["action_error"])
                if name == "truncated":
                    self.assertTrue(event["truncated"])
                    self.assertIn("output token limit", event["action_error"])
                feedback = self.model_requests[1]["messages"][-1]["content"]
                self.assertIn("rejected", feedback)
                self.assertIn("| cost 0 |", feedback)

    def test_budget_below_the_submission_price_stops_without_any_call(self):
        for budget in (0, 1, 2.5):
            with self.subTest(budget=budget):
                self.setUp()
                self.state["budget_remaining"] = budget
                state = self.run_ok()
                self.assertEqual(self.service_requests, [])
                self.assertEqual(self.model_requests, [])
                self.assertEqual(state["budget_remaining"], budget)
                self.assertEqual(state["stop_reason"], "budget_exhausted_before_submit")
                self.assertTrue(state["loop_stopped"])
                self.assertFalse(state["task_done"])
                self.assertIn("submission costs 3", state["final_response"])
                self.assertIn("Phase 1 is incomplete", state["final_response"])
                self.assertEqual(self.execute().json()["state"], state)

    def test_a_budget_of_three_submits_without_exploration(self):
        self.state["budget_remaining"] = 3
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()
        self.assertEqual(self.paths(), ["/submit"])
        self.assertEqual(state["budget_remaining"], 0)
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["task_done"])

    def test_budget_spent_below_the_submission_price_stops_before_the_next_action(self):
        self.state["budget_remaining"] = 4
        self.actions = [action("get_schema"), action("get_all_column_meanings")]
        state = self.run_ok()
        self.assertEqual(self.paths(), ["/schema", "/all_column_meanings"])
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(state["budget_remaining"], 2)
        self.assertEqual(state["stop_reason"], "budget_exhausted_before_submit")
        self.assertFalse(state["task_done"])

    def test_action_limit_stops_the_session(self):
        self.state["budget_remaining"] = 60
        self.actions = [action("get_schema")] * MAX_MODEL_ACTIONS
        state = self.run_ok()
        self.assertEqual(len(self.model_requests), MAX_MODEL_ACTIONS)
        self.assertEqual(self.paths(), ["/schema"] * MAX_MODEL_ACTIONS)
        self.assertEqual(state["budget_remaining"], 60 - MAX_MODEL_ACTIONS)
        self.assertEqual(state["stop_reason"], "action_limit_reached")
        self.assertIn(str(MAX_MODEL_ACTIONS), state["final_response"])
        self.assertIn("Phase 1 is incomplete", state["final_response"])

    def test_session_time_limit_after_an_action_stops_before_the_next_model_call(self):
        clock = FakeClock()
        self.actions = [action("get_schema"), action("get_schema")]
        self.on_service_call = lambda path: setattr(clock, "value", 1600.0)
        with mock.patch.object(tool_loop, "_now", clock):
            state = self.run_ok()
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(self.paths(), ["/schema"])
        self.assertEqual(state["budget_remaining"], 17)
        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertIn("1500-second session limit", state["final_response"])
        self.assertIn("Phase 1 is incomplete", state["final_response"])

    def test_session_time_limit_after_the_model_response_blocks_the_action(self):
        clock = FakeClock()
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        self.on_model_call = lambda: setattr(clock, "value", 1501.0)
        with mock.patch.object(tool_loop, "_now", clock):
            state = self.run_ok()
        self.assertEqual(self.service_requests, [])
        self.assertEqual(state["budget_remaining"], 18)
        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertIn("was reached after the model response", state["final_response"])
        self.assertFalse(state["task_done"])
        self.assertFalse(state["phase1_completed"])
        self.assertEqual(len(model_events(state)), 1)
        self.assertNotIn("action", model_events(state)[0])
        self.assertEqual(state["session_elapsed_seconds"], 1501.0)

    def test_state_beyond_the_allowed_inputs_never_reaches_the_model(self):
        self.state["internal_note"] = "do-not-send"
        self.state["hidden_answer"] = "do-not-send-either"
        self.state["dialogue_history"] = [{"role": "user", "content": "do-not-send-either"}]
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()
        sent = json.dumps(self.model_requests)
        self.assertNotIn("do-not-send", sent)
        self.assertNotIn("hidden_answer", sent)
        self.assertEqual(state["dialogue_history"], [{"role": "user", "content": "do-not-send-either"}])

    def test_repeated_run_returns_the_cached_outcome_without_extra_calls(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        self.initialize()
        first = self.execute().json()
        second = self.execute().json()
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(first["response"], second["response"])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(self.paths(), ["/submit"])

    def test_reset_starts_a_new_session_and_reset_false_reuses_it(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        first = self.initialize()
        self.execute()
        reused = self.initialize(reset=False)
        self.assertEqual(first["session_id"], reused["session_id"])
        self.execute()
        self.assertEqual(len(self.model_requests), 1)
        fresh = self.initialize(reset=True)
        self.assertNotEqual(first["session_id"], fresh["session_id"])
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.execute()
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(self.paths(), ["/submit", "/submit"])

    def test_run_before_init_session_is_not_found(self):
        response = self.execute()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.model_requests, [])
        self.assertEqual(self.service_requests, [])

    def test_missing_question_is_an_input_error_without_any_request(self):
        self.initialize(state={"task_id": "test-task", "db_name": "demo", "budget_remaining": 18})
        response = self.execute(message="")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.model_requests, [])
        self.assertEqual(self.service_requests, [])


class ExploreAndAskTests(ToolLoopTestCase):
    """execute_sql and ask_user observations, prices, caching and dialogue."""

    def test_execute_sql_result_feeds_the_next_action_and_scores_nothing(self):
        sql = "SELECT count(*) FROM customers;"
        self.actions = [action("execute_sql", sql=sql), action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.service_requests, [
            ("/execute", {"task_id": "test-task", "sql": sql}),
            ("/submit", {"task_id": "test-task", "sql": "SELECT 1;"}),
        ])
        self.assertEqual(state["total_reward"], 0.7)
        self.assertEqual(state["budget_remaining"], 14)
        self.assertEqual(
            [(entry["cost"], entry["budget_before"], entry["budget_after"])
             for entry in state["tool_trajectory"]],
            [(1.0, 18, 17), (3.0, 17, 14)],
        )
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["phase"], 1)
        self.assertEqual(entry["args"], {"sql": sql})
        self.assertEqual(entry["result"], {
            "kind": "result", "success": True, "result": EXECUTE_RESULT, "error": None,
        })
        self.assertGreaterEqual(entry["elapsed_seconds"], 0)
        self.assertNotIn("failure", state)
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn(EXECUTE_RESULT, feedback)
        self.assertIn("never changes the score", self.model_requests[0]["messages"][0]["content"])

    def test_official_sql_error_is_an_observation_without_a_refund(self):
        self.execute_payload = {"success": False, "result": "", "error": EXECUTE_ERROR}
        self.actions = [action("execute_sql", sql="SELECT nope;"),
                        action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertNotIn("failure", state)
        self.assertEqual(state["budget_remaining"], 14)
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["result"]["kind"], "sql_error")
        self.assertEqual(entry["result"]["success"], False)
        self.assertEqual(entry["result"]["error"], EXECUTE_ERROR)
        self.assertEqual(entry["cost"], 1.0)
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn(EXECUTE_ERROR, feedback)
        self.assertIn("nothing was submitted", feedback)

    def test_repeated_execute_sql_and_ask_user_are_sent_and_charged_again(self):
        sql = "SELECT count(*) FROM customers;"
        question = "Which customers?"
        self.actions = [
            action("execute_sql", sql=sql),
            action("execute_sql", sql=sql),
            action("ask_user", question=question),
            action("ask_user", question=question),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/execute", "/execute", "/ask", "/ask", "/submit"])
        self.assertEqual(state["budget_remaining"], 9)
        self.assertEqual(
            [entry["cost"] for entry in state["tool_trajectory"]],
            [1.0, 1.0, 2.0, 2.0, 3.0],
        )
        self.assertNotIn("cached", state["tool_trajectory"][1])
        self.assertNotIn("cached", state["tool_trajectory"][3])
        self.assertEqual(state["dialogue_history"], [
            {"role": "agent", "content": question, "phase": 1},
            {"role": "user", "content": USER_ANSWER, "phase": 1},
            {"role": "agent", "content": question, "phase": 1},
            {"role": "user", "content": USER_ANSWER, "phase": 1},
        ])

    def test_phase2_question_reaches_the_same_simulator_session(self):
        question = "Should the follow-up include empty regions?"
        self.actions = [
            action("submit_sql", sql="SELECT count(*) FROM customers;"),
            action("submit_sql", sql="SELECT count(id) FROM customers;"),
            action("ask_user", question=question),
            action("submit_sql", sql="SELECT region, count(*) FROM customers GROUP BY region;"),
        ]
        self.grader_payloads = [
            dict(GRADER_FAILURE), dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS),
        ]
        state = self.run_ok()

        self.assertEqual(
            self.paths(), ["/submit", "/submit", "/phase_transition", "/ask", "/submit"]
        )
        self.assertEqual(self.service_requests[3], (
            "/ask", {"task_id": "test-task", "question": question},
        ))
        self.assertEqual(state["current_phase"], 2)
        self.assertEqual(state["dialogue_history"], [
            {"role": "agent", "content": question, "phase": 2},
            {"role": "user", "content": USER_ANSWER, "phase": 2},
        ])
        self.assertEqual(state["tool_trajectory"][3]["phase"], 2)
        feedback = self.model_requests[3]["messages"][-1]["content"]
        self.assertIn(USER_ANSWER, feedback)
        self.assertIn("[action 3 | phase 2 | ask_user | cost 2 |", feedback)
        self.assertTrue(state["task_done"])


REVIEWER_SYSTEM_PROMPT = "You review SQL. Only report problems."


class ConsultTests(ToolLoopTestCase):
    """The local consult action: harness-defined, free and never an official call."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name) / "consult-harness"
        (root / "sub_agents").mkdir(parents=True)
        (root / "system.md").write_text(
            "Consult the reviewer before submitting.\n", encoding="utf-8"
        )
        (root / "sub_agents" / "reviewer.md").write_text(
            "---\n"
            "description: Reviews candidate SQL.\n"
            "context: task, dialogue, observations\n"
            "---\n"
            f"{REVIEWER_SYSTEM_PROMPT}\n",
            encoding="utf-8",
        )
        (root / "sub_agents" / "task_only.md").write_text(
            "---\n"
            "description: Looks only at the task.\n"
            "context: task\n"
            "---\n"
            "Look only at the task.\n",
            encoding="utf-8",
        )
        (root / "sub_agents" / "dialogue_only.md").write_text(
            "---\n"
            "description: Looks only at the dialogue.\n"
            "context: dialogue\n"
            "---\n"
            "Look only at the dialogue.\n",
            encoding="utf-8",
        )
        (root / "sub_agents" / "observations_only.md").write_text(
            "---\n"
            "description: Looks only at the observations.\n"
            "context: observations\n"
            "---\n"
            "Look only at the observations.\n",
            encoding="utf-8",
        )
        self.harness = load_harness(root)
        self.fail_subagent = False
        super().setUp()

    def model_handler(self, request):
        body = json.loads(request.content)
        system = body["messages"][0]
        if (
            self.fail_subagent
            and system["role"] == "system"
            and system["content"] == REVIEWER_SYSTEM_PROMPT
        ):
            self.model_requests.append(body)
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return super().model_handler(request)

    def subagent_events(self, state):
        return [event for event in state["adk_events"] if event["type"] == "subagent_call"]

    def test_consult_sends_the_selected_context_and_returns_an_observation(self):
        answer = "Problem 1: the query does not filter by region."
        self.state["internal_note"] = "do-not-send"
        self.state["hidden_answer"] = "do-not-send-either"
        self.state["dialogue_history"] = [
            {"role": "agent", "content": "Which region?", "phase": 1},
            {"role": "user", "content": "The west region.", "phase": 1},
        ]
        execute = action("execute_sql", sql="SELECT 1;")
        request = "Check SELECT count(*) FROM customers;"
        self.actions = [
            execute,
            action("consult", agent="reviewer", request=request),
            answer,
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        # One main call per action, plus exactly one sub-agent call.
        self.assertEqual(len(self.model_requests), 4)
        subagent_call = self.model_requests[2]
        self.assertEqual(subagent_call["messages"][0], {
            "role": "system", "content": REVIEWER_SYSTEM_PROMPT,
        })
        context = subagent_call["messages"][1]["content"]
        self.assertTrue(context.startswith("Task:\n"))
        self.assertIn("Database: demo", context)
        self.assertIn("Phase: 1", context)
        self.assertIn("Phase 1 question:\nHow many customers?", context)
        self.assertNotIn("Follow-up question:", context)
        self.assertIn("Dialogue:\nAgent: Which region?\nUser: The west region.", context)
        self.assertIn(f"Observations:\nAssistant action:\n{execute}", context)
        self.assertIn(
            "Observation:\n[action 1 | phase 1 | execute_sql | cost 1 | budget 17]", context
        )
        self.assertIn(f"Request:\n{request}", context)
        self.assertNotIn("submit_sql", context)
        self.assertNotIn("do-not-send", context)

        # The answer reaches the next main call as the consult observation.
        feedback = self.model_requests[3]["messages"][-1]["content"]
        self.assertIn(answer, feedback)
        self.assertIn("[action 2 | phase 1 | consult | cost 0 | budget 17]", feedback)

        # consult is free, recorded, and never reaches an official service.
        self.assertEqual(self.paths(), ["/execute", "/submit"])
        self.assertEqual(
            self.trajectory(state),
            [("execute_sql", 1.0), ("consult", 0.0), ("submit_sql", 3.0)],
        )
        consult = state["tool_trajectory"][1]
        self.assertEqual(consult["args"], {"agent": "reviewer", "request": request})
        self.assertEqual((consult["budget_before"], consult["budget_after"]), (17, 17))
        self.assertEqual(consult["phase"], 1)
        self.assertGreaterEqual(consult["elapsed_seconds"], 0)
        self.assertEqual(consult["result"], {
            "kind": "answer", "content": answer, "error": None, "truncated": False,
        })
        self.assertEqual(state["budget_remaining"], 14)

        events = self.subagent_events(state)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["agent"], "reviewer")
        self.assertEqual(event["prompt"], subagent_call["messages"])
        self.assertEqual(event["content"], answer)
        self.assertFalse(event["truncated"])
        self.assertEqual(event["usage"], {
            "prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14,
        })
        self.assertEqual(event["harness"], state["harness"])
        self.assertEqual(tool_loop._model_actions(state), 3)

    def test_only_the_configured_context_items_are_sent(self):
        self.state["dialogue_history"] = [
            {"role": "agent", "content": "Which region?", "phase": 1},
            {"role": "user", "content": "The west region.", "phase": 1},
        ]
        self.actions = [
            action("get_schema"),
            action("consult", agent="task_only", request="Check the question."),
            "No problems found.",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        context = self.model_requests[2]["messages"][1]["content"]
        self.assertTrue(context.startswith("Task:\n"))
        self.assertNotIn("Dialogue:", context)
        self.assertNotIn("Observations:", context)
        self.assertNotIn("Which region?", context)
        self.assertIn("Request:\nCheck the question.", context)
        self.assertEqual(self.paths(), ["/schema", "/submit"])
        self.assertEqual(state["budget_remaining"], 14)

    def test_each_context_item_can_be_included_or_left_out(self):
        self.state["dialogue_history"] = [
            {"role": "agent", "content": "Which region?", "phase": 1},
            {"role": "user", "content": "The west region.", "phase": 1},
        ]
        self.actions = [
            action("get_schema"),
            action("consult", agent="dialogue_only", request="Check the dialogue."),
            "Dialogue looks consistent.",
            action("consult", agent="observations_only", request="Check the observations."),
            "Observations look consistent.",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        dialogue_only = self.model_requests[2]["messages"][1]["content"]
        self.assertIn(
            "Dialogue:\nAgent: Which region?\nUser: The west region.", dialogue_only
        )
        self.assertNotIn("Task:", dialogue_only)
        self.assertNotIn("Observations:", dialogue_only)
        self.assertIn("Request:\nCheck the dialogue.", dialogue_only)

        observations_only = self.model_requests[4]["messages"][1]["content"]
        self.assertIn(
            f"Observations:\nAssistant action:\n{action('get_schema')}", observations_only
        )
        self.assertIn(
            "Observation:\n[action 1 | phase 1 | get_schema | cost 1 | budget 17]",
            observations_only,
        )
        # The earlier consult answer is an observation too.
        self.assertIn("Dialogue looks consistent.", observations_only)
        self.assertNotIn("Task:", observations_only)
        self.assertNotIn("Dialogue:", observations_only)
        self.assertIn("Request:\nCheck the observations.", observations_only)
        self.assertEqual(self.paths(), ["/schema", "/submit"])
        self.assertEqual(state["budget_remaining"], 14)

    def test_the_phase_two_follow_up_is_part_of_the_task_context(self):
        self.state["current_phase"] = 2
        self.state["follow_up_query"] = "And by region?"
        self.state["phase1_completed"] = True
        self.actions = [
            action("consult", agent="task_only", request="Check the follow-up."),
            "No problems found.",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE2_PASS)
        self.run_ok()

        context = self.model_requests[1]["messages"][1]["content"]
        self.assertIn("Phase: 2", context)
        self.assertIn("Phase 1 question:\nHow many customers?", context)
        self.assertIn("Follow-up question:\nAnd by region?", context)

    def test_an_unknown_sub_agent_is_rejected_without_a_model_call(self):
        self.actions = [
            action("consult", agent="missing", request="Check this."),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(self.paths(), ["/submit"])
        event = model_events(state)[0]
        self.assertIsNone(event["action"])
        self.assertIn("Unknown sub-agent 'missing'", event["action_error"])
        self.assertIn("reviewer", event["action_error"])
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn("rejected", feedback)
        self.assertIn("| cost 0 |", feedback)
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual(state["tool_trajectory"][0]["tool"], "submit_sql")
        self.assertEqual(self.subagent_events(state), [])

    def test_invalid_consult_arguments_are_rejected_before_any_call(self):
        self.actions = [
            '{"action": "consult", "args": {"agent": "reviewer"}}',
            '{"action": "consult", "args": {"agent": "reviewer", "request": " "}}',
            '{"action": "consult", "args": {"agent": "reviewer", "request": "x", "extra": "y"}}',
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(len(self.model_requests), 4)
        self.assertEqual(self.paths(), ["/submit"])
        for index in range(3):
            with self.subTest(index=index):
                event = model_events(state)[index]
                self.assertIsNone(event["action"])
                self.assertTrue(event["action_error"])
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual(self.subagent_events(state), [])

    def test_a_sub_agent_model_failure_keeps_the_session_alive(self):
        self.fail_subagent = True
        self.actions = [
            action("consult", agent="reviewer", request="Check this."),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        # One consult call, the failed retries, then the next main action.
        self.assertEqual(len(self.model_requests), MAX_RETRIES + 3)
        self.assertEqual(self.paths(), ["/submit"])
        self.assertEqual(
            self.trajectory(state), [("consult", 0.0), ("submit_sql", 3.0)]
        )
        consult = state["tool_trajectory"][0]
        self.assertEqual(consult["result"]["kind"], "model_error")
        self.assertIn(f"after {MAX_RETRIES + 1} attempts", consult["result"]["error"])
        self.assertNotIn("failure", state)
        self.assertEqual(state["budget_remaining"], 15)

        feedback = self.model_requests[-1]["messages"][-1]["content"]
        self.assertIn("The sub-agent 'reviewer' call failed", feedback)
        self.assertIn("does not affect the budget or the score", feedback)
        self.assertIn("| cost 0 |", feedback)
        events = state["adk_events"]
        self.assertEqual(
            [event["type"] for event in events],
            ["model_call", "subagent_call", "model_call"],
        )
        self.assertEqual(events[1]["content"], "")
        self.assertIn("error", events[1])
        self.assertEqual(tool_loop._model_actions(state), 2)

    def test_a_truncated_sub_agent_answer_is_announced(self):
        self.actions = [
            action("consult", agent="reviewer", request="Check this."),
            {"content": "partial review", "finish_reason": "length"},
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        feedback = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn("partial review", feedback)
        self.assertIn("cut off by the output token limit", feedback)
        consult = state["tool_trajectory"][0]
        self.assertTrue(consult["result"]["truncated"])
        self.assertEqual(consult["result"]["content"], "partial review")
        self.assertTrue(self.subagent_events(state)[0]["truncated"])

    def test_consult_counts_one_model_action_and_the_sub_agent_call_none(self):
        self.actions = []
        for _ in range(MAX_MODEL_ACTIONS):
            self.actions.append(
                action("consult", agent="reviewer", request="Check this.")
            )
            self.actions.append("No problems found.")
        state = self.run_ok()

        self.assertEqual(tool_loop._model_actions(state), MAX_MODEL_ACTIONS)
        self.assertEqual(len(self.subagent_events(state)), MAX_MODEL_ACTIONS)
        self.assertEqual(len(self.model_requests), MAX_MODEL_ACTIONS * 2)
        self.assertEqual(state["stop_reason"], "action_limit_reached")
        self.assertEqual(state["budget_remaining"], 18)

    def test_the_session_time_limit_ends_a_slow_sub_agent_call(self):
        self.actions = [action("consult", agent="reviewer", request="Check this.")]

        async def handler(request):
            body = json.loads(request.content)
            self.model_requests.append(body)
            system = body["messages"][0]
            if system["role"] == "system" and system["content"] == REVIEWER_SYSTEM_PROMPT:
                await asyncio.sleep(2.0)
            return httpx.Response(200, json=model_reply(self.actions.pop(0)))

        client = TestClient(create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(transport=httpx.MockTransport(handler), api_key="test-key"),
            mode="tools",
            harness=self.harness,
        ))
        with mock.patch.object(tool_loop, "SESSION_TIME_LIMIT_SECONDS", 1.0):
            initialized = client.post("/init_session", json={
                "task_id": "test-task", "mode": "a-interact", "reset": True,
                "state": dict(self.state),
            })
            self.assertEqual(initialized.status_code, 200)
            response = client.post("/run_session", json={
                "task_id": "test-task", "mode": "a-interact", "message": "Answer.",
            })
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()["state"]

        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertIn("session limit", state["final_response"])
        self.assertEqual(self.service_requests, [])
        self.assertEqual(state["budget_remaining"], 18)
        consult = state["tool_trajectory"][0]
        self.assertEqual(consult["tool"], "consult")
        self.assertTrue(consult["timed_out"])
        self.assertEqual(consult["result"]["kind"], "cancelled")
        self.assertEqual(consult["cost"], 0.0)
        self.assertNotIn("failure", state)
        self.assertEqual(self.subagent_events(state)[0]["type"], "subagent_call")


FETCHED_SCHEMA_RAW = "RAW-SCHEMA-MARKER\n" + SCHEMA_TEXT
SCHEMA_FINDER_PROMPT = "You extract schema facts. Copy the fetched text."
REVIEWER_PROMPT = "You review answers."


def fetch_harness(root: Path) -> Path:
    (root / "sub_agents").mkdir(parents=True)
    (root / "system.md").write_text("Ask the finders for extracts.\n", encoding="utf-8")
    (root / "sub_agents" / "schema_finder.md").write_text(
        "---\n"
        "description: Extracts schema facts.\n"
        "context: task, dialogue\n"
        "fetch: get_schema\n"
        "---\n"
        f"{SCHEMA_FINDER_PROMPT}\n",
        encoding="utf-8",
    )
    (root / "sub_agents" / "reviewer.md").write_text(
        "---\n"
        "description: Reviews an answer.\n"
        "context: task, dialogue, observations\n"
        "---\n"
        f"{REVIEWER_PROMPT}\n",
        encoding="utf-8",
    )
    return root


class FetchConsultTests(ToolLoopTestCase):
    """A consult whose definition names one argument-free official lookup."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = load_harness(fetch_harness(Path(self._tmp.name) / "fetch-harness"))
        super().setUp()

    def subagent_events(self, state):
        return [event for event in state["adk_events"] if event["type"] == "subagent_call"]

    def test_one_consult_fetches_once_charges_once_and_returns_only_the_answer(self):
        self.info_payloads["/schema"] = {"schema": FETCHED_SCHEMA_RAW}
        answer = "Tables: customers."
        request = "Extract the customers table."
        self.actions = [
            action("consult", agent="schema_finder", request=request),
            answer,
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.service_requests, [
            ("/schema", {"task_id": "test-task"}),
            ("/submit", {"task_id": "test-task", "sql": "SELECT 1;"}),
        ])
        # One main call, exactly one sub-agent call, then the next main call.
        self.assertEqual(len(self.model_requests), 3)
        sub_call = self.model_requests[1]
        self.assertEqual(sub_call["messages"][0], {
            "role": "system", "content": SCHEMA_FINDER_PROMPT,
        })
        context = sub_call["messages"][1]["content"]
        self.assertIn(f"Fetched get_schema:\n{FETCHED_SCHEMA_RAW}", context)
        self.assertLess(
            context.index("Fetched get_schema:"), context.index(f"Request:\n{request}")
        )
        self.assertIn("Task:", context)
        self.assertIn("Dialogue:", context)
        self.assertNotIn("Observations:", context)

        trajectory = state["tool_trajectory"]
        self.assertEqual(
            [entry["tool"] for entry in trajectory], ["get_schema", "consult", "submit_sql"]
        )
        fetch = trajectory[0]
        self.assertEqual(fetch["args"], {})
        self.assertEqual(fetch["result"], FETCHED_SCHEMA_RAW)
        self.assertEqual(fetch["cost"], 1.0)
        self.assertEqual((fetch["budget_before"], fetch["budget_after"]), (18, 17))
        self.assertEqual(fetch["phase"], 1)
        self.assertGreaterEqual(fetch["elapsed_seconds"], 0)
        self.assertEqual(fetch["requested_by"], {"action": "consult", "agent": "schema_finder"})
        consult = trajectory[1]
        self.assertEqual(consult["cost"], 0.0)
        self.assertEqual(consult["fetch"], "get_schema")
        self.assertEqual((consult["budget_before"], consult["budget_after"]), (17, 17))
        self.assertEqual(consult["result"]["content"], answer)
        self.assertEqual(state["budget_remaining"], 14)

        feedback = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn(f"Sub-agent 'schema_finder' answer:\n{answer}", feedback)
        self.assertIn("get_schema was fetched for the sub-agent (cost 1).", feedback)
        self.assertIn("[action 1 | phase 1 | consult | cost 1 | budget 17]", feedback)

        events = self.subagent_events(state)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["fetch"], {"tool": "get_schema", "cost": 1.0})
        self.assertEqual(events[0]["prompt"], sub_call["messages"])

    def test_two_consults_fetch_and_charge_twice(self):
        self.info_payloads["/schema"] = {"schema": FETCHED_SCHEMA_RAW}
        self.actions = [
            action("consult", agent="schema_finder", request="First."),
            "First answer.",
            action("consult", agent="schema_finder", request="Second."),
            "Second answer.",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/schema", "/submit"])
        self.assertEqual(
            [entry["tool"] for entry in state["tool_trajectory"]],
            ["get_schema", "consult", "get_schema", "consult", "submit_sql"],
        )
        self.assertEqual(
            [entry["cost"] for entry in state["tool_trajectory"]],
            [1.0, 0.0, 1.0, 0.0, 3.0],
        )
        self.assertEqual(state["budget_remaining"], 13)
        self.assertEqual(len(self.subagent_events(state)), 2)
        self.assertNotIn("cached", state["tool_trajectory"][2])
        self.assertEqual(state["tool_trajectory"][2]["result"], FETCHED_SCHEMA_RAW)

    def test_the_raw_output_never_enters_the_main_dialogue_or_another_sub_agent(self):
        self.info_payloads["/schema"] = {"schema": FETCHED_SCHEMA_RAW}
        self.actions = [
            action("consult", agent="schema_finder", request="Extract."),
            "SCHEMA-ANSWER",
            action("consult", agent="reviewer", request="Check."),
            "REVIEW-ANSWER",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        # The main call after the consult sees the answer and the fetch notice only.
        next_main = self.model_requests[2]
        self.assertNotIn(
            "RAW-SCHEMA-MARKER", json.dumps(next_main, ensure_ascii=False)
        )
        self.assertIn("SCHEMA-ANSWER", next_main["messages"][-1]["content"])
        self.assertIn(
            "get_schema was fetched for the sub-agent (cost 1).",
            next_main["messages"][-1]["content"],
        )

        # The observations context of another sub-agent is built from the main
        # dialogue, so the raw output is absent there too.
        other = self.model_requests[3]["messages"][1]["content"]
        self.assertNotIn("RAW-SCHEMA-MARKER", other)
        self.assertIn("SCHEMA-ANSWER", other)
        self.assertIn("get_schema was fetched for the sub-agent (cost 1).", other)
        self.assertIn("Request:\nCheck.", other)

        # The replayed prompt of the next main call is the same dialogue.
        self.assertEqual(model_events(state)[1]["prompt"], next_main["messages"])
        for event in model_events(state):
            self.assertNotIn("RAW-SCHEMA-MARKER", json.dumps(event["prompt"]))
        # The sub-agent event keeps the prompt it actually sent, raw output included.
        self.assertIn(
            "RAW-SCHEMA-MARKER",
            self.subagent_events(state)[0]["prompt"][1]["content"],
        )

    def test_a_phase_two_consult_fetches_the_current_state_once(self):
        self.state["current_phase"] = 2
        self.state["phase1_completed"] = True
        self.state["follow_up_query"] = "And by region?"
        self.info_payloads["/schema"] = {"schema": FETCHED_SCHEMA_RAW}
        self.actions = [
            action("consult", agent="schema_finder", request="Extract for the follow-up."),
            "Tables: customers.",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE2_PASS)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/submit"])
        fetch = state["tool_trajectory"][0]
        self.assertEqual(fetch["tool"], "get_schema")
        self.assertEqual(fetch["phase"], 2)
        self.assertEqual(fetch["cost"], 1.0)
        sub_context = self.model_requests[1]["messages"][1]["content"]
        self.assertIn("Phase: 2", sub_context)
        self.assertIn("Follow-up question:\nAnd by region?", sub_context)
        self.assertIn(f"Fetched get_schema:\n{FETCHED_SCHEMA_RAW}", sub_context)

    def test_a_consult_without_fetch_keeps_the_task_013_record(self):
        self.actions = [
            action("consult", agent="reviewer", request="Check this."),
            "No problems found.",
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/submit"])
        consult = state["tool_trajectory"][0]
        self.assertEqual(consult["tool"], "consult")
        self.assertEqual(consult["cost"], 0.0)
        self.assertNotIn("fetch", consult)
        self.assertEqual(consult["result"], {
            "kind": "answer", "content": "No problems found.", "error": None,
            "truncated": False,
        })
        self.assertNotIn("fetch", self.subagent_events(state)[0])
        sub_context = self.model_requests[1]["messages"][1]["content"]
        self.assertNotIn("Fetched ", sub_context)
        feedback = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn("[action 1 | phase 1 | consult | cost 0 | budget 18]", feedback)
        self.assertNotIn("was fetched", feedback)


class FetchConsultFailureTests(unittest.IsolatedAsyncioTestCase):
    """Fetch errors follow the official lookup contract; model errors do not."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = load_harness(fetch_harness(Path(self._tmp.name) / "fetch-harness"))
        self.actions = []
        self.model_requests = []
        self.service_requests = []

    def state(self, budget: float = 18) -> dict:
        return {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": budget, "initial_budget": budget,
            "total_reward": 0.0, "tool_trajectory": [], "adk_events": [],
            "current_phase": 1,
        }

    def client(self) -> ModelClient:
        def handler(request):
            self.model_requests.append(json.loads(request.content))
            return httpx.Response(200, json=model_reply(self.actions.pop(0)))

        return ModelClient(transport=httpx.MockTransport(handler), api_key="test-key")

    def environment(self, handler) -> Environment:
        return Environment(httpx.MockTransport(handler))

    async def test_a_fetch_service_error_is_a_session_failure(self):
        def handler(request):
            self.service_requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"schema": 5})

        self.actions = [action("consult", agent="schema_finder", request="Extract.")]
        state = self.state()
        with self.assertRaises(GradingError):
            await tool_loop.run(
                state, "Answer.", self.environment(handler), self.client(),
                harness=self.harness,
            )

        self.assertEqual(state["failure"]["stage"], "information")
        self.assertIn("did not contain a schema string", state["failure"]["message"])
        self.assertEqual(state["budget_remaining"], 17)
        self.assertEqual([entry["tool"] for entry in state["tool_trajectory"]], ["get_schema"])
        self.assertEqual(
            state["tool_trajectory"][0]["requested_by"],
            {"action": "consult", "agent": "schema_finder"},
        )
        self.assertNotIn("loop_stopped", state)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual([path for path, _ in self.service_requests], ["/schema"])

    async def test_a_fetch_timeout_ends_the_session_with_the_standard_reason(self):
        async def handler(request):
            self.service_requests.append((request.url.path, json.loads(request.content)))
            await asyncio.sleep(1.0)
            return httpx.Response(200, json={"schema": SCHEMA_TEXT})

        self.actions = [action("consult", agent="schema_finder", request="Extract.")]
        state = self.state()
        with mock.patch.object(tool_loop, "SESSION_TIME_LIMIT_SECONDS", 0.2):
            await tool_loop.run(
                state, "Answer.", self.environment(handler), self.client(),
                harness=self.harness,
            )

        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertIn("session limit", state["final_response"])
        self.assertEqual(state["budget_remaining"], 17)
        self.assertEqual([entry["tool"] for entry in state["tool_trajectory"]], ["get_schema"])
        self.assertTrue(state["tool_trajectory"][0]["timed_out"])
        self.assertNotIn("failure", state)
        self.assertEqual(len(self.model_requests), 1)

    async def test_a_sub_agent_model_error_after_a_fetch_keeps_the_session_and_the_charge(self):
        def handler(request):
            body = json.loads(request.content)
            self.model_requests.append(body)
            if body["messages"][0]["content"] == SCHEMA_FINDER_PROMPT:
                return httpx.Response(500, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=model_reply(self.actions.pop(0)))

        self.actions = [
            action("consult", agent="schema_finder", request="Extract."),
            action("submit_sql", sql="SELECT 1;"),
        ]

        def service_handler(request):
            path = request.url.path
            payload = json.loads(request.content)
            self.service_requests.append((path, payload))
            if path == "/schema":
                return httpx.Response(200, json={"schema": FETCHED_SCHEMA_RAW})
            return httpx.Response(200, json=dict(PHASE1_FINAL))

        state = self.state()
        client = ModelClient(transport=httpx.MockTransport(handler), api_key="test-key")
        await tool_loop.run(
            state, "Answer.", self.environment(service_handler), client, harness=self.harness,
        )

        self.assertNotIn("failure", state)
        self.assertEqual(state["stop_reason"], "phase1_completed")
        self.assertEqual(state["budget_remaining"], 14)
        self.assertEqual(
            [entry["tool"] for entry in state["tool_trajectory"]],
            ["get_schema", "consult", "submit_sql"],
        )
        self.assertEqual(state["tool_trajectory"][0]["cost"], 1.0)
        consult = state["tool_trajectory"][1]
        self.assertEqual(consult["cost"], 0.0)
        self.assertEqual(consult["fetch"], "get_schema")
        self.assertEqual(consult["result"]["kind"], "model_error")
        self.assertIn(f"after {MAX_RETRIES + 1} attempts", consult["result"]["error"])

        feedback = self.model_requests[-1]["messages"][-1]["content"]
        self.assertIn("The sub-agent 'schema_finder' call failed", feedback)
        self.assertIn(
            "The get_schema lookup cost 1 bird-coins and was not refunded.", feedback
        )
        subagent = next(
            event for event in state["adk_events"] if event["type"] == "subagent_call"
        )
        self.assertEqual(subagent["fetch"], {"tool": "get_schema", "cost": 1.0})
        self.assertIn("error", subagent)
        self.assertEqual(tool_loop._model_actions(state), 2)
        # One main call, the failed sub-agent attempts, then the next main call.
        self.assertEqual(len(self.model_requests), MAX_RETRIES + 3)

    async def test_a_budget_below_the_fetch_cost_refuses_without_any_call(self):
        def handler(request):
            raise AssertionError("refused consult must not reach a service")

        def model_handler(request):
            raise AssertionError("refused consult must not call a model")

        state = self.state(budget=0.5)
        client = ModelClient(
            transport=httpx.MockTransport(model_handler), api_key="test-key"
        )
        messages = [
            {"role": "system", "content": "contract"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": action("consult", agent="schema_finder", request="Extract."),
            },
        ]
        body = await tool_loop._consult_action(
            state, client, self.harness, self.environment(handler), "schema_finder",
            "Extract.", "How many customers?", messages, 1,
            tool_loop._now() + 60.0,
        )

        self.assertIn("was not consulted", body)
        self.assertIn("costs 1 bird-coins", body)
        self.assertEqual(state["budget_remaining"], 0.5)
        self.assertEqual(state.get("tool_trajectory", []), [])
        self.assertEqual(state.get("adk_events", []), [])
        self.assertEqual(self.service_requests, [])
        self.assertEqual(self.model_requests, [])


class MultipleActionAndStatementTests(ToolLoopTestCase):
    """Accepted multi-action responses and rejected multi-statement exploration."""

    def test_only_the_first_of_several_actions_runs_and_the_rest_are_noticed(self):
        content = action("get_schema") + "\n\n" + action("get_all_column_meanings")
        self.actions = [content, action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/submit"])
        self.assertEqual(self.trajectory(state), [("get_schema", 1.0), ("submit_sql", 3.0)])
        self.assertEqual(state["budget_remaining"], 14)
        event = model_events(state)[0]
        self.assertEqual(event["action"]["name"], "get_schema")
        self.assertEqual(event["action_count"], 2)
        self.assertEqual(event["ignored_actions"], 1)
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn("contained 2 actions", feedback)
        self.assertIn("only the first", feedback)
        self.assertIn("Send exactly one action per response", feedback)

    def test_a_repeated_identical_action_is_not_announced(self):
        same = action("get_schema")
        self.actions = [
            f"{same}\n```json\n{same}\n```", action("submit_sql", sql="SELECT 1;")
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/submit"])
        event = model_events(state)[0]
        self.assertEqual(event["action_count"], 2)
        self.assertNotIn("ignored_actions", event)
        feedback = self.model_requests[1]["messages"][-1]["content"]
        self.assertNotIn("contained 2 actions", feedback)

    def test_dsml_only_response_runs_its_first_invoke(self):
        self.actions = [
            dsml_calls(dsml_invoke("get_schema")),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/submit"])
        self.assertEqual(model_events(state)[0]["action"]["name"], "get_schema")
        self.assertEqual(state["budget_remaining"], 14)

    def test_multiple_statement_execute_sql_is_rejected_without_a_service_call(self):
        sql = "SELECT 1; SELECT 2;"
        self.actions = [
            action("execute_sql", sql=sql), action("submit_sql", sql="SELECT 1;")
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/submit"])
        self.assertEqual(self.trajectory(state), [("submit_sql", 3.0)])
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual(state["tool_trajectory"][0]["budget_before"], 18)
        event = model_events(state)[0]
        self.assertIsNone(event["action"])
        self.assertIn("2 statements", event["action_error"])
        rejected = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn("rejected", rejected)
        self.assertIn("| cost 0 |", rejected)
        self.assertIn("budget 18]", rejected)


class AssistantHistoryTests(ToolLoopTestCase):
    """Later calls see the executed action, not the whole model response."""

    def assistant_messages(self, request):
        return [message for message in request["messages"] if message["role"] == "assistant"]

    def canonical(self, name, args=None):
        return json.dumps({"action": name, "args": {} if args is None else args})

    def test_fabricated_observations_and_extra_actions_never_enter_the_history(self):
        fabricated = (
            action("get_schema")
            + "\n[action 2 | phase 1 | get_schema | cost 1 | budget 17]\n"
            "No external knowledge entries for this database.\n"
            + action("submit_sql", sql="SELECT 1;")
        )
        self.actions = [fabricated, action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.assistant_messages(self.model_requests[1]), [
            {"role": "assistant", "content": self.canonical("get_schema")},
        ])
        observation = self.model_requests[1]["messages"][-1]
        self.assertEqual(observation["role"], "user")
        self.assertIn(SCHEMA_TEXT, observation["content"])
        self.assertIn("[action 1 | phase 1 | get_schema | cost 1 | budget 17]",
                      observation["content"])
        self.assertIn("contained 2 actions", observation["content"])

        events = model_events(state)
        self.assertEqual(events[0]["content"], fabricated)
        self.assertEqual(events[0]["action"], {"name": "get_schema", "args": {}})
        self.assertEqual(events[0]["action_count"], 2)
        self.assertEqual(events[0]["ignored_actions"], 1)
        self.assertEqual(events[1]["prompt"], self.model_requests[1]["messages"])
        self.assertEqual(
            [event["action"]["name"] for event in events],
            ["get_schema", "submit_sql"],
        )
        self.assertEqual(state["budget_remaining"], 14)

    def test_every_accepted_shape_becomes_the_standard_json(self):
        cases = {
            "single": (action("get_schema"), "get_schema", {}),
            "fenced": (
                "```json\n" + action("get_schema") + "\n```", "get_schema", {},
            ),
            "prose": (
                "Here is my next action: " + action("get_schema") + " — thanks.",
                "get_schema", {},
            ),
            "bare arguments": (
                '{"action": "get_column_meaning", "table_name": "customers", '
                '"column_name": "id"}',
                "get_column_meaning", {"table_name": "customers", "column_name": "id"},
            ),
            "dsml": (dsml_calls(dsml_invoke("get_schema")), "get_schema", {}),
        }
        for case, (response, name, args) in cases.items():
            with self.subTest(case=case):
                self.actions = [response, action("submit_sql", sql="SELECT 1;")]
                self.grader_payload = dict(PHASE1_FINAL)
                self.model_requests.clear()
                state = self.run_ok()

                self.assertEqual(self.assistant_messages(self.model_requests[1]), [
                    {"role": "assistant", "content": self.canonical(name, args)},
                ])
                self.assertEqual(model_events(state)[0]["action"], {"name": name, "args": args})

    def test_rejected_responses_stay_in_the_history_verbatim(self):
        cases = {
            "format error": ("not a json action", "stop"),
            "multi-statement execute_sql": (
                '{"action": "execute_sql", "args": {"sql": "SELECT 1; SELECT 2;"}}', "stop",
            ),
            "truncated": ('{"action": "get_schema", "args": {}', "length"),
        }
        for case, (content, finish_reason) in cases.items():
            with self.subTest(case=case):
                self.actions = [
                    {"content": content, "finish_reason": finish_reason},
                    action("submit_sql", sql="SELECT 1;"),
                ]
                self.grader_payload = dict(PHASE1_FINAL)
                self.model_requests.clear()
                state = self.run_ok()

                self.assertEqual(self.assistant_messages(self.model_requests[1]), [
                    {"role": "assistant", "content": content},
                ])
                self.assertEqual(model_events(state)[0]["content"], content)
                self.assertIsNone(model_events(state)[0]["action"])
                self.assertTrue(model_events(state)[0]["action_error"])

    def test_retries_and_phase_two_keep_the_standard_json_in_the_history(self):
        first, second, third = "SELECT 0;", "SELECT 1;", "SELECT 2;"
        self.actions = [
            action("submit_sql", sql=first),
            action("submit_sql", sql=second),
            action("get_schema"),
            action("submit_sql", sql=third),
        ]
        self.grader_payloads = [dict(GRADER_FAILURE), dict(PHASE1_WITH_FOLLOW_UP)]
        self.grader_payload = dict(PHASE2_PASS)
        state = self.run_ok()

        self.assertEqual(state["total_reward"], 1.0)
        self.assertEqual(state["stop_reason"], "phase2_completed")
        expected = [
            ("submit_sql", {"sql": first}),
            ("submit_sql", {"sql": second}),
            ("get_schema", {}),
        ]
        for index, (name, args) in enumerate(expected, start=1):
            with self.subTest(call=index + 1):
                self.assertEqual(
                    self.model_requests[index]["messages"][-2],
                    {"role": "assistant", "content": self.canonical(name, args)},
                )
        self.assertEqual(
            [event["phase"] for event in model_events(state)], [1, 1, 2, 2]
        )
        self.assertEqual(state["budget_remaining"], 8)


class ToolLoopFailureTests(unittest.IsolatedAsyncioTestCase):
    """Failures inspected on the state dict, which HTTP 502 responses omit."""

    def setUp(self):
        self.actions = []
        self.service_requests = []
        self.model_requests = []
        self.harness = load_harness()

    def client(self, service_status: dict | None = None, model_status: int = 200) -> ModelClient:
        def handler(request):
            self.model_requests.append(json.loads(request.content))
            if model_status != 200:
                return httpx.Response(model_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=model_reply(self.actions.pop(0)))

        return ModelClient(transport=httpx.MockTransport(handler), api_key="test-key")

    def environment(self, service_status: dict | None = None, grader_payload=None) -> Environment:
        status = service_status or {}

        def handler(request):
            path = request.url.path
            payload = json.loads(request.content)
            self.service_requests.append((path, payload))
            if status.get(path):
                return httpx.Response(status[path], json={"error": {"message": "boom"}})
            if path == "/submit":
                return httpx.Response(200, json=dict(grader_payload or PHASE1_FINAL))
            if path == "/phase_transition":
                return httpx.Response(200, json={"status": "ok", "phase": 2})
            if path == "/execute":
                return httpx.Response(
                    200, json={"success": True, "result": EXECUTE_RESULT, "error": None}
                )
            if path == "/ask":
                return httpx.Response(200, json={"answer": USER_ANSWER})
            official = {
                "/schema": {"schema": SCHEMA_TEXT},
                "/all_column_meanings": {"column_meanings": MEANINGS_TEXT},
                "/column_meaning": {"meaning": COLUMN_MEANING},
                "/knowledge_names": {"names": KNOWLEDGE_NAMES},
                "/knowledge": {"knowledge": KNOWLEDGE_TEXT},
            }
            if path in status:
                return httpx.Response(status[path], json={"error": {"message": "boom"}})
            return httpx.Response(200, json=official[path])

        return Environment(httpx.MockTransport(handler))

    def state(self, budget: float = 18) -> dict:
        return {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": budget, "initial_budget": budget,
            "total_reward": 0.0, "tool_trajectory": [], "adk_events": [],
            "current_phase": 1,
        }

    async def test_model_failure_is_recorded_and_charges_nothing(self):
        self.actions = [action("get_schema")]
        state = self.state()
        with self.assertRaises(ModelError) as caught:
            await tool_loop.run(
                state, "Answer.", self.environment(), self.client(model_status=500),
                harness=self.harness,
            )
        self.assertEqual(state["failure"]["stage"], "model")
        self.assertEqual(state["budget_remaining"], 18)
        self.assertEqual(len(self.model_requests), MAX_RETRIES + 1)
        self.assertIn(f"after {MAX_RETRIES + 1} attempts", str(caught.exception))
        self.assertEqual(self.service_requests, [])
        self.assertEqual(model_events(state), [])
        self.assertNotIn("loop_stopped", state)

    async def test_information_contract_failure_stops_the_run(self):
        self.actions = [action("get_schema")]
        state = self.state()
        environment = Environment(httpx.MockTransport(self._broken_schema_handler))
        with self.assertRaises(GradingError):
            await tool_loop.run(
                state, "Answer.", environment, self.client(), harness=self.harness
            )
        self.assertEqual(state["failure"]["stage"], "information")
        self.assertEqual(state["budget_remaining"], 17)
        self.assertEqual(
            state["tool_trajectory"][0]["result"]["error"],
            "Schema fetch response did not contain a schema string",
        )
        self.assertNotIn("loop_stopped", state)
        self.assertEqual(len(self.model_requests), 1)

    def _broken_schema_handler(self, request):
        return httpx.Response(200, json={"schema": 5})

    async def test_grader_failure_is_recorded_and_not_turned_into_a_reward(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        state = self.state()
        with self.assertRaises(GradingError):
            await tool_loop.run(
                state, "Answer.", self.environment({"/submit": 500}), self.client(),
                harness=self.harness,
            )
        self.assertEqual(state["failure"]["stage"], "grader")
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual(state["total_reward"], 0.0)
        self.assertNotIn("last_submit", state)
        self.assertNotIn("loop_stopped", state)

    async def test_transition_failure_keeps_the_phase1_result(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        state = self.state()
        with self.assertRaises(GradingError):
            await tool_loop.run(
                state, "Answer.",
                self.environment({"/phase_transition": 500}, PHASE1_WITH_FOLLOW_UP),
                self.client(),
                harness=self.harness,
            )
        self.assertEqual(state["failure"]["stage"], "transition")
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertEqual(state["current_phase"], 2)
        self.assertNotIn("loop_stopped", state)

    async def test_failures_are_not_retried_without_a_reset(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        state = self.state()
        environment = self.environment({"/submit": 500})
        for _ in range(2):
            with self.assertRaises(GradingError):
                await tool_loop.run(
                    state, "Answer.", environment, self.client(), harness=self.harness
                )
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual([path for path, _ in self.service_requests], ["/submit"])

    async def test_execute_sql_http_failure_is_a_service_error_without_a_retry(self):
        self.actions = [action("execute_sql", sql="SELECT 1;"), action("execute_sql", sql="SELECT 2;")]
        state = self.state()
        environment = self.environment({"/execute": 500})
        with self.assertRaises(GradingError) as caught:
            await tool_loop.run(
                state, "Answer.", environment, self.client(), harness=self.harness
            )
        self.assertIn("SQL execution", str(caught.exception))
        self.assertEqual(state["failure"]["stage"], "execute_sql")
        self.assertEqual(state["budget_remaining"], 17)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual([path for path, _ in self.service_requests], ["/execute"])
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["result"]["kind"], "service_error")
        self.assertEqual(entry["cost"], 1.0)
        self.assertEqual(entry["budget_before"], 18)
        self.assertNotIn("submit_attempts", state)
        # The failed attempt stays cached inside the session; it is never retried.
        with self.assertRaises(GradingError):
            await tool_loop.run(
                state, "Answer.", environment, self.client(), harness=self.harness
            )
        self.assertEqual([path for path, _ in self.service_requests], ["/execute"])

    async def test_ask_user_http_failure_never_invents_an_answer(self):
        self.actions = [
            action("ask_user", question="Which customers?"), action("submit_sql", sql="SELECT 1;"),
        ]
        state = self.state()
        with self.assertRaises(GradingError):
            await tool_loop.run(
                state, "Answer.", self.environment({"/ask": 500}), self.client(),
                harness=self.harness,
            )
        self.assertEqual(state["failure"]["stage"], "ask_user")
        self.assertEqual(state.get("dialogue_history", []), [])
        self.assertEqual(state["budget_remaining"], 16)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual([path for path, _ in self.service_requests], ["/ask"])
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["result"]["kind"], "service_error")
        self.assertIsNone(entry["result"]["answer"])
        self.assertIn("User question", entry["result"]["error"])
        self.assertEqual(entry["cost"], 2.0)
        self.assertGreaterEqual(entry["elapsed_seconds"], 0)

    async def test_empty_ask_answer_is_a_service_error_not_a_user_reply(self):
        self.actions = [action("ask_user", question="Which customers?")]
        state = self.state()

        def handler(request):
            self.service_requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"answer": "   "})

        with self.assertRaises(GradingError) as caught:
            await tool_loop.run(
                state, "Answer.", Environment(httpx.MockTransport(handler)), self.client(),
                harness=self.harness,
            )
        self.assertIn("non-empty answer", str(caught.exception))
        self.assertEqual(state["failure"]["stage"], "ask_user")
        self.assertEqual(state.get("dialogue_history", []), [])
        self.assertEqual(state["budget_remaining"], 16)


class ToolLoopDeadlineTests(unittest.TestCase):
    """R-2: the session deadline also bounds calls that are already in flight."""

    LIMIT = 1.0
    DELAY = 8.0

    def setUp(self):
        self.model_requests = []
        self.service_requests = []
        self.slow_paths = set()
        self.actions = []
        self.grader_payload = dict(GRADER_FAILURE)

        async def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            if not self.actions:
                raise AssertionError("The loop asked for a model action the script does not provide")
            return httpx.Response(200, json=model_reply(self.actions.pop(0)))

        async def service_handler(request):
            path = request.url.path
            payload = json.loads(request.content)
            self.service_requests.append((path, payload))
            if path in self.slow_paths:
                await asyncio.sleep(self.DELAY)
            if path == "/submit":
                return httpx.Response(200, json=dict(self.grader_payload))
            if path == "/phase_transition":
                return httpx.Response(200, json={"status": "ok", "phase": 2})
            if path == "/execute":
                return httpx.Response(
                    200, json={"success": True, "result": EXECUTE_RESULT, "error": None}
                )
            if path == "/ask":
                return httpx.Response(200, json={"answer": USER_ANSWER})
            official = {
                "/schema": {"schema": SCHEMA_TEXT},
                "/all_column_meanings": {"column_meanings": MEANINGS_TEXT},
                "/column_meaning": {"meaning": COLUMN_MEANING},
                "/knowledge_names": {"names": KNOWLEDGE_NAMES},
                "/knowledge": (
                    {"knowledge": KNOWLEDGE_ENTRY} if payload.get("knowledge_name")
                    else {"knowledge": KNOWLEDGE_TEXT}
                ),
            }
            return httpx.Response(200, json=official[path])

        self.client = TestClient(create_app(
            Environment(httpx.MockTransport(service_handler)),
            ModelClient(transport=httpx.MockTransport(model_handler), api_key="test-key"),
            mode="tools",
        ))

    def run_session(self) -> dict:
        limit = mock.patch.object(tool_loop, "SESSION_TIME_LIMIT_SECONDS", self.LIMIT)
        with limit:
            response = self.client.post("/init_session", json={
                "task_id": "test-task", "mode": "a-interact", "reset": True,
                "state": {"task_id": "test-task", "db_name": "demo",
                          "user_query": "How many customers?", "budget_remaining": 18},
            })
            self.assertEqual(response.status_code, 200)
            response = self.client.post("/run_session", json={
                "task_id": "test-task", "mode": "a-interact", "message": "Answer.",
            })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["state"]

    def test_deadline_cancels_a_slow_information_lookup(self):
        self.slow_paths = {"/schema"}
        self.actions = [action("get_schema"), action("submit_sql", sql="SELECT 1;")]
        state = self.run_session()

        self.assertEqual([path for path, _ in self.service_requests], ["/schema"])
        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertEqual(state["session_elapsed_seconds"] >= self.LIMIT, True)
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["tool"], "get_schema")
        self.assertEqual(entry["timed_out"], True)
        self.assertEqual(entry["cost"], 1.0)
        self.assertEqual(state["budget_remaining"], 17)
        self.assertNotIn("failure", state)
        self.assertIn("outcome is unknown", state["final_response"])

    def test_deadline_cancels_a_slow_execute_sql_without_a_refund(self):
        self.slow_paths = {"/execute"}
        self.actions = [
            action("execute_sql", sql="SELECT 1;"), action("submit_sql", sql="SELECT 2;"),
        ]
        state = self.run_session()

        self.assertEqual([path for path, _ in self.service_requests], ["/execute"])
        self.assertEqual(state["stop_reason"], "session_time_limit")
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["tool"], "execute_sql")
        self.assertEqual(entry["timed_out"], True)
        self.assertEqual(entry["result"]["kind"], "cancelled")
        self.assertEqual(entry["cost"], 1.0)
        self.assertEqual(state["budget_remaining"], 17)
        self.assertNotIn("failure", state)
        self.assertIn("outcome is unknown", state["final_response"])

    def test_deadline_cancels_a_slow_ask_user_without_inventing_an_answer(self):
        self.slow_paths = {"/ask"}
        self.actions = [
            action("ask_user", question="Which customers?"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        state = self.run_session()

        self.assertEqual([path for path, _ in self.service_requests], ["/ask"])
        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertEqual(state["dialogue_history"], [])
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["tool"], "ask_user")
        self.assertEqual(entry["timed_out"], True)
        self.assertEqual(entry["result"]["kind"], "cancelled")
        self.assertIsNone(entry["result"]["answer"])
        self.assertEqual(entry["cost"], 2.0)
        self.assertEqual(state["budget_remaining"], 16)
        self.assertNotIn("failure", state)
        self.assertIn("outcome is unknown", state["final_response"])

    def test_deadline_cancels_a_slow_submission_and_marks_the_outcome_unknown(self):
        self.slow_paths = {"/submit"}
        self.actions = [action("submit_sql", sql="SELECT 1;"), action("submit_sql", sql="SELECT 2;")]
        state = self.run_session()

        self.assertEqual([path for path, _ in self.service_requests], ["/submit"])
        self.assertEqual(state["stop_reason"], "session_time_limit")
        entry = state["tool_trajectory"][0]
        self.assertEqual(entry["tool"], "submit_sql")
        self.assertEqual(entry["timed_out"], True)
        self.assertIn("outcome is unknown", entry["result"]["error"])
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual(state["total_reward"], 0.0)
        self.assertNotIn("last_submit", state)
        self.assertNotIn("submit_attempts", state)
        self.assertNotIn("failure", state)

    def test_deadline_cancels_a_slow_phase_transition_and_keeps_the_reward(self):
        self.slow_paths = {"/phase_transition"}
        self.grader_payload = dict(PHASE1_WITH_FOLLOW_UP)
        self.actions = [action("submit_sql", sql="SELECT 1;"), action("submit_sql", sql="SELECT 2;")]
        state = self.run_session()

        self.assertEqual(
            [path for path, _ in self.service_requests], ["/submit", "/phase_transition"]
        )
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(state["stop_reason"], "session_time_limit")
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertEqual(state["current_phase"], 2)
        self.assertEqual(state["phase_transition"]["completed"], False)
        self.assertIn("outcome is unknown", state["phase_transition"]["message"])
        self.assertFalse(state["task_done"])
        self.assertNotIn("failure", state)


class ToolLoopConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model_requests = []
        self.service_paths = []
        self.model_status = 200

        async def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            await asyncio.sleep(0.05)
            if self.model_status != 200:
                return httpx.Response(self.model_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=model_reply(action("submit_sql", sql="SELECT 1;")))

        async def service_handler(request):
            path = request.url.path
            self.service_paths.append(path)
            return httpx.Response(200, json=dict(PHASE1_FINAL))

        app = create_app(
            Environment(httpx.MockTransport(service_handler)),
            ModelClient(transport=httpx.MockTransport(model_handler), api_key="test-key"),
            mode="tools",
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://agent"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def initialize(self):
        response = await self.client.post("/init_session", json={
            "task_id": "test-task", "mode": "a-interact", "reset": True,
            "state": {"task_id": "test-task", "db_name": "demo",
                      "user_query": "How many customers?", "budget_remaining": 18},
        })
        self.assertEqual(response.status_code, 200)

    async def test_simultaneous_requests_do_not_duplicate_calls(self):
        await self.initialize()
        payload = {"task_id": "test-task", "mode": "a-interact", "message": "Answer."}
        responses = await asyncio.gather(
            self.client.post("/run_session", json=payload),
            self.client.post("/run_session", json=payload),
        )
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(self.service_paths, ["/submit"])
        self.assertEqual(responses[0].json()["response"], responses[1].json()["response"])
        self.assertTrue(responses[0].json()["state"]["task_done"])

    async def test_simultaneous_requests_after_model_retries_are_exhausted_do_not_replay(self):
        self.model_status = 500
        await self.initialize()
        payload = {"task_id": "test-task", "mode": "a-interact", "message": "Answer."}
        responses = await asyncio.gather(
            self.client.post("/run_session", json=payload),
            self.client.post("/run_session", json=payload),
        )
        self.assertEqual([response.status_code for response in responses], [502, 502])
        self.assertEqual(len(self.model_requests), MAX_RETRIES + 1)
        self.assertEqual(self.service_paths, [])


if __name__ == "__main__":
    unittest.main()
