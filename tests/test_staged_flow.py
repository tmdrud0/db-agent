"""TASK-016 staged-flow contracts with synthetic fixtures only.

The mock service and the scripted model return the values written in this
file. No ground truth, ``private/`` or ``results/`` file is read.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.environment import Environment
from agent.flow import (
    ASK_LIMIT_ENV,
    FLOW_ENV,
    LOCAL_ACTION_SPECS,
    PHASE2_RESERVE_ENV,
    FlowConfig,
    FlowError,
    FlowRejection,
    StagedFlow,
    load_flow_config,
)
from agent.harness import load_harness
from agent.model import ModelClient
from agent.oracle_context import load_oracle_context
from agent.server import create_app
from agent.tool_loop import _initial_prompt, _observation, system_prompt
from agent.tools import ALLOWED_TOOLS, ActionError, parse_response, tool_metadata
from test_tool_loop import (
    GRADER_FAILURE,
    PHASE1_FINAL,
    PHASE1_WITH_FOLLOW_UP,
    PHASE2_PASS,
    ToolLoopTestCase,
    action,
    dsml_calls,
    dsml_invoke,
    mimo_call,
    model_events,
)
from test_oracle_context import write_context

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import analyze_staged_flow as analysis  # noqa: E402

# The contract the baseline loop builds at the base commit, frozen here so a
# change to the free mode fails instead of going unnoticed.
FROZEN_BASELINE_PROMPT = (
    "You solve PostgreSQL tasks for a benchmark by choosing exactly one action per turn.\n"
    "Every action costs bird-coins from a fixed budget, so spend coins only on actions that\n"
    "improve your final SQL.\n"
    "\n"
    "Reply with one JSON object and nothing else:\n"
    '{"action": "<tool name>", "args": {<tool arguments>}}\n'
    "\n"
    "Available actions:\n"
    "- get_schema: the database schema (CREATE TABLE statements). arguments: none. Cost: 1\n"
    "- get_all_column_meanings: the meanings of all database columns. arguments: none. Cost: 1\n"
    '- get_column_meaning: the meaning of one column. arguments: {"table_name": "<table>", '
    '"column_name": "<column>"}. Cost: 0.5\n'
    "- get_all_external_knowledge_names: the names of the external knowledge entries for "
    "this database. arguments: none. Cost: 0.5\n"
    '- get_knowledge_definition: the definition of one external knowledge entry. arguments: '
    '{"knowledge_name": "<name>"}. Cost: 0.5\n'
    "- get_all_knowledge_definitions: the definitions of all external knowledge entries. "
    "arguments: none. Cost: 1\n"
    "- execute_sql: run one read-only statement against the task database and see its result "
    'as exploration. arguments: {"sql": "<one PostgreSQL statement starting with SELECT, '
    'WITH or EXPLAIN>"}. Cost: 1\n'
    "- ask_user: ask the task user one clarification question and see the official answer. "
    'arguments: {"question": "<one question for the task user>"}. Cost: 2\n'
    "- submit_sql: submit one SQL statement to the official grader; only a passing "
    'submission completes the task. arguments: {"sql": "<PostgreSQL statement>"}. Cost: 3\n'
    "\n"
    "Rules:\n"
    "- Only the actions above exist. Any other action name is rejected without cost.\n"
    "- execute_sql explores with one read-only statement. Its result never changes the score\n"
    "  and never replaces submit_sql; an official SQL error is an observation, not a penalty.\n"
    "- ask_user receives the official answer of the task user. The answer is an observation;\n"
    "  decide yourself whether and when to ask.\n"
    "- A call is charged its coin cost when it starts; a failed or interrupted call is not\n"
    "  refunded.\n"
    "- A failed submission can be retried while the budget lasts. The official rewards are 0.7\n"
    "  for phase 1 and 0.3 for phase 2.\n"
    "- A passing phase 1 submission applies its SQL to the database and starts phase 2 with a\n"
    "  follow-up question.\n"
    "- The session stops after 24 model actions or 1500 seconds, and it stops when fewer than "
    "3 bird-coins remain because a submission would no longer be possible.\n"
)


def plan(questions=(), assumptions=()):
    return action("plan", questions=list(questions), assumptions=list(assumptions))


def reopen(reason="The result raised a new ambiguity.", questions=("Which year?",)):
    return action("reopen", reason=reason, questions=list(questions))


class StagedFlowTestCase(ToolLoopTestCase):
    """The shared mock services with the staged harness and flow enabled."""

    harness_name = "staged"
    flow_config = FlowConfig()

    def setUp(self):
        self.harness = load_harness(ROOT / "harness" / self.harness_name)
        with mock.patch.dict(os.environ, {FLOW_ENV: ""}):
            super().setUp()
        self.client = self.build_client(self.flow_config)

    def build_client(self, config, oracle=None):
        return TestClient(create_app(
            Environment(httpx.MockTransport(self.service_handler)),
            ModelClient(transport=httpx.MockTransport(self.model_handler), api_key="test-key"),
            mode="tools", harness=self.harness, oracle_context=oracle, flow=config,
        ))

    def stages(self, state):
        return [event.get("stage") for event in model_events(state)]

    def phases(self, state):
        return [event.get("phase") for event in model_events(state)]

    def transitions(self, state):
        return [
            (item["phase"], item["from"], item["to"], item["reason"])
            for item in state["flow"]["transitions"]
        ]


class FlowSettingTests(unittest.TestCase):
    def test_the_mode_is_off_without_the_variable_or_with_free(self):
        for value in ({}, {FLOW_ENV: ""}, {FLOW_ENV: "free"}, {FLOW_ENV: " FREE "}):
            with self.subTest(environ=value):
                self.assertIsNone(load_flow_config(environ=value))

    def test_staged_reads_its_setting_values(self):
        config = load_flow_config(environ={
            FLOW_ENV: "staged", ASK_LIMIT_ENV: "2", PHASE2_RESERVE_ENV: "3",
        })
        self.assertEqual(
            config, FlowConfig(setting="staged", ask_limit=2, phase2_reserve=3.0)
        )
        self.assertEqual(load_flow_config(environ={FLOW_ENV: "STAGED"}).ask_limit, 4)

    def test_an_unknown_value_or_number_stops_the_server(self):
        cases = (
            {FLOW_ENV: "linear"},
            {FLOW_ENV: "staged", ASK_LIMIT_ENV: "0"},
            {FLOW_ENV: "staged", ASK_LIMIT_ENV: "many"},
            {FLOW_ENV: "staged", PHASE2_RESERVE_ENV: "-1"},
            {FLOW_ENV: "staged", PHASE2_RESERVE_ENV: "two"},
        )
        for environ in cases:
            with self.subTest(environ=environ), mock.patch.dict(os.environ, environ):
                with self.assertRaises(FlowError):
                    load_flow_config()
                with self.assertRaises(FlowError):
                    create_app(mode="tools")

    def test_health_adds_flow_only_when_the_mode_is_on(self):
        with mock.patch.dict(os.environ, {FLOW_ENV: "staged"}):
            health = TestClient(create_app(mode="tools")).get("/health").json()
        self.assertEqual(health["flow"], {
            "mode": "staged", "setting": "staged", "ask_limit": 4, "phase2_reserve": 0.0,
        })
        self.assertEqual(health["tools"], tool_metadata())
        self.assertEqual(len(health["tools"]), 9)
        plain = TestClient(create_app(mode="tools", flow=None)).get("/health").json()
        self.assertNotIn("flow", plain)
        self.assertEqual(plain["tools"], tool_metadata())

    def test_an_explicit_config_skips_the_environment(self):
        with mock.patch.dict(os.environ, {FLOW_ENV: "linear"}):
            app = create_app(mode="tools", flow=FlowConfig(ask_limit=2))
            health = TestClient(app).get("/health").json()
        self.assertEqual(health["flow"]["ask_limit"], 2)


class FreeModeParityTests(unittest.TestCase):
    def test_the_baseline_prompt_is_the_frozen_contract(self):
        harness = load_harness(ROOT / "harness" / "baseline")
        self.assertEqual(system_prompt(harness), FROZEN_BASELINE_PROMPT)
        for marker in ("Staged flow", "- plan:", "- ready:", "- reopen:"):
            self.assertNotIn(marker, system_prompt(harness))

    def test_the_free_action_set_is_the_nine_official_tools(self):
        from agent import tool_loop

        harness = load_harness(ROOT / "harness" / "baseline")
        self.assertEqual(tool_loop._allowed_actions(harness), ALLOWED_TOOLS)
        self.assertEqual(
            _initial_prompt(
                {"db_name": "demo", "initial_budget": 18, "budget_remaining": 18}, "Q?"
            ),
            "Database: demo\n\nUser Query:\nQ?\n\nYou have 18 bird-coins for this task and "
            "18 remain. Send one JSON action.",
        )
        self.assertEqual(
            _observation(1, 1, "get_schema", 1.0, {"budget_remaining": 17}, "BODY"),
            "[action 1 | phase 1 | get_schema | cost 1 | budget 17]\nBODY",
        )

class FreeSessionTests(ToolLoopTestCase):
    def setUp(self):
        with mock.patch.dict(os.environ, {FLOW_ENV: ""}):
            super().setUp()

    def test_a_free_session_records_no_flow_fields(self):
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()
        self.assertNotIn("flow", state)
        for event in model_events(state):
            self.assertNotIn("stage", event)
            self.assertNotIn("flow", event)
        self.assertEqual(
            self.model_requests[0]["messages"][0]["content"], FROZEN_BASELINE_PROMPT
        )


class StagedHarnessTests(unittest.TestCase):
    def test_the_staged_harness_extends_the_baseline_with_stage_guidance(self):
        staged = load_harness(ROOT / "harness" / "staged")
        baseline = load_harness(ROOT / "harness" / "baseline").system.strip()
        self.assertTrue(staged.system.startswith(baseline))
        self.assertEqual(staged.sub_agents, {})
        for marker in ("understand:", "ask:", "verify:", "submit:"):
            self.assertIn(marker, staged.system)
        self.assertIn("plan", staged.system)
        self.assertIn("execute_sql", staged.system)


class LocalActionParsingTests(unittest.TestCase):
    allowed = {**ALLOWED_TOOLS, **LOCAL_ACTION_SPECS}

    def test_plan_is_read_from_json_dsml_and_mimo_forms(self):
        args = {"questions": ["What counts as active?"], "assumptions": ["All regions."]}
        forms = (
            json.dumps({"action": "plan", "args": args}),
            dsml_calls(dsml_invoke("plan", json.dumps(args))),
            mimo_call("plan", args=json.dumps(args)),
        )
        for content in forms:
            with self.subTest(content=content):
                parsed = parse_response(content, self.allowed)
                self.assertEqual(parsed.name, "plan")
                self.assertEqual(parsed.args, args)

    def test_invalid_local_arguments_are_rejected(self):
        invalid = (
            {"action": "plan", "args": {"questions": ["q"]}},
            {"action": "plan", "args": {"questions": "q", "assumptions": []}},
            {"action": "plan", "args": {"questions": ["q", 3], "assumptions": []}},
            {"action": "plan", "args": {"questions": [" "], "assumptions": []}},
            {"action": "reopen", "args": {"questions": ["q"]}},
            {"action": "reopen", "args": {"reason": "r", "questions": []}},
            {"action": "ready", "args": {"reason": "r"}},
        )
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ActionError):
                parse_response(json.dumps(item), self.allowed)

    def test_the_local_actions_are_unknown_in_the_free_mode(self):
        plan_text = json.dumps({
            "action": "plan", "args": {"questions": [], "assumptions": []},
        })
        with self.assertRaises(ActionError) as caught:
            parse_response(plan_text)
        self.assertIn("Unknown action 'plan'", str(caught.exception))


class StageTransitionTests(StagedFlowTestCase):
    def test_plan_questions_lead_through_ask_verify_and_submit(self):
        self.actions = [
            plan(["What counts as an active customer?"], ["All regions are included."]),
            action("ask_user", question="What counts as an active customer?"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.paths(), ["/ask", "/execute", "/submit"])
        self.assertEqual(
            self.stages(state), ["understand", "ask", "verify", "verify", "submit"]
        )
        self.assertEqual(self.transitions(state), [
            (1, "understand", "ask", "plan"),
            (1, "ask", "verify", "declared_questions_asked"),
            (1, "verify", "submit", "ready"),
        ])
        self.assertEqual(state["flow"]["questions"], ["What counts as an active customer?"])
        self.assertEqual(state["flow"]["assumptions"], ["All regions are included."])
        self.assertEqual(state["flow"]["plans"], [{
            "action": 1, "phase": 1,
            "questions": ["What counts as an active customer?"],
            "assumptions": ["All regions are included."],
        }])
        self.assertTrue(state["flow"]["tested"])
        self.assertEqual(state["budget_remaining"], 12)
        self.assertEqual(
            [(entry["tool"], entry["cost"]) for entry in state["tool_trajectory"]],
            [("plan", 0.0), ("ask_user", 2.0), ("execute_sql", 1.0), ("ready", 0.0),
             ("submit_sql", 3.0)],
        )

    def test_the_plan_observation_echoes_questions_and_the_new_stage(self):
        self.actions = [
            plan(["Q one", "Q two"], ["Assumption one"]),
            action("plan_done"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        observation = self.model_requests[1]["messages"][-1]["content"]
        self.assertTrue(observation.startswith(
            "[action 1 | phase 1 | stage ask | allowed ask_user, plan_done | plan | "
            "cost 0 | budget 18]"
        ))
        self.assertIn("- Q one", observation)
        self.assertIn("- Q two", observation)
        self.assertIn("- Assumption one", observation)
        self.assertIn("The flow moved to the ask stage", observation)
        self.assertEqual(self.transitions(state)[1], (1, "ask", "verify", "plan_done"))

    def test_no_open_questions_lead_straight_to_verify(self):
        self.actions = [
            plan([], ["The metric is the row count."]),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.transitions(state), [
            (1, "understand", "verify", "plan"),
            (1, "verify", "submit", "ready"),
        ])
        self.assertEqual(
            self.stages(state), ["understand", "verify", "verify", "submit"]
        )
        self.assertEqual(self.paths(), ["/execute", "/submit"])

    def test_a_failed_submission_returns_to_verify(self):
        self.actions = [
            plan([]),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
            action("execute_sql", sql="SELECT 2"),
            action("ready"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(GRADER_FAILURE), dict(PHASE1_FINAL)]
        state = self.run_ok()

        self.assertEqual(
            self.stages(state),
            ["understand", "verify", "verify", "submit", "verify", "verify", "submit"],
        )
        self.assertEqual(self.transitions(state), [
            (1, "understand", "verify", "plan"),
            (1, "verify", "submit", "ready"),
            (1, "submit", "verify", "submit_failed"),
            (1, "verify", "submit", "ready"),
        ])
        failed = self.model_requests[4]["messages"][-1]["content"]
        self.assertIn("[action 4 | phase 1 | stage verify | allowed", failed)
        self.assertIn("did not pass", failed)
        self.assertIn("moved back to the verify stage", failed)
        self.assertEqual(state["budget_remaining"], 10)

    def test_reopen_is_allowed_once_then_rejected(self):
        self.actions = [
            plan([]),
            action("execute_sql", sql="SELECT 1"),
            reopen(questions=["Which year?"]),
            action("ask_user", question="Which year?"),
            reopen(questions=["Which region?"]),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        events = model_events(state)
        self.assertEqual(events[4]["rejection"], "flow")
        self.assertEqual(events[4]["action"]["name"], "reopen")
        self.assertIn("already used in this phase", events[4]["action_error"])
        self.assertEqual(len(state["flow"]["reopen"]), 1)
        self.assertEqual(state["flow"]["reopen"][0]["questions"], ["Which year?"])
        self.assertEqual(state["flow"]["rejections"]["flow"], 1)
        self.assertEqual(self.transitions(state), [
            (1, "understand", "verify", "plan"),
            (1, "verify", "ask", "reopen"),
            (1, "ask", "verify", "declared_questions_asked"),
            (1, "verify", "submit", "ready"),
        ])
        rejection = self.model_requests[5]["messages"][-1]["content"]
        self.assertTrue(rejection.startswith(
            "[action 5 | phase 1 | stage verify | allowed execute_sql, "
            "get_column_meaning, get_knowledge_definition, reopen, ready | rejected | "
            "cost 0 | budget 15]"
        ))

    def test_phase_two_starts_at_understand_again(self):
        self.actions = [
            plan(["What counts as active?"]),
            action("ask_user", question="What counts as active?"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
            plan([]),
            action("execute_sql", sql="SELECT 2"),
            action("ready"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        state = self.run_ok()

        self.assertEqual(self.phases(state), [1, 1, 1, 1, 1, 2, 2, 2, 2])
        self.assertEqual(
            self.stages(state),
            ["understand", "ask", "verify", "verify", "submit",
             "understand", "verify", "verify", "submit"],
        )
        self.assertIn((1, "submit", "understand", "phase1_passed"), self.transitions(state))
        self.assertEqual(state["flow"]["phase"], 2)
        self.assertEqual(state["total_reward"], 1.0)
        follow_up = self.model_requests[5]["messages"][-1]["content"]
        self.assertIn("Phase 2 begins in the understand stage again.", follow_up)
        self.assertTrue(follow_up.startswith(
            "[action 5 | phase 2 | stage understand | allowed"
        ))

    def test_the_test_flag_resets_with_the_phase(self):
        self.actions = [
            plan([]),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
            plan([]),
            action("ready"),
            action("execute_sql", sql="SELECT 2"),
            action("ready"),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payloads = [dict(PHASE1_WITH_FOLLOW_UP), dict(PHASE2_PASS)]
        state = self.run_ok()

        events = model_events(state)
        self.assertEqual(events[5]["rejection"], "flow")
        self.assertIn("execute_sql must run in this phase", events[5]["action_error"])
        self.assertEqual(self.transitions(state)[-1], (2, "verify", "submit", "ready"))
        self.assertIn((1, "submit", "understand", "phase1_passed"), self.transitions(state))
        self.assertEqual(state["phase2_completed"], True)


class StageViolationTests(StagedFlowTestCase):
    def test_actions_of_another_stage_are_rejected_without_a_call_or_a_charge(self):
        self.actions = [
            plan(["What counts as active?"]),
            action("execute_sql", sql="SELECT 1"),
            action("ask_user", question="What counts as active?"),
            action("ask_user", question="Which region?"),
            action("execute_sql", sql="SELECT 2"),
            action("ready"),
            plan([]),
            action("submit_sql", sql="SELECT 2;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        events = model_events(state)
        self.assertEqual([event.get("rejection") for event in events], [
            None, "stage", None, "stage", None, None, "stage", None,
        ])
        self.assertEqual(
            [event.get("action", {}).get("name") for event in events],
            ["plan", "execute_sql", "ask_user", "ask_user", "execute_sql", "ready",
             "plan", "submit_sql"],
        )
        self.assertEqual(self.paths(), ["/ask", "/execute", "/submit"])
        self.assertEqual(state["flow"]["rejections"]["stage"], 3)
        self.assertEqual(state["budget_remaining"], 12)
        self.assertEqual(model_events(state)[1]["action_error"], (
            "The ask stage does not allow 'execute_sql'. Allowed actions here: "
            "ask_user, plan_done. Nothing was executed and no bird-coins were charged."
        ))
        first_rejection = self.model_requests[2]["messages"][-1]["content"]
        self.assertTrue(first_rejection.startswith(
            "[action 2 | phase 1 | stage ask | allowed ask_user, plan_done | "
            "rejected | cost 0 | budget 18]"
        ))


class SubmitReadinessTests(StagedFlowTestCase):
    def test_ready_without_a_test_is_rejected_without_cost(self):
        self.actions = [
            plan([]),
            action("ready"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        events = model_events(state)
        self.assertEqual(events[1]["rejection"], "flow")
        self.assertIn("execute_sql must run in this phase", events[1]["action_error"])
        self.assertEqual(events[1]["action"]["name"], "ready")
        self.assertEqual(self.transitions(state), [
            (1, "understand", "verify", "plan"),
            (1, "verify", "submit", "ready"),
        ])
        self.assertEqual(self.paths(), ["/execute", "/submit"])
        self.assertEqual(state["budget_remaining"], 14)

    def test_the_test_requirement_is_waived_below_the_test_price(self):
        self.state = {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": 3.5, "initial_budget": 3.5,
        }
        self.actions = [
            plan([]),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(state["flow"]["rejections"]["flow"], 0)
        self.assertEqual(self.transitions(state), [
            (1, "understand", "verify", "plan"),
            (1, "verify", "submit", "ready"),
        ])
        self.assertEqual(state["budget_remaining"], 0.5)

    def test_a_blocked_stage_moves_to_submit_before_the_model_call(self):
        self.state = {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": 3, "initial_budget": 3,
        }
        self.actions = [action("submit_sql", sql="SELECT 1;")]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        first_prompt = self.model_requests[0]["messages"][-1]["content"]
        self.assertTrue(first_prompt.startswith(
            "[flow notice | next action 1 | phase 1]"
        ))
        self.assertIn("no action of the understand stage".lower(), first_prompt.lower())
        self.assertEqual(self.stages(state), ["submit"])
        self.assertEqual(self.transitions(state), [
            (1, "understand", "submit", "no_affordable_action"),
        ])
        self.assertEqual(state["budget_remaining"], 0)


class BudgetRuleTests(StagedFlowTestCase):
    def test_an_action_that_would_remove_the_submission_is_rejected(self):
        self.state = {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": 3.5, "initial_budget": 3.5,
        }
        self.actions = [
            action("get_schema"),
            plan([]),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        event = model_events(state)[0]
        self.assertEqual(event["rejection"], "budget")
        self.assertIn("'get_schema' costs 1 bird-coins", event["action_error"])
        self.assertNotIn("/schema", self.paths())
        self.assertEqual(state["budget_remaining"], 0.5)
        second_prompt = self.model_requests[1]["messages"][-1]["content"]
        self.assertIn("budget 3.5]", second_prompt)

    def test_the_phase_two_reserve_uses_its_boundary(self):
        self.state = {
            "task_id": "test-task", "db_name": "demo", "user_query": "How many customers?",
            "budget_remaining": 6.5, "initial_budget": 6.5,
        }
        self.flow_config = FlowConfig(phase2_reserve=3.0)
        self.client = self.build_client(self.flow_config)
        self.actions = [
            action("get_schema"),
            action("get_column_meaning", table_name="customers", column_name="id"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        events = model_events(state)
        self.assertEqual(events[0]["rejection"], "budget")
        self.assertIn("phase 2 reserve of 3", events[0]["action_error"])
        self.assertNotIn("/schema", self.paths())
        self.assertEqual(events[1].get("rejection"), None)
        self.assertEqual(events[2]["stage"], "submit")
        self.assertEqual(self.transitions(state), [
            (1, "understand", "submit", "no_affordable_action"),
        ])
        self.assertEqual(state["budget_remaining"], 3)

    def test_unit_boundaries_for_reserve_zero_and_three(self):
        plain = StagedFlow(
            FlowConfig(), {"budget_remaining": 4, "current_phase": 1}, ALLOWED_TOOLS
        )
        plain.check("get_schema", {}, 4)
        with self.assertRaises(FlowRejection) as caught:
            plain.check("get_schema", {}, 3.5)
        self.assertEqual(caught.exception.kind, "budget")

        reserved = StagedFlow(
            FlowConfig(phase2_reserve=3.0),
            {"budget_remaining": 7, "current_phase": 1}, ALLOWED_TOOLS,
        )
        reserved.check("get_schema", {}, 7)
        with self.assertRaises(FlowRejection) as caught:
            reserved.check("get_schema", {}, 6.5)
        self.assertEqual(caught.exception.kind, "budget")

        # A submission is never subject to the reserve or the 3-coin floor.
        reserved.run_local("plan", {"questions": [], "assumptions": []}, 1, 1)
        reserved.mark_tested()
        reserved.check("ready", {}, 3)
        reserved.run_local("ready", {}, 2, 1)
        reserved.state["budget_remaining"] = 3
        reserved.check("submit_sql", {"sql": "SELECT 1"}, 3)

    def test_a_stage_without_affordable_actions_does_not_auto_move_when_local_actions_are_the_exit(self):
        config = FlowConfig(phase2_reserve=3.0)
        state = {"budget_remaining": 7, "current_phase": 1}
        flow = StagedFlow(config, state, ALLOWED_TOOLS)
        self.assertIsNone(flow.ensure_progress(7.0, 1))
        self.assertEqual(flow.stage, "understand")
        self.assertIn("moved to the submit stage", flow.ensure_progress(6.0, 2))


class AskLimitTests(StagedFlowTestCase):
    flow_config = FlowConfig(ask_limit=1)

    def test_the_ask_limit_ends_the_stage_after_one_question(self):
        self.actions = [
            plan(["Q one", "Q two"]),
            action("ask_user", question="Q one"),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(self.transitions(state)[1], (1, "ask", "verify", "ask_limit_reached"))
        answer = self.model_requests[2]["messages"][-1]["content"]
        self.assertIn("per-phase ask limit of 1", answer)
        self.assertEqual(state["flow"]["asked_in_stage"], 1)
        self.assertEqual(len([t for t in state["tool_trajectory"] if t["tool"] == "ask_user"]), 1)

    def test_reopen_does_not_reset_the_per_phase_ask_limit(self):
        self.actions = [
            plan(["Q one"]),
            action("ask_user", question="Q one"),
            action("execute_sql", sql="SELECT 1"),
            reopen(questions=["Q two"]),
            action("ask_user", question="Q two"),
            action("plan_done"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        events = model_events(state)
        self.assertEqual(events[4]["rejection"], "flow")
        self.assertIn("ask limit of 1 per phase is used up", events[4]["action_error"])
        self.assertEqual(state["flow"]["asked_in_phase"], 1)
        self.assertEqual(state["flow"]["rejections"]["flow"], 1)
        self.assertEqual(self.transitions(state), [
            (1, "understand", "ask", "plan"),
            (1, "ask", "verify", "ask_limit_reached"),
            (1, "verify", "ask", "reopen"),
            (1, "ask", "verify", "plan_done"),
            (1, "verify", "submit", "ready"),
        ])
        reopen_observation = self.model_requests[4]["messages"][-1]["content"]
        self.assertIn("ask limit is already used up", reopen_observation)
        self.assertEqual(state["budget_remaining"], 12)


class FlowRecordTests(StagedFlowTestCase):
    def test_the_state_and_every_model_call_carry_the_stage(self):
        self.actions = [
            plan([], ["Assume the current year."]),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        self.assertEqual(state["flow"]["mode"], "staged")
        self.assertEqual(state["flow"]["setting"], "staged")
        self.assertEqual(state["flow"]["ask_limit"], 4)
        self.assertEqual(state["flow"]["phase2_reserve"], 0.0)
        for event in model_events(state):
            self.assertEqual(event["flow"], {"mode": "staged", "setting": "staged"})
            self.assertIn(event["stage"], ("understand", "ask", "verify", "submit"))
        self.assertEqual(
            [event["call_index"] for event in model_events(state)], [1, 2, 3, 4]
        )


class OracleStagedTests(StagedFlowTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        write_context(Path(directory.name) / "A")
        self.oracle = load_oracle_context(Path(directory.name) / "A")
        self.client = self.build_client(self.flow_config, oracle=self.oracle)

    def test_understand_allows_only_plan_and_the_mode_still_completes(self):
        self.actions = [
            plan([]),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        first_prompt = self.model_requests[0]["messages"][1]["content"]
        self.assertTrue(first_prompt.endswith("Allowed actions: plan."))
        system = self.model_requests[0]["messages"][0]["content"]
        self.assertIn("- plan:", system)
        self.assertNotIn("- get_schema:", system)
        self.assertEqual(self.transitions(state), [
            (1, "understand", "verify", "plan"),
            (1, "verify", "submit", "ready"),
        ])
        health = self.client.get("/health").json()
        self.assertEqual(health["tools"], tool_metadata())
        self.assertEqual(health["experiment"]["oracle_context"], "A")
        self.assertEqual(health["flow"]["mode"], "staged")

    def test_a_stage_violation_still_names_the_oracle_actions(self):
        self.actions = [
            action("get_schema"),
            plan([]),
            action("execute_sql", sql="SELECT 1"),
            action("ready"),
            action("submit_sql", sql="SELECT 1;"),
        ]
        self.grader_payload = dict(PHASE1_FINAL)
        state = self.run_ok()

        # get_schema is not an action of this experiment at all.
        self.assertIn("Unknown action 'get_schema'", model_events(state)[0]["action_error"])
        self.assertEqual(state["flow"]["rejections"]["stage"], 0)
        self.assertEqual(self.paths(), ["/execute", "/submit"])


class SubAgentConflictTests(unittest.TestCase):
    def test_a_harness_with_sub_agents_stops_the_server_at_startup(self):
        harness = load_harness(ROOT / "harness" / "sql_review")
        with self.assertRaises(FlowError) as caught:
            create_app(mode="tools", harness=harness, flow=FlowConfig())
        self.assertIn("cannot be combined", str(caught.exception))
        self.assertIn("sql_reviewer", str(caught.exception))


def _call(stage=None, name=None, args=None, *, phase=1, rejection=None):
    event = {
        "type": "model_call", "phase": phase,
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    if stage is not None:
        event["stage"] = stage
    if name is not None:
        event["action"] = {"name": name, "args": args or {}}
    if rejection is not None:
        event["rejection"] = rejection
        event["action_error"] = "rejected"
    return event


def synthetic_staged_task(task_id, understand_actions=1):
    events = [
        _call("understand", "plan", {"questions": ["q1", "q2"], "assumptions": []}),
        _call("ask", "ask_user", {"question": "q1"}),
        _call("verify", "execute_sql", {"sql": "SELECT 1"}),
        _call("verify", "ready"),
        _call("submit", "submit_sql", {"sql": "SELECT 1"}),
        _call("submit", "submit_sql", {"sql": "SELECT 2"}, phase=2),
    ]
    events.insert(1, _call(
        "understand", "submit_sql", {"sql": "SELECT 1"}, rejection="stage"
    ))
    events.insert(2, _call("understand", "get_schema", rejection="budget"))
    for _ in range(understand_actions - 1):
        events.insert(1, _call("understand", "get_schema"))
    trajectory = [
        {"tool": "plan", "phase": 1, "cost": 0.0, "budget_after": 18,
         "result": {"kind": "plan", "questions": ["q1", "q2"], "assumptions": []}},
        {"tool": "ask_user", "phase": 1, "cost": 2.0, "budget_after": 16},
        {"tool": "execute_sql", "phase": 1, "cost": 1.0, "budget_after": 15},
        {"tool": "ready", "phase": 1, "cost": 0.0, "budget_after": 15},
        {"tool": "submit_sql", "phase": 1, "cost": 3.0, "budget_after": 12,
         "result": {"passed": True}},
        {"tool": "submit_sql", "phase": 2, "cost": 3.0, "budget_after": 9,
         "result": {"passed": True}},
    ]
    return {
        "task_id": task_id,
        "total_reward": 1.0,
        "phase1_passed": True,
        "phase2_passed": True,
        "elapsed_seconds": 30.0,
        "final_response": "Phase 2 correct! (Reward: 0.3). Task finished.",
        "tool_trajectory": trajectory,
        "adk_events": events,
    }


def synthetic_free_task(task_id):
    events = [
        _call(None, "execute_sql", {"sql": "SELECT 1"}),
        _call(None, "submit_sql", {"sql": "SELECT 1"}),
        _call(None, "submit_sql", {"sql": "SELECT 2"}, phase=2),
    ]
    trajectory = [
        {"tool": "execute_sql", "phase": 1, "cost": 1.0, "budget_after": 17},
        {"tool": "submit_sql", "phase": 1, "cost": 3.0, "budget_after": 14,
         "result": {"passed": True}},
        {"tool": "submit_sql", "phase": 2, "cost": 3.0, "budget_after": 11,
         "result": {"passed": True}},
    ]
    return {
        "task_id": task_id,
        "total_reward": 1.0,
        "phase1_passed": True,
        "phase2_passed": True,
        "elapsed_seconds": 20.0,
        "final_response": "Phase 2 correct! (Reward: 0.3). Task finished.",
        "tool_trajectory": trajectory,
        "adk_events": events,
    }


class StagedAnalysisTests(unittest.TestCase):
    def test_the_report_reads_result_fields_and_shows_runs_and_ranges(self):
        staged = synthetic_staged_task("t1")
        free = synthetic_free_task("t2")
        stats = analysis.summarize([staged])
        # Rejected responses are model calls, but not executed stage actions.
        self.assertEqual(stats["stage_understand"], 1)
        self.assertEqual(stats["stage_ask"], 1)
        self.assertEqual(stats["stage_verify"], 2)
        self.assertEqual(stats["stage_submit"], 2)
        self.assertEqual(stats["stage_violations"], 1)
        self.assertEqual(stats["budget_rejections"], 1)
        self.assertEqual(stats["flow_rejections"], 0)
        self.assertEqual(stats["plan_questions"], 2)
        self.assertEqual(stats["ask_user"], 1)
        self.assertEqual(stats["reopen"], 0)
        self.assertEqual(stats["coins_at_phase1_pass"], 12)
        self.assertEqual(stats["phase2_action_tasks"], 1)
        self.assertEqual(stats["first_submit_pass_rate"], 1.0)

        other = synthetic_staged_task("t3", understand_actions=2)
        other["total_reward"] = 0.7
        text = analysis.report({"F": [[free]], "S": [[staged], [other]]})
        self.assertIn("Staged-flow comparison", text)
        self.assertIn("F: 1 run(s)", text)
        self.assertIn("S: 2 run(s)", text)
        self.assertIn("0.317-0.463", text)
        self.assertIn("| understand actions per task |", text)
        self.assertIn("[1.00-2.00]", text)
        self.assertIn("| t1 | not run | 1.0 (Y/Y) |", text)

    def test_a_free_run_reports_zero_stage_metrics(self):
        stats = analysis.summarize([synthetic_free_task("t2")])
        self.assertEqual(stats["stage_understand"], 0)
        self.assertEqual(stats["stage_violations"], 0)
        self.assertEqual(stats["plan_questions"], 0)


if __name__ == "__main__":
    unittest.main()
