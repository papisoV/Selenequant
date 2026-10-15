"""Backtest demonstration script — runs SMA Cross strategy on historical BTC/USDT data.

Usage:
    python examples/backtest_demo.py

This script demonstrates the full backtest workflow:
1. Load a strategy from YAML template
2. Compile it into an executable runtime
3. Fetch historical kline data from Binance
4. Run backtest simulation
5. Output performance metrics (Sharpe, MaxDD, WinRate)
"""

from __future__ import annotations

import asyncio
import io
import sys
from typing import Any, NoReturn
from pathlib import Path

# Fix Windows console encoding for the box-drawing/emoji report below.
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_market.sources.binance_source import BinanceSource
from selene_space_strategy.compiler import compile
from selene_space_strategy.models import load_strategy
from selene_space_strategy.runtime import StrategyRuntime

_TEMPLATE = (
    project_root
    / "packages"
    / "strategy-engine"
    / "selene_space_strategy"
    / "templates"
    / "sma_cross.yaml"
)
_RULE = "=" * 60
#: 报告每行 "  <标签><值>" 的标签列宽(所有标签恰好在此对齐)。
_LABEL_WIDTH = 19

#: (标签, BacktestMetrics 属性名, 格式化 spec)。
_CORE_METRIC_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Sharpe Ratio:", "sharpe_ratio", ".3f"),
    ("Max Drawdown:", "max_drawdown", ".2%"),
    ("Win Rate:", "win_rate", ".2%"),
    ("Total Return:", "total_return", ".2%"),
)
_RISK_METRIC_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Sortino Ratio:", "sortino_ratio", ".3f"),
    ("Calmar Ratio:", "calmar_ratio", ".3f"),
)


def _row(label: str, value: object) -> None:
    """打印报告的一行, 标签统一左对齐到 ``_LABEL_WIDTH``。"""
    print(f"  {label:<{_LABEL_WIDTH}}{value}")


def _abort(prefix: str, exc: BaseException) -> NoReturn:
    """打印失败原因并以状态码 1 退出。"""
    print(f"✗ {prefix}: {exc}")
    sys.exit(1)


def _print_metric_rows(metrics: Any, rows: tuple[tuple[str, str, str], ...]) -> None:
    """按表逐行打印指标: 属性值套上各自的格式 spec。"""
    for label, attr, spec in rows:
        _row(label, format(getattr(metrics, attr), spec))


def _compile_template(config: Any) -> tuple[Any, StrategyRuntime]:
    """把策略配置编译成可执行 runtime。"""
    try:
        compiled = compile(config)
    except Exception as exc:
        _abort("Compilation failed", exc)
    return compiled, StrategyRuntime(compiled)


def _load_runtime() -> tuple[Any, StrategyRuntime]:
    """Load + compile the SMA cross template. Exits on failure."""
    print("\n[1/4] Loading strategy from YAML...")
    try:
        config = load_strategy(_TEMPLATE)
    except Exception as exc:
        _abort("Failed to load strategy", exc)
    print(f"✓ Strategy loaded: {config.name} (id={config.id})")
    print(f"  Symbol: {config.symbols[0]}, Interval: {config.interval}")

    print("\n[2/4] Compiling strategy...")
    compiled, runtime = _compile_template(config)
    print("✓ Strategy compiled successfully")
    print(f"  Indicators: {list(compiled.indicators.keys())}")
    print(
        f"  Entry conditions: {len(compiled.long_entry_conditions)} long, "
        f"{len(compiled.short_entry_conditions)} short"
    )
    return config, runtime


def _summarize_klines(klines: list[Any]) -> None:
    """打印取回的 K 线规模与时间范围。"""
    print(f"✓ Fetched {len(klines)} klines")
    print(f"  Date range: {klines[0].timestamp} → {klines[-1].timestamp}")


async def _fetch_klines() -> list[Any]:
    """Fetch 500 1h candles from Binance. Exits on failure."""
    print("\n[3/4] Fetching historical data from Binance...")
    source = BinanceSource(market_type="spot")
    await source.start()
    try:
        klines = await source.fetch_klines_rest(
            symbol="BTC/USDT", interval="1h", limit=500
        )
        _summarize_klines(klines)
        return list(klines)
    except Exception as exc:
        _abort("Failed to fetch data", exc)
    finally:
        await source.stop()


def _trade_stats(trades: list[Any]) -> dict[str, float]:
    """Win/loss breakdown over closed trades."""
    pnls = [t["pnl"] for t in trades] if trades else []
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(-p for p in losses) / len(losses) if losses else 0.0,
    }


def _print_trade_stats(result: Any) -> None:
    """打印成交统计(胜负笔数与平均盈亏)。"""
    stats = _trade_stats(result.trades)
    print("\n💰 Trade Statistics:")
    _row("Total Trades:", result.trades_count)
    _row("Winning Trades:", stats["wins"])
    _row("Losing Trades:", stats["losses"])
    _row("Avg Win:", f"${stats['avg_win']:.2f}")
    _row("Avg Loss:", f"${stats['avg_loss']:.2f}")
    _row("Profit Factor:", format(result.metrics.profit_factor, ".2f"))


def _print_period(result: Any) -> None:
    """打印回测区间与天数。"""
    start, end = result.start_date, result.end_date
    print("\n📅 Backtest Period:")
    _row("Start Date:", start.isoformat())
    _row("End Date:", end.isoformat())
    _row("Duration:", f"{(end - start).days} days")


def _report(result: Any) -> None:
    """Print the performance report."""
    print("\n" + _RULE)
    print("BACKTEST PERFORMANCE REPORT")
    print(_RULE)

    print("\n📊 Core Metrics:")
    _print_metric_rows(result.metrics, _CORE_METRIC_ROWS)

    _print_trade_stats(result)

    print("\n📈 Risk Metrics:")
    _print_metric_rows(result.metrics, _RISK_METRIC_ROWS)

    _print_period(result)

    print("\n" + _RULE)
    print("✓ Backtest demo completed successfully")
    print(_RULE)


async def run_backtest() -> None:
    """Execute a complete backtest demonstration."""
    print(_RULE)
    print("SeleneQuant Backtest Demo — SMA Cross Strategy")
    print(_RULE)

    config, runtime = _load_runtime()
    klines = await _fetch_klines()

    print("\n[4/4] Running backtest...")
    demo_config = dict(
        initial_cash=10_000.0,
        strategy_id=config.id,
        symbol=config.symbols[0],
        interval=config.interval,
    )
    engine = BacktestEngine(config=BacktestConfig(**demo_config))
    try:
        result = engine.run(strategy=runtime, data=klines)
    except Exception as exc:
        _abort("Backtest failed", exc)
    print("✓ Backtest completed")
    print(f"  Trades executed: {result.trades_count}")

    _report(result)


if __name__ == "__main__":
    asyncio.run(run_backtest())
