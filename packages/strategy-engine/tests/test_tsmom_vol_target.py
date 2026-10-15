"""Tests for TsmomVolTargetStrategy (time-series momentum + vol targeting).

Validates the QC Moskowitz-Ooi-Pedersen transfer: sign of the trailing
lookback return decides direction (long / short), realized-volatility
scaling de-risks position size, and momentum decay closes positions.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.factory import create_tsmom_vol_target_strategy
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestResult
from selene_space_strategy.templates.presets.tsmom_vol_target import (
    TsmomVolTargetStrategy,
)
from selene_space_types.market import KlineEvent

_EPOCH = pd.Timestamp("2026-01-01", tz="UTC")
_DAY = 86_400_000


def _kline(close: float, i: int) -> KlineEvent:
    ts = int(_EPOCH.value // 1_000_000) + i * _DAY
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        closed=True,
    )


class TestTsmomVolTarget:
    def test_long_enters_on_uptrend_closes_on_decay(self) -> None:
        """Rising trend triggers a long; flattening momentum closes it."""
        strategy = TsmomVolTargetStrategy(symbol="BTC/USDT", lookback=3)
        # Uptrend (+10%/bar for 3 bars) then flat: mom = 0.33, then 0.0.
        # 尾部多留一根平盘:次根开盘成交语义下,CLOSE 单需要下一根才能回补。
        prices = [100.0, 110.0, 121.0, 133.0, 133.0, 133.0, 133.0, 133.0]
        events = [_kline(p, i) for i, p in enumerate(prices)]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.trades[0]["action"] == "close_long"

    def test_short_enters_on_downtrend_when_enabled(self) -> None:
        """With enable_short, a downtrend opens a short and flattens later."""
        strategy = TsmomVolTargetStrategy(
            symbol="BTC/USDT", lookback=3, enable_short=True
        )
        # Downtrend (-10%/bar for 3 bars) then flat: mom = -0.27, then 0.0.
        prices = [100.0, 90.0, 81.0, 73.0, 73.0, 73.0, 73.0, 73.0]
        events = [_kline(p, i) for i, p in enumerate(prices)]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.trades[0]["action"] == "close_short"

    def test_no_short_when_disabled(self) -> None:
        """Default enable_short=False keeps a downtrend flat."""
        strategy = TsmomVolTargetStrategy(symbol="BTC/USDT", lookback=3)
        prices = [100.0, 90.0, 81.0, 73.0, 73.0, 73.0, 73.0]
        events = [_kline(p, i) for i, p in enumerate(prices)]

        result = strategy.run_backtest(events)
        assert result.total_trades == 0

    def test_vol_target_scales_position_down_in_high_vol(self) -> None:
        """Realized-vol scaling shrinks the position fraction in high vol."""
        strategy = TsmomVolTargetStrategy(
            symbol="BTC/USDT",
            vol_target=0.20,
            vol_lookback=5,
            position_size_pct=1.0,
            max_position_pct=1.0,
        )
        # Calm market: realized vol ~ 0 -> full base size.
        strategy._bars = [{"close": 100.0} for _ in range(20)]
        low_vol_frac = strategy._compute_position_fraction()

        # Choppy market: alternating 100/150 -> high realized vol -> de-risk.
        strategy._bars = [{"close": 100.0 if i % 2 == 0 else 150.0} for i in range(20)]
        high_vol_frac = strategy._compute_position_fraction()

        assert low_vol_frac == 1.0
        assert 0.0 < high_vol_frac < low_vol_frac

    def test_from_config(self) -> None:
        config = EngineStrategyConfig(
            id="tsmom-e2e",
            name="TSMOM E2E",
            type="custom",
            symbols=["ETH/USDT"],
            interval="1d",
            parameters={
                "lookback": 120,
                "entry_threshold": 0.01,
                "exit_threshold": -0.005,
                "vol_target": 0.15,
                "vol_lookback": 60,
                "enable_short": True,
            },
        )
        strategy = create_tsmom_vol_target_strategy(config)
        assert isinstance(strategy, TsmomVolTargetStrategy)
        assert strategy.symbol == "ETH/USDT"
        assert strategy.lookback == 120
        assert strategy.entry_threshold == 0.01
        assert strategy.exit_threshold == -0.005
        assert strategy.vol_target == 0.15
        assert strategy.vol_lookback == 60
        assert strategy.enable_short is True
        assert strategy.long_only is False  # derived from enable_short
