"""Research artifact schema v1 — evidence roles, finding blocks, lineage blocks.

selene-native naming (no PRAXIST terms). Schema discipline:
- RESEARCH_ARTIFACT_SCHEMA_VERSION = 1; every break change bumps the version
  and gets a migration handler in scripts/migrate_research_schema.py.
- Additive-only evolution within a version: never delete or repurpose fields.
- data/tuning_results/*.json and data/research/findings/findings.jsonl both
  validate against this module.

Source annotation policy (replaces raw absolute db_path):
- ``source.db`` is a replica-relative path (e.g. services/local-api/data/klines.db).
- ``source.snapshot`` freezes {row_count, max_timestamp, data_dir} computed from
  the producing database at write/migration time, so results stay interpretable
  even when the original replica is gone or the db has since grown.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

RESEARCH_ARTIFACT_SCHEMA_VERSION = 1


class Role(str, Enum):
    CANONICAL = "canonical"
    VALIDATION_SIGNAL = "validation_signal"
    DERIVED_VIEW = "derived_view"
    AUDIT_SNAPSHOT = "audit_snapshot"
    PARTIAL_OUTPUT = "partial_output"


class FindingStatus(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class FindingBlock:
    status: str = FindingStatus.INCONCLUSIVE.value
    why: str | None = None
    negative_reason: str | None = None
    regime_condition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "why": self.why,
            "negative_reason": self.negative_reason,
            "regime_condition": self.regime_condition,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> FindingBlock:
        raw = raw or {}
        return cls(
            status=raw.get("status", FindingStatus.INCONCLUSIVE.value),
            why=raw.get("why"),
            negative_reason=raw.get("negative_reason"),
            regime_condition=raw.get("regime_condition"),
        )


@dataclass(frozen=True)
class LineageBlock:
    parents: list[str] = field(default_factory=list)
    protocol_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"parents": list(self.parents), "protocol_hash": self.protocol_hash}

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> LineageBlock:
        raw = raw or {}
        return cls(
            parents=list(raw.get("parents", [])),
            protocol_hash=raw.get("protocol_hash"),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _relative_db_path(db_path: Path) -> tuple[str, str | None]:
    """Split a db path into (replica-relative path, replica hint).

    Locates the ``services`` segment and treats its parent as the replica root,
    so an absolute path from another replica like
    ``<local-path-redacted>``
    becomes ("services/local-api/data/klines.db", "<other-replica>"). Absolute
    paths are never stored — replica copies must stay portable.
    """
    pure = PureWindowsPath(db_path) if "\\" in str(db_path) else PurePosixPath(db_path)
    parts = pure.parts
    if "services" in parts:
        idx = parts.index("services")
        rel = "/".join(parts[idx:])
        hint = parts[idx - 1] if idx > 0 else None
        return rel, hint
    return pure.as_posix(), None


def build_source_annotation(
    db_path: Path | str,
    combos: list[tuple[str, str, str, str]] | None = None,
) -> dict[str, Any]:
    """Build the ``source`` block: replica-relative db + data snapshot.

    combos are (exchange, market_type, symbol, interval) tuples the artifact
    was computed over; the snapshot aggregates row counts / max timestamp for
    exactly those slices. If the db is gone, snapshot fields are null — the
    annotation still records where the data lived.
    """
    db_path = Path(db_path)
    rel, replica_hint = _relative_db_path(db_path)
    snapshot: dict[str, Any] = {
        "row_count": None,
        "max_timestamp": None,
        "data_dir": None,
        "computed_at": None,
    }
    if db_path.is_file():
        data_dir_rel, _ = _relative_db_path(db_path.parent)
        snapshot["data_dir"] = data_dir_rel
        snapshot["computed_at"] = utc_now_iso()
        try:
            con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                cur = con.cursor()
                if combos:
                    total, max_ts = 0, 0
                    for exchange, market_type, symbol, interval in combos:
                        row = cur.execute(
                            "SELECT COUNT(*), COALESCE(MAX(timestamp), 0) FROM klines "
                            "WHERE exchange=? AND market_type=? AND symbol=? AND interval=?",
                            (exchange, market_type, symbol, interval),
                        ).fetchone()
                        total += row[0]
                        max_ts = max(max_ts, row[1])
                    snapshot["row_count"] = total
                    snapshot["max_timestamp"] = max_ts or None
            finally:
                con.close()
        except sqlite3.Error:
            pass
    return {
        "db": rel,
        "replica_hint": replica_hint,
        "snapshot": snapshot,
    }


def validate_tuning_artifact(doc: dict[str, Any]) -> list[str]:
    """Validate a data/tuning_results JSON document against schema v1."""
    errors: list[str] = []
    if doc.get("schema_version") != RESEARCH_ARTIFACT_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {RESEARCH_ARTIFACT_SCHEMA_VERSION}, "
            f"got {doc.get('schema_version')!r}"
        )
    role = doc.get("role")
    if role not in {r.value for r in Role}:
        errors.append(f"role invalid: {role!r}")
    finding = doc.get("finding")
    if not isinstance(finding, dict):
        errors.append("finding must be a dict")
    else:
        if finding.get("status") not in {s.value for s in FindingStatus}:
            errors.append(f"finding.status invalid: {finding.get('status')!r}")
    lineage = doc.get("lineage")
    if not isinstance(lineage, dict):
        errors.append("lineage must be a dict")
    else:
        if not isinstance(lineage.get("parents"), list):
            errors.append("lineage.parents must be a list")
        ph = lineage.get("protocol_hash")
        if ph is not None and not isinstance(ph, str):
            errors.append("lineage.protocol_hash must be a string or null")
    source = doc.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("db"), str):
        errors.append("source.db must be a string (replica-relative path)")
    return errors


def validate_finding_row(row: dict[str, Any]) -> list[str]:
    """Validate one findings.jsonl line against schema v1."""
    errors: list[str] = []
    if row.get("schema_version") != RESEARCH_ARTIFACT_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {RESEARCH_ARTIFACT_SCHEMA_VERSION}, "
            f"got {row.get('schema_version')!r}"
        )
    if not row.get("artifact_id"):
        errors.append("artifact_id required")
    if row.get("role") not in {r.value for r in Role}:
        errors.append(f"role invalid: {row.get('role')!r}")
    if not row.get("created_at"):
        errors.append("created_at required")
    source = row.get("source")
    if not isinstance(source, dict):
        errors.append("source must be a dict")
    else:
        for key in ("generation", "script", "run_id"):
            if key not in source:
                errors.append(f"source.{key} required")
    lineage = row.get("lineage")
    if not isinstance(lineage, dict):
        errors.append("lineage must be a dict")
    else:
        if not isinstance(lineage.get("parents"), list):
            errors.append("lineage.parents must be a list")
        if not lineage.get("hypothesis"):
            errors.append("lineage.hypothesis required")
        if not lineage.get("changes"):
            errors.append("lineage.changes required (declarative change list)")
    finding = row.get("finding")
    if not isinstance(finding, dict):
        errors.append("finding must be a dict")
    else:
        if finding.get("status") not in {s.value for s in FindingStatus}:
            errors.append(f"finding.status invalid: {finding.get('status')!r}")
        if not finding.get("why"):
            errors.append("finding.why required (structured reason)")
    return errors
