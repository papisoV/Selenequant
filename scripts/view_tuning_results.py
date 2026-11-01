#!/usr/bin/env python3
"""查看调参回测结果。

用法:
    python scripts/view_tuning_results.py                    # 查看最新的结果
    python scripts/view_tuning_results.py results.json       # 查看指定文件
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Fix Windows console encoding
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def load_results(file_path: Path) -> dict:
    """加载调参结果JSON文件。"""
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


def format_pct(value: float) -> str:
    """格式化百分比。"""
    return f"{value * 100:.2f}%"


def print_summary(data: dict) -> None:
    """打印调参结果汇总。"""
    config = data.get("config", {})
    results = data.get("results", [])

    print("=" * 80)
    print("调参结果汇总")
    print("=" * 80)

    # 配置信息
    print("\n配置:")
    print(f"  周期: {config.get('intervals', [])}")
    print(f"  策略数: {len(config.get('strategies', []))}")
    print(f"  迭代次数: {config.get('n_trials', 'N/A')}")

    if not results:
        print("\n无结果数据!")
        return

    # 按Sharpe排序
    results_sorted = sorted(results, key=lambda x: x.get("best_sharpe", 0), reverse=True)

    # 表头
    print(f"\n{'排名':<4} {'策略':<20} {'周期':<6} {'Sharpe':>10} {'收益':>10} {'最大回撤':>10} {'胜率':>8} {'交易数':>6}")
    print("-" * 80)

    for rank, r in enumerate(results_sorted, 1):
        name = r.get("strategy_name", r.get("strategy_id", "unknown"))
        interval = r.get("interval", "?")
        sharpe = r.get("best_sharpe", 0)

        backtest = r.get("backtest_result", {})
        ret = format_pct(backtest.get("total_return", 0))
        dd = format_pct(abs(backtest.get("max_drawdown", 0)))
        win_rate = format_pct(backtest.get("win_rate", 0))
        trades = backtest.get("total_trades", 0)

        print(f"{rank:<4} {name:<20} {interval:<6} {sharpe:>10.4f} {ret:>10} {dd:>10} {win_rate:>8} {trades:>6}")

    # 详细参数展示
    print("\n" + "=" * 80)
    print("最佳策略详情")
    print("=" * 80)

    for r in results_sorted[:5]:  # 只展示前5个
        name = r.get("strategy_name", r.get("strategy_id", "unknown"))
        params = r.get("best_params", {})
        sharpe = r.get("best_sharpe", 0)

        print(f"\n[{name}] Sharpe: {sharpe:.4f}")
        print("  最佳参数:")
        for k, v in params.items():
            if isinstance(v, float):
                print(f"    {k}: {v:.4f}")
            else:
                print(f"    {k}: {v}")

        backtest = r.get("backtest_result", {})
        if backtest:
            print("  回测指标:")
            print(f"    总收益: {format_pct(backtest.get('total_return', 0))}")
            print(f"    年化收益: {format_pct(backtest.get('annual_return', 0))}")
            print(f"    最大回撤: {format_pct(abs(backtest.get('max_drawdown', 0)))}")
            print(f"    胜率: {format_pct(backtest.get('win_rate', 0))}")
            print(f"    盈亏比: {backtest.get('profit_factor', 0):.2f}")
            print(f"    交易次数: {backtest.get('total_trades', 0)}")
            print(f"    Calmar: {backtest.get('calmar_ratio', 0):.4f}")
            print(f"    Sortino: {backtest.get('sortino_ratio', 0):.4f}")


def main() -> None:
    args = sys.argv[1:]

    if args:
        file_path = Path(args[0])
    else:
        # 找最新的结果文件
        results_dir = PROJECT_ROOT / "data" / "tuning_results"
        if not results_dir.exists():
            print("无调参结果目录!")
            sys.exit(1)

        files = sorted(results_dir.glob("tuning_*.json"), reverse=True)
        if not files:
            print("无调参结果文件!")
            sys.exit(1)

        file_path = files[0]

    if not file_path.exists():
        print(f"文件不存在: {file_path}")
        sys.exit(1)

    print(f"查看文件: {file_path}")
    data = load_results(file_path)
    print_summary(data)


if __name__ == "__main__":
    main()
