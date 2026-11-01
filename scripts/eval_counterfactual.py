#!/usr/bin/env python
"""Counterfactual replay: score next_period.direction against realised bars.

For every persisted decision-workflow record that carries
``stage1.diagnosis_or_decision.next_period.direction`` — including the
gate-blocked majority the outcome loop can never settle — pull the bars that
followed the record's window from klines.db (via the B1 forward read,
``load_bars(after_ms=window_end)``) and score the judgment against what the
market actually did (B2 scorer, ``eval_counterfactual``).

This is the instrument that makes a blocked decision falsifiable: the report
says what Stage 1 predicted, what happened, and whether it hit. Population
zero is a legitimate output (nothing scorable yet), printed as such — the
exit-code diagnostic below fires only when records exist but no forward bars
at all, which means klines.db is stale relative to the record corpus.

Exit status
-----------
``0``   report written (including population-zero).
``3``   diagnostic: population records exist but none has any forward bar —
        sync klines.db before trusting any "unsettled" column.

Usage:
    python scripts/eval_counterfactual.py
    python scripts/eval_counterfactual.py --horizon 24 --out data/tuning_results/
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

from selene_space_decision_workflow.eval_counterfactual import (  # noqa: E402
    score_record,
    summarize,
)
from selene_space_decision_workflow.trading_pipeline import load_bars  # noqa: E402
from selene_space_storage.migrations import MigrationRunner  # noqa: E402
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS  # noqa: E402
from selene_space_storage.repositories.analysis_records import (  # noqa: E402
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import (  # noqa: E402
    DEFAULT_DB_PATH,
    AsyncSQLiteManager,
)

_EXIT_STALE_KLINES = 3


def _print_summary(report: dict, out_path: Path) -> None:
    s = report["summary"]
    print("反事实回放报告(next_period.direction × 真实后续 bar)")
    print(f"  靶群(带 next_period)     : {s['population']}")
    print(f"  已结算(前向 bar 足够)    : {s['settled']}")
    print(f"  未结算(bar 未攒够)       : {s['unsettled']}")
    print(
        f"  方向样本 / 中性样本       : {s['directional_settled']} / {s['neutral_settled']}"
    )
    if s["hit_rate"] is not None:
        print(f"  方向命中率                : {s['hit_rate']:.1%}")
        mp = s["mean_pnl_pct_directional"]
        if mp is not None:
            print(f"  方向样本平均收益          : {mp:+.2%}")
    else:
        print("  方向命中率                : N/A(方向样本不足)")
    print(f"  报告已写入                : {out_path}")


def _row(record_id: str, rec: dict, verdict: dict | None, horizon: int) -> dict:
    row: dict = {"record_id": record_id}
    if verdict is None:
        row["in_population"] = False
        return row
    row["in_population"] = True
    row.update(verdict)
    return row


async def _main(args: argparse.Namespace) -> int:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(args.db, migration_runner=runner) as db:
        repo = AnalysisRecordRepository(db)
        records = await repo.list_records(limit=None)

    rows: list[dict] = []
    population = 0
    any_forward_bars = False
    for rec in records:
        rid = (rec.get("meta") or {}).get("record_id") or "?"
        verdict = score_record(rec, [], horizon=args.horizon)
        if verdict is None:
            rows.append(_row(rid, rec, None, args.horizon))
            continue
        population += 1
        # Forward read: bars starting at the record's window end (B1). The
        # anchor bar itself closes the decision window, so strictly-after bars
        # begin at the next timestamp; horizon counting starts from bar 1.
        idi = rec.get("input_data") or {}
        summary = idi.get("summary") or {}
        anchor_ms = summary.get("window_end_iso")
        symbol = idi.get("symbol")
        interval = summary.get("interval") or idi.get("interval")
        forward: list[dict] = []
        if isinstance(anchor_ms, (int, float)) and symbol:
            forward = load_bars(
                Path(args.klines_db),
                symbol,
                interval or "1h",
                args.horizon,
                after_ms=int(anchor_ms) + 1,
                min_bars=0,
            )
        if forward:
            any_forward_bars = True
        settled = score_record(rec, forward, horizon=args.horizon)
        rows.append(_row(rid, rec, settled, args.horizon))

    verdicts = [r for r in rows if r.get("in_population")]
    report = {
        "generated_by": "eval_counterfactual",
        "horizon": args.horizon,
        "klines_db": args.klines_db,
        "summary": summarize(verdicts),
        "rows": rows,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"eval_counterfactual-{stamp}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _print_summary(report, out_path)

    if population > 0 and not any_forward_bars:
        print(
            f"  [诊断] {population} 条记录带 next_period,但 klines.db "
            f"({args.klines_db}) 里没有任何后续 bar —— 先 sync_klines 再读报告, "
            f"否则 unsettled 列全是数据缺失不是市场未走完。",
            file=sys.stderr,
        )
        return _EXIT_STALE_KLINES
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="反事实回放:next_period 判定 × 真实后续 bar"
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), help="分析记录库(analysis_records 表)"
    )
    parser.add_argument(
        "--klines-db", default="data/klines.db", help="K 线数据源(副本根真库)"
    )
    parser.add_argument(
        "--horizon", type=int, default=24, help="结算所需前向 bar 数(默认 24)"
    )
    parser.add_argument(
        "--out", default="data/tuning_results/", help="报告输出目录(不存在则创建)"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
