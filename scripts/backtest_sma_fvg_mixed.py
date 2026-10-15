#!/usr/bin/env python
"""Backtest sma_fvg_mixed.yaml on real BTC/ETH/SOL 4h data from klines.db.

Loads 4h klines directly from SQLite, compiles sma_fvg_mixed.yaml,
wraps it in a BacktestStrategyTemplate adapter, runs Qbot backtest.

Usage:
    python scripts/backtest_sma_fvg_mixed.py                  # BTC default
    python scripts/backtest_sma_fvg_mixed.py --symbol ETH/USDT
    python scripts/backtest_sma_fvg_mixed.py --symbol SOL/USDT --start 2024-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
for p in ["packages/shared-types", "packages/strategy-engine", "packages/backtest-engine"]:
    sys.path.insert(0, f"{PROJECT_ROOT}/{p}")

import pandas as pd  # noqa: E402

from selene_space_strategy.compiler import compile  # noqa: E402
from selene_space_strategy.models import load_strategy  # noqa: E402
from selene_space_strategy.runtime import StrategyRuntime  # noqa: E402
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType  # noqa: E402
from selene_space_types.market import KlineEvent  # noqa: E402
from selene_space_types.trading import OrderSide  # noqa: E402


class YamlBacktestAdapter(BacktestStrategyTemplate):
    """Wraps a YAML-compiled StrategyRuntime in the Qbot backtest template."""

    def __init__(self, compiled, symbol: str, interval: str = "4h", initial_capital: float = 10_000.0, **kwargs):
        super().__init__(symbol=symbol, initial_capital=initial_capital, **kwargs)
        self._runtime = StrategyRuntime(compiled)
        self._interval = interval

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < 2:
            return SignalType.HOLD
        last = data.iloc[-1]
        ts = int(last.name) if isinstance(last.name, (int, float)) else 0
        event = KlineEvent(
            exchange="binance",
            symbol=self.symbol,
            timestamp=ts,
            interval=self._interval,
            open=float(last["open"]),
            high=float(last["high"]),
            low=float(last["low"]),
            close=float(last["close"]),
            volume=float(last.get("volume", 0.0)),
            closed=True,
        )
        sig = self._runtime.on_kline(event)
        if sig is None:
            return SignalType.HOLD
        if sig.side == OrderSide.BUY:
            return SignalType.BUY
        if sig.side == OrderSide.SELL:
            return SignalType.SELL if not self.long_only else SignalType.CLOSE_LONG
        return SignalType.HOLD


def load_klines(db_path: str, symbol: str, interval: str, start_ts: int | None, end_ts: int | None) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    query = (
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE exchange='binance' AND market_type='spot' AND symbol=? AND interval=? "
    )
    params: list = [symbol, interval]
    if start_ts is not None:
        query += "AND timestamp >= ? "
        params.append(start_ts)
    if end_ts is not None:
        query += "AND timestamp <= ? "
        params.append(end_ts)
    query += "ORDER BY timestamp ASC"
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    df["timestamp"] = df["timestamp"].astype(int)
    return df.set_index("timestamp")


def run_backtest(symbol: str, interval: str, start_ts: int | None, end_ts: int | None, long_only: bool = True) -> None:
    klines_db = f"{PROJECT_ROOT}/services/local-api/data/klines.db"
    yaml_path = (
        f"{PROJECT_ROOT}/packages/strategy-engine/selene_space_strategy/templates/sma_fvg_mixed.yaml"
    )

    df = load_klines(klines_db, symbol, interval, start_ts, end_ts)
    if len(df) == 0:
        print(f"ERROR: No {symbol} {interval} klines in DB for given range.")
        sys.exit(1)

    range_str = f"{pd.to_datetime(df.index.min(), unit='ms')} → {pd.to_datetime(df.index.max(), unit='ms')}"
    print("=" * 70)
    print(f"Backtest: sma_fvg_mixed.yaml | {symbol} {interval} | long_only={long_only}")
    print(f"Data: {len(df)} bars | {range_str}")
    print("=" * 70)

    config = load_strategy(Path(yaml_path))
    compiled = compile(config)

    strategy = YamlBacktestAdapter(
        compiled=compiled,
        symbol=symbol,
        interval=interval,
        initial_capital=10_000.0,
        long_only=long_only,
        position_size_pct=1.0,
        fee_rate=0.0005,
        slippage_bps=1.5,
    )

    events = [
        KlineEvent(
            exchange="binance",
            symbol=symbol,
            timestamp=int(ts),
            interval=interval,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            closed=True,
        )
        for ts, row in df.iterrows()
    ]

    result = strategy.run_backtest(events)
    print(f"  total_trades:    {result.total_trades}")
    print(f"  total_return:    {result.total_return:.4%}")
    print(f"  annual_return:   {result.annual_return:.4%}")
    print(f"  sharpe_ratio:    {result.sharpe_ratio:.4f}")
    print(f"  max_drawdown:    {result.max_drawdown:.4%}")
    print(f"  win_rate:        {result.win_rate:.4%}")
    print(f"  profit_factor:   {result.profit_factor:.4f}")
    print(f"  sortino_ratio:   {result.sortino_ratio:.4f}")
    print(f"  calmar_ratio:    {result.calmar_ratio:.4f}")
    print(f"  expectancy:      {result.expectancy:.4f}")
    verdict = "赚钱" if result.total_return > 0 else "不赚钱"
    print(f"  判定: {verdict}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument("--both-directions", action="store_true", help="Also run short (not just long)")
    args = parser.parse_args()

    start_ts = int(pd.Timestamp(args.start).timestamp() * 1000) if args.start else None
    end_ts = int(pd.Timestamp(args.end).timestamp() * 1000) if args.end else None

    run_backtest(args.symbol, args.interval, start_ts, end_ts, long_only=True)
    if args.both_directions:
        run_backtest(args.symbol, args.interval, start_ts, end_ts, long_only=False)


if __name__ == "__main__":
    main()
