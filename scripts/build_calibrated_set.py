"""Build the calibrated evaluation input from the fixed task set and private patches.

The original evaluation input and the prepared combined dataset stay untouched:
the calibrated file is written next to them under a different name, and the
printed summary records the hashes and the applied changes for the report.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark" / "calibration"))

import calibration_config


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(ROOT / "private/lite30.combined.jsonl"))
    parser.add_argument("--patches", default=str(ROOT / "private/calibration/TASK-002.calibration.json"))
    parser.add_argument("--output", default=str(ROOT / "private/lite30.calibrated.jsonl"))
    parser.add_argument("--manifest", default=str(ROOT / "private/lite30.calibrated.manifest.json"))
    args = parser.parse_args()

    source, patches, output = Path(args.source), Path(args.patches), Path(args.output)
    config = calibration_config.load_config(str(patches))
    rows = read_rows(source)
    patched, changes = calibration_config.apply_dataset_patches(rows, config.get("dataset_patches", {}))

    if len(patched) != len(rows) or [row["instance_id"] for row in patched] != [row["instance_id"] for row in rows]:
        raise SystemExit("Calibrated input changed the task list or task order")
    changed = [old["instance_id"] for old, new in zip(rows, patched) if old != new]
    undeclared = [task_id for task_id in changed if task_id not in config.get("dataset_patches", {})]
    if undeclared:
        raise SystemExit(f"Tasks changed without a declared patch: {undeclared}")

    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in patched), encoding="utf-8")
    manifest = {
        "task": config.get("task", ""),
        "source": str(source),
        "source_sha256": calibration_config.file_sha256(str(source)),
        "source_canonical_sha256": calibration_config.rows_canonical_sha256(rows),
        "patches": str(patches),
        "patches_sha256": calibration_config.file_sha256(str(patches)),
        "output": str(output),
        "output_sha256": calibration_config.file_sha256(str(output)),
        "output_canonical_sha256": calibration_config.rows_canonical_sha256(patched),
        "tasks": len(patched),
        "changed_tasks": changed,
        "changes": changes,
    }
    Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
