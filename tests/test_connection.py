"""Contract checks using a mock HTTP grader; these are not BIRD scores."""

import json
import unittest

import httpx
from fastapi.testclient import TestClient

from agent.environment import Environment
from agent.server import create_app


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reply = {"passed": False, "reward": 0, "message": "Your SQL is not correct."}
        self.status = 200

        def respond(request):
            self.calls.append((request.url.path, json.loads(request.content)))
            return httpx.Response(self.status, json=self.reply)

        environment = Environment(httpx.MockTransport(respond))
        self.client = TestClient(create_app(environment))
        self.payload = {
            "task_id": "test-task", "mode": "a-interact", "reset": True,
            "state": {"task_id": "test-task", "budget_remaining": 18, "initial_budget": 18},
        }

    def initialize(self):
        response = self.client.post("/init_session", json=self.payload)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def execute(self):
        return self.client.post("/run_session", json={
            "task_id": "test-task", "mode": "a-interact", "message": "Find customers.",
        })

    def test_empty_sql_reaches_grader_and_failure_is_returned(self):
        self.initialize()
        response = self.execute()
        self.assertEqual(response.status_code, 200)
        state = response.json()["state"]
        self.assertEqual(self.calls, [("/submit", {"task_id": "test-task", "sql": ""})])
        self.assertEqual(state["total_reward"], 0)
        self.assertEqual(state["budget_remaining"], 15)
        self.assertFalse(state["phase1_completed"])
        self.assertFalse(state["task_done"])
        self.assertTrue(state["loop_stopped"])
        self.execute()
        self.assertEqual(len(self.calls), 1)

    def test_reset_false_reuses_state_and_reset_true_clears_it(self):
        first = self.initialize()
        self.execute()
        self.payload["reset"] = False
        reused = self.initialize()
        self.assertEqual(first["session_id"], reused["session_id"])
        self.assertEqual(self.execute().json()["state"]["budget_remaining"], 15)
        self.payload["reset"] = True
        fresh = self.initialize()
        self.assertNotEqual(first["session_id"], fresh["session_id"])
        self.execute()
        self.assertEqual(len(self.calls), 2)

    def test_reward_comes_from_backend_even_if_empty_sql_passes(self):
        self.reply = {
            "passed": True, "reward": 0.7, "message": "Passed",
            "phase_completed": 1, "has_follow_up": True, "follow_up_query": "Follow up",
        }
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["total_reward"], 0.7)
        self.assertTrue(state["phase1_completed"])
        self.assertEqual(state["current_phase"], 2)
        self.assertFalse(state["phase2_completed"])
        self.assertEqual(self.calls[1], ("/phase_transition", {"task_id": "test-task"}))

    def test_low_budget_still_allows_final_submission(self):
        self.payload["state"]["budget_remaining"] = 2
        self.initialize()
        state = self.execute().json()["state"]
        self.assertEqual(state["budget_remaining"], -1)
        self.assertEqual(len(self.calls), 1)

    def test_missing_session_and_backend_failure_are_explicit_errors(self):
        self.assertEqual(self.execute().status_code, 404)
        self.initialize()
        self.status = 500
        self.assertEqual(self.execute().status_code, 502)


if __name__ == "__main__":
    unittest.main()
