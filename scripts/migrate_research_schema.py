"""Migrate legacy tuning JSONs to research artifact schema v1 (additive-only).

Behavior:
- --dry-run: print planned changes per file, write nothing.
- --apply: for each data/tuning_results/tuning_*.json lacking schema_version,
  add {schema_version, role, finding, lineage, source} fields; replace the
  legacy absolute ``config.db_path`` string with the replica-relative source
  annotation (config.db_path is dropped — its information now lives in
  source.db + source.snapshot, fixing the cross-replica broken link).
- Idempotent: files already carrying schema_version are left untouched.
- Every apply appends one line to data/research/migrations.jsonl.
- --rollback: restore .bak snapshots created before the first --apply.

Legacy ``config.db_path`` pointing at another replica (e.g. SeleneQuant) is a real
data problem: absolute paths break on replica copies and leak machine layout.
The fix records WHERE the data lived (replica-relative db + replica hint) and
WHAT the data looked like (row_count/max_timestamp snapshot), not a dead path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from research_artifacts import (  # noqa: E402
    RESEARCH_ARTIFACT_SCHEMA_VERSION,
    Role,
    FindingStatus,
    build_source_annotation,
    validate_tuning_artifact,
    utc_now_iso,
)

MIGRATIONS_LEDGER = PROJECT_ROOT / "data" / "research" / "migrations.jsonl"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_legacy_broken_path(doc: dict) -> bool:
    db = doc.get("config", {}).get("db_path", "")
    return isinstance(db, str) and (
        "\\" in db or db.startswith("/") or db.startswith("D:")
    )


def _combos_from_legacy_config(config: dict) -> list[tuple[str, str, str, str]]:
    """Rebuild (exchange, market_type, symbol, interval) combos from legacy config
    so the source snapshot can aggregate real row counts / max timestamps."""
    combos: list[tuple[str, str, str, str]] = []
    for ex in config.get("exchanges", ["binance"]):
        for mt in config.get("market_types", ["futures"]):
            for sym in config.get("symbols", []):
                for iv in config.get("intervals", []):
                    combos.append((ex, mt, sym, iv))
    return combos


def migrate_doc(doc: dict, json_path: Path) -> tuple[dict, list[str]]:
    """Return (new_doc, change_descriptions). Pure — no I/O."""
    changes: list[str] = []
    new = dict(doc)
    new["schema_version"] = RESEARCH_ARTIFACT_SCHEMA_VERSION
    changes.append(f"add schema_version={RESEARCH_ARTIFACT_SCHEMA_VERSION}")

    if "role" not in new:
        new["role"] = Role.CANONICAL.value
        changes.append(f"add role={Role.CANONICAL.value}")

    if "finding" not in new:
        new["finding"] = {
            "status": FindingStatus.INCONCLUSIVE.value,
            "why": None,
            "negative_reason": None,
            "regime_condition": None,
        }
        changes.append("add finding (inconclusive, no negative_reason)")

    if "lineage" not in new:
        new["lineage"] = {"parents": [], "protocol_hash": None}
        changes.append("add lineage (empty parents, protocol_hash null)")

    config = dict(new.get("config", {}))
    legacy_db = config.pop("db_path", None)
    if legacy_db is not None:
        combos = _combos_from_legacy_config(config)
        source = build_source_annotation(Path(legacy_db), combos)
        new["source"] = source
        changes.append(
            f"replace config.db_path ({legacy_db}) with source annotation "
            f"(db={source['db']}, replica_hint={source['replica_hint']}, "
            f"snapshot.computed={source['snapshot']['computed_at'] is not None})"
        )
    new["config"] = config
    return new, changes


def migrate_file(path: Path, apply: bool) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") == RESEARCH_ARTIFACT_SCHEMA_VERSION:
        return [f"{path.name}: already v{RESEARCH_ARTIFACT_SCHEMA_VERSION}, skip"]

    new_doc, changes = migrate_doc(doc, path)

    if apply:
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(new_doc, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)

    return [f"{path.name}: {c}" for c in changes]


def append_ledger(entry: dict) -> None:
    MIGRATIONS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(MIGRATIONS_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "tuning_results",
        help="Directory of tuning JSONs to migrate",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print changes, write nothing"
    )
    parser.add_argument("--apply", action="store_true", help="Apply migration in place")
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Restore all .bak snapshots and clear ledger",
    )
    args = parser.parse_args()

    if args.rollback:
        restored = 0
        for bak in sorted(args.dir.glob("*.json.bak")):
            original = bak.with_suffix("")
            shutil.copy2(bak, original)
            bak.unlink()
            restored += 1
            print(f"restored {original.name}")
        if restored and MIGRATIONS_LEDGER.exists():
            MIGRATIONS_LEDGER.unlink()
        print(f"rollback complete: {restored} file(s) restored")
        return 0

    if not args.dry_run and not args.apply:
        parser.error("choose one of --dry-run / --apply / --rollback")

    files = sorted(args.dir.glob("tuning_*.json"))
    if not files:
        print(f"no tuning_*.json in {args.dir}")
        return 0

    files_affected: list[str] = []
    all_changes: list[str] = []
    for path in files:
        lines = migrate_file(path, apply=args.apply)
        for line in lines:
            print(line)
        if args.apply and any("skip" not in line for line in lines):
            files_affected.append(path.name)
            all_changes.extend(lines)

    if args.apply and files_affected:
        checksums_before = {
            p.name: _sha256_file(p.with_suffix(p.suffix + ".bak")) for p in files
        }
        append_ledger(
            {
                "version": RESEARCH_ARTIFACT_SCHEMA_VERSION,
                "applied_at": utc_now_iso(),
                "files_affected": files_affected,
                "checksums_before": checksums_before,
            }
        )
        print(f"ledger appended: {MIGRATIONS_LEDGER}")

    # Post-condition check
    if args.apply:
        bad = []
        for path in files:
            errors = validate_tuning_artifact(
                json.loads(path.read_text(encoding="utf-8"))
            )
            if errors:
                bad.append(f"{path.name}: {errors}")
        if bad:
            print("VALIDATION ERRORS after migration:")
            for b in bad:
                print(f"  {b}")
            return 1
        print("post-migration validation: all files pass schema v1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
