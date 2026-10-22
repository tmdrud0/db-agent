"""Compare TASK-016 staged-flow runs with free-loop runs from result files only.

Each run is given as ``LABEL=path`` to a ``results/*.json`` file written by the
official runner. Repeating a label collects several runs of the same
condition; the report shows each condition's run count, the mean across runs
and the run-to-run range.

Usage:
  python scripts/analyze_staged_flow.py \
      --run F=results/tools_dev30_calibrated_task013_baseline_r1.json \
      --run S=results/tools_dev30_calibrated_flow_S_r1.json \
      --run S=results/tools_dev30_calibrated_flow_S_r2.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

# Average rewards of earlier dev30 runs (TASK-016 spec v1), used to frame the
# comparison: a difference inside this spread is not a conclusion.
PRIOR_RANGE = (0.317, 0.463)
STAGES = ("understand", "ask", "verify", "submit")
STOP_REASONS = (
    ("Phase 2 correct", "phase2_completed"),
    ("Task finished", "phase1_completed"),
    ("model actions", "action_limit_reached"),
    ("-second session limit", "session_time_limit"),
    ("bird-coins remain", "budget_exhausted_before_submit"),
)
NUMERIC_ROWS = (
    ("tasks per run", "tasks", "{}"),
    ("average reward", "average_reward", "{:.3f}"),
    ("phase 1 passed", "phase1_passed", "{:.1f}"),
    ("phase 2 passed", "phase2_passed", "{:.1f}"),
    ("first phase 1 submission pass rate", "first_submit_pass_rate", "{:.2f}"),
    ("understand actions per task", "stage_understand", "{:.2f}"),
    ("ask actions per task", "stage_ask", "{:.2f}"),
    ("verify actions per task", "stage_verify", "{:.2f}"),
    ("submit actions per task", "stage_submit", "{:.2f}"),
    ("stage violations per task", "stage_violations", "{:.2f}"),
    ("budget-rule rejections per task", "budget_rejections", "{:.2f}"),
    ("other flow rejections per task", "flow_rejections", "{:.2f}"),
    ("plan questions declared per task", "plan_questions", "{:.2f}"),
    ("ask_user per task", "ask_user", "{:.2f}"),
    ("reopen per task", "reopen", "{:.2f}"),
    ("coins left when phase 1 passed", "coins_at_phase1_pass", "{:.2f}"),
    ("tasks that passed phase 1", "phase1_passed_tasks", "{:.1f}"),
    ("tasks with a phase 2 action", "phase2_action_tasks", "{:.1f}"),
    ("execute_sql per task", "execute_sql", "{:.2f}"),
    ("submit_sql per task", "submit_sql", "{:.2f}"),
    ("main agent input tokens per task", "input_tokens", "{:.0f}"),
    ("main agent output tokens per task", "output_tokens", "{:.0f}"),
    ("seconds per task", "seconds", "{:.1f}"),
)


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("results", []))


def model_calls(result: dict) -> list[dict]:
    return [event for event in result.get("adk_events") or [] if event.get("type") == "model_call"]


def executed_calls(result: dict) -> list[dict]:
    """Model calls that executed an action; rejected responses are not actions."""
    return [
        event for event in model_calls(result)
        if event.get("action") and not event.get("rejection")
    ]


def actions_by_stage(result: dict) -> Counter:
    return Counter(event.get("stage") or "-" for event in executed_calls(result))


def rejections(result: dict, kind: str) -> int:
    return sum(1 for event in model_calls(result) if event.get("rejection") == kind)


def tools(result: dict, name: str) -> list[dict]:
    return [entry for entry in result.get("tool_trajectory") or [] if entry.get("tool") == name]


def declared_questions(result: dict) -> int:
    plans = tools(result, "plan")
    if plans:
        total = 0
        for entry in plans:
            outcome = entry.get("result")
            if isinstance(outcome, dict):
                total += len(outcome.get("questions") or [])
        return total
    total = 0
    for event in model_calls(result):
        action = event.get("action") or {}
        if action.get("name") == "plan":
            total += len((action.get("args") or {}).get("questions") or [])
    return total


def first_submit_passed(result: dict) -> bool | None:
    submits = [entry for entry in tools(result, "submit_sql") if entry.get("phase", 1) == 1]
    if not submits:
        return None
    outcome = submits[0].get("result")
    return bool(outcome.get("passed")) if isinstance(outcome, dict) else False


def phase1_pass_remaining(result: dict) -> float | None:
    for entry in tools(result, "submit_sql"):
        if entry.get("phase", 1) != 1:
            continue
        outcome = entry.get("result")
        if isinstance(outcome, dict) and outcome.get("passed"):
            return float(entry.get("budget_after") or 0.0)
    return None


def has_phase2_action(result: dict) -> bool:
    return any(
        int(event.get("phase") or 1) == 2 and event.get("action")
        and not event.get("rejection")
        for event in model_calls(result)
    )


def stop_reason(result: dict) -> str:
    if result.get("error"):
        return "error"
    text = str(result.get("final_response") or "")
    for marker, reason in STOP_REASONS:
        if marker in text:
            return reason
    return "other"


def usage(result: dict, key: str) -> int:
    return sum(int((event.get("usage") or {}).get(key) or 0) for event in model_calls(result))


def summarize(results: list[dict]) -> dict:
    """One run's per-task means, so runs of a condition can be compared."""
    count = len(results)
    first = [first_submit_passed(result) for result in results]
    submitted = [value for value in first if value is not None]
    remaining = [
        value for value in (phase1_pass_remaining(result) for result in results)
        if value is not None
    ]
    stage_counts = {
        stage: mean(actions_by_stage(result).get(stage, 0) for result in results)
        if results else 0.0
        for stage in STAGES
    }
    reasons: Counter = Counter(stop_reason(run) for run in results)
    return {
        "tasks": count,
        "average_reward": mean(float(r.get("total_reward") or 0) for r in results) if results else 0.0,
        "phase1_passed": sum(1 for r in results if r.get("phase1_passed")),
        "phase2_passed": sum(1 for r in results if r.get("phase2_passed")),
        "first_submit_pass_rate": (sum(submitted) / len(submitted)) if submitted else 0.0,
        "stage_understand": stage_counts["understand"],
        "stage_ask": stage_counts["ask"],
        "stage_verify": stage_counts["verify"],
        "stage_submit": stage_counts["submit"],
        "stage_violations": mean(rejections(r, "stage") for r in results) if results else 0.0,
        "budget_rejections": mean(rejections(r, "budget") for r in results) if results else 0.0,
        "flow_rejections": mean(rejections(r, "flow") for r in results) if results else 0.0,
        "plan_questions": mean(declared_questions(r) for r in results) if results else 0.0,
        "ask_user": mean(len(tools(r, "ask_user")) for r in results) if results else 0.0,
        "reopen": mean(len(tools(r, "reopen")) for r in results) if results else 0.0,
        "coins_at_phase1_pass": mean(remaining) if remaining else 0.0,
        "phase1_passed_tasks": len(remaining),
        "phase2_action_tasks": sum(1 for r in results if has_phase2_action(r)),
        "execute_sql": mean(len(tools(r, "execute_sql")) for r in results) if results else 0.0,
        "submit_sql": mean(len(tools(r, "submit_sql")) for r in results) if results else 0.0,
        "input_tokens": mean(usage(r, "prompt_tokens") for r in results) if results else 0.0,
        "output_tokens": mean(usage(r, "completion_tokens") for r in results) if results else 0.0,
        "seconds": mean(float(r.get("elapsed_seconds") or 0) for r in results) if results else 0.0,
        "stop_reasons": reasons,
    }


def aggregate(runs: dict[str, list[list[dict]]]) -> dict[str, dict]:
    """Per condition: metric -> (mean, min, max) over runs."""
    aggregated = {}
    for label, run_results in runs.items():
        stats = [summarize(results) for results in run_results]
        metrics = {}
        for _title, key, _fmt in NUMERIC_ROWS:
            values = [float(stat[key]) for stat in stats]
            metrics[key] = (mean(values), min(values), max(values))
        reasons: Counter = Counter()
        for stat in stats:
            reasons.update(stat["stop_reasons"])
        metrics["stop_reasons"] = reasons
        metrics["runs"] = len(stats)
        aggregated[label] = metrics
    return aggregated


def _cell(values: tuple[float, float, float], fmt: str) -> str:
    average, low, high = values
    if high - low < 1e-9:
        return fmt.format(average)
    return f"{fmt.format(average)} [{fmt.format(low)}-{fmt.format(high)}]"


def report(runs: dict[str, list[list[dict]]]) -> str:
    stats = aggregate(runs)
    labels = list(runs)
    counts = ", ".join(f"{label}: {stats[label]['runs']} run(s)" for label in labels)
    lines = [
        "Staged-flow comparison (TASK-016), read from result JSON files only.",
        counts + ".",
        f"Earlier dev30 runs averaged {PRIOR_RANGE[0]:.3f}-{PRIOR_RANGE[1]:.3f}; a difference "
        "smaller than that spread is not a conclusion.",
        "",
        "| metric | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for title, key, fmt in NUMERIC_ROWS:
        lines.append(
            f"| {title} | "
            + " | ".join(_cell(stats[label][key], fmt) for label in labels)
            + " |"
        )
    reasons = sorted({reason for label in labels for reason in stats[label]["stop_reasons"]})
    for reason in reasons:
        lines.append(
            f"| stop: {reason} | "
            + " | ".join(str(stats[label]["stop_reasons"].get(reason, 0)) for label in labels)
            + " |"
        )
    lines += ["", "Per-task reward (P1/P2 passed):", "",
              "| task | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    tasks = sorted({
        result.get("task_id")
        for run in runs.values() for results in run for result in results
    })
    index = {
        label: {
            result.get("task_id"): result
            for results in run for result in results
        }
        for label, run in runs.items()
    }
    for task in tasks:
        cells = []
        for label in labels:
            result = index[label].get(task)
            if result is None:
                cells.append("not run")
            elif result.get("error"):
                cells.append("error")
            else:
                cells.append(
                    f"{float(result.get('total_reward') or 0):.1f} "
                    f"({'Y' if result.get('phase1_passed') else 'N'}/"
                    f"{'Y' if result.get('phase2_passed') else 'N'})"
                )
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", action="append", required=True, metavar="LABEL=PATH",
        help="a result JSON file; repeat the same LABEL for another run of that condition",
    )
    args = parser.parse_args(argv)
    runs: dict[str, list[list[dict]]] = {}
    for item in args.run:
        label, separator, path = item.partition("=")
        if not separator or not label or not path:
            parser.error(f"--run needs LABEL=PATH, got {item!r}")
        runs.setdefault(label, []).append(load(Path(path)))
    sys.stdout.write(report(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
