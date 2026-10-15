#!/usr/bin/env python
"""失败样本分析（US-203）：exception 类型分布 top-N。

读取分析记录库中带顶层 ``exception`` 字段的失败记录（真实 LLM 失败入库，
见 AnalysisRecordRepository.list_failures），统计 exception.type 分布并输出
top-N，作为 LLM 调优输入。

Usage:
    python scripts/analyze_failures.py
    python scripts/analyze_failures.py --limit 500 --top 5
    python scripts/analyze_failures.py --db ~/.selenespace/data/selenespace.db --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# Ensure packages are importable when run from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for pkg in (
    "packages/market-analysis",
    "packages/storage",
    "packages/strategy-engine",
):
    path = str(_REPO_ROOT / pkg)
    if path not in sys.path:
        sys.path.insert(0, path)

from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.repositories.analysis_records import AnalysisRecordRepository  # noqa: E402
from selene_space_storage.sqlite import AsyncSQLiteManager, DEFAULT_DB_PATH  # noqa: E402


def _distribute(failures: list[dict]) -> list[dict]:
    """统计 exception.type 分布，按出现次数倒序。

    每个失败样本取 ``exception_type``（list_failures 已用 json_extract 抽出）；
    个别行可能因 schema 差异无 type，归入 ``(missing)``。
    """
    counts: Counter[str] = Counter(
        (item.get("exception_type") or "(missing)") for item in failures
    )
    total = sum(counts.values()) or 1
    rows = [
        {
            "exception_type": exc_type,
            "count": n,
            "share": n / total,
        }
        for exc_type, n in counts.most_common()
    ]
    return rows


def _print_report(rows: list[dict], *, total: int, top: int) -> None:
    print("失败样本分析（exception 类型分布）")
    print(f"  失败样本总数             : {total}")
    for i, row in enumerate(rows[:top], start=1):
        print(
            f"  {i}. {row['exception_type']}: {row['count']} "
            f"({row['share']:.1%})"
        )
    if len(rows) > top:
        print(f"  …（另有 {len(rows) - top} 种类型未展开）")


async def _main(args: argparse.Namespace) -> None:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(args.db, migration_runner=runner) as db:
        repo = AnalysisRecordRepository(db)
        failures = await repo.list_failures(limit=args.limit, offset=args.offset)

    rows = _distribute(failures)
    if args.json:
        print(
            json.dumps(
                {"total": len(failures), "types": rows[: args.top]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_report(rows, total=len(failures), top=args.top)


def main() -> None:
    parser = argparse.ArgumentParser(description="失败样本 exception 类型分布 top-N")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="分析记录库（analysis_records 表）")
    parser.add_argument("--limit", type=int, default=500, help="最多拉取的失败样本数")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 条")
    parser.add_argument("--top", type=int, default=10, help="输出 top-N 类型")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
