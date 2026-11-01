#!/usr/bin/env python
"""next_bar_forecast 离线评估报告（US-012 C3）。

从分析记录库取历史记录，结合 klines.db 的真实后续 K 线，输出预测方向命中率
与动量基线对照。命中率未达阈值（--threshold，默认 0.52）且样本不足时，
前端预测字段保持不展示（MVP 已按 N5 处理）。

Usage:
    python scripts/eval_next_bar.py --symbol BTC/USDT --interval 1h
    python scripts/eval_next_bar.py --symbol BTCUSDT --limit 200
    python scripts/eval_next_bar.py --db ~/.selenespace/data/selenespace.db --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

from selene_space_market_analysis.data_source import KlineSource  # noqa: E402
from selene_space_market_analysis.eval_next_bar import evaluate_next_bar  # noqa: E402
from selene_space_market_analysis.persistence_sqlite import SqliteRecordPersistence  # noqa: E402
from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.sqlite import AsyncSQLiteManager, DEFAULT_DB_PATH  # noqa: E402

_DEFAULT_THRESHOLD = 0.52
_DEFAULT_MIN_SAMPLES = 30


def _subsequent_closes(
    records: list[dict],
    df,
    *,
    symbol: str,
    interval: str,
) -> dict[str, float]:
    """record_id → 记录最后一根 K 线之后那根的实际收盘价（无后续则缺省）。"""
    closes_by_ts: dict[int, float] = {}
    for _, row in df.iterrows():
        closes_by_ts[int(row["timestamp"])] = float(row["close"])
    if not closes_by_ts:
        return {}
    later_ts = sorted(closes_by_ts)

    out: dict[str, float] = {}
    for rec in records:
        klines = (rec.get("input_data") or {}).get("klines") or []
        last_ts = max((int(k["timestamp"]) for k in klines), default=None)
        if last_ts is None:
            continue
        for ts in later_ts:
            if ts > last_ts:
                out[rec["meta"]["record_id"]] = closes_by_ts[ts]
                break
    return out


def _write_report(report: dict, out_path: Path) -> None:
    """把 JSON 报告落盘（原子写：先写临时文件再替换）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    print(f"  报告已落盘: {out_path}")


def _print_report(report: dict, *, threshold: float, min_samples: int) -> None:
    fr = report["hit_rate_forecast"]
    samples = report["samples_with_forecast"]
    eligible = fr is not None and samples >= min_samples and fr >= threshold

    print("next_bar 离线评估报告")
    print(f"  样本（有后续 K 线）       : {report['samples_total']}")
    print(f"  含预测方向样本           : {samples}")
    print(
        f"  预测命中率               : {fr:.1%}"
        if fr is not None
        else "  预测命中率               : N/A（无样本）"
    )
    br = report["hit_rate_baseline"]
    print(
        f"  动量基线命中率           : {br:.1%}"
        if br is not None
        else "  动量基线命中率           : N/A"
    )
    print(
        f"  开放预测字段前端展示     : {'是' if eligible else '否（未达阈值/样本不足）'}"
    )
    print(f"  （阈值 {threshold:.0%}，最小样本 {min_samples}）")


async def _main(args: argparse.Namespace) -> None:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(args.db, migration_runner=runner) as db:
        persistence = SqliteRecordPersistence(db)
        records = await persistence.list_recent(symbol=args.symbol, limit=args.limit)

    source = KlineSource(args.klines_db)
    df = source.load(
        args.exchange,
        args.market_type,
        args.symbol,
        args.interval,
        limit=args.kline_limit,
    )
    subsequent = _subsequent_closes(
        records, df, symbol=args.symbol, interval=args.interval
    )
    report = evaluate_next_bar(
        records,
        subsequent,
        window=args.window,
        tolerance=args.tolerance,
    )

    if args.out:
        _write_report(report, Path(args.out))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report, threshold=args.threshold, min_samples=args.min_samples)
        for row in report["rows"]:
            f = row["forecast"]
            print(
                f"  {row['record_id']}: forecast={f or '-'} baseline={row['baseline']} "
                f"actual={row['actual']} "
                f"{'✓' if row['forecast_hit'] else '✗' if f else '-'}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="next_bar 离线评估报告")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="分析记录库（analysis_records 表）"
    )
    # Default to the replica-root klines.db (the synced one, per activate.bat's
    # SELENE_KLINES_DB pin). KlineSource's own default points at
    # services/local-api/data/klines.db, which is a 16KB empty stub here —
    # against it every record joins zero later bars and the report reads 0
    # samples forever (silent-zero class, found 2026-09-18).
    parser.add_argument(
        "--klines-db", default="data/klines.db", help="K 线数据源（含后续 K 线）"
    )
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--market-type", default="futures")
    parser.add_argument("--limit", type=int, default=100, help="最多评估的记录数")
    parser.add_argument("--kline-limit", type=int, default=2000, help="K 线源加载条数")
    parser.add_argument("--window", type=int, default=3, help="动量基线窗口")
    parser.add_argument("--tolerance", type=float, default=0.0, help="flat 判定容差")
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_THRESHOLD,
        help="开放预测字段的命中率阈值",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=_DEFAULT_MIN_SAMPLES,
        help="开放预测字段的最小样本数",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument(
        "--out",
        default=None,
        help="把 JSON 报告写入该路径（如 data/tuning_results/next_bar_eval.json）",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
