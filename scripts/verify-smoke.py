"""Record evidence from the real one-task smoke evaluation; never call an LLM."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
repo = root / "vendor/BIRD-Interact"
data = repo / "BIRD-Interact-ADK/bird-interact-lite"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def revision(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


oracle = json.loads((root / "results/oracle_smoke.json").read_text())
empty = json.loads((root / "results/empty_submit.json").read_text())
assert len(oracle["results"]) == len(empty["results"]) == 1
positive, negative = oracle["results"][0], empty["results"][0]
assert positive["task_id"] == negative["task_id"] == "alien_1"
assert positive["phase1_passed"] and positive["phase2_passed"]
assert positive["total_reward"] == 1.0
assert not negative.get("error") and negative["total_reward"] == 0.0
assert negative["tool_trajectory"][0]["args"]["sql"] == ""
assert negative["tool_trajectory"][0]["result"]["message"] == (
    "SQL failed Phase 1. Your SQL is not correct."
)

public_rows = [json.loads(line) for line in (data / "bird_interact_data.jsonl").read_text().splitlines() if line.strip()]
gt_file = root / "bird_interact_gt_kg_testcases_1008.jsonl"
gt_rows = [json.loads(line) for line in gt_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
public_ids = {row["instance_id"] for row in public_rows}
gt_ids = {row["instance_id"] for row in gt_rows}
assert len(public_rows) == len(gt_rows) == len(public_ids) == len(gt_ids) == 300
assert public_ids == gt_ids

manifest = {
    "verified_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
    "benchmark_commit": revision(repo),
    "public_dataset_commit": revision(data),
    "ground_truth_file": gt_file.name,
    "ground_truth_sha256": digest(gt_file),
    "public_data_sha256": digest(data / "bird_interact_data.jsonl"),
    "combined_data_sha256": digest(data / "bird_interact_data.combined.jsonl"),
    "matched_tasks": 300,
    "missing_gt": 0,
    "evaluated_task": negative["task_id"],
    "category": "Query",
    "llm_calls": 0,
    "oracle_metrics": oracle["metrics"],
    "empty_submit_metrics": empty["metrics"],
    "empty_submit_elapsed_seconds": negative["elapsed_seconds"],
    "checks": {
        "oracle_phase1_passed": True,
        "oracle_phase2_passed": True,
        "empty_submission_reached_grader": True,
        "empty_submission_failed": True,
    },
    "scope": "One Query task; full dataset and custom Python test cases were not evaluated.",
}
destination = root / "results/grading-verification.json"
destination.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps({"task": negative["task_id"], "oracle": 1.0, "empty": 0.0,
                  "elapsed_seconds": negative["elapsed_seconds"], "evidence": str(destination)}))
