"""Tool-selecting loop contracts with a mock model and grader; not BIRD scores."""

import asyncio
import json
import os
import unittest
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
from agent.model import API_URL, MAX_OUTPUT_TOKENS, MODEL_ID, ModelClient, ModelError
from agent.server import AGENT_NAMES, create_app
from agent.tool_loop import MAX_MODEL_ACTIONS, submission_key
from agent.tools import (
    ALLOWED_TOOLS,
    ASK_USER_COST,
    EXECUTE_SQL_COST,
    SUBMIT_COST,
    TOOL_COSTS,
    ActionError,
    parse_action,
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


def model_reply(content: str, finish_reason: str = "stop") -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


class FakeClock:
    """A monotonic clock the tests move to check the session deadline."""

    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class SubmissionKeyTests(unittest.TestCase):
    """R-1: the duplicate key keeps SQL that means something different apart."""

    def test_whitespace_between_tokens_is_normalized(self):
        self.assertEqual(submission_key("  SELECT 1; \n"), "SELECT 1;")
        self.assertEqual(
            submission_key("SELECT   a,\n\t b  FROM t;"), submission_key("SELECT a, b FROM t;")
        )

    def test_string_literal_and_quoted_identifier_content_is_preserved(self):
        self.assertNotEqual(submission_key("SELECT 'a  b';"), submission_key("SELECT 'a b';"))
        self.assertNotEqual(
            submission_key("SELECT 'it''s  ok';"), submission_key("SELECT 'it''s ok';")
        )
        self.assertNotEqual(
            submission_key('SELECT "a  b" FROM t;'), submission_key('SELECT "a b" FROM t;')
        )
        self.assertEqual(
            submission_key("SELECT  'a  b'   AS x;"), submission_key("SELECT 'a  b' AS x;")
        )

    def test_adjacent_string_constants_keep_their_separating_whitespace(self):
        # 'a' 'b' is a syntax error, while 'a'\n'b' concatenates to 'ab'.
        self.assertEqual(submission_key("SELECT 'a'\n'b';"), "SELECT 'a'\n'b';")
        self.assertNotEqual(submission_key("SELECT 'a'\n'b';"), submission_key("SELECT 'a' 'b';"))
        self.assertNotEqual(submission_key("SELECT 'a'\n'b';"), submission_key("SELECT 'a'  'b';"))
        self.assertNotEqual(
            submission_key("SELECT 'a'\nE'b';"), submission_key("SELECT 'a' E'b';")
        )
        self.assertEqual(
            submission_key("SELECT  'a'\n'b'  ;"), submission_key("SELECT 'a'\n'b' ;")
        )

    def test_comment_text_and_line_breaks_are_preserved(self):
        self.assertNotEqual(
            submission_key("SELECT 1; -- a\nb"), submission_key("SELECT 1; -- a b")
        )
        self.assertEqual(
            submission_key("SELECT 1; -- a\nSELECT 2;"), submission_key("SELECT 1;   -- a\nSELECT 2;")
        )
        self.assertNotEqual(
            submission_key("SELECT /* a  b */ 1;"), submission_key("SELECT /* a b */ 1;")
        )
        self.assertEqual(
            submission_key("SELECT   /* a b */   1;"), submission_key("SELECT /* a b */ 1;")
        )
        self.assertNotEqual(
            submission_key("SELECT /* a\nb */ 1;"), submission_key("SELECT /* a b */ 1;")
        )

    def test_dollar_quoted_bodies_are_preserved(self):
        self.assertNotEqual(submission_key("SELECT $$a  b$$;"), submission_key("SELECT $$a b$$;"))
        self.assertNotEqual(
            submission_key("SELECT $tag$a  b$tag$;"), submission_key("SELECT $tag$a b$tag$;")
        )
        self.assertNotEqual(
            submission_key("SELECT $한$a  b$한$;"), submission_key("SELECT $한$a b$한$;")
        )
        self.assertEqual(
            submission_key("SELECT 1 WHERE x = $1;"), "SELECT 1 WHERE x = $1;"
        )
        self.assertEqual(
            submission_key("SELECT  1  WHERE  x = $1;"), submission_key("SELECT 1 WHERE x = $1;")
        )

    def test_ambiguous_escaping_or_unbalanced_text_falls_back_to_exact_matching(self):
        escaped = "SELECT E'a\\'b';"
        self.assertEqual(submission_key(escaped), escaped)
        self.assertNotEqual(submission_key(f"SELECT  {escaped}"), submission_key(escaped))
        for text in ("SELECT 'unterminated", 'SELECT "unterminated', "SELECT /* unterminated"):
            with self.subTest(text=text):
                self.assertEqual(submission_key(text), text)
        self.assertEqual(submission_key("SELECT /* a /* b */ c */ 1;"), "SELECT /* a /* b */ c */ 1;")


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
    def test_health_reports_the_selected_mode(self):
        for mode, name in AGENT_NAMES.items():
            with self.subTest(mode=mode):
                health = TestClient(create_app(mode=mode)).get("/health").json()
                self.assertEqual(health["status"], "healthy")
                self.assertEqual(health["service"], "system_agent")
                self.assertEqual(health["agent"], name)
                self.assertEqual(health["mode"], mode)
                self.assertEqual(health["model"], MODEL_ID)
                expected_tools = tool_metadata() if mode == "tools" else []
                self.assertEqual(health["tools"], expected_tools)
                self.assertEqual(health["execute_sql_timeout_seconds"], EXECUTE_SQL_TIMEOUT_SECONDS)
                self.assertEqual(health["ask_user_timeout_seconds"], ASK_USER_TIMEOUT_SECONDS)

        with mock.patch.dict(os.environ, {"AGENT_MODE": "tools"}):
            self.assertEqual(TestClient(create_app()).get("/health").json()["mode"], "tools")
        with mock.patch.dict(os.environ, {"AGENT_MODE": ""}):
            self.assertEqual(TestClient(create_app()).get("/health").json()["mode"], "fixed")
        with mock.patch.dict(os.environ, {"AGENT_MODE": "unknown"}):
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
        self.assertEqual(
            state["adk_events"][-1]["prompt"], self.model_requests[-1]["messages"]
        )
        self.assertEqual([e["call_index"] for e in state["adk_events"]], list(range(1, 10)))
        self.assertEqual(
            [e["action"]["name"] for e in state["adk_events"]], list(TOOL_COSTS)
        )
        self.assertEqual(state["adk_events"][0]["settings"]["endpoint"], API_URL)
        self.assertEqual(state["adk_events"][0]["usage"]["total_tokens"], 14)
        self.assertNotIn("test-key", json.dumps(state))

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
                event = state["adk_events"][index]
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
        self.assertEqual([e["phase"] for e in state["adk_events"]], [1, 1, 2])
        self.assertEqual(state["adk_events"][1]["action"]["args"]["sql"],
                         "SELECT count(id) FROM customers;")

    def test_duplicate_key_keeps_meaningful_sql_changes_submittable(self):
        cases = {
            "different string literal": (
                "SELECT count(*) FROM customers WHERE name = 'a  b';",
                "SELECT count(*) FROM customers WHERE name = 'a b';",
            ),
            "different line comment": (
                "SELECT count(*) FROM customers; -- first  note",
                "SELECT count(*) FROM customers; -- first note",
            ),
            "syntax error fixed with a newline between adjacent strings": (
                "SELECT count(*) FROM customers WHERE name = 'a' 'b';",
                "SELECT count(*) FROM customers WHERE name = 'a'\n'b';",
            ),
            "different unicode dollar-quoted body": (
                "SELECT count(*) FROM customers WHERE name = $한$a  b$한$;",
                "SELECT count(*) FROM customers WHERE name = $한$a b$한$;",
            ),
        }
        for name, (first, revised) in cases.items():
            with self.subTest(name=name):
                self.setUp()
                final = "SELECT count(*) FROM customers;"
                self.actions = [
                    action("submit_sql", sql=first),
                    action("submit_sql", sql=revised),
                    action("submit_sql", sql=revised),
                    action("submit_sql", sql=final),
                ]
                self.grader_payloads = [
                    dict(GRADER_FAILURE), dict(GRADER_FAILURE), dict(PHASE1_FINAL),
                ]
                state = self.run_ok()

                self.assertEqual(
                    [payload["sql"] for path, payload in self.service_requests if path == "/submit"],
                    [first, revised, final],
                )
                self.assertEqual(state["total_reward"], 0.7)
                self.assertTrue(state["task_done"])
                self.assertEqual(state["stop_reason"], "phase1_completed")
                self.assertEqual(
                    [(entry["tool"], entry["cost"]) for entry in state["tool_trajectory"]],
                    [("submit_sql", 3.0), ("submit_sql", 3.0), ("submit_sql", 0.0),
                     ("submit_sql", 3.0)],
                )
                self.assertEqual(state["tool_trajectory"][2]["blocked"], "duplicate_sql")
                self.assertEqual(state["submit_attempts"], [
                    {"phase": 1, "sql": first},
                    {"phase": 1, "sql": revised},
                    {"phase": 1, "sql": final},
                ])

    def test_duplicate_sql_is_blocked_per_phase_without_a_charge(self):
        first = "SELECT 1;"
        second = "SELECT 2;"
        self.actions = [
            action("submit_sql", sql=first),
            action("submit_sql", sql=f"  {first}  "),
            action("submit_sql", sql=second),
            action("submit_sql", sql=first),
        ]
        self.grader_payloads = [
            dict(GRADER_FAILURE), dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS),
        ]
        state = self.run_ok()

        self.assertEqual([payload["sql"] for _, payload in self.service_requests
                          if _ == "/submit"], [first, second, first])
        self.assertEqual(
            [entry["phase"] for entry in state["tool_trajectory"]], [1, 1, 1, 2]
        )
        blocked = state["tool_trajectory"][1]
        self.assertEqual(blocked["tool"], "submit_sql")
        self.assertEqual(blocked["cost"], 0.0)
        self.assertEqual(blocked["blocked"], "duplicate_sql")
        self.assertEqual(blocked["budget_before"], blocked["budget_after"])
        self.assertEqual(
            [entry["budget_after"] for entry in state["tool_trajectory"]], [15, 15, 12, 9]
        )
        self.assertEqual(
            state["submit_attempts"],
            [{"phase": 1, "sql": first}, {"phase": 1, "sql": second}, {"phase": 2, "sql": first}],
        )
        feedback = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn("already submitted in phase 1", feedback)
        self.assertIn("| cost 0 |", feedback)

    def test_repeated_information_lookup_is_cached_and_still_charged(self):
        self.actions = [action("get_schema"), action("get_schema"),
                        action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/schema", "/submit"])
        self.assertEqual(
            self.trajectory(state), [("get_schema", 1.0), ("get_schema", 1.0), ("submit_sql", 3.0)]
        )
        cached = state["tool_trajectory"][1]
        self.assertEqual(cached["cached"], True)
        self.assertEqual(cached["result"], {"cache_hit": True})
        self.assertEqual(state["budget_remaining"], 13)
        feedback = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn("Served from the session cache", feedback)
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
                event = state["adk_events"][0]
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
        self.assertEqual(self.paths(), ["/schema"])
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
        self.assertEqual(len(state["adk_events"]), 1)
        self.assertNotIn("action", state["adk_events"][0])
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


class ToolLoopFailureTests(unittest.IsolatedAsyncioTestCase):
    """Failures inspected on the state dict, which HTTP 502 responses omit."""

    def setUp(self):
        self.actions = []
        self.service_requests = []
        self.model_requests = []

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
        with self.assertRaises(ModelError):
            await tool_loop.run(state, "Answer.", self.environment(), self.client(model_status=500))
        self.assertEqual(state["failure"]["stage"], "model")
        self.assertEqual(state["budget_remaining"], 18)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(self.service_requests, [])
        self.assertEqual(state["adk_events"], [])
        self.assertNotIn("loop_stopped", state)

    async def test_information_contract_failure_stops_the_run(self):
        self.actions = [action("get_schema")]
        state = self.state()
        environment = Environment(httpx.MockTransport(self._broken_schema_handler))
        with self.assertRaises(GradingError):
            await tool_loop.run(state, "Answer.", environment, self.client())
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
                state, "Answer.", self.environment({"/submit": 500}), self.client()
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
                await tool_loop.run(state, "Answer.", environment, self.client())
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual([path for path, _ in self.service_requests], ["/submit"])

    async def test_execute_sql_http_failure_is_a_service_error_without_a_retry(self):
        self.actions = [action("execute_sql", sql="SELECT 1;"), action("execute_sql", sql="SELECT 2;")]
        state = self.state()
        environment = self.environment({"/execute": 500})
        with self.assertRaises(GradingError) as caught:
            await tool_loop.run(state, "Answer.", environment, self.client())
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
            await tool_loop.run(state, "Answer.", environment, self.client())
        self.assertEqual([path for path, _ in self.service_requests], ["/execute"])

    async def test_ask_user_http_failure_never_invents_an_answer(self):
        self.actions = [
            action("ask_user", question="Which customers?"), action("submit_sql", sql="SELECT 1;"),
        ]
        state = self.state()
        with self.assertRaises(GradingError):
            await tool_loop.run(state, "Answer.", self.environment({"/ask": 500}), self.client())
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
                state, "Answer.", Environment(httpx.MockTransport(handler)), self.client()
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

        async def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            await asyncio.sleep(0.05)
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


if __name__ == "__main__":
    unittest.main()
