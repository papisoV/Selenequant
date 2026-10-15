#!/usr/bin/env python3
"""Filter tuning results to top 3-5 strategies by Sharpe/return/drawdown.

Usage:
    python scripts/filter_strategies.py                     # 最新一次 tuning
    python scripts/filter_strategies.py --file path.json   # 指定文件
    python scripts/filter_strategies.py --top 5             # 留前 N
    python scripts/filter_strategies.py --min-sharpe 1.0 --min-return 0.0 --max-drawdown 0.30

Output: markdown report to stdout + data/tuning_results/filter_report_<timestamp>.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent

CRITERIA_DEFAULT = dict(min_sharpe=1.0, min_return=0.0, max_drawdown=0.30)


def find_latest_tuning(results_dir: Path) -> Path | None:
    files = sorted(results_dir.glob("tuning_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def extract_metric(result: dict[str, Any], key: str) -> float | None:
    br = result.get("backtest_result")
    if not isinstance(br, dict):
        return None
    val = br.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "N/A"
    return f"{x * 100:+.2f}%" if abs(x) < 10 else f"{x:+.2f}"


def fmt_float(x: float | None, prec: int = 4) -> str:
    if x is None:
        return "N/A"
    return f"{x:.{prec}f}"


def filter_results(
    results: list[dict[str, Any]],
    min_sharpe: float,
    min_return: float,
    max_drawdown: float,
) -> list[dict[str, Any]]:
    passed: list[dict[str, Any]] = []
    for r in results:
        sharpe = r.get("best_sharpe")
        total_return = extract_metric(r, "total_return")
        max_dd = extract_metric(r, "max_drawdown")

        if sharpe is None or total_return is None or max_dd is None:
            continue
        # max_drawdown is typically negative; use abs
        dd_abs = abs(max_dd)
        if sharpe < min_sharpe:
            continue
        if total_return < min_return:
            continue
        if dd_abs > max_drawdown:
            continue
        r["_total_return"] = total_return
        r["_max_drawdown_abs"] = dd_abs
        r["_profit_factor"] = extract_metric(r, "profit_factor")
        r["_win_rate"] = extract_metric(r, "win_rate")
        r["_total_trades"] = extract_metric(r, "total_trades")
        passed.append(r)
    passed.sort(key=lambda x: x["best_sharpe"], reverse=True)
    return passed


def render_report(
    passed: list[dict[str, Any]],
    total_count: int,
    criteria: dict[str, float],
    source_file: str,
    top_n: int,
) -> str:
    kept = passed[:top_n]
    lines: list[str] = []
    lines.append("# 策略筛选报告")
    lines.append("")
    lines.append(f"**数据源**: `{source_file}`")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**总调参记录**: {total_count}")
    lines.append(
        f"**筛选标准**: Sharpe ≥ {criteria['min_sharpe']} & 总收益 ≥ {fmt_pct(criteria['min_return'])} "
        f"& 最大回撤 ≤ {fmt_pct(criteria['max_drawdown'])}"
    )
    lines.append(f"**通过筛选**: {len(passed)} 个")
    lines.append(f"**最终保留**: {len(kept)} 个(top {top_n})")
    lines.append("")

    if not kept:
        lines.append("⚠️ 没有策略通过筛选标准。建议:")
        lines.append("- 降低 Sharpe 阈值(如 0.5)")
        lines.append("- 放宽回撤(如 0.5)")
        lines.append("- 增加 trials(如 50-100)")
        lines.append("- 换周期或交易对")
        return "\n".join(lines)

    lines.append("## Top 策略")
    lines.append("")
    lines.append(
        "| 排名 | 策略 | 交易对 | 周期 | Sharpe | 总收益 | 最大回撤 | 胜率 | 盈亏比 | 交易数 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(kept, 1):
        lines.append(
            f"| {i} | {r['strategy_name']} | {r.get('symbol', '?')} | {r['interval']} | "
            f"{fmt_float(r['best_sharpe'])} | {fmt_pct(r['_total_return'])} | "
            f"{fmt_pct(r['_max_drawdown_abs'])} | {fmt_pct(r['_win_rate'])} | "
            f"{fmt_float(r['_profit_factor'], 2)} | {int(r['_total_trades']) if r['_total_trades'] else 'N/A'} |"
        )

    lines.append("")
    lines.append("## 最优参数详情")
    lines.append("")
    for i, r in enumerate(kept, 1):
        lines.append(f"### {i}. {r['strategy_name']} ({r.get('symbol', '?')} {r['interval']})")
        lines.append(f"- **Sharpe**: {fmt_float(r['best_sharpe'])}")
        lines.append(f"- **总收益**: {fmt_pct(r['_total_return'])}")
        lines.append(f"- **最大回撤**: {fmt_pct(r['_max_drawdown_abs'])}")
        lines.append(f"- **胜率**: {fmt_pct(r['_win_rate'])}")
        lines.append(f"- **盈亏比**: {fmt_float(r['_profit_factor'], 2)}")
        lines.append(f"- **迭代次数**: {r.get('n_trials', 'N/A')}")
        lines.append("- **最优参数**:")
        for k, v in r.get("best_params", {}).items():
            lines.append(f"  - `{k}`: {v}")
        lines.append("")

    lines.append("## 全部通过筛选的策略(完整列表)")
    lines.append("")
    if len(passed) > len(kept):
        lines.append(f"> 共 {len(passed)} 个通过,以下为前 {top_n} 之外的")
        lines.append("")
        for r in passed[top_n:]:
            lines.append(
                f"- {r['strategy_name']} ({r.get('symbol', '?')} {r['interval']}): "
                f"Sharpe={fmt_float(r['best_sharpe'])}, 收益={fmt_pct(r['_total_return'])}, "
                f"回撤={fmt_pct(r['_max_drawdown_abs'])}"
            )
    else:
        lines.append("> 所有通过筛选的策略已在上表")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter tuning results to top strategies")
    parser.add_argument("--file", type=Path, help="Specific tuning JSON file")
    parser.add_argument("--dir", type=Path, default=PROJECT_ROOT / "data" / "tuning_results",
                        help="Directory to search for tuning files")
    parser.add_argument("--top", type=int, default=5, help="Keep top N strategies")
    parser.add_argument("--min-sharpe", type=float, default=CRITERIA_DEFAULT["min_sharpe"])
    parser.add_argument("--min-return", type=float, default=CRITERIA_DEFAULT["min_return"])
    parser.add_argument("--max-drawdown", type=float, default=CRITERIA_DEFAULT["max_drawdown"])
    parser.add_argument("--output", type=Path, default=None,
                        help="Output markdown file (default: data/tuning_results/filter_report_<ts>.md)")
    args = parser.parse_args()

    source = args.file if args.file else find_latest_tuning(args.dir)
    if not source or not source.exists():
        print(f"Error: no tuning file found in {args.dir}", file=sys.stderr)
        sys.exit(1)

    with open(source, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = data.get("results", [])
    criteria = dict(min_sharpe=args.min_sharpe, min_return=args.min_return,
                    max_drawdown=args.max_drawdown)
    passed = filter_results(results, **criteria)

    report = render_report(passed, len(results), criteria, str(source), args.top)
    print(report)

    out_file = args.output or (args.dir / f"filter_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n---\n报告已保存: {out_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
