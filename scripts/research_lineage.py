"""Research lineage writers — findings ledger + generation boundary (selene-native).

Discipline:
- findings.jsonl is an append-only ledger. A finding row is never erased.
  Re-appending a row with an existing artifact_id performs idempotent
  materialization: non-null fields from the new row refresh the stored row,
  but previously stored non-null fields are never wiped.
- generation_boundary.json is the atomic commit point of a generation:
  written once via tmp+replace when the generation closes normally
  (late_results=false). A crashed generation writes no boundary.
- Every row / boundary is validated against research_artifacts schema v1
  before hitting disk. Invalid rows abort loudly instead of corrupting data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from research_artifacts import (  # noqa: E402
    validate_finding_row,
)

DEFAULT_FINDINGS_PATH = (
    PROJECT_ROOT / "data" / "research" / "findings" / "findings.jsonl"
)
DEFAULT_COST_LEDGER_PATH = PROJECT_ROOT / "data" / "research" / "cost_ledger.jsonl"

BOUNDARY_REQUIRED_KEYS = (
    "generation",
    "committed_at",
    "protocol_hash",
    "run_id",
    "late_results",
    "result_count",
)


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def append_finding(
    row: dict[str, Any],
    path: Path = DEFAULT_FINDINGS_PATH,
) -> str:
    """Validate + append one finding row (idempotent on artifact_id).

    Returns the artifact_id. Raises ValueError on schema violation.
    """
    errors = validate_finding_row(row)
    if errors:
        raise ValueError(f"finding row invalid: {errors}")

    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    existing.append(json.loads(line))

    aid = row["artifact_id"]
    merged = False
    for i, old in enumerate(existing):
        if old.get("artifact_id") == aid:
            refreshed = dict(old)
            for key, val in row.items():
                # idempotent materialization: refresh, never erase non-null
                if val is not None:
                    refreshed[key] = val
                elif key not in refreshed:
                    refreshed[key] = val
            existing[i] = refreshed
            merged = True
            break
    if not merged:
        existing.append(row)

    _atomic_write_jsonl(path, existing)
    return str(aid)


def write_generation_boundary(gen_dir: Path, payload: dict[str, Any]) -> Path:
    """Atomically write generation_boundary.json (the generation commit point)."""
    missing = [k for k in BOUNDARY_REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(f"boundary missing required keys: {missing}")
    gen_dir.mkdir(parents=True, exist_ok=True)
    out = gen_dir / "generation_boundary.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    return out


def append_cost_ledger(
    entry: dict[str, Any],
    path: Path = DEFAULT_COST_LEDGER_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
