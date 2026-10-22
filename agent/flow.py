"""Staged-flow experiment mode (TASK-016): code-owned stage order in one phase.

``AGENT_FLOW=staged`` makes every phase follow understand -> ask -> verify ->
submit. The mode adds four local actions (``plan``, ``plan_done``, ``reopen``,
``ready``) that are not official tools: they cost no bird-coins, but the model
response that chooses one still counts as one model action. A rejected action
never reaches an official service and is never charged.

Without the variable, or with ``free``, nothing in this module is used and the
loop, its prompt, its action set, ``/health`` and its result records are the
same as before the mode existed.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from agent.session import budget, record_action
from agent.tools import (
    EXECUTE_SQL_COST,
    INFORMATION_TOOLS,
    SUBMIT_COST,
    ActionError,
    ToolSpec,
)

FLOW_ENV = "AGENT_FLOW"
PHASE2_RESERVE_ENV = "AGENT_PHASE2_RESERVE"
ASK_LIMIT_ENV = "AGENT_ASK_LIMIT"
FREE = "free"
STAGED = "staged"
# The average dev30 task carries about four relevant ambiguities (3.17
# important plus 0.83 deleted knowledge), and one ask_user costs 2 coins.
# Four questions per phase covers that average without spending the whole
# budget on questions; the variable moves the cap for an experiment.
DEFAULT_ASK_LIMIT = 4
# The phase 2 reserve is off by default and is a separate comparison condition.
DEFAULT_PHASE2_RESERVE = 0.0

UNDERSTAND = "understand"
ASK = "ask"
VERIFY = "verify"
SUBMIT = "submit"

STAGE_ACTIONS = {
    UNDERSTAND: tuple(INFORMATION_TOOLS),
    ASK: ("ask_user",),
    VERIFY: ("execute_sql", "get_column_meaning", "get_knowledge_definition"),
    SUBMIT: ("submit_sql",),
}
STAGE_LOCAL_ACTIONS = {
    UNDERSTAND: ("plan",),
    ASK: ("plan_done",),
    VERIFY: ("reopen", "ready"),
    SUBMIT: (),
}

PLAN_SPEC = ToolSpec(
    name="plan", cost=0.0,
    arguments='{"questions": ["<open question>", ...], "assumptions": ["<assumption>", ...]}',
    summary="declare this phase's open questions and the assumptions you will make without asking",
    required=("questions", "assumptions"), run=None,
    list_args=("questions", "assumptions"),
)
PLAN_DONE_SPEC = ToolSpec(
    name="plan_done", cost=0.0, arguments="none",
    summary="leave the ask stage without asking more questions",
    required=(), run=None,
)
REOPEN_SPEC = ToolSpec(
    name="reopen", cost=0.0,
    arguments='{"reason": "<why new questions are needed>", "questions": ["<question>", ...]}',
    summary="return from verify to ask once per phase with new questions",
    required=("reason", "questions"), run=None,
    list_args=("questions",), nonempty_list_args=("questions",),
)
READY_SPEC = ToolSpec(
    name="ready", cost=0.0, arguments="none",
    summary="move from verify to submit after testing the SQL in this phase",
    required=(), run=None,
)
LOCAL_ACTION_SPECS = {
    spec.name: spec for spec in (PLAN_SPEC, PLAN_DONE_SPEC, REOPEN_SPEC, READY_SPEC)
}
LOCAL_ACTION_ORDER = ("plan", "plan_done", "reopen", "ready")


class FlowError(ValueError):
    """The flow setting or one of its numbers is unusable."""


class FlowRejection(ActionError):
    """A parsed action was refused by the stage order or the budget rule."""

    def __init__(self, message: str, *, kind: str, action: dict):
        super().__init__(message)
        self.kind = kind
        self.action = action


@dataclass(frozen=True)
class FlowConfig:
    """The staged mode's setting values, read once when the server starts."""

    setting: str = STAGED
    ask_limit: int = DEFAULT_ASK_LIMIT
    phase2_reserve: float = DEFAULT_PHASE2_RESERVE

    def descriptor(self) -> dict:
        return {
            "mode": STAGED,
            "setting": self.setting,
            "ask_limit": self.ask_limit,
            "phase2_reserve": self.phase2_reserve,
        }


def _ask_limit(raw: object) -> int:
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_ASK_LIMIT
    try:
        value = int(text)
    except ValueError as exc:
        raise FlowError(
            f"{ASK_LIMIT_ENV} must be a positive integer, got {text!r}"
        ) from exc
    if value < 1:
        raise FlowError(f"{ASK_LIMIT_ENV} must be a positive integer, got {text!r}")
    return value


def _phase2_reserve(raw: object) -> float:
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_PHASE2_RESERVE
    try:
        value = float(text)
    except ValueError as exc:
        raise FlowError(
            f"{PHASE2_RESERVE_ENV} must be a non-negative number, got {text!r}"
        ) from exc
    if value < 0:
        raise FlowError(f"{PHASE2_RESERVE_ENV} must be a non-negative number, got {text!r}")
    return value


def load_flow_config(
    setting: str | None = None, environ: Mapping | None = None
) -> FlowConfig | None:
    """Read ``AGENT_FLOW`` and its numbers; ``None`` means the mode is off."""
    environ = os.environ if environ is None else environ
    raw = setting if setting is not None else environ.get(FLOW_ENV, "")
    value = str(raw).strip().lower()
    if not value or value == FREE:
        return None
    if value != STAGED:
        raise FlowError(
            f"{FLOW_ENV}={value!r} is not supported; use {FREE!r} or {STAGED!r}"
        )
    return FlowConfig(
        setting=value,
        ask_limit=_ask_limit(environ.get(ASK_LIMIT_ENV, "")),
        phase2_reserve=_phase2_reserve(environ.get(PHASE2_RESERVE_ENV, "")),
    )


def allowed_specs(stage: str, overall: dict[str, ToolSpec]) -> dict[str, ToolSpec]:
    """The stage's official tools that this session offers, then its local actions."""
    specs = {
        name: overall[name] for name in STAGE_ACTIONS[stage] if name in overall
    }
    specs.update({name: LOCAL_ACTION_SPECS[name] for name in STAGE_LOCAL_ACTIONS[stage]})
    return specs


def local_catalog() -> str:
    lines = ["Local actions (not official tools; cost 0):"]
    for name in LOCAL_ACTION_ORDER:
        spec = LOCAL_ACTION_SPECS[name]
        lines.append(
            f"- {spec.name}: {spec.summary}. arguments: {spec.arguments}. Cost: {spec.cost:g}"
        )
    return "\n".join(lines)


def staged_rules(config: FlowConfig) -> str:
    if config.phase2_reserve > 0:
        budget_line = (
            f"- A non-submission action is rejected without cost when it would leave fewer than\n"
            f"  {SUBMIT_COST:g} bird-coins, or fewer than "
            f"{SUBMIT_COST + config.phase2_reserve:g} in phase 1 (a phase 2\n"
            f"  reserve of {config.phase2_reserve:g})."
        )
    else:
        budget_line = (
            f"- A non-submission action is rejected without cost when it would leave fewer than\n"
            f"  {SUBMIT_COST:g} bird-coins, so a submission stays possible."
        )
    return (
        "- Staged flow is on. The observation header shows the current stage and the actions it\n"
        "  allows.\n"
        "- understand: use this task's information lookups, then call plan with the open questions\n"
        "  and the assumptions you will make without asking. Declared questions lead to ask; an\n"
        "  empty question list leads to verify.\n"
        "- ask: ask the declared questions with ask_user, grouped by topic. plan_done moves to\n"
        "  verify when you need no more answers.\n"
        "- verify: run execute_sql at least once in this phase and check values, formats and NULLs.\n"
        "  get_column_meaning and get_knowledge_definition are also allowed here. reopen returns\n"
        "  to ask once per phase; ready moves to submit.\n"
        "- submit: submit_sql is the only action. A failed submission returns to verify; a passing\n"
        "  phase 1 submission starts phase 2 at understand.\n"
        "- plan, plan_done, reopen and ready are local actions, not official tools. They cost no\n"
        "  bird-coins, but the response that chooses one counts as one model action.\n"
        "- An action outside the current stage is rejected without cost, and the observation lists\n"
        "  the actions the stage allows.\n"
        f"{budget_line}\n"
        f"- The ask stage stops at {config.ask_limit} ask_user calls per phase."
    )


def flow_notice(index: int, phase: int, body: str) -> str:
    """An observation that is not the result of an executed action."""
    return f"[flow notice | next action {index} | phase {phase}]\n{body}"


class StagedFlow:
    """One session's stage machine; it mutates ``state['flow']`` only."""

    def __init__(self, config: FlowConfig, state: dict, overall: dict[str, ToolSpec]):
        self.config = config
        self.state = state
        self.overall = overall
        self.data = {
            "mode": STAGED,
            "setting": config.setting,
            "ask_limit": config.ask_limit,
            "phase2_reserve": config.phase2_reserve,
            "stage": UNDERSTAND,
            "phase": int(state.get("current_phase", 1)),
            "transitions": [],
            "plans": [],
            "questions": [],
            "assumptions": [],
            "pending_questions": [],
            "asked_in_stage": 0,
            "asked_in_phase": 0,
            "tested": False,
            "reopen": [],
            "rejections": {"stage": 0, "budget": 0, "flow": 0},
        }
        state["flow"] = self.data

    @property
    def stage(self) -> str:
        return self.data["stage"]

    @property
    def phase(self) -> int:
        return int(self.data["phase"])

    def allowed(self) -> dict[str, ToolSpec]:
        return allowed_specs(self.stage, self.overall)

    def allowed_names(self) -> list[str]:
        return list(self.allowed())

    def _transition(self, target: str, reason: str, action: int) -> dict:
        entry = {
            "action": int(action),
            "phase": self.phase,
            "from": self.stage,
            "to": target,
            "reason": reason,
        }
        self.data["transitions"].append(entry)
        self.data["stage"] = target
        return entry

    def _reject(self, kind: str, message: str, name: str, args: dict):
        self.data["rejections"][kind] = self.data["rejections"].get(kind, 0) + 1
        raise FlowRejection(message, kind=kind, action={"name": name, "args": args})

    def check(self, name: str, args: dict, current_budget: float) -> None:
        """Refuse an action that this stage or the budget rule does not allow."""
        specs = self.allowed()
        if name not in specs:
            self._reject(
                "stage",
                f"The {self.stage} stage does not allow '{name}'. Allowed actions here: "
                f"{', '.join(specs)}. Nothing was executed and no bird-coins were charged.",
                name, args,
            )
        if name == "reopen" and self._reopen_used():
            self._reject(
                "flow",
                "reopen was already used in this phase; it is allowed once per phase. "
                "Nothing was executed and no bird-coins were charged.",
                name, args,
            )
        if name == "ask_user" and self.data["asked_in_phase"] >= self.config.ask_limit:
            self._reject(
                "flow",
                f"The ask limit of {self.config.ask_limit} per phase is used up. Call "
                "plan_done to continue; no bird-coins were charged.",
                name, args,
            )
        if (
            name == "ready"
            and not self.data["tested"]
            and current_budget >= SUBMIT_COST + EXECUTE_SQL_COST
        ):
            self._reject(
                "flow",
                "At least one execute_sql must run in this phase before ready. Test the SQL, "
                "then call ready again. No bird-coins were charged.",
                name, args,
            )
        if name == "submit_sql":
            return
        cost = specs[name].cost
        if cost <= 0:
            return
        after = current_budget - cost
        if after < SUBMIT_COST:
            self._reject(
                "budget",
                f"'{name}' costs {cost:g} bird-coins, but only {current_budget:g} remain and a "
                f"submission costs {SUBMIT_COST:g}. The action was not executed and no "
                "bird-coins were charged.",
                name, args,
            )
        threshold = SUBMIT_COST + self.config.phase2_reserve
        if self.phase == 1 and self.config.phase2_reserve > 0 and after < threshold:
            self._reject(
                "budget",
                f"'{name}' costs {cost:g} bird-coins. In phase 1 it was not executed because "
                f"it would leave fewer than {threshold:g} bird-coins (submission "
                f"{SUBMIT_COST:g} plus a phase 2 reserve of "
                f"{self.config.phase2_reserve:g}). No bird-coins were charged.",
                name, args,
            )

    def _reopen_used(self) -> bool:
        return any(item["phase"] == self.phase for item in self.data["reopen"])

    def _affordable(self, cost: float, current_budget: float) -> bool:
        after = current_budget - cost
        if after < SUBMIT_COST:
            return False
        if self.phase == 1 and self.config.phase2_reserve > 0:
            return after >= SUBMIT_COST + self.config.phase2_reserve
        return True

    def ensure_progress(self, current_budget: float, action: int) -> str | None:
        """Move to submit when no paid action of the current stage can run.

        Local actions are free, so they never block a stage; they also never
        count as progress here. If the stage has no official action this
        session offers at all (understand in oracle-context mode), the model
        still leaves it with plan.
        """
        if self.stage == SUBMIT:
            return None
        official = [name for name in STAGE_ACTIONS[self.stage] if name in self.overall]
        if not official:
            return None
        if any(self._affordable(self.overall[name].cost, current_budget) for name in official):
            return None
        blocked = self.stage
        self._transition(SUBMIT, "no_affordable_action", action)
        return flow_notice(
            action, self.phase,
            f"No action of the {blocked} stage can be paid for while keeping a submission "
            "possible, so the flow moved to the submit stage. Allowed actions here: "
            f"{', '.join(self.allowed_names())}. Submit the SQL you have.",
        )

    def mark_tested(self) -> None:
        self.data["tested"] = True

    def _ask_allowance(self) -> int:
        return max(0, self.config.ask_limit - self.data["asked_in_phase"])

    def _enter_ask(self, questions: list[str], reason: str, action: int) -> None:
        self._transition(ASK, reason, action)
        self.data["pending_questions"] = list(questions)
        self.data["asked_in_stage"] = 0

    def _ask_instruction(self, count: int) -> str:
        allowance = min(count, self._ask_allowance())
        if allowance <= 0:
            return (
                "The phase's ask limit is already used up, so the flow moved to the ask stage "
                "only to let you call plan_done."
            )
        return (
            f"The flow moved to the ask stage. Ask up to {allowance} question(s) with "
            "ask_user, or call plan_done."
        )

    def run_local(self, name: str, args: dict, action: int, phase: int) -> str:
        """Execute one local action, record it, and return its observation body."""
        stage_before = self.stage
        budget_before = budget(self.state)
        result: dict = {"kind": name}
        if name == "plan":
            questions = list(args["questions"])
            assumptions = list(args["assumptions"])
            self.data["plans"].append({
                "action": action, "phase": phase,
                "questions": questions, "assumptions": assumptions,
            })
            self.data["questions"] = questions
            self.data["assumptions"] = assumptions
            body = (
                "Plan recorded.\nOpen questions:\n"
                + ("\n".join(f"- {question}" for question in questions) or "- (none)")
                + "\nAssumptions:\n"
                + ("\n".join(f"- {item}" for item in assumptions) or "- (none)")
            )
            if questions:
                self._enter_ask(questions, "plan", action)
                body += "\n" + self._ask_instruction(len(questions))
            else:
                self._transition(VERIFY, "plan", action)
                body += (
                    "\nNo open questions were declared, so the flow moved to the verify stage."
                )
            result.update({
                "questions": questions, "assumptions": assumptions,
                "next_stage": self.stage,
            })
        elif name == "plan_done":
            self._transition(VERIFY, "plan_done", action)
            result["next_stage"] = self.stage
            body = "The ask stage is finished; the flow moved to the verify stage."
        elif name == "reopen":
            reason = args["reason"]
            questions = list(args["questions"])
            self.data["reopen"].append({
                "action": action, "phase": phase, "reason": reason, "questions": questions,
            })
            self.data["questions"] = questions
            self._enter_ask(questions, "reopen", action)
            result.update({
                "reason": reason, "questions": questions, "next_stage": self.stage,
            })
            body = (
                f"Reopened the ask stage once in this phase.\nReason: {reason}\nNew open "
                "questions:\n"
                + "\n".join(f"- {question}" for question in questions)
                + "\n" + self._ask_instruction(len(questions))
            )
        else:
            self._transition(SUBMIT, "ready", action)
            result["next_stage"] = self.stage
            body = (
                "The flow moved to the submit stage. Allowed actions here: submit_sql."
            )
        record_action(
            self.state, name, dict(args), result, 0.0, budget_before,
            phase=phase, stage=stage_before,
        )
        return body

    def on_ask(self, action: int) -> str:
        """Count one answered question and leave ask when its target is met."""
        pending = len(self.data["pending_questions"])
        allowance = self._ask_allowance()
        self.data["asked_in_stage"] += 1
        self.data["asked_in_phase"] += 1
        target = min(pending, allowance) if pending else allowance
        if self.data["asked_in_stage"] < target:
            return ""
        limit_reached = allowance <= pending
        reason = "ask_limit_reached" if limit_reached else "declared_questions_asked"
        self._transition(VERIFY, reason, action)
        if limit_reached:
            return (
                f"\nThe per-phase ask limit of {self.config.ask_limit} was reached, so the flow "
                "moved to the verify stage."
            )
        return (
            f"\nAll {pending} declared question(s) were asked, so the flow moved to the verify "
            "stage."
        )

    def on_submit_failed(self, action: int) -> str:
        self._transition(VERIFY, "submit_failed", action)
        return (
            "The submission did not pass, so the flow moved back to the verify stage. Revise "
            "the SQL and call ready when it can be submitted again."
        )

    def on_phase1_passed(self, action: int) -> str:
        """Start phase 2 at understand; called after current_phase becomes 2."""
        self._transition(UNDERSTAND, "phase1_passed", action)
        self.data["phase"] = 2
        self.data["tested"] = False
        self.data["asked_in_stage"] = 0
        self.data["asked_in_phase"] = 0
        self.data["pending_questions"] = []
        self.data["questions"] = []
        self.data["assumptions"] = []
        return "Phase 2 begins in the understand stage again."
