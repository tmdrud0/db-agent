"""Experiment mode that tidies the conversation the model sees.

``AGENT_CONTEXT_COMPACT=ac`` turns on two kinds of clean-up; without it the
messages are built exactly as before.

A (drops that lose no information):
- a rejected response and its rejection observation are removed once the next
  action is executed, so the model saw the reason for one turn;
- the plan observation no longer repeats the questions and assumptions the
  model just wrote (the staged flow builds the short text);
- after each failed submission, the ``ready``/``plan_done`` actions with their
  stage-change observations are removed and older observation headers drop the
  stage, allowed actions and budget they reported at the time.

C (phase boundary): when phase 1 passes, every phase 1 message after the first
user message is replaced by one summary built by code from the recorded state:
the user's answers, the last phase 1 plan assumptions, the number of failed
submissions and the passing SQL. No model writes the summary.

The clean-up runs only at those points, so the message prefix stays the same
between them.
"""

import json
import os
import re
from collections.abc import Mapping

COMPACT_ENV = "AGENT_CONTEXT_COMPACT"
SETTINGS = ("ac",)
LOCAL_TRANSITIONS = ("ready", "plan_done")

# The staged header, then the free one; both keep the action, phase, name and cost.
_STAGED_HEADER = re.compile(
    r"^\[action (\d+) \| phase (\d+) \| stage \w+ \| allowed [^|\]]*\| (\w+) \| "
    r"cost ([^|\]]+) \| budget [^\]]+\]"
)
_FREE_HEADER = re.compile(
    r"^\[action (\d+) \| phase (\d+) \| (\w+) \| cost ([^|\]]+) \| budget [^\]]+\]"
)


class CompactionError(ValueError):
    """``AGENT_CONTEXT_COMPACT`` holds a value the mode does not support."""


def load_compaction(setting: str | None = None, environ: Mapping | None = None) -> str | None:
    """Read ``AGENT_CONTEXT_COMPACT``; ``None`` means the mode is off."""
    environ = os.environ if environ is None else environ
    raw = setting if setting is not None else environ.get(COMPACT_ENV, "")
    value = str(raw).strip().lower()
    if not value or value == "off":
        return None
    if value not in SETTINGS:
        raise CompactionError(
            f"{COMPACT_ENV}={value!r} is not supported; use 'off' or {', '.join(SETTINGS)}"
        )
    return value


def stats(state: dict, setting: str) -> dict:
    """The counters recorded in the state and copied into every model_call event."""
    return state.setdefault("context_compaction", {
        "setting": setting,
        "rejections_removed": 0,
        "local_actions_removed": 0,
        "headers_shortened": 0,
        "phase1_summary": None,
    })


def drop_messages(messages: list[dict], drop: list[dict]) -> int:
    """Remove the given message objects (by identity) and return how many went."""
    ids = {id(message) for message in drop}
    kept = [message for message in messages if id(message) not in ids]
    removed = len(messages) - len(kept)
    messages[:] = kept
    return removed


def _action_name(message: dict) -> str | None:
    if message.get("role") != "assistant":
        return None
    try:
        parsed = json.loads(message.get("content") or "")
    except ValueError:
        return None
    return parsed.get("action") if isinstance(parsed, dict) else None


def _short_header(content: str) -> str:
    for pattern in (_STAGED_HEADER, _FREE_HEADER):
        match = pattern.match(content)
        if match:
            action, phase, name, cost = match.groups()
            head = f"[action {action} | phase {phase} | {name} | cost {cost.strip()}]"
            return head + content[match.end():]
    return content


def tidy_after_submit(messages: list[dict], state: dict, setting: str) -> None:
    """Remove local stage changes and shorten old headers; the last message stays."""
    record = stats(state, setting)
    drop = []
    for index in range(2, len(messages) - 1):
        if _action_name(messages[index]) in LOCAL_TRANSITIONS:
            drop.append(messages[index])
            following = messages[index + 1]
            if (
                index + 1 < len(messages) - 1 and following.get("role") == "user"
                and following.get("content", "").startswith("[action")
            ):
                drop.append(following)
    record["local_actions_removed"] += drop_messages(messages, drop) // 2
    for message in messages[2:-1]:
        if message.get("role") != "user":
            continue
        shorter = _short_header(message.get("content") or "")
        if shorter != message["content"]:
            message["content"] = shorter
            record["headers_shortened"] += 1


def phase1_summary(state: dict, flow_data: dict | None) -> str:
    """The text that replaces the phase 1 messages after a passing submission."""
    answers = [
        entry for entry in state.get("tool_trajectory") or []
        if entry.get("tool") == "ask_user" and entry.get("phase", 1) == 1
        and isinstance(entry.get("result"), dict) and entry["result"].get("kind") == "answer"
    ]
    lines = [
        "Phase 1 summary. The earlier phase 1 messages were replaced by this summary.",
        "Answers from the task user in phase 1:",
    ]
    for number, entry in enumerate(answers, 1):
        lines.append(f"{number}. Q: {entry['args'].get('question', '')}")
        lines.append(f"   A: {entry['result'].get('answer', '')}")
    if not answers:
        lines.append("- (none)")
    plans = [
        plan for plan in (flow_data or {}).get("plans", []) if plan.get("phase", 1) == 1
    ]
    if plans:
        lines.append("Assumptions in the last phase 1 plan:")
        lines.extend(f"- {item}" for item in plans[-1]["assumptions"] or ["(none)"])
    submissions = [
        attempt for attempt in state.get("submit_attempts") or [] if attempt.get("phase") == 1
    ]
    failed = max(len(submissions) - 1, 0)
    lines.append(f"Phase 1 submissions: {failed} failed before the passing one.")
    lines.append("Passing phase 1 SQL (already applied to the database):")
    lines.append(submissions[-1]["sql"] if submissions else "(not recorded)")
    return "\n".join(lines)


def summarize_phase1(
    messages: list[dict], state: dict, setting: str, flow_data: dict | None
) -> None:
    """Replace messages[2:-1] with the summary and keep the transition observation."""
    record = stats(state, setting)
    replaced = messages[2:-1]
    transition = messages[-1]
    summary = phase1_summary(state, flow_data)
    before = sum(len(message.get("content") or "") for message in messages[2:])
    messages[2:] = [{"role": "user", "content": f"{summary}\n\n{transition['content']}"}]
    record["phase1_summary"] = {
        "replaced_messages": len(replaced),
        "chars_before": before,
        "chars_after": len(messages[2]["content"]),
    }
