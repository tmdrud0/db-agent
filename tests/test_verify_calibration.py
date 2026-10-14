"""Verifier input contracts using fake tasks and a mock HTTP service."""

import json
import unittest
from unittest import mock

import httpx

from scripts import verify_calibration


class ScenarioSubmissionTests(unittest.TestCase):
    def assert_submission_sql(self, sql_fields: dict, explicit_sql: str | None = None):
        task = {
            "instance_id": "test-task",
            "sol_sql": ["SELECT 101;"],
            "follow_up": {"sol_sql": "SELECT 202;"},
        }
        for phase, expected_sql in ((1, "SELECT 101;"), (2, "SELECT 202;")):
            with self.subTest(phase=phase):
                scenario = {
                    "task_id": "test-task",
                    "submissions": [{
                        "id": "attempt", "phase": phase,
                        "expect_passed": True, **sql_fields,
                    }],
                    "checks": [],
                }
                with mock.patch("builtins.open", mock.mock_open(
                    read_data=json.dumps({"verification": [scenario]})
                )):
                    config = verify_calibration.calibration_config.load_config("fake-config.json")

                calls = []

                def respond(request):
                    calls.append((request.url.path, json.loads(request.content)))
                    if request.url.path == "/submit":
                        return httpx.Response(200, json={
                            "passed": True, "phase_completed": phase,
                            "reward": 0.7 if phase == 1 else 0.3, "message": "Passed",
                        })
                    return httpx.Response(200, json={"status": "ok"})

                with httpx.Client(
                    transport=httpx.MockTransport(respond), base_url="http://mock"
                ) as client:
                    outcomes = verify_calibration.run_scenario(
                        client, config["verification"][0], task
                    )

                self.assertEqual([path for path, _ in calls], [
                    "/init_task", "/submit", "/cleanup_task",
                ])
                self.assertEqual(calls[1][1], {
                    "task_id": "test-task", "sql": explicit_sql or expected_sql,
                })
                self.assertEqual(calls[2][1], {"task_id": "test-task"})
                self.assertEqual(len(outcomes), 1)
                self.assertTrue(outcomes[0]["ok"])

    def test_omitted_sql_submits_the_phase_sql(self):
        self.assert_submission_sql({})

    def test_null_sql_submits_the_phase_sql(self):
        self.assert_submission_sql({"sql": None})

    def test_explicit_sql_is_submitted_unchanged(self):
        self.assert_submission_sql({"sql": "SELECT 303;"}, "SELECT 303;")


if __name__ == "__main__":
    unittest.main()
