"""Fix 30 development tasks in the original category proportions, using public metadata."""

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "vendor/BIRD-Interact/BIRD-Interact-ADK/bird-interact-lite"
MANIFEST = ROOT / "eval_sets/lite30.manifest.json"
OUTPUT = ROOT / "private/lite30.combined.jsonl"
SIZE = 30
SEED = 20261014


def allocate(counts: dict[str, int], size: int) -> dict[str, int]:
    """Largest remainders; equal remainders prefer the larger source category."""
    total = sum(counts.values())
    if not 0 < size <= total:
        raise ValueError("Subset size must be between 1 and the population size")
    quotas = {key: size * count // total for key, count in counts.items()}
    order = sorted(counts, key=lambda key: (-(size * counts[key] % total), -counts[key], key))
    for key in order[:size - sum(quotas.values())]:
        quotas[key] += 1
    return quotas


def select(public_rows: list[dict], size: int, seed: int) -> list[str]:
    ids = [row["instance_id"] for row in public_rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Public dataset has duplicate task IDs")
    groups = defaultdict(list)
    for row in public_rows:
        groups[row["category"]].append(row["instance_id"])
    quotas = allocate({key: len(values) for key, values in groups.items()}, size)
    rng = random.Random(seed)
    selected = []
    for category in sorted(groups):
        selected.extend(rng.sample(sorted(groups[category]), quotas[category]))
    rng.shuffle(selected)
    return selected


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def canonical_digest(rows: list[dict]) -> str:
    """Identify the same JSON data regardless of Windows/Linux line endings."""
    content = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def main() -> None:
    public_path = DATA / "bird_interact_data.jsonl"
    public_rows = read_rows(public_path)
    source_hash = hashlib.sha256(public_path.read_bytes()).hexdigest()
    canonical_hash = canonical_digest(public_rows)
    public_by_id = {row["instance_id"]: row for row in public_rows}
    selected_ids = select(public_rows, SIZE, SEED)
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        source_matches = (manifest.get("source_public_canonical_sha256") == canonical_hash
                          if "source_public_canonical_sha256" in manifest
                          else manifest["source_public_sha256"] == source_hash)
        if (not source_matches
                or manifest["selected_ids"] != selected_ids):
            raise ValueError("The fixed selection or public input changed; preserve the existing manifest and create a new set explicitly")
    else:
        selected_public = [public_by_id[task_id] for task_id in selected_ids]
        manifest = {
            "name": "lite30", "purpose": "temporary development evaluation",
            "algorithm": "category-proportional-largest-remainder-v1",
            "category_field": "category (Phase 1)", "size": SIZE, "seed": SEED,
            "source_public_sha256": source_hash,
            "source_category_counts": dict(Counter(row["category"] for row in public_rows)),
            "selected_category_counts": dict(Counter(row["category"] for row in selected_public)),
            "selected_database_counts": dict(Counter(row["selected_database"] for row in selected_public)),
            "selected_difficulty_counts": dict(Counter(row["difficulty_tier"] for row in selected_public)),
            "aggregation": "equal task weights; official phase rewards remain 0.7/0.3",
            "selected_ids": selected_ids,
            "selected_tasks": [{key: row.get(key) for key in
                                ("instance_id", "selected_database", "category", "difficulty_tier")}
                               for row in selected_public],
        }

    combined = read_rows(DATA / "bird_interact_data.combined.jsonl")
    combined_by_id = {row["instance_id"]: row for row in combined}
    if len(combined_by_id) != len(combined):
        raise ValueError("Combined dataset has duplicate task IDs")
    chosen = [combined_by_id[task_id] for task_id in selected_ids]
    for row in chosen:
        if not row.get("sol_sql") or row["category"] != public_by_id[row["instance_id"]]["category"]:
            raise ValueError(f"Missing ground truth or mismatched category for {row['instance_id']}")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    if not MANIFEST.exists() or "source_public_canonical_sha256" not in manifest:
        manifest["source_public_canonical_sha256"] = canonical_hash
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chosen), encoding="utf-8")
    print(json.dumps({"size": SIZE, "seed": SEED,
                      "categories": manifest["selected_category_counts"],
                      "databases": len(manifest["selected_database_counts"]),
                      "manifest": str(MANIFEST), "evaluation_data": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
