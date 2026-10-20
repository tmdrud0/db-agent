"""Langfuse trace contracts with a recording fake client; not BIRD scores."""

import asyncio
import json
import os
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from agent import model as model_module
from agent import server as server_module
from agent import tool_loop, tracing
from agent.environment import Environment
from agent.model import MAX_RETRIES, MODEL_ID, ModelClient
from agent.server import AGENT_NAME, create_app
from agent.tracing import LangfuseTracer, NoopTracer

PHASE1_FAIL = {
    "passed": False,
    "reward": 0.0,
    "message": "SQL failed Phase 1. Your SQL is not correct.",
}
PHASE1_FOLLOW_UP = {
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


def action(tool: str, **args) -> str:
    return json.dumps({"action": tool, "args": args})


def model_reply(content: str) -> dict:
    return {
        "model": MODEL_ID,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    }


class RecordedObservation:
    def __init__(self, client, name: str, as_type: str, kwargs: dict, parent=None):
        self.client = client
        self.name = name
        self.as_type = as_type
        self.initial = dict(kwargs)
        self.updates = []
        self.children = []
        self.scores = []
        self.ended = False
        self.parent = parent

    def update(self, **kwargs):
        self.updates.append(dict(kwargs))
        return self

    def end(self):
        self.ended = True

    def score_trace(self, **kwargs):
        self.scores.append(dict(kwargs))

    def start_observation(self, *, name, as_type="span", **kwargs):
        child = RecordedObservation(self.client, name, as_type, kwargs, parent=self)
        self.children.append(child)
        self.client.observations.append(child)
        return child


class FakeLangfuseClient:
    def __init__(self):
        self.observations = []
        self.flush_calls = 0
        self.flush_delay = 0.0
        self.flush_error = None

    def start_observation(self, *, name, as_type="span", **kwargs):
        observation = RecordedObservation(self, name, as_type, kwargs)
        self.observations.append(observation)
        return observation

    def flush(self):
        self.flush_calls += 1
        if self.flush_delay:
            time.sleep(self.flush_delay)
        if self.flush_error is not None:
            raise self.flush_error

    @property
    def roots(self):
        return [item for item in self.observations if item.parent is None]


class FakeSdkClient(FakeLangfuseClient):
    instances = []

    def __init__(self, **kwargs):
        super().__init__()
        self.init_kwargs = kwargs
        FakeSdkClient.instances.append(self)


def recording_tracer(client=None, **overrides):
    client = client or FakeLangfuseClient()
    options = {
        "eval_run_id": "eval-run-1",
        "code_version": "abc123-dirty",
        "agent_name": AGENT_NAME,
        "model_id": MODEL_ID,
    }
    options.update(overrides)
    return LangfuseTracer(client, lambda **kwargs: nullcontext(), **options), client


class RaisingFlushTracer(NoopTracer):
    async def flush(self, timeout=tracing.FLUSH_TIMEOUT_SECONDS):
        raise RuntimeError("flush failed")


class TracingSessionTestCase(unittest.TestCase):
    """A scripted model and grader through the HTTP server with a recording tracer."""

    def setUp(self):
        self.model_requests = []
        self.actions = []
        self.model_status = 200
        self.failing_attempts = 0
        self.grader_payloads = []
        self.grader_status = 200
        self.tracer, self.langfuse = recording_tracer()

    def model_handler(self, request):
        self.model_requests.append(json.loads(request.content))
        if self.model_status != 200:
            return httpx.Response(self.model_status, json={"error": {"message": "boom"}})
        if self.failing_attempts > 0:
            self.failing_attempts -= 1
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=model_reply(self.actions.pop(0)))

    def service_handler(self, request):
        path = request.url.path
        if path == "/submit":
            if self.grader_status != 200:
                return httpx.Response(self.grader_status, json={"error": {"message": "boom"}})
            payload = (
                self.grader_payloads.pop(0) if self.grader_payloads else PHASE1_FAIL
            )
            return httpx.Response(200, json=payload)
        if path == "/phase_transition":
            return httpx.Response(200, json={"status": "ok", "phase": 2})
        raise AssertionError(f"Unexpected service request: {path}")

    def app(self, tracer=None):
        return create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(
                transport=httpx.MockTransport(self.model_handler), api_key="test-key"
            ),
            mode="tools",
            tracer=tracer if tracer is not None else self.tracer,
        )

    def run_again(self, client) -> httpx.Response:
        return client.post("/run_session", json={
            "task_id": "test-task", "mode": "a-interact", "message": "Answer.",
        })

    def run_session(self, client, budget: float = 18) -> httpx.Response:
        initialized = client.post("/init_session", json={
            "task_id": "test-task", "mode": "a-interact", "reset": True,
            "state": {
                "task_id": "test-task", "db_name": "demo",
                "user_query": "How many customers?", "budget_remaining": budget,
            },
        })
        self.assertEqual(initialized.status_code, 200)
        return self.run_again(client)

    def root(self):
        roots = self.langfuse.roots
        self.assertEqual(len(roots), 1)
        return roots[0]

    def generations(self, root):
        return [item for item in root.children if item.as_type == "generation"]

    def tool_spans(self, root):
        return [
            item for item in root.children
            if item.as_type == "span" and item.name != "phase_transition"
        ]


class SuccessfulTraceTests(TracingSessionTestCase):
    def test_one_session_records_model_calls_tool_spans_scores_and_phase_two(self):
        first = "SELECT count(*) FROM customers;"
        second = "SELECT count(id) FROM customers;"
        third = "SELECT region, count(*) FROM customers GROUP BY region;"
        script = [
            action("submit_sql", sql=first),
            action("submit_sql", sql=second),
            action("submit_sql", sql=third),
        ]
        self.actions = list(script)
        self.grader_payloads = [dict(PHASE1_FAIL), dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        with TestClient(self.app()) as client:
            response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()["state"]

        root = self.root()
        self.assertIn("test-task", root.name)
        self.assertIn(AGENT_NAME, root.name)
        self.assertEqual(root.initial["input"], "How many customers?")
        self.assertEqual(root.initial["version"], "abc123-dirty")
        metadata = root.initial["metadata"]
        self.assertEqual(metadata["task_id"], "test-task")
        self.assertEqual(metadata["agent"], AGENT_NAME)
        self.assertEqual(metadata["model"], MODEL_ID)
        self.assertEqual(metadata["eval_run_id"], "eval-run-1")
        self.assertEqual(metadata["code_version"], "abc123-dirty")
        self.assertTrue(root.ended)

        # The trace output and scores come from the official state.
        final = root.updates[-1]
        self.assertEqual(final["output"], PHASE2_PASS["message"])
        self.assertEqual(final["metadata"]["total_reward"], 1.0)
        self.assertEqual(final["metadata"]["phase1_completed"], True)
        self.assertEqual(final["metadata"]["phase2_completed"], True)
        self.assertEqual(final["metadata"]["stop_reason"], "phase2_completed")
        self.assertEqual(state["total_reward"], 1.0)
        self.assertEqual(state["stop_reason"], "phase2_completed")

        scores = {score["name"]: score for score in root.scores}
        self.assertEqual(
            set(scores),
            {"total_reward", "phase1_completed", "phase2_completed", "stop_reason"},
        )
        self.assertEqual(scores["total_reward"]["value"], state["total_reward"])
        self.assertEqual(scores["total_reward"]["data_type"], "NUMERIC")
        self.assertEqual(scores["phase1_completed"]["value"], 1.0)
        self.assertEqual(scores["phase1_completed"]["data_type"], "BOOLEAN")
        self.assertEqual(scores["phase2_completed"]["value"], 1.0)
        self.assertEqual(scores["phase2_completed"]["data_type"], "BOOLEAN")
        self.assertEqual(scores["stop_reason"]["value"], state["stop_reason"])
        self.assertEqual(scores["stop_reason"]["data_type"], "CATEGORICAL")

        # One generation per model call and one span per executed tool action.
        self.assertEqual(len(self.model_requests), 3)
        self.assertEqual(len(self.generations(root)), len(self.model_requests))
        self.assertEqual(len(self.tool_spans(root)), len(state["tool_trajectory"]))
        self.assertTrue(all(item.ended for item in self.langfuse.observations))

        generations = self.generations(root)
        for index, generation in enumerate(generations):
            update = generation.updates[0]
            self.assertEqual(generation.initial["input"], self.model_requests[index]["messages"])
            self.assertEqual(generation.initial["model"], MODEL_ID)
            self.assertEqual(update["model"], MODEL_ID)
            self.assertEqual(update["output"], script[index])
            self.assertEqual(
                update["usage_details"], {"input": 11, "output": 3, "total": 14}
            )
            self.assertEqual(update["metadata"]["attempts"], 1)
            self.assertFalse(update["metadata"]["truncated"])
            self.assertEqual(update["metadata"]["action"]["name"], "submit_sql")
        self.assertEqual(
            [item.updates[0]["metadata"]["phase"] for item in generations], [1, 1, 2]
        )

        tool_spans = self.tool_spans(root)
        self.assertEqual([item.name for item in tool_spans], ["submit_sql"] * 3)
        self.assertEqual(
            [item.initial["input"]["sql"] for item in tool_spans], [first, second, third]
        )
        self.assertEqual(
            [item.updates[0]["metadata"]["cost"] for item in tool_spans], [3.0, 3.0, 3.0]
        )
        self.assertEqual(
            [item.updates[0]["metadata"]["budget_before"] for item in tool_spans], [18, 15, 12]
        )
        self.assertEqual(
            [item.updates[0]["metadata"]["budget_after"] for item in tool_spans], [15, 12, 9]
        )
        self.assertEqual(tool_spans[0].updates[0]["metadata"]["passed"], False)
        self.assertEqual(tool_spans[1].updates[0]["metadata"]["passed"], True)
        self.assertEqual(tool_spans[1].updates[0]["metadata"]["reward"], 0.7)
        self.assertEqual(tool_spans[2].updates[0]["metadata"]["reward"], 0.3)
        self.assertEqual(
            tool_spans[1].updates[0]["metadata"]["phase_transition"], {"completed": True}
        )
        transitions = [
            child for child in tool_spans[1].children if child.name == "phase_transition"
        ]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0].initial["metadata"]["completed"], True)
        self.assertTrue(transitions[0].ended)
        self.assertEqual(tool_spans[0].children, [])
        self.assertEqual(tool_spans[2].children, [])

        self.assertEqual(self.langfuse.flush_calls, 2)

    def test_model_retry_count_reaches_the_generation_metadata(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.failing_attempts = 2
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        self.actions.append(
            action("submit_sql", sql="SELECT region, count(*) FROM customers GROUP BY region;")
        )
        with TestClient(self.app()) as client:
            response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        metadata = self.generations(self.root())[0].updates[0]["metadata"]
        self.assertEqual(metadata["attempts"], 3)
        self.assertEqual(len(self.model_requests), 1 + MAX_RETRIES)

    def test_a_cached_rerun_does_not_create_a_new_trace(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        with TestClient(self.app()) as client:
            first = self.run_session(client)
            second = self.run_again(client)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["state"], second.json()["state"])
        self.assertEqual(len(self.langfuse.roots), 1)
        self.assertEqual(len(self.generations(self.root())), 2)
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(self.langfuse.flush_calls, 3)


class FailureTraceTests(TracingSessionTestCase):
    def test_model_failure_marks_only_the_trace_as_an_error(self):
        self.actions = []
        self.model_status = 500
        with TestClient(self.app()) as client:
            response = self.run_session(client)
        self.assertEqual(response.status_code, 502, response.text)

        root = self.root()
        self.assertEqual(root.updates[-1]["level"], "ERROR")
        self.assertIn("model:", root.updates[-1]["status_message"])
        self.assertEqual(root.updates[-1]["metadata"]["failure.stage"], "model")
        self.assertEqual(root.scores, [])
        self.assertTrue(root.ended)

        generations = self.generations(root)
        self.assertEqual(len(generations), 1)
        failure = generations[0].updates[-1]
        self.assertEqual(failure["level"], "ERROR")
        self.assertEqual(failure["metadata"]["attempts"], MAX_RETRIES + 1)
        self.assertEqual(generations[0].children, [])
        self.assertEqual(self.tool_spans(root), [])
        self.assertEqual(len(self.model_requests), MAX_RETRIES + 1)
        self.assertEqual(self.langfuse.flush_calls, 2)

    def test_grader_failure_marks_the_trace_and_the_submit_span(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_status = 500
        with TestClient(self.app()) as client:
            response = self.run_session(client)
        self.assertEqual(response.status_code, 502, response.text)

        root = self.root()
        self.assertEqual(root.updates[-1]["level"], "ERROR")
        self.assertIn("grader:", root.updates[-1]["status_message"])
        self.assertEqual(root.updates[-1]["metadata"]["failure.stage"], "grader")
        self.assertEqual(root.scores, [])
        spans = self.tool_spans(root)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].updates[-1]["level"], "ERROR")
        self.assertIn("Grader", spans[0].updates[-1]["status_message"])
        self.assertTrue(spans[0].ended)

    def test_a_failure_replay_does_not_create_a_new_trace(self):
        self.actions = []
        self.model_status = 500
        with TestClient(self.app()) as client:
            first = self.run_session(client)
            second = self.run_again(client)
        self.assertEqual([first.status_code, second.status_code], [502, 502])
        self.assertEqual(len(self.langfuse.roots), 1)
        self.assertEqual(len(self.model_requests), MAX_RETRIES + 1)
        self.assertEqual(self.langfuse.flush_calls, 3)


class FlushTests(TracingSessionTestCase):
    def test_flush_runs_before_the_response_and_failures_are_harmless(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        self.langfuse.flush_error = RuntimeError("network down")
        with TestClient(self.app()) as client:
            response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"]["total_reward"], 1.0)
        self.assertEqual(self.langfuse.flush_calls, 2)

    def test_a_tracer_whose_flush_raises_does_not_change_the_response(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        with TestClient(self.app(tracer=RaisingFlushTracer())) as client:
            response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"]["stop_reason"], "phase2_completed")

    def test_flush_is_bounded_and_excluded_from_the_session_elapsed_time(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        self.langfuse.flush_delay = 0.4
        with mock.patch.object(server_module, "FLUSH_TIMEOUT_SECONDS", 0.05):
            with TestClient(self.app()) as client:
                started = time.monotonic()
                response = self.run_session(client)
                wall_seconds = time.monotonic() - started
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLess(wall_seconds, 0.35)
        self.assertLess(response.json()["state"]["session_elapsed_seconds"], 0.2)
        self.assertEqual(self.langfuse.flush_calls, 2)


class StepClock:
    def __init__(self, value: float = 1000.0, step: float = 0.01):
        self.value = value
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class FlushParityTests(TracingSessionTestCase):
    SCRIPT = (
        action("submit_sql", sql="SELECT count(*) FROM customers;"),
        action("submit_sql", sql="SELECT count(*) FROM customers WHERE id > 0;"),
    )
    GRADER = (dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS))

    def run_scenario(self, tracer, *, model_status=200, grader_status=200):
        self.actions = list(self.SCRIPT)
        self.grader_payloads = [dict(payload) for payload in self.GRADER]
        self.model_status = model_status
        self.grader_status = grader_status
        # Both clocks are fixed: the loop records tool/session time from
        # tool_loop._now and the model reply records time from model.time.
        with mock.patch.object(tool_loop, "_now", StepClock()), \
                mock.patch.object(
                    model_module, "time", SimpleNamespace(monotonic=StepClock())
                ):
            with TestClient(self.app(tracer=tracer)) as client:
                return self.run_session(client)

    @staticmethod
    def without_session_id(response):
        body = response.json()
        body.pop("session_id", None)
        return body

    @staticmethod
    def failing_flush_tracer():
        tracer, client = recording_tracer()
        client.flush_error = RuntimeError("network down")
        return tracer

    def test_flush_failure_keeps_the_success_response_and_state_identical(self):
        without = self.run_scenario(NoopTracer())
        with_failure = self.run_scenario(self.failing_flush_tracer())
        self.assertEqual(without.status_code, with_failure.status_code)
        self.assertEqual(
            self.without_session_id(without), self.without_session_id(with_failure)
        )

    def test_flush_delay_keeps_the_success_response_and_state_identical(self):
        without = self.run_scenario(NoopTracer())
        traced, client = recording_tracer()
        client.flush_delay = 0.2
        with mock.patch.object(server_module, "FLUSH_TIMEOUT_SECONDS", 0.05):
            with_delay = self.run_scenario(traced)
        self.assertEqual(without.status_code, with_delay.status_code)
        self.assertEqual(
            self.without_session_id(without), self.without_session_id(with_delay)
        )

    def test_flush_failure_keeps_a_model_error_response_identical(self):
        without = self.run_scenario(NoopTracer(), model_status=500)
        with_failure = self.run_scenario(self.failing_flush_tracer(), model_status=500)
        self.assertEqual(without.status_code, 502)
        self.assertEqual(without.json(), with_failure.json())

    def test_flush_delay_keeps_a_model_error_response_identical(self):
        without = self.run_scenario(NoopTracer(), model_status=500)
        traced, client = recording_tracer()
        client.flush_delay = 0.2
        with mock.patch.object(server_module, "FLUSH_TIMEOUT_SECONDS", 0.05):
            with_delay = self.run_scenario(traced, model_status=500)
        self.assertEqual(without.status_code, 502)
        self.assertEqual(without.json(), with_delay.json())

    def test_flush_failure_keeps_a_grader_error_response_identical(self):
        without = self.run_scenario(NoopTracer(), grader_status=500)
        with_failure = self.run_scenario(self.failing_flush_tracer(), grader_status=500)
        self.assertEqual(without.status_code, 502)
        self.assertEqual(without.json(), with_failure.json())

    def test_a_completed_flush_is_not_part_of_the_session_elapsed_time(self):
        self.actions = list(self.SCRIPT)
        self.grader_payloads = [dict(payload) for payload in self.GRADER]
        self.langfuse.flush_delay = 0.6
        with mock.patch.object(server_module, "FLUSH_TIMEOUT_SECONDS", 2.0):
            with TestClient(self.app()) as client:
                started = time.monotonic()
                response = self.run_session(client)
                wall_seconds = time.monotonic() - started
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(wall_seconds, 0.5)
        self.assertLess(response.json()["state"]["session_elapsed_seconds"], 0.5)


class DisabledTracingTests(unittest.TestCase):
    def test_missing_keys_disable_tracing_without_loading_the_sdk(self):
        cleared = {
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
            "LANGFUSE_BASE_URL": "",
        }
        with mock.patch.dict(os.environ, cleared), mock.patch.object(
            tracing, "_load_sdk", side_effect=AssertionError("SDK must not be imported")
        ):
            tracer = tracing.create_tracer(agent_name=AGENT_NAME, model_id=MODEL_ID)
        self.assertIsInstance(tracer, NoopTracer)
        self.assertFalse(tracer.enabled)

        session = tracer.start_session(task_id="task", input="question")
        generation = session.model_start(phase=1, call_index=1, messages=[])
        generation.finish(SimpleNamespace(content="", response_model=None, usage=None,
                                          elapsed_seconds=0.0, truncated=False))
        generation.error("boom")
        span = session.tool_start(
            name="get_schema", args={}, phase=1, cost=1.0, budget_before=10.0
        )
        span.finish({})
        span.transition(completed=True)
        session.finish({})
        session.fail("model", "boom")
        session.end()
        asyncio.run(tracer.flush(1.0))

    def test_one_key_is_not_enough(self):
        for missing in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            with self.subTest(missing=missing):
                environment = {
                    "LANGFUSE_PUBLIC_KEY": "fake-public",
                    "LANGFUSE_SECRET_KEY": "fake-secret",
                    missing: "",
                }
                with mock.patch.dict(os.environ, environment), mock.patch.object(
                    tracing, "_load_sdk", side_effect=AssertionError("SDK must not load")
                ):
                    self.assertIsInstance(
                        tracing.create_tracer(agent_name="agent", model_id="model"),
                        NoopTracer,
                    )

    def test_sdk_import_failure_falls_back_to_a_no_op(self):
        environment = {
            "LANGFUSE_PUBLIC_KEY": "fake-public",
            "LANGFUSE_SECRET_KEY": "fake-secret",
        }
        with mock.patch.dict(os.environ, environment), mock.patch.object(
            tracing, "_load_sdk", side_effect=ImportError("missing")
        ):
            tracer = tracing.create_tracer(agent_name="agent", model_id="model")
        self.assertIsInstance(tracer, NoopTracer)

    def test_tracing_does_not_change_the_agent_result(self):
        script = [action("submit_sql", sql="SELECT 1;")]

        def run(tracer):
            model_requests = []
            grader = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
            actions = [script[0], action("submit_sql", sql="SELECT 2;")]

            def model_handler(request):
                model_requests.append(json.loads(request.content))
                return httpx.Response(200, json=model_reply(actions.pop(0)))

            def service_handler(request):
                if request.url.path == "/phase_transition":
                    return httpx.Response(200, json={"status": "ok", "phase": 2})
                return httpx.Response(200, json=grader.pop(0))

            app = create_app(
                Environment(httpx.MockTransport(service_handler)),
                ModelClient(
                    transport=httpx.MockTransport(model_handler), api_key="test-key"
                ),
                mode="tools",
                tracer=tracer,
            )
            with TestClient(app) as client:
                client.post("/init_session", json={
                    "task_id": "test-task", "mode": "a-interact", "reset": True,
                    "state": {
                        "task_id": "test-task", "db_name": "demo",
                        "user_query": "How many customers?", "budget_remaining": 18,
                    },
                })
                response = client.post("/run_session", json={
                    "task_id": "test-task", "mode": "a-interact", "message": "Answer.",
                })
            return response

        without = run(NoopTracer())
        traced, _ = recording_tracer()
        with_trace = run(traced)
        self.assertEqual(without.status_code, with_trace.status_code)
        self.assertEqual(without.json()["response"], with_trace.json()["response"])
        left = without.json()["state"]
        right = with_trace.json()["state"]
        for key in (
            "total_reward", "phase1_completed", "phase2_completed", "task_done",
            "stop_reason", "budget_remaining", "submit_attempts", "current_phase",
        ):
            self.assertEqual(left[key], right[key])
        self.assertEqual(
            [entry["tool"] for entry in left["tool_trajectory"]],
            [entry["tool"] for entry in right["tool_trajectory"]],
        )
        self.assertEqual(
            [event["action"] for event in left["adk_events"]],
            [event["action"] for event in right["adk_events"]],
        )


class ServerTracerLifecycleTests(TracingSessionTestCase):
    def test_a_test_app_without_lifespan_never_creates_a_langfuse_client(self):
        self.actions = [
            action("submit_sql", sql="SELECT 1;"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        environment = {
            "LANGFUSE_PUBLIC_KEY": "fake-public",
            "LANGFUSE_SECRET_KEY": "fake-secret",
        }
        with mock.patch.dict(os.environ, environment), mock.patch.object(
            server_module, "create_tracer", side_effect=AssertionError("must not create")
        ):
            app = create_app(
                Environment(httpx.MockTransport(self.service_handler)),
                ModelClient(
                    transport=httpx.MockTransport(self.model_handler), api_key="test-key"
                ),
                mode="tools",
            )
            client = TestClient(app)
            response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"]["stop_reason"], "phase2_completed")

    def test_lifespan_startup_creates_the_client_once_and_uses_it(self):
        self.actions = [
            action("submit_sql", sql="SELECT 1;"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        tracer, langfuse = recording_tracer()
        app = create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(
                transport=httpx.MockTransport(self.model_handler), api_key="test-key"
            ),
            mode="tools",
        )
        with mock.patch.object(
            server_module, "create_tracer", return_value=tracer
        ) as factory:
            with TestClient(app) as client:
                response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(len(langfuse.roots), 1)

    def test_an_injected_tracer_is_not_replaced_by_the_lifespan(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payloads = [dict(PHASE1_FOLLOW_UP), dict(PHASE2_PASS)]
        self.actions.append(action("submit_sql", sql="SELECT 2;"))
        with mock.patch.object(
            server_module, "create_tracer", side_effect=AssertionError("must not create")
        ):
            with TestClient(self.app(tracer=self.tracer)) as client:
                response = self.run_session(client)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.langfuse.roots), 1)


class TracerConstructionTests(unittest.TestCase):
    def test_both_keys_create_a_client_with_the_base_url_and_run_metadata(self):
        FakeSdkClient.instances.clear()
        fake_sdk = SimpleNamespace(
            Langfuse=FakeSdkClient,
            propagate_attributes=lambda **kwargs: nullcontext(),
        )
        provider = object()
        environment = {
            "LANGFUSE_PUBLIC_KEY": "fake-public-key-123",
            "LANGFUSE_SECRET_KEY": "fake-secret-key-456",
            "LANGFUSE_BASE_URL": "https://langfuse.example.test",
            "AGENT_TRACE_SESSION_ID": "run-20261020",
            "AGENT_CODE_VERSION": "deadbeef-dirty",
        }
        with mock.patch.dict(os.environ, environment), mock.patch.object(
            tracing, "_load_sdk", return_value=fake_sdk
        ), mock.patch.object(tracing, "_load_tracer_provider", return_value=provider):
            tracer = tracing.create_tracer(agent_name=AGENT_NAME, model_id=MODEL_ID)

        self.assertTrue(tracer.enabled)
        self.assertIsInstance(tracer, LangfuseTracer)
        client = FakeSdkClient.instances[-1]
        self.assertEqual(client.init_kwargs["public_key"], "fake-public-key-123")
        self.assertEqual(client.init_kwargs["secret_key"], "fake-secret-key-456")
        self.assertEqual(client.init_kwargs["base_url"], "https://langfuse.example.test")
        self.assertIs(client.init_kwargs["tracer_provider"], provider)

        session = tracer.start_session(task_id="task-1", input="A question")
        root = client.roots[0]
        self.assertEqual(root.initial["metadata"]["eval_run_id"], "run-20261020")
        self.assertEqual(root.initial["metadata"]["code_version"], "deadbeef-dirty")
        self.assertEqual(root.initial["version"], "deadbeef-dirty")
        session.end()

        recorded = json.dumps([item.initial for item in client.observations])
        self.assertNotIn("fake-public-key-123", recorded)
        self.assertNotIn("fake-secret-key-456", recorded)
        self.assertNotIn("langfuse.example.test", recorded)


if __name__ == "__main__":
    unittest.main()
