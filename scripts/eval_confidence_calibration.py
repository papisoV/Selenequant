#!/usr/bin/env python
"""Confidence × outcome emit-yield audit (Phase A of the PA C2 plan).

Reads decision-workflow analysis records from the local DB and reports how
many carry a Stage-2 confidence at the contract path, how many carry an
outcome, and where the literal key ``confidence`` lives in the corpus. See
``selene_space_decision_workflow.eval_confidence_calibration`` for the pure
reduction; this file is only the I/O shell.

Exit status
-----------
``0``   report written (including the all-zero / empty-corpus case).
``3``   diagnostic fired: the corpus carries the literal key ``confidence``
        somewhere but no record carries it at the contract path — that points
        at a wrong accessor or an unwritten channel, never at a healthy zero.

Usage:
    python scripts/eval_confidence_calibration.py
    python scripts/eval_confidence_calibration.py --db ~/.selenespace/data/selenespace.db
    python scripts/eval_confidence_calibration.py --out data/tuning_results/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure packages are importable when run from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in ("packages/decision-workflow", "packages/storage"):
    path = str(_REPO_ROOT / pkg)
    if path not in sys.path:
        sys.path.insert(0, path)

from selene_space_decision_workflow.eval_confidence_calibration import (  # noqa: E402
    build_calibration_report,
)
from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.repositories.analysis_records import (  # noqa: E402
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import (  # noqa: E402
    DEFAULT_DB_PATH,
    AsyncSQLiteManager,
)

_EXIT_DIAGNOSTIC = 3


def _print_summary(report: dict, out_path: Path) -> None:
    cov = report["coverage"]
    print("置信度 emit-yield 审计（Phase A）")
    print(
        f"  记录总数                 : {cov['total']}（decision-workflow 形状 {cov['total_dw']}）"
    )
    print(f"  非本物种（excluded_by_shape）: {cov['excluded_by_shape']}")
    print(f"  契约路径带 confidence    : {cov['with_confidence']}")
    print(f"  带 outcome               : {cov['with_outcome']}")
    print(f"  两者兼有                 : {cov['with_both']}")
    print(f"  confidence 键出现在任意层 : {cov['confidence_key_present_anywhere']}")
    if report["note"]:
        print(f"  注: {report['note']}")
    print(f"  报告已写入               : {out_path}")


async def _main(args: argparse.Namespace) -> int:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(args.db, migration_runner=runner) as db:
        repo = AnalysisRecordRepository(db)
        records = await repo.list_records(limit=None)

    report = build_calibration_report(records)
    cov = report["coverage"]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"eval_confidence_calibration-{stamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _print_summary(report, out_path)

    # The zero-pass this guards against: keys exist in the corpus but not at the
    # contract path. A healthy corpus with no confidence yet has key_present == 0.
    if (
        cov["with_stage2"] > 0
        and cov["with_confidence"] == 0
        and cov["confidence_key_present_anywhere"] > 0
    ):
        print(
            f"  [诊断] {cov['confidence_key_present_anywhere']} 条记录在任意层带 "
            f"'confidence' 键，但没有一条落在契约路径 "
            f"({report['contract_path']}) —— 检查访问器或通道是否真的写出过。",
            file=sys.stderr,
        )
        return _EXIT_DIAGNOSTIC
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="置信度 × 结果 emit-yield 审计（PA C2 Phase A）"
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="分析记录库（analysis_records 表）"
    )
    parser.add_argument(
        "--out", default="data/tuning_results/", help="报告输出目录（不存在则创建）"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
