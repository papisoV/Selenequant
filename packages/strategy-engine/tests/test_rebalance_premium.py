"""Tests for RebalancePremiumStrategy (volatility harvesting factor).

Validates the rebalancing premium computation: equal-weight daily
rebalancing vs buy-and-hold drift over an anticorrelated universe yields a
positive premium -> long entry, then decays to zero -> exit.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.factory import create_rebalance_premium_strategy
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestResult
from selene_space_strategy.templates.presets.rebalance_premium import (
    RebalancePremiumStrategy,
)
from selene_space_types.market import KlineEvent

_EPOCH = pd.Timestamp("2026-01-01", tz="UTC")
_DAY = 86_400_000


def _kline(close: float, ts: int, symbol: str = "A/USDT") -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=ts,
        interval="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        closed=True,
    )


def _universe(ts: list[int]) -> dict[str, pd.DataFrame]:
    """Anticorrelated A/B that oscillate around a flat level.

    Equal-weight rebalancing captures the oscillation (premium grows);
    buy-and-hold stays flat. After the oscillation both flatten so the
    premium delta decays to zero and the long exits.
    """
    a = [100.0, 150.0, 100.0, 150.0, 100.0, 100.0, 100.0, 100.0]
    b = [100.0, 50.0, 100.0, 50.0, 100.0, 100.0, 100.0, 100.0]
    return {
        "A/USDT": pd.DataFrame({"timestamp": ts, "close": a}),
        "B/USDT": pd.DataFrame({"timestamp": ts, "close": b}),
    }


class TestRebalancePremium:
    def test_harvests_premium_then_exits_on_decay(self) -> None:
        ts = [int(_EPOCH.value // 1_000_000) + i * _DAY for i in range(8)]
        strategy = RebalancePremiumStrategy(
            symbol="A/USDT",
            lookback=2,
            entry_threshold=0.01,
            exit_threshold=0.01,
        )
        strategy.set_universe_data(_universe(ts))
        events = [_kline(100.0, t) for t in ts]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        # BUY on premium harvest, CLOSE_LONG when the premium delta flattens.
        assert result.total_trades == 1
        assert result.trades[0]["action"] == "close_long"

    def test_no_universe_safe(self) -> None:
        """Without set_universe_data, run_backtest does not crash — signals
        are suppressed to HOLD and 0 trades are recorded."""
        ts = [int(_EPOCH.value // 1_000_000) + i * _DAY for i in range(5)]
        strategy = RebalancePremiumStrategy(symbol="A/USDT", lookback=2)
        result = strategy.run_backtest([_kline(100.0, t) for t in ts])
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0

    def test_from_config(self) -> None:
        config = EngineStrategyConfig(
            id="rebal-e2e",
            name="Rebalance E2E",
            type="custom",
            symbols=["BTC/USDT"],
            interval="1d",
            parameters={
                "lookback": 10,
                "entry_threshold": 0.02,
                "exit_threshold": 0.005,
            },
        )
        strategy = create_rebalance_premium_strategy(config)
        assert isinstance(strategy, RebalancePremiumStrategy)
        assert strategy.symbol == "BTC/USDT"
        assert strategy.lookback == 10
        assert strategy.entry_threshold == 0.02
        assert strategy.exit_threshold == 0.005
