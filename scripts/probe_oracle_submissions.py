"""Submit selected tasks' ground-truth SQL through the official DB service.

Reproduces the observed Phase 1/Phase 2 grading results of a dataset task by task
(init -> Phase 1 submission -> Phase 2 submission) without any model call. Used to
record the original failures and to confirm a single task after a calibration change.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent


def read_tasks(path: Path) -> dict:
    tasks = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line)
            tasks[row["instance_id"]] = row
    return tasks


def follow_up_sql(task: dict) -> str | None:
    follow_up = task.get("follow_up") or {}
    sql = follow_up.get("sol_sql")
    if isinstance(sql, list):
        sql = sql[0] if sql else None
    return sql or None


def submit(client: httpx.Client, task_id: str, sql: str) -> dict:
    response = client.post("/submit", json={"task_id": task_id, "sql": sql})
    response.raise_for_status()
    payload = response.json()
    return {
        "passed": bool(payload.get("passed")),
        "reward": payload.get("reward"),
        "message": payload.get("message"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="dataset JSONL with ground-truth SQL")
    parser.add_argument("--tasks", required=True, help="comma separated instance IDs")
    parser.add_argument("--db-env-url", default="http://127.0.0.1:6002")
    args = parser.parse_args()

    tasks = read_tasks(Path(args.data))
    selected = [name.strip() for name in args.tasks.split(",") if name.strip()]
    missing = [name for name in selected if name not in tasks]
    if missing:
        raise SystemExit(f"Tasks missing from {args.data}: {missing}")

    results = []
    with httpx.Client(base_url=args.db_env_url, timeout=180.0, trust_env=False) as client:
        for task_id in selected:
            task = tasks[task_id]
            phase1_sql = task["sol_sql"]
            phase1_sql = phase1_sql[0] if isinstance(phase1_sql, list) else phase1_sql
            result = {"task_id": task_id}
            client.post("/init_task", json={
                "task_id": task_id, "task_data": {**task, "_interact_mode": "a-interact"},
            }).raise_for_status()
            try:
                result["phase1"] = submit(client, task_id, phase1_sql)
                phase2_sql = follow_up_sql(task)
                if result["phase1"]["passed"] and phase2_sql:
                    result["phase2"] = submit(client, task_id, phase2_sql)
            finally:
                client.post("/cleanup_task", json={"task_id": task_id}).raise_for_status()
            results.append(result)
            summary = json.dumps(result, ensure_ascii=False)
            print(summary, file=sys.stderr)

    print(json.dumps({"data": args.data, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
