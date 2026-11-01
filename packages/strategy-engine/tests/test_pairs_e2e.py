"""End-to-end smoke test for PairsTradingStrategy via factory helper.

Covers US-005 acceptance: Pairs e2e —
create_pairs_strategy(config) + set_secondary_data(eth_df) + run_backtest(btc_events)
returns BacktestResult with total_trades >= 0 (no error = pass).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.factory import create_pairs_strategy
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestResult
from selene_space_types.market import KlineEvent


def _btc_kline(close: float, ts: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="1h",
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
        closed=True,
    )


def _eth_kline(close: float, ts: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="ETH/USDT",
        timestamp=ts,
        interval="1h",
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1,
        close=close,
        volume=1000.0,
        closed=True,
    )


class TestPairsE2E:
    def test_pairs_backtest_runs(self) -> None:
        """End-to-end: construct via factory, set secondary data, run backtest.

        BTC + ETH cointegrated-ish synthetic data: ETH tracks BTC with noise
        so the spread mean-reverts and the strategy runs without error.
        """
        config = EngineStrategyConfig(
            id="pairs-e2e",
            name="Pairs E2E",
            type="custom",
            symbols=["BTC/USDT", "ETH/USDT"],
            interval="1h",
            parameters={
                "entry_z": 2.0,
                "exit_z": 0.5,
                "hurst_threshold": 0.5,
                "lookback": 20,
            },
        )
        strategy = create_pairs_strategy(config)
        assert strategy is not None
        assert strategy.symbol == "BTC/USDT"
        assert strategy.symbol_b == "ETH/USDT"

        # Build 60 hours of BTC + ETH data. ETH = BTC * 0.03 + noise.
        np.random.seed(42)
        btc_closes = 40000 + np.cumsum(np.random.randn(60) * 50)
        eth_closes = btc_closes * 0.03 + np.random.randn(60) * 2

        btc_events = [_btc_kline(float(c), i * 3600_000) for i, c in enumerate(btc_closes)]
        eth_df = pd.DataFrame(
            {
                "timestamp": [i * 3600_000 for i in range(len(eth_closes))],
                "close": eth_closes,
            }
        )

        strategy.set_secondary_data(eth_df)

        result = strategy.run_backtest(btc_events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0

    def test_pairs_strategy_secondary_data_missing_safe(self) -> None:
        """Without set_secondary_data, run_backtest does not crash — it
        silently returns HOLD signals (get_signal swallows ValueError
        from _calculate_spread and returns HOLD). Verify it returns a
        valid BacktestResult with 0 trades rather than raising.
        """
        config = EngineStrategyConfig(
            id="pairs-no-data",
            name="Pairs No Data",
            type="custom",
            symbols=["BTC/USDT", "ETH/USDT"],
            interval="1h",
            parameters={},
        )
        strategy = create_pairs_strategy(config)
        btc_events = [_btc_kline(40000.0 + i, i * 3600_000) for i in range(20)]
        # Must not raise — get_signal catches the missing-data ValueError
        result = strategy.run_backtest(btc_events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0  # No trades because signals suppressed
