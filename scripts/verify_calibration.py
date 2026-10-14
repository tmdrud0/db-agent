"""Run the calibration verification scenarios against a live calibrated DB service.

Each scenario submits the dataset's ground-truth SQL plus the configured
counterexamples and then reads back the database state. Failures are printed with
the official message so connection errors, execution errors, and rejected
counterexamples stay distinguishable.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "calibration"))

import calibration_config


def read_tasks(path: Path) -> dict:
    tasks = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line)
            tasks[row["instance_id"]] = row
    return tasks


def phase_sql(task: dict, phase: int) -> str:
    if phase == 1:
        sql = task["sol_sql"]
    else:
        sql = (task.get("follow_up") or {}).get("sol_sql")
    if isinstance(sql, list):
        sql = sql[0] if sql else None
    if not sql:
        raise SystemExit(f"No Phase {phase} SQL for {task['instance_id']}")
    return sql


def check_service(client: httpx.Client, config: dict) -> list[str]:
    problems = []
    response = client.get("/calibration")
    if response.status_code != 200:
        problems.append("The calibrated DB service is not running (start it with scripts/run-dev30-calibrated.ps1)")
        return problems
    info = response.json()
    if info.get("service") != "db_environment_calibration":
        problems.append(f"Unexpected service identity: {info.get('service')}")
    expected = sorted(config.get("eval_prep", {}))
    reported = sorted(info.get("eval_prep", {}))
    if reported != expected:
        problems.append(f"Active eval_prep {reported} does not match the config {expected}")
    return problems


def run_scenario(client: httpx.Client, scenario: dict, task: dict) -> list[dict]:
    task_id = scenario["task_id"]
    outcomes: list[dict] = []
    pending = list(scenario.get("checks", []))
    client.post("/init_task", json={
        "task_id": task_id, "task_data": {**task, "_interact_mode": "a-interact"},
    }).raise_for_status()
    try:
        for submission in scenario["submissions"]:
            sql = submission["sql"] or phase_sql(task, submission["phase"])
            response = client.post("/submit", json={"task_id": task_id, "sql": sql})
            response.raise_for_status()
            payload = response.json()
            passed = bool(payload.get("passed"))
            outcomes.append({
                "kind": "submission", "task_id": task_id, "id": submission["id"],
                "phase": submission["phase"], "expected_passed": submission["expect_passed"],
                "actual_passed": passed, "reward": payload.get("reward"),
                "message": payload.get("message"), "note": submission.get("note", ""),
                "ok": passed == submission["expect_passed"],
            })
            due = [check for check in pending if check.get("after") == submission["id"]]
            for check in due:
                outcomes.append(run_check(client, task_id, check))
                pending.remove(check)
        for check in pending:
            outcomes.append(run_check(client, task_id, check))
    finally:
        client.post("/cleanup_task", json={"task_id": task_id}).raise_for_status()
    return outcomes


def run_check(client: httpx.Client, task_id: str, check: dict) -> dict:
    response = client.post("/execute", json={"task_id": task_id, "sql": check["sql"]})
    response.raise_for_status()
    payload = response.json()
    rows = calibration_config.parse_execute_result(payload.get("result") or "")
    return {
        "kind": "check", "task_id": task_id, "id": check["id"],
        "expected_rows": check["expect_rows"], "actual_rows": rows,
        "error": payload.get("error"), "note": check.get("note", ""),
        "ok": payload.get("success") and rows == check["expect_rows"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(ROOT / "private/lite30.calibrated.jsonl"))
    parser.add_argument("--config", default=str(ROOT / "private/calibration/TASK-002.calibration.json"))
    parser.add_argument("--db-env-url", default="http://127.0.0.1:6002")
    args = parser.parse_args()

    config = calibration_config.load_config(args.config)
    tasks = read_tasks(Path(args.data))
    outcomes: list[dict] = []
    with httpx.Client(base_url=args.db_env_url, timeout=180.0, trust_env=False) as client:
        problems = check_service(client, config)
        if problems:
            print(json.dumps({"problems": problems}, ensure_ascii=False, indent=2))
            raise SystemExit(1)
        for scenario in config.get("verification", []):
            task_id = scenario["task_id"]
            if task_id not in tasks:
                outcomes.append({"kind": "scenario", "task_id": task_id, "ok": False,
                                 "message": f"missing from {args.data}"})
                continue
            outcomes.extend(run_scenario(client, scenario, tasks[task_id]))

    for outcome in outcomes:
        print(json.dumps(outcome, ensure_ascii=False), file=sys.stderr)
    failed = [outcome for outcome in outcomes if not outcome["ok"]]
    print(json.dumps({
        "data": args.data, "checks": len(outcomes), "failed": len(failed),
        "tasks": sorted({outcome["task_id"] for outcome in outcomes}),
        "passed": not failed,
    }, ensure_ascii=False))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
