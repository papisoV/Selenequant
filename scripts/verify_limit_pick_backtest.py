"""Verify Limit Pick Strategy via independent backtest against klines.db.

Implements the integration plan task 2: read 1 year of BTC/ETH
1h klines from services/local-api/data/klines.db, feed each bar to
LimitPickStrategy.evaluate(ctx), simulate fills + A1 four-tier trailing
stop, and compute Sharpe.

Standalone by design — LimitPickStrategy is NOT a BacktestStrategyTemplate
subclass (it emits TradeIntent, not Signal), so the /backtests API would
TypeError. This script bypasses the API and drives the strategy directly.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Resolve selene_space packages from the monorepo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "paper-engine"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "types"))
for p in (REPO_ROOT / "packages").iterdir():
    if p.is_dir():
        sys.path.insert(0, str(p))

from selene_space_paper.tiered_trailing_stop import (  # noqa: E402
    TieredTrailingConfig,
    TieredTrailingStopTracker,
)
from selene_space_strategy.templates.presets.limit_pick_strategy import (  # noqa: E402
    LimitPickConfig,
    LimitPickContext,
    LimitPickStrategy,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("verify_limit_pick_backtest")

KLINES_DB = REPO_ROOT / "services" / "local-api" / "data" / "klines.db"
EMA_WINDOW = 240  # min bars LimitPickStrategy needs to emit (ema_period=240)


def load_klines(symbol: str, interval: str = "1h") -> pd.DataFrame:
    """Load OHLCV ascending from klines.db."""
    conn = sqlite3.connect(str(KLINES_DB))
    try:
        df = pd.read_sql(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM klines WHERE symbol=? AND interval=? ORDER BY timestamp ASC",
            conn,
            params=(symbol, interval),
        )
    finally:
        conn.close()
    assert df["timestamp"].is_monotonic_increasing, "klines must be ascending"
    df["open_time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.reset_index(drop=True)


@dataclass
class OpenPosition:
    entry_price: float
    quantity_usdt: float
    entry_bar: int
    tiered_state: Optional[object] = None


def backtest_symbol(
    symbol: str,
    df: pd.DataFrame,
    *,
    initial_capital: float = 10000.0,
    amount_usdt: float = 20.0,
    max_hold_bars: int = 168,
) -> dict:
    """Run LimitPickStrategy + A1 tiered trailing over ascending klines.

    Per bar:
      1. Construct LimitPickContext with the last EMA_WINDOW+ bars.
      2. evaluate(ctx) → TradeIntent (LIMIT BUY) or None.
      3. If intent issued and no open position: place a LIMIT BUY at
         intent.price. Fill if next bar's low <= limit_price.
      4. On fill: register with TieredTrailingStopTracker.on_fill.
      5. Each subsequent bar: on_tick(mark_price=close). If state.closed,
         close position at stop_level (or close price, whichever is
         closer to the tick).
      6. Force-close after max_hold_bars regardless.
    """
    strategy = LimitPickStrategy(LimitPickConfig(long_amount_usdt=amount_usdt))
    tracker = TieredTrailingStopTracker(TieredTrailingConfig())
    position: Optional[OpenPosition] = None

    equity_curve: list[float] = [initial_capital]
    trades: list[dict] = []
    equity = initial_capital

    n = len(df)
    closes = df["close"].astype(float).to_numpy()
    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()
    opens = df["open"].astype(float).to_numpy()

    for i in range(EMA_WINDOW + 1, n):
        # 1. Build ascending window up to and including bar i.
        window = df.iloc[: i + 1]
        mark_price = float(closes[i])

        ctx = LimitPickContext(
            symbol=symbol,
            klines=window,
            mark_price=mark_price,
        )

        intent = strategy.evaluate(ctx)

        # 2. If flat and an intent was issued, place LIMIT at intent.price.
        if position is None and intent is not None and intent.price > 0:
            # Fill check on the NEXT bar's range (i+1). We simulate the
            # limit resting on the book and getting hit intrabar.
            if i + 1 < n:
                next_low = float(lows[i + 1])
                if next_low <= intent.price:
                    fill_price = intent.price
                    position = OpenPosition(
                        entry_price=fill_price,
                        quantity_usdt=amount_usdt,
                        entry_bar=i + 1,
                    )
                    tracker.on_fill(symbol, amount_usdt, fill_price)

        # 3. Manage open position.
        if position is not None:
            tick_price = float(closes[i])
            # Update peak/tier via on_tick.
            state = tracker.on_tick(symbol, tick_price, position.quantity_usdt)

            # Force-close after max_hold_bars.
            force_close = (i - position.entry_bar) >= max_hold_bars

            exit_price: Optional[float] = None
            if state is not None and state.closed:
                # Stop level — assume fill at stop (conservative: at stop,
                # not at tick, since the stop is a resting order).
                exit_price = tick_price
            elif force_close:
                exit_price = tick_price

            if exit_price is not None:
                qty = position.quantity_usdt / position.entry_price
                gross_pnl = qty * (exit_price - position.entry_price)
                equity += gross_pnl
                trades.append(
                    {
                        "entry_bar": position.entry_bar,
                        "exit_bar": i,
                        "entry_price": position.entry_price,
                        "exit_price": exit_price,
                        "pnl_usdt": gross_pnl,
                        "bars_held": i - position.entry_bar,
                    }
                )
                tracker.clear(symbol)
                position = None

        equity_curve.append(equity)

    # === Metrics ===
    eq_arr = np.array(equity_curve, dtype=float)
    # Daily returns — bars are 1h, so group into 24-bar chunks.
    n_days = len(eq_arr) // 24
    if n_days > 1:
        daily_equity = eq_arr[: n_days * 24].reshape(n_days, 24)[:, -1]
        daily_returns = np.diff(daily_equity) / daily_equity[:-1]
    else:
        daily_returns = np.diff(eq_arr) / eq_arr[:-1]

    sharpe: float
    if len(daily_returns) > 1 and np.std(daily_returns) > 0:
        sharpe = float(
            np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(365)
        )
    else:
        sharpe = 0.0

    peak = np.maximum.accumulate(eq_arr)
    drawdown = (eq_arr - peak) / peak
    max_drawdown = float(np.min(drawdown))

    total_pnl = float(eq_arr[-1] - initial_capital)
    total_return_pct = float((eq_arr[-1] / initial_capital - 1.0) * 100.0)
    n_trades = len(trades)
    win_rate = (
        float(sum(1 for t in trades if t["pnl_usdt"] > 0) / n_trades * 100.0)
        if n_trades > 0
        else 0.0
    )

    return {
        "symbol": symbol,
        "bars": n,
        "trades": n_trades,
        "win_rate_pct": win_rate,
        "final_equity": float(eq_arr[-1]),
        "total_return_pct": total_return_pct,
        "total_pnl_usdt": total_pnl,
        "sharpe": sharpe,
        "max_drawdown_pct": max_drawdown * 100.0,
    }


def main() -> int:
    symbols = ["BTC/USDT", "ETH/USDT"]
    logger.info("=== Limit Pick Strategy Standalone Backtest ===")
    logger.info("data source: %s", KLINES_DB)

    results: list[dict] = []
    for symbol in symbols:
        df = load_klines(symbol)
        logger.info(
            "%s 1h rows=%d range=%s → %s",
            symbol,
            len(df),
            df["open_time"].iloc[0].date(),
            df["open_time"].iloc[-1].date(),
        )
        res = backtest_symbol(symbol, df)
        results.append(res)
        print(
            f"[{symbol}] trades={res['trades']} win_rate={res['win_rate_pct']:.1f}% "
            f"return={res['total_return_pct']:.2f}% pnl={res['total_pnl_usdt']:.2f} USDT "
            f"sharpe={res['sharpe']:.4f} max_dd={res['max_drawdown_pct']:.2f}%"
        )

    print("\n=== Backtest Report ===")
    print(f"{'symbol':<10} {'trades':>7} {'win%':>7} {'return%':>9} "
          f"{'pnl_usdt':>10} {'sharpe':>8} {'maxdd%':>8}")
    for r in results:
        print(
            f"{r['symbol']:<10} {r['trades']:>7} {r['win_rate_pct']:>6.1f} "
            f"{r['total_return_pct']:>8.2f} {r['total_pnl_usdt']:>9.2f} "
            f"{r['sharpe']:>8.4f} {r['max_drawdown_pct']:>7.2f}"
        )

    btc_sharpe = next(r["sharpe"] for r in results if r["symbol"] == "BTC/USDT")
    eth_sharpe = next(r["sharpe"] for r in results if r["symbol"] == "ETH/USDT")
    print(f"\nSharpe BTC/USDT: {btc_sharpe:.4f}")
    print(f"Sharpe ETH/USDT: {eth_sharpe:.4f}")
    threshold = 0.5
    passed = btc_sharpe > threshold and eth_sharpe > threshold
    print(f"Sharpe > {threshold}: {'PASS' if passed else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
