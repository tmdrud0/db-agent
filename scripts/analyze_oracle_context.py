"""Compare TASK-015 oracle-context runs with a baseline run from result files only.

Diagnostic only: the oracle-context groups receive context selected with
ground truth, so their numbers are never agent performance. Each run is given
as ``LABEL=path`` to a ``results/*.json`` file written by the official runner.

Usage:
  python scripts/analyze_oracle_context.py \
      --run baseline=results/tools_dev30_calibrated_task013_baseline_r1.json \
      --run A=results/tools_dev30_calibrated_oracle_context_A_r1.json ...
"""

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

# Average rewards of earlier dev30 runs (TASK-015 spec v2), used to frame the
# one-run comparison: a difference inside this spread is not a conclusion.
PRIOR_RANGE = (0.327, 0.463)
STOP_REASONS = (
    ("Phase 2 correct", "phase2_completed"),
    ("Task finished", "phase1_completed"),
    ("model actions", "action_limit_reached"),
    ("-second session limit", "session_time_limit"),
    ("bird-coins remain", "budget_exhausted_before_submit"),
)


def load(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("results", []))


def model_calls(result: dict) -> list[dict]:
    return [event for event in result.get("adk_events") or [] if event.get("type") == "model_call"]


def experiment(result: dict) -> dict:
    calls = model_calls(result)
    return dict(calls[0].get("experiment") or {}) if calls else {}


def tools(result: dict, name: str) -> list[dict]:
    return [entry for entry in result.get("tool_trajectory") or [] if entry.get("tool") == name]


def first_submit_passed(result: dict) -> bool | None:
    submits = [entry for entry in tools(result, "submit_sql") if entry.get("phase", 1) == 1]
    if not submits:
        return None
    outcome = submits[0].get("result")
    return bool(outcome.get("passed")) if isinstance(outcome, dict) else False


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
    count = len(results)
    first = [first_submit_passed(result) for result in results]
    submitted = [value for value in first if value is not None]
    offsets = [float(experiment(result).get("budget_offset") or 0) for result in results]
    chars = [experiment(result).get("context_chars") or {} for result in results]
    reasons: dict[str, int] = {}
    for result in results:
        reason = stop_reason(result)
        reasons[reason] = reasons.get(reason, 0) + 1
    groups = sorted({experiment(result).get("oracle_context") for result in results} - {None})

    def per_task(values):
        return mean(values) if values else 0.0

    return {
        "tasks": count,
        "oracle_context": ",".join(groups) or "-",
        "average_reward": per_task([float(r.get("total_reward") or 0) for r in results]),
        "phase1_passed": sum(1 for r in results if r.get("phase1_passed")),
        "phase2_passed": sum(1 for r in results if r.get("phase2_passed")),
        "first_submit_pass_rate": (sum(submitted) / len(submitted)) if submitted else 0.0,
        "ask_user": per_task([len(tools(r, "ask_user")) for r in results]),
        "execute_sql": per_task([len(tools(r, "execute_sql")) for r in results]),
        "submit_sql": per_task([len(tools(r, "submit_sql")) for r in results]),
        # budget_used includes the experiment offset; the spent coins exclude it.
        "coins_spent": per_task([
            float(r.get("budget_used") or 0) - offset for r, offset in zip(results, offsets)
        ]),
        "input_tokens": per_task([usage(r, "prompt_tokens") for r in results]),
        "output_tokens": per_task([usage(r, "completion_tokens") for r in results]),
        "seconds": per_task([float(r.get("elapsed_seconds") or 0) for r in results]),
        "context_chars_p1": per_task([int(c.get("phase1") or 0) for c in chars]),
        "context_chars_p2": per_task([int(c.get("phase2") or 0) for c in chars]),
        "stop_reasons": reasons,
    }


ROWS = (
    ("oracle context group", "oracle_context", "{}"),
    ("tasks", "tasks", "{}"),
    ("average reward", "average_reward", "{:.3f}"),
    ("phase 1 passed", "phase1_passed", "{}"),
    ("phase 2 passed", "phase2_passed", "{}"),
    ("first phase 1 submission pass rate", "first_submit_pass_rate", "{:.2f}"),
    ("ask_user per task", "ask_user", "{:.2f}"),
    ("execute_sql per task", "execute_sql", "{:.2f}"),
    ("submit_sql per task", "submit_sql", "{:.2f}"),
    ("coins spent per task (offset excluded)", "coins_spent", "{:.2f}"),
    ("main agent input tokens per task", "input_tokens", "{:.0f}"),
    ("main agent output tokens per task", "output_tokens", "{:.0f}"),
    ("seconds per task", "seconds", "{:.1f}"),
    ("provided context chars, phase 1", "context_chars_p1", "{:.0f}"),
    ("provided context chars, phase 2 additions", "context_chars_p2", "{:.0f}"),
)


def report(runs: dict[str, list[dict]]) -> str:
    stats = {label: summarize(results) for label, results in runs.items()}
    labels = list(runs)
    lines = [
        "Diagnostic oracle-context comparison (TASK-015). The oracle-context groups receive "
        "ground-truth-selected context; these numbers are not agent performance.",
        f"One run per group. Earlier dev30 runs averaged {PRIOR_RANGE[0]:.3f}-{PRIOR_RANGE[1]:.3f}; "
        "a difference smaller than that spread is not a conclusion.",
        "",
        "| metric | " + " | ".join(labels) + " |",
        "|---|" + "---|" * len(labels),
    ]
    for title, key, form in ROWS:
        lines.append(f"| {title} | " + " | ".join(form.format(stats[l][key]) for l in labels) + " |")
    reasons = sorted({reason for label in labels for reason in stats[label]["stop_reasons"]})
    for reason in reasons:
        lines.append(f"| stop: {reason} | " + " | ".join(
            str(stats[l]["stop_reasons"].get(reason, 0)) for l in labels) + " |")

    lines += ["", "Per-task reward (P1/P2 passed):", "",
              "| task | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    tasks = sorted({r.get("task_id") for results in runs.values() for r in results})
    index = {label: {r.get("task_id"): r for r in results} for label, results in runs.items()}
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
    parser.add_argument("--run", action="append", required=True, metavar="LABEL=PATH")
    args = parser.parse_args(argv)
    runs = {}
    for item in args.run:
        label, separator, path = item.partition("=")
        if not separator or not label or not path:
            parser.error(f"--run needs LABEL=PATH, got {item!r}")
        runs[label] = load(Path(path))
    sys.stdout.write(report(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
