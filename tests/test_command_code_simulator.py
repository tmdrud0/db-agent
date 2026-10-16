"""Contract tests for the Command Code user-simulator adapter; no real model calls.

The adapter is started instead of the vendor app in compose, so these tests
check the official provider seam, the replaced /health, /init_task and /ask
routes, per-request failure reporting and per-request metric isolation.
"""

import asyncio
import json
import logging
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "user_sim"))
sys.path.insert(0, str(ROOT / "vendor" / "BIRD-Interact" / "BIRD-Interact-ADK"))

import command_code_simulator as simulator  # noqa: E402
from user_simulator import server as official  # noqa: E402

TASK_ID = "adapter-task-1"
TASK_DATA = {
    "instance_id": TASK_ID,
    "selected_database": "demo",
    "amb_user_query": "How many customers are there?",
    "query": "How many customers are there in total?",
    "sol_sql": ["SELECT count(*) FROM customers;"],
    "user_query_ambiguity": {"critical_ambiguity": [{"term": "customers", "reason": "scope"}]},
    "knowledge_ambiguity": [],
    "follow_up": {},
}
SCHEMA_TEXT = "CREATE TABLE customers (id integer);"
ENV = {"CMD_API_KEY": "test-key", "USER_SIM_MODEL": "deepseek/deepseek-v4.1-flash"}


def completion(content: str, finish_reason: str = "stop") -> dict:
    return {
        "model": "deepseek/deepseek-v4.1-flash",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 41, "completion_tokens": 7, "total_tokens": 48},
    }


def answer_content(answer: str) -> str:
    """The official generator extracts the text between the <s> markers."""
    return f"<s>{answer}</s>"


def answer_for(request: httpx.Request) -> str:
    prompt = json.loads(request.content)["messages"][0]["content"]
    if "Q-alpha" in prompt:
        return "alpha answer"
    if "Q-beta" in prompt:
        return "beta answer"
    raise AssertionError("unexpected prompt")


def completion_client(handler) -> mock._patch:
    transport = httpx.MockTransport(handler)
    return mock.patch.object(
        simulator, "_completion_client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )


class ModelCallTests(unittest.TestCase):
    """call_llm is the official provider extension point."""

    def setUp(self):
        self.requests = []
        self.reply = lambda request: httpx.Response(200, json=completion(answer_content("ok")))

    def handler(self, request):
        self.requests.append(request)
        return self.reply(request)

    def test_the_command_code_request_uses_the_specified_settings(self):
        with mock.patch.dict(os.environ, ENV), completion_client(self.handler):
            content = simulator.call_llm(
                [{"role": "user", "content": "prompt"}], model_name="anthropic/other",
                temperature=0, max_tokens=500,
            )
        self.assertEqual(content, answer_content("ok"))
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), simulator.API_URL)
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        payload = json.loads(request.content)
        self.assertEqual(payload["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["max_tokens"], simulator.MAX_OUTPUT_TOKENS)
        self.assertEqual(payload["temperature"], 0)
        for absent in ("reasoning", "reasoning_effort", "effort"):
            self.assertNotIn(absent, payload)

    def test_failures_raise_instead_of_returning_an_empty_answer(self):
        cases = {
            "http error": lambda request: httpx.Response(500, json={"error": {"message": "boom"}}),
            "invalid json": lambda request: httpx.Response(200, content=b"<html>not json</html>"),
            "empty content": lambda request: httpx.Response(200, json=completion("  ")),
            "no choices": lambda request: httpx.Response(200, json={"model": "x"}),
        }
        for name, reply in cases.items():
            with self.subTest(name=name):
                self.requests = []
                self.reply = reply
                with mock.patch.dict(os.environ, ENV), completion_client(self.handler):
                    with self.assertRaises(simulator.SimulatorModelError):
                        simulator.call_llm([{"role": "user", "content": "prompt"}])
                self.assertEqual(len(self.requests), 1)

    def test_a_missing_key_is_reported_without_a_request(self):
        with mock.patch.dict(os.environ, {**ENV, "CMD_API_KEY": ""}), \
                completion_client(self.handler):
            with self.assertRaises(simulator.SimulatorModelError) as caught:
                simulator.call_llm([{"role": "user", "content": "prompt"}])
        self.assertIn("CMD_API_KEY", str(caught.exception))
        self.assertEqual(self.requests, [])

    def test_calls_are_recorded_per_request_without_prompt_text(self):
        recorded: list = []
        token = simulator._request_calls.set(recorded)
        try:
            with mock.patch.dict(os.environ, ENV), completion_client(self.handler):
                simulator.call_llm([{"role": "user", "content": "prompt"}])
        finally:
            simulator._request_calls.reset(token)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["usage"]["total_tokens"], 48)
        self.assertGreaterEqual(recorded[0]["elapsed_seconds"], 0)
        self.assertEqual(sorted(recorded[0]), ["elapsed_seconds", "usage"])
        summary = simulator._summarize(recorded, 0.0)
        self.assertEqual(summary["model_calls"], 1)
        self.assertEqual(summary["prompt_tokens"], 41)
        self.assertEqual(summary["completion_tokens"], 7)
        self.assertEqual(summary["errors"], [])

    def test_failures_are_also_recorded(self):
        recorded: list = []
        token = simulator._request_calls.set(recorded)
        try:
            self.reply = lambda request: httpx.Response(500, json={"error": {"message": "boom"}})
            with mock.patch.dict(os.environ, ENV), completion_client(self.handler):
                with self.assertRaises(simulator.SimulatorModelError):
                    simulator.call_llm([{"role": "user", "content": "prompt"}])
        finally:
            simulator._request_calls.reset(token)
        summary = simulator._summarize(recorded, 0.0)
        self.assertEqual(summary["model_calls"], 1)
        self.assertTrue(summary["errors"])

    def test_settings_can_be_set_explicitly_and_are_validated(self):
        with mock.patch.dict(os.environ, {
            **ENV, "USER_SIM_TIMEOUT_SECONDS": "12.5", "USER_SIM_MAX_OUTPUT_TOKENS": "256",
        }):
            settings = simulator.model_settings()
        self.assertEqual(settings["timeout_seconds"], 12.5)
        self.assertEqual(settings["max_output_tokens"], 256)
        self.assertEqual(settings["max_retries"], 0)
        with mock.patch.dict(os.environ, {**ENV, "USER_SIM_TIMEOUT_SECONDS": "0"}):
            with self.assertRaises(ValueError):
                simulator.model_settings()

    def test_the_provider_extension_point_and_the_official_seam_are_wired(self):
        simulator.install_provider()
        extension = sys.modules["shared._local_provider"]
        self.assertIs(extension.call_llm, simulator.call_llm)
        with self.assertRaises(NotImplementedError):
            extension.build_adk_model()

        class FakeOfficial:
            settings = mock.Mock(user_sim_model="anthropic/default")

        simulator.install_failing_seam(FakeOfficial)
        shared_llm = __import__("shared.llm", fromlist=["call_llm"])
        self.assertIs(shared_llm.call_llm, simulator.call_llm)
        boom = simulator.SimulatorModelError("boom")
        with mock.patch.object(shared_llm, "call_llm", side_effect=boom) as patched:
            with self.assertRaises(simulator.SimulatorModelError):
                FakeOfficial._call_llm("prompt", max_tokens=500)
        patched.assert_called_once()
        self.assertEqual(
            patched.call_args.kwargs,
            {"model_name": "anthropic/default", "temperature": 0, "max_tokens": 500},
        )
        self.assertEqual(
            patched.call_args.args, ([{"role": "user", "content": "prompt"}],),
        )


class AdapterAppTests(unittest.TestCase):
    def setUp(self):
        official._task_states.clear()
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.replies = []

        def handler(request):
            if not self.replies:
                raise AssertionError("The adapter sent more model calls than the test provides")
            return self.replies.pop(0)

        self.client_patch = completion_client(handler)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.app = simulator.create_app()
        self.client = TestClient(self.app)

    def health(self) -> dict:
        return self.client.get("/health").json()

    def register_task(self, task_id: str = TASK_ID, db_schema: str = SCHEMA_TEXT):
        state = official.TaskSimState(dict(TASK_DATA, instance_id=task_id))
        state.db_schema = db_schema
        official._task_states[task_id] = state
        return state

    def test_adapter_installs_the_official_routes_and_logs_once(self):
        paths = [getattr(route, "path", None) for route in self.app.router.routes]
        self.assertEqual(paths.count("/ask"), 1)
        self.assertEqual(paths.count("/health"), 1)
        self.assertEqual(paths.count("/init_task"), 1)
        self.assertEqual(simulator.logger.level, logging.INFO)
        self.assertEqual(len(simulator.logger.handlers), 1)
        simulator.create_app()
        self.assertEqual(len(simulator.logger.handlers), 1)

    def test_health_reports_the_connection_without_the_key(self):
        health = self.health()
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["service"], "user_simulator_command_code")
        self.assertEqual(health["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(health["endpoint"], simulator.API_URL)
        self.assertEqual(health["prompt_version"], "v2")
        self.assertEqual(health["max_output_tokens"], 16384)
        self.assertEqual(health["timeout_seconds"], 300.0)
        self.assertEqual(health["temperature"], 0)
        self.assertEqual(health["max_retries"], 0)
        self.assertTrue(health["api_key_present"])
        self.assertNotIn("test-key", json.dumps(health))

    def test_ask_returns_the_official_answer_and_counts_the_model_calls(self):
        before = self.health()["model_calls"]
        self.register_task()
        self.replies = [
            httpx.Response(200, json=completion("<think>reasoning</think><s>AMB</s>")),
            httpx.Response(200, json=completion(answer_content("The total count."))),
        ]
        response = self.client.post("/ask", json={"task_id": TASK_ID, "question": "How many?"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"answer": "The total count."})
        health = self.health()
        self.assertEqual(health["model_calls"] - before, 2)
        self.assertEqual(health["model_failures"], 0)
        self.assertNotIn("test-key", response.text)
        self.assertEqual(health["prompt_tokens"] > 0, True)

    def test_model_failures_are_service_errors_and_never_answers(self):
        cases = {
            "http error": httpx.Response(500, json={"error": {"message": "boom"}}),
            "empty completion": httpx.Response(200, json=completion("")),
        }
        before = self.health()["model_failures"]
        for name, reply in cases.items():
            with self.subTest(name=name):
                official._task_states.clear()
                self.register_task()
                self.replies = [reply]
                response = self.client.post(
                    "/ask", json={"task_id": TASK_ID, "question": "How many?"}
                )
                self.assertEqual(response.status_code, 502, response.text)
                self.assertIn("Command Code model call failed", response.json()["detail"])
                self.assertNotIn("I'm not sure", response.text)
                self.assertNotIn("answer", response.json())
        self.assertEqual(self.health()["model_failures"] - before, len(cases))

    def test_stage_two_failure_is_reported_instead_of_a_partial_answer(self):
        self.register_task()
        self.replies = [
            httpx.Response(200, json=completion("<s>AMB</s>")),
            httpx.Response(500, json={"error": {"message": "boom"}}),
        ]
        response = self.client.post("/ask", json={"task_id": TASK_ID, "question": "How many?"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("Command Code model call failed", response.json()["detail"])

    def test_ask_for_an_unknown_task_is_not_found(self):
        response = self.client.post("/ask", json={"task_id": "missing", "question": "How many?"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.replies, [])

    def test_init_task_loads_the_schema_from_the_configured_db_environment(self):
        initialized = []

        async def fake_official_init(req):
            initialized.append(req.task_id)
            official._task_states[req.task_id] = official.TaskSimState(req.task_data)
            return {"status": "ok", "task_id": req.task_id}

        async def fake_schema(task_id):
            self.assertEqual(task_id, TASK_ID)
            return SCHEMA_TEXT

        before = self.health()["schema_failures"]
        with mock.patch.object(official, "init_task", fake_official_init), \
                mock.patch.object(simulator, "fetch_task_schema", fake_schema):
            response = self.client.post(
                "/init_task", json={"task_id": TASK_ID, "task_data": TASK_DATA}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"status": "ok", "task_id": TASK_ID})
        self.assertEqual(initialized, [TASK_ID])
        self.assertEqual(official._task_states[TASK_ID].db_schema, SCHEMA_TEXT)
        self.assertEqual(self.health()["schema_failures"] - before, 0)

    def test_init_task_reports_a_schema_failure_instead_of_an_empty_prompt(self):
        async def fake_official_init(req):
            official._task_states[req.task_id] = official.TaskSimState(req.task_data)
            return {"status": "ok", "task_id": req.task_id}

        async def broken_schema(task_id):
            raise httpx.ConnectError("connection refused")

        before = self.health()["schema_failures"]
        with mock.patch.object(official, "init_task", fake_official_init), \
                mock.patch.object(simulator, "fetch_task_schema", broken_schema):
            response = self.client.post(
                "/init_task", json={"task_id": TASK_ID, "task_data": TASK_DATA}
            )
        self.assertEqual(response.status_code, 502)
        self.assertIn("schema", response.json()["detail"])
        self.assertEqual(self.health()["schema_failures"] - before, 1)

    def test_schema_fetch_uses_the_configured_db_environment_url(self):
        requests = []

        def handler(request):
            requests.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, json={"schema": SCHEMA_TEXT})

        transport = httpx.MockTransport(handler)
        with mock.patch.dict(os.environ, {"DB_ENV_URL": "http://db-environment:6002"}), \
                mock.patch.object(
                    simulator, "_async_client",
                    lambda timeout: httpx.AsyncClient(timeout=timeout, transport=transport),
                ):
            schema = asyncio.run(simulator.fetch_task_schema(TASK_ID))
        self.assertEqual(schema, SCHEMA_TEXT)
        self.assertEqual(requests, [("http://db-environment:6002/schema", {"task_id": TASK_ID})])

    def test_phase_transition_and_debug_state_stay_official(self):
        state = self.register_task()
        response = self.client.post("/phase_transition", json={"task_id": TASK_ID})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["phase"], 2)
        self.assertEqual(state.current_phase, 2)
        debug = self.client.get(f"/debug_state/{TASK_ID}").json()
        self.assertEqual(debug["current_phase"], 2)
        self.assertEqual(debug["db_name"], "demo")


class AdapterConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Model-call state must stay inside the request that produced it."""

    def handler(self, request):
        # A real edit of the prompts is not needed: the question text is part of
        # every stage prompt, so the answer is derived from the prompt itself.
        answer = answer_for(request)
        time.sleep(0.02)
        return httpx.Response(200, json=completion(answer_content(answer)))

    async def asyncSetUp(self):
        official._task_states.clear()
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.addAsyncCleanup(self.env.stop)
        self.client_patch = completion_client(self.handler)
        self.client_patch.start()
        self.addAsyncCleanup(self.client_patch.stop)
        self.app = simulator.create_app()
        for task_id in ("Q-alpha", "Q-beta"):
            state = official.TaskSimState(dict(TASK_DATA, instance_id=task_id))
            state.db_schema = SCHEMA_TEXT
            official._task_states[task_id] = state
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://simulator"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def test_concurrent_questions_keep_their_own_answers(self):
        before = (await self.client.get("/health")).json()["model_calls"]
        responses = await asyncio.gather(
            self.client.post("/ask", json={"task_id": "Q-alpha", "question": "Q-alpha please"}),
            self.client.post("/ask", json={"task_id": "Q-beta", "question": "Q-beta please"}),
        )
        self.assertEqual([response.status_code for response in responses], [200, 200])
        by_task = {response.json()["answer"]: None for response in responses}
        self.assertEqual(sorted(by_task), ["alpha answer", "beta answer"])
        after = (await self.client.get("/health")).json()["model_calls"]
        self.assertEqual(after - before, 4)


if __name__ == "__main__":
    unittest.main()
