"""Direct functional check of the execute_sql and ask_user connections.

This is a connection check, not an evaluation: the agent is not started, no
SQL is submitted, and nothing is scored. It initializes one task on the DB
environment and on the user simulator, then calls the two official HTTP
endpoints once each with a harmless read-only statement and a public question.
The /ask call includes the real model calls of the official simulator.

The non-secret metadata is written to results/ (never committed) so the report
can reference code, vendor, input and service settings without copying any
ground truth or key.
"""

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = ROOT / "private" / "lite30.calibrated.jsonl"
DEFAULT_OUTPUT = ROOT / "results" / "tool_connection.json"
VENDOR = ROOT / "vendor" / "BIRD-Interact"
# A harmless read-only query on the task database: no task table is required
# and no ground truth is involved.
EXECUTE_STATEMENT = (
    "SELECT count(*) AS table_count FROM information_schema.tables "
    "WHERE table_schema = 'public';"
)
QUESTION = "Before I continue: what should the final result contain for this request?"
INIT_TIMEOUT_SECONDS = 120.0
EXECUTE_TIMEOUT_SECONDS = 120.0
ASK_TIMEOUT_SECONDS = 660.0


def git_output(path: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), *args], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def revision(path: Path) -> str:
    return git_output(path, "rev-parse", "HEAD").strip() or "unknown"


def parse_tracked_changes(porcelain: str) -> list[str]:
    changes = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        changes.append(path.strip().strip('"'))
    return changes


def tracked_changes(path: Path) -> list[str]:
    return parse_tracked_changes(
        git_output(
            path, "-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=no"
        )
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counter_delta(after: dict, before: dict, key: str) -> int | None:
    new, old = after.get(key), before.get(key)
    return new - old if isinstance(new, int) and isinstance(old, int) else None


def post(url: str, payload: dict, timeout: float) -> dict:
    started = time.monotonic()
    response = httpx.post(url, json=payload, timeout=timeout, trust_env=False)
    elapsed = round(time.monotonic() - started, 3)
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:200]}
    return {"status": response.status_code, "body": body, "elapsed_seconds": elapsed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--task-index", type=int, default=0)
    parser.add_argument("--db-env", default="http://127.0.0.1:6002")
    parser.add_argument("--user-sim", default="http://127.0.0.1:6001")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    db_env = args.db_env.rstrip("/")
    user_sim = args.user_sim.rstrip("/")
    if not args.data.exists():
        raise SystemExit(f"Calibrated input is missing: {args.data}")
    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    task_data = rows[args.task_index]
    task_id = task_data["instance_id"]

    simulator_health = httpx.get(f"{user_sim}/health", timeout=5, trust_env=False).json()
    calls_before = simulator_health.get("model_calls")
    payload = {"task_id": task_id, "task_data": {**task_data, "_interact_mode": "a-interact"}}
    code_changes = tracked_changes(ROOT)
    vendor_changes = tracked_changes(VENDOR)

    record = {
        "verified_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "mode": "tool-connection-check",
        "code_sha": revision(ROOT),
        "code_dirty": bool(code_changes),
        "code_uncommitted_paths": code_changes,
        "vendor_sha": revision(VENDOR),
        "vendor_dirty": bool(vendor_changes),
        "vendor_uncommitted_paths": vendor_changes,
        "data_file": args.data.name,
        "data_sha256": digest(args.data),
        "task_index": args.task_index,
        "task_id": task_id,
        "run_mode": "calibrated" if "calibrated" in args.data.name else "default",
        "services": {
            "db_environment": httpx.get(f"{db_env}/health", timeout=5, trust_env=False).json(),
            "user_simulator": simulator_health,
        },
        "execute_statement": EXECUTE_STATEMENT,
        "question": QUESTION,
    }

    record["db_env_init"] = post(f"{db_env}/init_task", payload, INIT_TIMEOUT_SECONDS)
    record["user_sim_init"] = post(f"{user_sim}/init_task", payload, INIT_TIMEOUT_SECONDS)
    record["execute_sql"] = post(
        f"{db_env}/execute", {"task_id": task_id, "sql": EXECUTE_STATEMENT},
        EXECUTE_TIMEOUT_SECONDS,
    )
    record["ask_user"] = post(
        f"{user_sim}/ask", {"task_id": task_id, "question": QUESTION}, ASK_TIMEOUT_SECONDS
    )
    after = httpx.get(f"{user_sim}/health", timeout=5, trust_env=False).json()
    record["simulator_after"] = after
    record["simulator_calls_during_check"] = counter_delta(after, simulator_health, "model_calls")
    record["simulator_tokens_during_check"] = {
        key: counter_delta(after, simulator_health, key)
        for key in ("prompt_tokens", "completion_tokens")
    }
    record["cleanup"] = post(f"{db_env}/cleanup_task", {"task_id": task_id}, INIT_TIMEOUT_SECONDS)

    execute_body = record["execute_sql"]["body"]
    ask_body = record["ask_user"]["body"]
    record["checks"] = {
        "execute_sql_http_ok": record["execute_sql"]["status"] == 200,
        "execute_sql_reported_success": bool(execute_body.get("success"))
        if isinstance(execute_body, dict) else False,
        "ask_user_http_ok": record["ask_user"]["status"] == 200,
        "ask_user_answer_present": bool(str(ask_body.get("answer", "")).strip())
        if isinstance(ask_body, dict) else False,
        "simulator_model_calls_at_least_two": isinstance(record["simulator_calls_during_check"], int)
        and record["simulator_calls_during_check"] >= 2,
    }
    record["scope"] = (
        "Direct connection check of /execute and /ask only; no agent run, no model "
        "tool choice and no submission were involved."
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "task_id": task_id,
        "checks": record["checks"],
        "model_calls_during_check": record["simulator_calls_during_check"],
        "execute_elapsed_seconds": record["execute_sql"]["elapsed_seconds"],
        "ask_elapsed_seconds": record["ask_user"]["elapsed_seconds"],
        "evidence": str(args.output),
    }, ensure_ascii=False))
    return 0 if all(record["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
