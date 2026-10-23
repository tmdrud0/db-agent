"""Context clean-up (AGENT_CONTEXT_COMPACT=ac) contracts with synthetic fixtures only.

The scripted model and the mock services return the values written here. No
ground truth, ``private/`` or ``results/`` file is read.
"""

import sys
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.compaction import CompactionError, load_compaction
from agent.environment import Environment
from agent.harness import load_harness
from agent.model import ModelClient
from agent.server import create_app
from test_staged_flow import ROOT, StagedFlowTestCase, plan
from test_tool_loop import (
    GRADER_FAILURE,
    PHASE1_WITH_FOLLOW_UP,
    PHASE2_PASS,
    USER_ANSWER,
    action,
    model_events,
)


class CompactionSettingTests(unittest.TestCase):
    def test_off_values_and_unknown_values(self):
        self.assertIsNone(load_compaction(environ={}))
        self.assertIsNone(load_compaction(environ={"AGENT_CONTEXT_COMPACT": " off "}))
        self.assertEqual(load_compaction(environ={"AGENT_CONTEXT_COMPACT": "AC"}), "ac")
        with self.assertRaises(CompactionError):
            load_compaction(environ={"AGENT_CONTEXT_COMPACT": "all"})


class CompactionTestCase(StagedFlowTestCase):
    def build_client(self, config, oracle=None):
        return TestClient(create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(transport=httpx.MockTransport(self.model_handler), api_key="test-key"),
            mode="tools", harness=self.harness, oracle_context=oracle, flow=config,
            compaction="ac",
        ))

    def prompt(self, call: int) -> list[dict]:
        return self.model_requests[call]["messages"]

    def text(self, call: int) -> str:
        return "\n".join(message["content"] for message in self.prompt(call))


class CompactionTests(CompactionTestCase):
    def test_health_reports_the_mode(self):
        info = self.client.get("/health").json()
        self.assertEqual(info["context_compaction"], "ac")

    def test_the_plan_observation_does_not_repeat_the_plan(self):
        self.actions = [
            plan(["What counts as active?"], ["All regions."]),
            action("plan_done"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE2_PASS, phase_completed=1, has_follow_up=False)
        self.run_ok()
        observation = self.prompt(1)[-1]["content"]
        self.assertIn("Plan recorded: 1 open question(s) and 1 assumption(s)", observation)
        self.assertNotIn("- What counts as active?", observation)
        self.assertIn("What counts as active?", self.prompt(1)[-2]["content"])

    def test_a_rejection_stays_one_turn_and_leaves_after_an_executed_action(self):
        self.actions = [
            plan(),
            action("submit_sql", sql="SELECT 0;"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE2_PASS, phase_completed=1, has_follow_up=False)
        state = self.run_ok()
        self.assertIn("SELECT 0;", self.text(2))
        self.assertIn("does not allow 'submit_sql'", self.text(2))
        self.assertNotIn("SELECT 0;", self.text(3))
        self.assertNotIn("| rejected |", self.text(3))
        self.assertEqual(state["context_compaction"]["rejections_removed"], 1)
        self.assertEqual(model_events(state)[-1]["context_compaction"]["rejections_removed"], 1)

    def test_a_failed_submission_drops_local_steps_and_shortens_old_headers(self):
        self.actions = [
            plan(),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
            action("execute_sql", sql="SELECT 2"),
            action("ready"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [
            dict(GRADER_FAILURE), dict(PHASE2_PASS, phase_completed=1, has_follow_up=False),
        ]
        self.run_ok()
        after = self.prompt(4)
        contents = [message["content"] for message in after]
        self.assertFalse(any('"action": "ready"' in text for text in contents))
        self.assertFalse(any("moved to the submit stage" in text for text in contents))
        execute_obs = next(text for text in contents if "| execute_sql |" in text)
        self.assertTrue(execute_obs.startswith("[action 2 | phase 1 | execute_sql | cost 1]"))
        # The newest observation keeps its full header.
        self.assertIn("| stage verify | allowed", contents[-1])

    def test_phase_two_starts_from_a_summary_of_phase_one(self):
        self.actions = [
            plan(["What counts as active?"], ["All regions."]),
            action("ask_user", question="What counts as active?"),
            action("execute_sql", sql="SELECT 0"),
            action("ready"),
            action("submit_sql", sql="SELECT 9;"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
            plan(),
            action("execute_sql", sql="SELECT 2"),
            action("ready"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [
            dict(GRADER_FAILURE), dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS),
        ]
        state = self.run_ok()
        first_p2 = self.prompt(8)
        self.assertEqual(len(first_p2), 3)
        summary = first_p2[2]["content"]
        self.assertTrue(summary.startswith("Phase 1 summary."))
        self.assertIn("Q: What counts as active?", summary)
        self.assertIn(f"A: {USER_ANSWER}", summary)
        self.assertIn("- All regions.", summary)
        self.assertIn("Phase 1 submissions: 1 failed before the passing one.", summary)
        self.assertIn("SELECT 1;", summary)
        self.assertNotIn("SELECT 9;", summary)
        self.assertIn("Follow-up question:\nAnd by region?", summary)
        self.assertIn("stage understand", summary)
        record = state["context_compaction"]["phase1_summary"]
        # Eight phase 1 actions leave 15 messages before the transition; the
        # failed submission already removed the first ready pair.
        self.assertEqual(record["replaced_messages"], 13)
        self.assertLess(record["chars_after"], record["chars_before"])


class CompactionOffTests(StagedFlowTestCase):
    def test_without_the_mode_the_plan_echo_and_history_are_unchanged(self):
        self.actions = [
            plan(["What counts as active?"], ["All regions."]),
            action("plan_done"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE2_PASS, phase_completed=1, has_follow_up=False)
        state = self.run_ok()
        self.assertIn("- What counts as active?", self.model_requests[1]["messages"][-1]["content"])
        self.assertNotIn("context_compaction", state)
        self.assertNotIn("context_compaction", model_events(state)[0])
        self.assertNotIn("context_compaction", self.client.get("/health").json())


class OpenAskHarnessTests(unittest.TestCase):
    def test_the_open_ask_harness_loads_with_its_ask_note(self):
        harness = load_harness(ROOT / "harness" / "staged_ask_open")
        self.assertIn("open questions", harness.system)
        self.assertIn("ask_user", harness.tool_notes)
        self.assertIn("rather than", harness.tool_notes["ask_user"])


if __name__ == "__main__":
    unittest.main()
