"""Aggregate OKX backtest results and generate ranked leaderboard.

Usage::

    python scripts/okx_backtest_summary.py data/okx_backtest_results/okx_backtest_XXX.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_results(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("results", [])


def rank_by(results: list[dict], key: str, top_n: int = 20) -> list[dict]:
    """Sort results by a metric, filtering out NaN/Inf/zero-trade entries."""
    valid = [
        r for r in results
        if r.get(key, 0) not in (float("inf"), float("-inf"))
        and r.get(key, 0) == r.get(key, 0)  # not NaN
        and r.get("total_trades", 0) >= 3  # minimum trades
    ]
    valid.sort(key=lambda r: r.get(key, 0), reverse=True)
    return valid[:top_n]


def cross_symbol_summary(results: list[dict]) -> list[dict]:
    """Per-strategy cross-symbol stats."""
    from collections import defaultdict

    groups = defaultdict(list)
    for r in results:
        groups[(r["strategy"], r["interval"])].append(r)

    summary = []
    for (strat, intv), recs in groups.items():
        sharpes = [r["sharpe"] for r in recs if r["total_trades"] >= 3]
        wf_oos = [
            r["walk_forward"]["avg_oos_sharpe"]
            for r in recs
            if "walk_forward" in r and r["total_trades"] >= 3
        ]
        if not sharpes:
            continue

        summary.append(
            {
                "strategy": strat,
                "interval": intv,
                "n_symbols": len(recs),
                "n_profitable": sum(1 for r in recs if r.get("total_return", 0) > 0),
                "avg_sharpe": round(sum(sharpes) / len(sharpes), 4),
                "max_sharpe": round(max(sharpes), 4),
                "min_sharpe": round(min(sharpes), 4),
                "sharpe_std": round(
                    (sum((s - sum(sharpes) / len(sharpes)) ** 2 for s in sharpes) / len(sharpes)) ** 0.5, 4
                ),
                "avg_wf_oos": round(sum(wf_oos) / len(wf_oos), 4) if wf_oos else None,
                "best_symbol": max(recs, key=lambda r: r.get("sharpe", 0))["symbol"],
            }
        )

    summary.sort(key=lambda s: s["avg_sharpe"], reverse=True)
    return summary


def main() -> None:
    if len(sys.argv) < 2:
        # Find latest result file
        result_dir = Path("data/okx_backtest_results")
        files = sorted(result_dir.glob("okx_backtest_*.json"))
        if not files:
            sys.exit("No result files found. Run backtest_okx_all.py first.")
        path = str(files[-1])
    else:
        path = sys.argv[1]

    results = load_results(path)
    print(f"=== OKX Backtest Summary: {len(results)} runs ===\n")

    # Top by Sharpe
    print("## Top 20 by Sharpe Ratio")
    print(f"{'Rank':<5} {'Strategy':<22} {'Symbol':<12} {'Intv':<5} {'Sharpe':<8} {'Return':<9} {'MaxDD':<8} {'WR':<7} {'PF':<7} {'Trades':<6} {'WF_OOS':<8}")
    print("-" * 110)
    for i, r in enumerate(rank_by(results, "sharpe"), 1):
        wf = r.get("walk_forward", {})
        oos = wf.get("avg_oos_sharpe", 0) if wf else 0
        print(
            f"{i:<5} {r['strategy']:<22} {r['symbol']:<12} {r['interval']:<5} "
            f"{r['sharpe']:<8.2f} {r['total_return']:<9.1%} {r['max_drawdown']:<8.1%} "
            f"{r['win_rate']:<7.1%} {r['profit_factor']:<7.2f} {r['total_trades']:<6} "
            f"{oos:<8.2f}"
        )

    # Top by Calmar
    print("\n## Top 20 by Calmar Ratio (return / max drawdown)")
    print(f"{'Rank':<5} {'Strategy':<22} {'Symbol':<12} {'Intv':<5} {'Calmar':<8} {'Sharpe':<8} {'Return':<9} {'MaxDD':<8} {'Trades':<6}")
    print("-" * 100)
    for i, r in enumerate(rank_by(results, "calmar"), 1):
        print(
            f"{i:<5} {r['strategy']:<22} {r['symbol']:<12} {r['interval']:<5} "
            f"{r['calmar']:<8.2f} {r['sharpe']:<8.2f} {r['total_return']:<9.1%} "
            f"{r['max_drawdown']:<8.1%} {r['total_trades']:<6}"
        )

    # Cross-symbol consistency
    print("\n## Cross-Symbol Consistency (per strategy × interval)")
    css = cross_symbol_summary(results)
    print(f"{'Strategy':<22} {'Intv':<5} {'Symbols':<8} {'Profitable':<11} {'Avg Sharpe':<11} {'Max':<7} {'Min':<7} {'Std':<7} {'Avg WF_OOS':<11} {'Best Sym':<12}")
    print("-" * 115)
    for s in css:
        wf_str = f"{s['avg_wf_oos']:.2f}" if s["avg_wf_oos"] is not None else "N/A"
        print(
            f"{s['strategy']:<22} {s['interval']:<5} {s['n_symbols']:<8} "
            f"{s['n_profitable']}/{s['n_symbols']:<9} {s['avg_sharpe']:<11.4f} "
            f"{s['max_sharpe']:<7.2f} {s['min_sharpe']:<7.2f} {s['sharpe_std']:<7.4f} "
            f"{wf_str:<11} {s['best_symbol']:<12}"
        )

    # Walk-forward leaders
    wf_results = [r for r in results if "walk_forward" in r and r["total_trades"] >= 3]
    if wf_results:
        print("\n## Top 10 by Walk-Forward OOS Sharpe")
        wf_sorted = sorted(wf_results, key=lambda r: r["walk_forward"]["avg_oos_sharpe"], reverse=True)
        print(f"{'Rank':<5} {'Strategy':<22} {'Symbol':<12} {'Intv':<5} {'WF_OOS':<8} {'IS Sharpe':<10} {'Stability':<10} {'Return':<9} {'Trades':<6}")
        print("-" * 100)
        for i, r in enumerate(wf_sorted[:10], 1):
            wf = r["walk_forward"]
            print(
                f"{i:<5} {r['strategy']:<22} {r['symbol']:<12} {r['interval']:<5} "
                f"{wf['avg_oos_sharpe']:<8.2f} {wf['avg_is_sharpe']:<10.2f} "
                f"{wf['stability']:<10.2f} {r['total_return']:<9.1%} {r['total_trades']:<6}"
            )


if __name__ == "__main__":
    main()
