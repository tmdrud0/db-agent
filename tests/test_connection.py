"""Session API contract checks with a mock model and grader; not BIRD scores."""

import asyncio
import json
import os
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from agent.environment import Environment, GradingError
from agent.loop import run
from agent.model import API_URL, MAX_OUTPUT_TOKENS, MODEL_ID, ModelClient, ModelError
from agent.server import AGENT_NAME, create_app

SCHEMA_TEXT = "CREATE TABLE customers (\n  id integer,\n  name text\n);"
MEANINGS_TEXT = '{\n  "customers|id": "identifier",\n  "customers|name": "full name"\n}'
KNOWLEDGE_TEXT = '[{"id": 7, "knowledge": "Active customer", "definition": "A customer with orders."}]'
INFO_PATHS = ("/schema", "/all_column_meanings", "/knowledge")
OFFICIAL_INFORMATION = {
    "/schema": {"schema": SCHEMA_TEXT},
    "/all_column_meanings": {"column_meanings": MEANINGS_TEXT},
    "/knowledge": {"knowledge": KNOWLEDGE_TEXT},
}

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
PHASE2_PASS = {
    "passed": True,
    "reward": 0.3,
    "message": "Phase 2 correct! (Reward: 0.3). Task finished.",
    "phase_completed": 2,
}
PHASE2_FAILURE = {
    "passed": False,
    "reward": 0.0,
    "message": "SQL failed Phase 2. Your SQL is not correct.",
}


def model_reply(content: str = "SELECT 1;", finish_reason: str = "stop") -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


class SessionContractTests(unittest.TestCase):
    def setUp(self):
        self.model_requests = []
        self.service_requests = []
        self.model_status = 200
        self.model_payload = model_reply()
        self.model_payloads = []
        self.model_raw_text = None
        self.model_error = None
        self.grader_status = 200
        self.grader_payload = dict(GRADER_FAILURE)
        self.grader_payloads = []
        self.grader_raw_text = None
        self.info_status = {}
        self.info_payloads = {}
        self.info_raw_text = {}
        self.transition_status = 200

        def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            if self.model_error is not None:
                raise self.model_error
            if self.model_raw_text is not None:
                return httpx.Response(200, content=self.model_raw_text.encode())
            if self.model_status != 200:
                return httpx.Response(self.model_status, json={"error": {"message": "boom"}})
            payload = self.model_payloads.pop(0) if self.model_payloads else self.model_payload
            return httpx.Response(200, json=payload)

        def service_handler(request):
            path = request.url.path
            self.service_requests.append((path, json.loads(request.content)))
            if path == "/submit":
                if self.grader_raw_text is not None:
                    return httpx.Response(self.grader_status, content=self.grader_raw_text.encode())
                payload = self.grader_payloads.pop(0) if self.grader_payloads else self.grader_payload
                return httpx.Response(self.grader_status, json=payload)
            if path == "/phase_transition":
                if self.transition_status != 200:
                    return httpx.Response(self.transition_status, json={"error": {"message": "boom"}})
                return httpx.Response(200, json={"status": "ok", "phase": 2})
            if path in self.info_raw_text:
                return httpx.Response(
                    self.info_status.get(path, 200), content=self.info_raw_text[path].encode()
                )
            if self.info_status.get(path, 200) != 200:
                return httpx.Response(self.info_status[path], json={"error": {"message": "boom"}})
            return httpx.Response(200, json=self.info_payloads.get(path, OFFICIAL_INFORMATION[path]))

        self.app = create_app(
            Environment(httpx.MockTransport(service_handler)),
            ModelClient(transport=httpx.MockTransport(model_handler), api_key="test-key"),
        )
        self.client = TestClient(self.app)
        self.state = {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": 18, "initial_budget": 18,
        }

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

    def information_requests(self):
        return [request for request in self.service_requests if request[0] in INFO_PATHS]

    def submit_requests(self):
        return [request for request in self.service_requests if request[0] == "/submit"]

    def transition_requests(self):
        return [request for request in self.service_requests if request[0] == "/phase_transition"]

    def prompts(self):
        return [request["messages"][1]["content"] for request in self.model_requests]

    def test_health_identifies_the_fixed_context_two_phase_baseline(self):
        health = self.client.get("/health").json()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["service"], "system_agent")
        self.assertEqual(health["agent"], AGENT_NAME)
        self.assertEqual(health["model"], MODEL_ID)

    def test_fixed_information_round_and_phase1_prompt(self):
        self.state["internal_note"] = "do-not-send"
        self.state["hidden_answer"] = "do-not-send-either"
        self.initialize()
        self.assertEqual(self.execute().status_code, 200)

        self.assertEqual(
            self.service_requests,
            [
                ("/schema", {"task_id": "test-task"}),
                ("/all_column_meanings", {"task_id": "test-task"}),
                ("/knowledge", {"task_id": "test-task"}),
                ("/submit", {"task_id": "test-task", "sql": "SELECT 1;"}),
            ],
        )
        self.assertEqual(len(self.model_requests), 1)
        request = self.model_requests[0]
        self.assertEqual(request["model"], MODEL_ID)
        self.assertFalse(request["stream"])
        self.assertEqual(request["max_tokens"], MAX_OUTPUT_TOKENS)
        system, user = request["messages"]
        self.assertEqual(system["role"], "system")
        self.assertIn("postgresql", system["content"].lower())
        self.assertIn("only", system["content"].lower())
        self.assertEqual(user["role"], "user")
        prompt = user["content"]
        self.assertIn("Database: demo", prompt)
        self.assertIn("How many customers?", prompt)
        for text in (SCHEMA_TEXT, MEANINGS_TEXT, KNOWLEDGE_TEXT):
            self.assertIn(text, prompt)
        self.assertNotIn("do-not-send", json.dumps(request))

    def test_empty_information_values_are_used_as_returned(self):
        self.info_payloads = {
            "/all_column_meanings": {"column_meanings": "{}"},
            "/knowledge": {"knowledge": "[]"},
        }
        self.initialize()
        self.assertEqual(self.execute().status_code, 200)
        prompt = self.prompts()[0]
        self.assertIn('official get_all_column_meanings response):\n{}', prompt)
        self.assertIn("official get_all_knowledge_definitions response):\n[]", prompt)

    def test_official_result_and_model_call_are_recorded_in_state(self):
        self.initialize()
        state = self.execute().json()["state"]

        self.assertEqual(state["total_reward"], 0.0)
        self.assertEqual(state["budget_remaining"], 12)
        self.assertFalse(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertFalse(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["stop_reason"], "phase1_failed")
        self.assertEqual(state["final_response"], "SQL failed Phase 1. Your SQL is not correct.")
        self.assertEqual(state["last_submit"]["passed"], False)
        self.assertEqual(state["last_submit"]["message"], "SQL failed Phase 1. Your SQL is not correct.")

        self.assertEqual(len(state["adk_events"]), 1)
        event = state["adk_events"][0]
        self.assertEqual(event["type"], "model_call")
        self.assertEqual(event["call_index"], 1)
        self.assertEqual(event["phase"], 1)
        self.assertEqual(event["request_model"], MODEL_ID)
        self.assertEqual(event["response_model"], MODEL_ID)
        self.assertEqual(event["sql"], "SELECT 1;")
        self.assertEqual(event["usage"]["total_tokens"], 14)
        self.assertEqual(event["settings"]["endpoint"], API_URL)
        self.assertEqual(event["settings"]["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertGreaterEqual(event["elapsed_seconds"], 0)
        self.assertEqual(event["prompt"], self.prompts()[0])

        fetches = state["tool_trajectory"][:3]
        self.assertEqual(
            [entry["tool"] for entry in fetches],
            ["get_schema", "get_all_column_meanings", "get_all_knowledge_definitions"],
        )
        self.assertEqual(fetches[0]["args"], {"task_id": "test-task"})
        self.assertEqual(fetches[0]["result"], SCHEMA_TEXT)
        self.assertEqual(fetches[1]["result"], MEANINGS_TEXT)
        self.assertEqual(fetches[2]["result"], KNOWLEDGE_TEXT)
        self.assertEqual(
            [(entry["cost"], entry["budget_before"], entry["budget_after"]) for entry in fetches],
            [(1.0, 18, 17), (1.0, 17, 16), (1.0, 16, 15)],
        )
        submit = state["tool_trajectory"][3]
        self.assertEqual(submit["tool"], "submit_sql")
        self.assertEqual(submit["args"], {"sql": "SELECT 1;"})
        self.assertEqual(submit["cost"], 3.0)
        self.assertEqual(submit["budget_before"], 15)
        self.assertEqual(submit["budget_after"], 12)
        self.assertEqual(submit["result"]["passed"], False)
        self.assertNotIn("test-key", json.dumps(state))

    def test_fenced_dml_response_is_submitted_unchanged(self):
        sql = "UPDATE customers SET active = true;"
        self.model_payload = model_reply(f"```sql\n{sql}\n```")
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(self.submit_requests()[0][1], {"task_id": "test-task", "sql": sql})
        self.assertEqual(state["adk_events"][0]["sql"], sql)
        self.assertEqual(state["tool_trajectory"][3]["args"]["sql"], sql)

    def test_generation_failures_never_reach_the_grader(self):
        cases = {
            "empty content": {"payload": model_reply("   ")},
            "truncated": {"payload": model_reply("SELECT", "length")},
            "missing choices": {"payload": {"model": MODEL_ID}},
            "invalid json": {"raw": "<html>not json</html>"},
            "http error": {"status": 500},
            "timeout": {"error": httpx.ReadTimeout("timed out")},
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                self.model_payload = case.get("payload", model_reply())
                self.model_raw_text = case.get("raw")
                self.model_status = case.get("status", 200)
                self.model_error = case.get("error")
                self.service_requests = []
                self.model_requests = []
                self.initialize()
                response = self.execute()
                self.assertEqual(response.status_code, 502)
                self.assertIn("Model service error", response.json()["detail"])
                self.assertEqual(self.submit_requests(), [])
                self.assertEqual(len(self.information_requests()), 3)
                self.assertEqual(len(self.model_requests), 1)
                self.assertNotIn("test-key", response.text)

    def test_information_fetch_failures_are_explicit_and_stop_the_run(self):
        cases = {
            "schema http error": {
                "info_status": {"/schema": 500}, "label": "Schema fetch", "fetches": 1,
            },
            "meaning contract violation": {
                "info_payloads": {"/all_column_meanings": {"column_meanings": {}}},
                "label": "Column meanings fetch", "fetches": 2,
            },
            "knowledge invalid json": {
                "info_raw_text": {"/knowledge": "<html>not json</html>"},
                "label": "Knowledge fetch", "fetches": 3,
            },
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                self.info_status = case.get("info_status", {})
                self.info_payloads = case.get("info_payloads", {})
                self.info_raw_text = case.get("info_raw_text", {})
                self.service_requests = []
                self.model_requests = []
                self.initialize()
                response = self.execute()
                self.assertEqual(response.status_code, 502)
                self.assertIn("Benchmark service error", response.json()["detail"])
                self.assertIn(case["label"], response.json()["detail"])
                self.assertEqual(len(self.information_requests()), case["fetches"])
                self.assertEqual(self.model_requests, [])
                self.assertEqual(self.submit_requests(), [])

    def test_grader_failures_are_explicit_and_never_faked(self):
        cases = {
            "http error": {"status": 500},
            "invalid json": {"raw": "not json"},
            "missing fields": {"payload": {"message": "no result fields"}},
            "passing without a phase": {"payload": {"passed": True, "message": "ok", "reward": 0.7}},
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                self.grader_status = case.get("status", 200)
                self.grader_raw_text = case.get("raw")
                self.grader_payload = case.get("payload", dict(GRADER_FAILURE))
                self.service_requests = []
                self.model_requests = []
                self.initialize()
                response = self.execute()
                self.assertEqual(response.status_code, 502)
                self.assertIn("Benchmark service error", response.json()["detail"])
                self.assertEqual(len(self.information_requests()), 3)
                self.assertEqual(len(self.model_requests), 1)
                self.assertEqual(len(self.submit_requests()), 1)

    def test_phase1_failure_stops_with_one_model_call_and_one_submission(self):
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["stop_reason"], "phase1_failed")
        self.assertEqual(state["current_phase"], 1)
        self.assertFalse(state["phase1_completed"])
        self.assertFalse(state["task_done"])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.submit_requests()), 1)
        self.assertEqual(self.transition_requests(), [])

    def test_phase1_success_without_follow_up_completes_the_task(self):
        self.grader_payload = {
            "passed": True, "reward": 0.7, "message": "Phase 1 correct! (Reward: 0.7). Task finished.",
            "phase_completed": 1, "has_follow_up": False,
        }
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertTrue(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["current_phase"], 1)
        self.assertEqual(state["stop_reason"], "phase1_completed")
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.submit_requests()), 1)
        self.assertEqual(self.transition_requests(), [])

    def test_phase1_success_with_follow_up_runs_phase2(self):
        sql1 = "SELECT count(*) FROM customers;"
        sql2 = "SELECT region, count(*) FROM customers GROUP BY region;"
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        self.model_payloads = [model_reply(sql1), model_reply(sql2)]
        self.initialize()
        state = self.execute().json()["state"]

        self.assertEqual(
            [path for path, _ in self.service_requests],
            ["/schema", "/all_column_meanings", "/knowledge", "/submit", "/phase_transition", "/submit"],
        )
        self.assertEqual(self.transition_requests(), [("/phase_transition", {"task_id": "test-task"})])
        self.assertEqual(self.submit_requests()[1][1], {"task_id": "test-task", "sql": sql2})

        self.assertEqual(len(self.model_requests), 2)
        first, second = self.prompts()
        self.assertNotIn("And by region?", first)
        for text in (
            SCHEMA_TEXT, MEANINGS_TEXT, KNOWLEDGE_TEXT, "How many customers?",
            sql1, PHASE1_WITH_FOLLOW_UP["message"], "And by region?",
        ):
            self.assertIn(text, second)
        self.assertIn('"reward": 0.7', second)
        self.assertIn("already applied", second)

        self.assertEqual(state["total_reward"], 1.0)
        self.assertTrue(state["phase1_completed"])
        self.assertTrue(state["phase2_completed"])
        self.assertTrue(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["current_phase"], 2)
        self.assertEqual(state["follow_up_query"], "And by region?")
        self.assertEqual(state["stop_reason"], "phase2_completed")
        self.assertEqual(state["budget_remaining"], 9)

        events = state["adk_events"]
        self.assertEqual([event["phase"] for event in events], [1, 2])
        self.assertEqual([event["call_index"] for event in events], [1, 2])
        self.assertEqual([event["sql"] for event in events], [sql1, sql2])
        self.assertEqual(events[1]["prompt"], second)
        self.assertEqual(len(state["tool_trajectory"]), 5)
        self.assertEqual(state["tool_trajectory"][4]["tool"], "submit_sql")
        self.assertEqual(state["tool_trajectory"][4]["result"]["phase_completed"], 2)

    def test_phase2_failure_keeps_phase1_reward_and_stops(self):
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_FAILURE)]
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertFalse(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["current_phase"], 2)
        self.assertEqual(state["stop_reason"], "phase2_failed")
        self.assertEqual(state["final_response"], "SQL failed Phase 2. Your SQL is not correct.")
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.submit_requests()), 2)
        self.assertEqual(len(self.transition_requests()), 1)

        cached = self.execute().json()["state"]
        self.assertEqual(cached, state)
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.submit_requests()), 2)

    def test_rewards_are_summed_from_official_responses(self):
        self.grader_payloads = [
            {**PHASE1_WITH_FOLLOW_UP, "reward": 0.6, "message": "Phase 1 correct! (Reward: 0.6)."},
            {**PHASE2_PASS, "reward": 0.15, "message": "Phase 2 correct! (Reward: 0.15)."},
        ]
        self.model_payloads = [model_reply("SELECT 1;"), model_reply("SELECT 2;")]
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["total_reward"], 0.75)
        self.assertEqual(state["last_submit"]["reward"], 0.15)

    def test_no_budget_stops_before_the_information_fetches(self):
        self.state["budget_remaining"] = 0
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(self.service_requests, [])
        self.assertEqual(self.model_requests, [])
        self.assertEqual(state["budget_remaining"], 0)
        self.assertEqual(state["stop_reason"], "budget_exhausted_before_information")
        self.assertTrue(state["loop_stopped"])
        self.assertFalse(state["task_done"])
        self.assertEqual(self.execute().json()["state"], state)

    def test_budget_runs_out_during_the_information_fetches(self):
        self.state["budget_remaining"] = 2
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(
            [path for path, _ in self.service_requests], ["/schema", "/all_column_meanings"]
        )
        self.assertEqual(state["budget_remaining"], 0)
        self.assertEqual(state["stop_reason"], "budget_exhausted_before_information")
        self.assertEqual(self.model_requests, [])
        self.assertEqual(self.submit_requests(), [])

    def test_budget_below_the_submission_cost_stops_after_the_fetches(self):
        for budget in (3, 4):
            with self.subTest(budget=budget):
                self.service_requests = []
                self.model_requests = []
                self.state["budget_remaining"] = budget
                self.initialize()
                state = self.execute().json()["state"]
                self.assertEqual(len(self.information_requests()), 3)
                self.assertEqual(state["budget_remaining"], budget - 3)
                self.assertEqual(state["stop_reason"], "budget_exhausted_before_phase1")
                self.assertEqual(self.model_requests, [])
                self.assertEqual(self.submit_requests(), [])
                self.assertGreaterEqual(state["budget_remaining"], 0)

    def test_budget_of_six_allows_phase1_then_stops_before_phase2(self):
        self.state["budget_remaining"] = 6
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP)]
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["budget_remaining"], 0)
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertFalse(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["current_phase"], 2)
        self.assertEqual(state["stop_reason"], "budget_exhausted_before_phase2")
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.submit_requests()), 1)
        self.assertEqual(self.transition_requests(), [])

    def test_budget_of_nine_runs_both_phases(self):
        self.state["budget_remaining"] = 9
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        self.model_payloads = [model_reply("SELECT 1;"), model_reply("SELECT 2;")]
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["budget_remaining"], 0)
        self.assertEqual(state["total_reward"], 1.0)
        self.assertTrue(state["phase2_completed"])
        self.assertTrue(state["task_done"])
        self.assertEqual(state["stop_reason"], "phase2_completed")
        self.assertEqual(len(self.submit_requests()), 2)
        self.assertGreaterEqual(state["budget_remaining"], 0)

    def test_failed_attempts_are_not_retried_without_a_reset(self):
        self.grader_status = 500
        self.initialize()
        first, second = self.execute(), self.execute()
        self.assertEqual(first.status_code, 502)
        self.assertEqual(second.status_code, 502)
        self.assertEqual(first.json()["detail"], second.json()["detail"])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.information_requests()), 3)
        self.assertEqual(len(self.submit_requests()), 1)

        self.initialize(reset=True)
        self.assertEqual(self.execute().status_code, 502)
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.information_requests()), 6)
        self.assertEqual(len(self.submit_requests()), 2)

    def test_model_failure_is_not_retried_without_a_reset(self):
        self.model_status = 500
        self.initialize()
        self.assertEqual(self.execute().status_code, 502)
        self.assertEqual(self.execute().status_code, 502)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.information_requests()), 3)
        self.assertEqual(self.submit_requests(), [])

    def test_repeated_run_returns_cached_outcome_without_extra_calls(self):
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        self.model_payloads = [model_reply("SELECT 1;"), model_reply("SELECT 2;")]
        self.initialize()
        first = self.execute().json()
        second = self.execute().json()
        self.assertEqual(first["response"], second["response"])
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.information_requests()), 3)
        self.assertEqual(len(self.submit_requests()), 2)
        self.assertEqual(len(self.transition_requests()), 1)

    def test_reset_false_reuses_the_session_and_reset_true_starts_over(self):
        first = self.initialize()
        self.execute()
        reused = self.initialize(reset=False)
        self.assertEqual(first["session_id"], reused["session_id"])
        self.execute()
        self.assertEqual(len(self.model_requests), 1)
        fresh = self.initialize(reset=True)
        self.assertNotEqual(first["session_id"], fresh["session_id"])
        self.execute()
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.information_requests()), 6)
        self.assertEqual(len(self.submit_requests()), 2)

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

    def test_missing_api_key_is_reported_after_the_information_round(self):
        def forbidden(request):
            raise AssertionError("No model request may be sent without a key")

        service_requests = []

        def service_handler(request):
            path = request.url.path
            service_requests.append(path)
            if path in OFFICIAL_INFORMATION:
                return httpx.Response(200, json=OFFICIAL_INFORMATION[path])
            return httpx.Response(200, json=dict(GRADER_FAILURE))

        with mock.patch.dict(os.environ, {"CMD_API_KEY": ""}):
            client = TestClient(create_app(
                Environment(httpx.MockTransport(service_handler)),
                ModelClient(transport=httpx.MockTransport(forbidden)),
            ))
            client.post("/init_session", json={
                "task_id": "test-task", "mode": "a-interact", "reset": True,
                "state": {"task_id": "test-task", "db_name": "demo",
                          "user_query": "How many customers?", "budget_remaining": 18},
            })
            response = client.post("/run_session", json={
                "task_id": "test-task", "mode": "a-interact", "message": "Answer.",
            })
        self.assertEqual(response.status_code, 502)
        self.assertIn("CMD_API_KEY", response.json()["detail"])
        self.assertEqual(service_requests, ["/schema", "/all_column_meanings", "/knowledge"])


class LoopFailureStateTests(unittest.IsolatedAsyncioTestCase):
    """Failure runs inspected on the state dict, which HTTP 502 responses omit."""

    def setUp(self):
        self.service_requests = []
        self.model_requests = []

    def environment(self, handler) -> Environment:
        return Environment(httpx.MockTransport(handler))

    def model_client(self, status: int = 200) -> ModelClient:
        def handler(request):
            self.model_requests.append(json.loads(request.content))
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=model_reply())

        return ModelClient(transport=httpx.MockTransport(handler), api_key="test-key")

    def state(self, budget: float = 18) -> dict:
        return {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": budget, "initial_budget": budget,
            "total_reward": 0.0, "tool_trajectory": [], "adk_events": [],
        }

    async def test_information_fetch_failure_records_the_attempt(self):
        def handler(request):
            path = request.url.path
            self.service_requests.append(path)
            if path == "/schema":
                return httpx.Response(500, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=OFFICIAL_INFORMATION[path])

        state = self.state()
        with self.assertRaises(GradingError) as caught:
            await run(state, "Answer.", self.environment(handler), self.model_client())

        self.assertIn("Schema fetch", str(caught.exception))
        self.assertEqual(self.service_requests, ["/schema"])
        self.assertEqual(self.model_requests, [])
        self.assertEqual(state["failure"]["stage"], "information")
        self.assertEqual(state["budget_remaining"], 17)
        self.assertNotIn("loop_stopped", state)
        attempt = state["tool_trajectory"][0]
        self.assertEqual(attempt["tool"], "get_schema")
        self.assertEqual(attempt["cost"], 1.0)
        self.assertEqual(attempt["budget_before"], 18)
        self.assertEqual(attempt["budget_after"], 17)
        self.assertIn("Schema fetch", attempt["result"]["error"])

    async def test_model_failure_does_not_charge_a_submission(self):
        def handler(request):
            self.service_requests.append(request.url.path)
            return httpx.Response(200, json=OFFICIAL_INFORMATION[request.url.path])

        state = self.state()
        with self.assertRaises(ModelError):
            await run(state, "Answer.", self.environment(handler), self.model_client(status=500))

        self.assertEqual(len(self.service_requests), 3)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(state["failure"]["stage"], "model")
        self.assertEqual(state["budget_remaining"], 15)
        self.assertEqual([entry["tool"] for entry in state["tool_trajectory"]], [
            "get_schema", "get_all_column_meanings", "get_all_knowledge_definitions",
        ])
        self.assertEqual(state["adk_events"], [])

    async def test_submit_failure_records_the_attempt(self):
        def handler(request):
            path = request.url.path
            self.service_requests.append(path)
            if path == "/submit":
                return httpx.Response(500, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=OFFICIAL_INFORMATION[path])

        state = self.state()
        with self.assertRaises(GradingError):
            await run(state, "Answer.", self.environment(handler), self.model_client())

        self.assertEqual(state["failure"]["stage"], "grader")
        self.assertEqual(state["budget_remaining"], 12)
        attempt = state["tool_trajectory"][3]
        self.assertEqual(attempt["tool"], "submit_sql")
        self.assertEqual(attempt["cost"], 3.0)
        self.assertEqual(attempt["budget_before"], 15)
        self.assertEqual(attempt["budget_after"], 12)
        self.assertNotIn("last_submit", state)

    async def test_transition_failure_keeps_the_phase1_result(self):
        def handler(request):
            path = request.url.path
            self.service_requests.append(path)
            if path == "/phase_transition":
                return httpx.Response(500, json={"error": {"message": "boom"}})
            if path == "/submit":
                return httpx.Response(200, json=dict(PHASE1_WITH_FOLLOW_UP))
            return httpx.Response(200, json=OFFICIAL_INFORMATION[path])

        state = self.state()
        with self.assertRaises(GradingError):
            await run(state, "Answer.", self.environment(handler), self.model_client())

        self.assertEqual(
            self.service_requests,
            ["/schema", "/all_column_meanings", "/knowledge", "/submit", "/phase_transition"],
        )
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(state["failure"]["stage"], "transition")
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertEqual(state["current_phase"], 2)
        self.assertEqual(state["follow_up_query"], "And by region?")
        self.assertNotIn("loop_stopped", state)


class ConcurrentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model_requests = []
        self.service_requests = []
        self.model_status = 200

        async def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            await asyncio.sleep(0.05)
            if self.model_status != 200:
                return httpx.Response(self.model_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=model_reply())

        async def service_handler(request):
            path = request.url.path
            self.service_requests.append(path)
            if path == "/submit":
                return httpx.Response(200, json=dict(GRADER_FAILURE))
            return httpx.Response(200, json=OFFICIAL_INFORMATION[path])

        app = create_app(
            Environment(httpx.MockTransport(service_handler)),
            ModelClient(transport=httpx.MockTransport(model_handler), api_key="test-key"),
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

    async def run_twice(self):
        payload = {"task_id": "test-task", "mode": "a-interact", "message": "Answer."}
        return await asyncio.gather(
            self.client.post("/run_session", json=payload),
            self.client.post("/run_session", json=payload),
        )

    async def test_simultaneous_requests_do_not_duplicate_calls(self):
        await self.initialize()
        responses = await self.run_twice()
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(
            self.service_requests,
            ["/schema", "/all_column_meanings", "/knowledge", "/submit"],
        )
        self.assertEqual(
            responses[0].json()["response"], responses[1].json()["response"]
        )

    async def test_simultaneous_requests_after_a_failure_do_not_retry(self):
        self.model_status = 500
        await self.initialize()
        responses = await self.run_twice()
        self.assertEqual([response.status_code for response in responses], [502, 502])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(
            self.service_requests, ["/schema", "/all_column_meanings", "/knowledge"]
        )


if __name__ == "__main__":
    unittest.main()
