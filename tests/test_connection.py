"""Session API contract checks with a mock model and grader; not BIRD scores."""

import asyncio
import json
import os
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from agent.environment import Environment
from agent.model import API_URL, MAX_OUTPUT_TOKENS, MODEL_ID, ModelClient
from agent.server import AGENT_NAME, create_app


def model_reply(content: str = "SELECT 1;", finish_reason: str = "stop") -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


GRADER_FAILURE = {"passed": False, "reward": 0.0, "message": "Your SQL is not correct."}


class SessionContractTests(unittest.TestCase):
    def setUp(self):
        self.model_requests = []
        self.grader_requests = []
        self.model_status = 200
        self.model_payload = model_reply()
        self.model_raw_text = None
        self.model_error = None
        self.grader_status = 200
        self.grader_payload = dict(GRADER_FAILURE)
        self.grader_raw_text = None

        def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            if self.model_error is not None:
                raise self.model_error
            if self.model_raw_text is not None:
                return httpx.Response(200, content=self.model_raw_text.encode())
            if self.model_status != 200:
                return httpx.Response(self.model_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=self.model_payload)

        def grader_handler(request):
            self.grader_requests.append((request.url.path, json.loads(request.content)))
            if self.grader_raw_text is not None:
                return httpx.Response(self.grader_status, content=self.grader_raw_text.encode())
            return httpx.Response(self.grader_status, json=self.grader_payload)

        self.app = create_app(
            Environment(httpx.MockTransport(grader_handler)),
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

    def test_health_identifies_the_phase1_baseline(self):
        health = self.client.get("/health").json()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["service"], "system_agent")
        self.assertEqual(health["agent"], AGENT_NAME)
        self.assertEqual(health["model"], MODEL_ID)

    def test_model_request_carries_only_the_question_and_database(self):
        self.state["internal_note"] = "do-not-send"
        self.initialize()
        response = self.execute()
        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(self.model_requests), 1)
        request = self.model_requests[0]
        self.assertEqual(request["model"], MODEL_ID)
        self.assertFalse(request["stream"])
        self.assertEqual(request["max_tokens"], MAX_OUTPUT_TOKENS)
        system, user = request["messages"]
        self.assertEqual(system["role"], "system")
        self.assertIn("postgresql", system["content"].lower())
        self.assertEqual(user["role"], "user")
        self.assertIn("How many customers?", user["content"])
        self.assertIn("demo", user["content"])
        self.assertNotIn("do-not-send", json.dumps(request))

        self.assertEqual(
            self.grader_requests,
            [("/submit", {"task_id": "test-task", "sql": "SELECT 1;"})],
        )

    def test_official_result_and_model_call_are_recorded_in_state(self):
        self.initialize()
        state = self.execute().json()["state"]

        self.assertEqual(state["total_reward"], 0.0)
        self.assertEqual(state["budget_remaining"], 15)
        self.assertFalse(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertFalse(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["stop_reason"], "phase1_baseline_failed")
        self.assertEqual(state["final_response"], "Your SQL is not correct.")
        self.assertEqual(state["last_submit"]["passed"], False)
        self.assertEqual(state["last_submit"]["message"], "Your SQL is not correct.")

        self.assertEqual(len(state["adk_events"]), 1)
        event = state["adk_events"][0]
        self.assertEqual(event["type"], "model_call")
        self.assertEqual(event["call_index"], 1)
        self.assertEqual(event["request_model"], MODEL_ID)
        self.assertEqual(event["response_model"], MODEL_ID)
        self.assertEqual(event["sql"], "SELECT 1;")
        self.assertEqual(event["usage"]["total_tokens"], 14)
        self.assertEqual(event["settings"]["endpoint"], API_URL)
        self.assertEqual(event["settings"]["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertGreaterEqual(event["elapsed_seconds"], 0)

        submit = state["tool_trajectory"][0]
        self.assertEqual(submit["tool"], "submit_sql")
        self.assertEqual(submit["args"], {"sql": "SELECT 1;"})
        self.assertEqual(submit["cost"], 3.0)
        self.assertEqual(submit["budget_before"], 18)
        self.assertEqual(submit["budget_after"], 15)
        self.assertNotIn("test-key", json.dumps(state))

    def test_fenced_dml_response_is_submitted_unchanged(self):
        sql = "UPDATE customers SET active = true;"
        self.model_payload = model_reply(f"```sql\n{sql}\n```")
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(self.grader_requests[0][1], {"task_id": "test-task", "sql": sql})
        self.assertEqual(state["adk_events"][0]["sql"], sql)
        self.assertEqual(state["tool_trajectory"][0]["args"]["sql"], sql)

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
                grader_before = len(self.grader_requests)
                self.initialize()
                response = self.execute()
                self.assertEqual(response.status_code, 502)
                self.assertIn("Model service error", response.json()["detail"])
                self.assertEqual(len(self.grader_requests) - grader_before, 0)
                self.assertNotIn("test-key", response.text)

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
                model_before = len(self.model_requests)
                grader_before = len(self.grader_requests)
                self.initialize()
                response = self.execute()
                self.assertEqual(response.status_code, 502)
                self.assertIn("Benchmark service error", response.json()["detail"])
                self.assertEqual(len(self.model_requests) - model_before, 1)
                self.assertEqual(len(self.grader_requests) - grader_before, 1)

    def test_failed_attempts_are_not_retried_without_a_reset(self):
        self.grader_status = 500
        self.initialize()
        first, second = self.execute(), self.execute()
        self.assertEqual(first.status_code, 502)
        self.assertEqual(second.status_code, 502)
        self.assertEqual(first.json()["detail"], second.json()["detail"])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.grader_requests), 1)

        self.initialize(reset=True)
        self.assertEqual(self.execute().status_code, 502)
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.grader_requests), 2)

    def test_model_failure_is_not_retried_without_a_reset(self):
        self.model_status = 500
        self.initialize()
        self.assertEqual(self.execute().status_code, 502)
        self.assertEqual(self.execute().status_code, 502)
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(self.grader_requests, [])

    def test_repeated_run_returns_cached_outcome_without_extra_calls(self):
        self.initialize()
        first = self.execute().json()
        second = self.execute().json()
        self.assertEqual(first["response"], second["response"])
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.grader_requests), 1)

    def test_phase1_success_with_follow_up_skips_phase2(self):
        self.grader_payload = {
            "passed": True, "reward": 0.7, "message": "Phase 1 correct! Moving to Phase 2.",
            "phase_completed": 1, "has_follow_up": True, "follow_up_query": "And by region?",
        }
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertFalse(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.assertEqual(state["stop_reason"], "phase1_baseline_phase2_skipped")
        self.assertEqual(state["follow_up_query"], "And by region?")
        self.assertEqual(state["current_phase"], 1)
        self.assertEqual([path for path, _ in self.grader_requests], ["/submit"])

    def test_phase1_success_without_follow_up_completes_the_task(self):
        self.grader_payload = {
            "passed": True, "reward": 0.7, "message": "Phase 1 correct! Task finished.",
            "phase_completed": 1, "has_follow_up": False,
        }
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertFalse(state["phase2_completed"])
        self.assertTrue(state["task_done"])
        self.assertEqual(state["stop_reason"], "phase1_baseline_completed")

    def test_low_budget_still_allows_the_final_submission(self):
        self.state["budget_remaining"] = 2
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["budget_remaining"], -1.0)
        self.assertEqual(len(self.grader_requests), 1)

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
        self.assertEqual(len(self.grader_requests), 2)

    def test_run_before_init_session_is_not_found(self):
        response = self.execute()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.model_requests, [])

    def test_missing_question_is_an_input_error_without_a_model_call(self):
        self.initialize(state={"task_id": "test-task", "db_name": "demo", "budget_remaining": 18})
        response = self.execute(message="")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.model_requests, [])

    def test_missing_api_key_is_reported_and_makes_no_request(self):
        def forbidden(request):
            raise AssertionError("No model request may be sent without a key")

        with mock.patch.dict(os.environ, {"CMD_API_KEY": ""}):
            client = TestClient(create_app(
                Environment(httpx.MockTransport(lambda request: httpx.Response(200, json={}))),
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


class ConcurrentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model_requests = []
        self.grader_requests = []
        self.model_status = 200

        async def model_handler(request):
            self.model_requests.append(json.loads(request.content))
            await asyncio.sleep(0.05)
            if self.model_status != 200:
                return httpx.Response(self.model_status, json={"error": {"message": "boom"}})
            return httpx.Response(200, json=model_reply())

        async def grader_handler(request):
            self.grader_requests.append(request.url.path)
            return httpx.Response(200, json=dict(GRADER_FAILURE))

        app = create_app(
            Environment(httpx.MockTransport(grader_handler)),
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
        self.assertEqual(self.grader_requests, ["/submit"])
        self.assertEqual(
            responses[0].json()["response"], responses[1].json()["response"]
        )

    async def test_simultaneous_requests_after_a_failure_do_not_retry(self):
        self.model_status = 500
        await self.initialize()
        responses = await self.run_twice()
        self.assertEqual([response.status_code for response in responses], [502, 502])
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(self.grader_requests, [])


if __name__ == "__main__":
    unittest.main()
