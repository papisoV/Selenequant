"""Tests for CrossSectionalMomentumStrategy (cross-sectional decile momentum).

Validates the ranking logic: the primary symbol's trailing return is
ranked against the universe — top quantile -> long entry, bottom quantile
-> short entry, then exits as the rank decays out of the quantile.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.factory import create_cross_sectional_momentum_strategy
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestResult
from selene_space_strategy.templates.presets.cross_sectional_momentum import (
    CrossSectionalMomentumStrategy,
)
from selene_space_types.market import KlineEvent

_EPOCH = pd.Timestamp("2026-01-01", tz="UTC")
_DAY = 86_400_000

_OTHER_FLAT = {
    "ETH/USDT": [100.0] * 8,
    "SOL/USDT": [100.0] * 8,
    "ADA/USDT": [100.0] * 8,
    "DOT/USDT": [100.0] * 8,
}


def _kline(close: float, ts: int, symbol: str = "BTC/USDT") -> KlineEvent:
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


def _ts(n: int = 8) -> list[int]:
    return [int(_EPOCH.value // 1_000_000) + i * _DAY for i in range(n)]


def _universe(ts: list[int], btc: list[float]) -> dict[str, pd.DataFrame]:
    """Universe where BTC alone moves and the rest stay flat."""
    data = {sym: list(closes) for sym, closes in _OTHER_FLAT.items()}
    data["BTC/USDT"] = btc
    return {
        sym: pd.DataFrame({"timestamp": ts, "close": closes})
        for sym, closes in data.items()
    }


class TestCrossSectionalMomentum:
    def test_long_when_main_in_top_quantile(self) -> None:
        """BTC spikes to top of the universe -> BUY; flatline later decays
        its rank -> CLOSE_LONG."""
        ts = _ts()
        # At bar 3 BTC returns +50% (100 -> 150) while others stay flat, so
        # BTC is the top-ranked symbol. At bar 6 BTC is back to flat while
        # others were flat the whole time, so its rank falls out of the top
        # quantile and the long closes. Tail bar lets the CLOSE fill at the
        # next bar's open under engine semantics.
        btc = [100.0, 100.0, 100.0, 150.0, 100.0, 100.0, 100.0, 100.0]
        strategy = CrossSectionalMomentumStrategy(
            symbol="BTC/USDT",
            lookback=3,
            top_quantile=0.2,
            bottom_quantile=0.2,
        )
        strategy.set_universe_data(_universe(ts, btc))
        events = [_kline(c, t) for c, t in zip(btc, ts)]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.trades[0]["action"] == "close_long"

    def test_short_when_main_in_bottom_quantile(self) -> None:
        """BTC drops to the bottom of the universe -> SELL; recovery out of
        the bottom quantile -> CLOSE_SHORT (short leg enabled)."""
        ts = _ts()
        # BTC dips to 70 at bar 3 and stays there through bar 5 (worst-ranked,
        # short held), then recovers to 100 at bar 6, leaving the bottom
        # quantile, and the short closes. Tail bar lets the CLOSE fill at
        # the next bar's open under engine semantics.
        btc = [100.0, 100.0, 100.0, 70.0, 70.0, 70.0, 100.0, 100.0]
        strategy = CrossSectionalMomentumStrategy(
            symbol="BTC/USDT",
            lookback=3,
            top_quantile=0.2,
            bottom_quantile=0.2,
            enable_short=True,
        )
        strategy.set_universe_data(_universe(ts, btc))
        events = [_kline(c, t) for c, t in zip(btc, ts)]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.trades[0]["action"] == "close_short"

    def test_short_disabled_no_short_entry(self) -> None:
        """With enable_short=False, a bottom-ranked symbol produces no trade."""
        ts = _ts()
        btc = [100.0, 100.0, 100.0, 70.0, 100.0, 100.0, 100.0, 100.0]
        strategy = CrossSectionalMomentumStrategy(
            symbol="BTC/USDT",
            lookback=3,
            top_quantile=0.2,
            bottom_quantile=0.2,
            enable_short=False,
        )
        strategy.set_universe_data(_universe(ts, btc))
        events = [_kline(c, t) for c, t in zip(btc, ts)]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0

    def test_no_universe_safe(self) -> None:
        """Without set_universe_data, run_backtest does not crash — signals
        are suppressed to HOLD and 0 trades are recorded."""
        ts = _ts(5)
        strategy = CrossSectionalMomentumStrategy(symbol="BTC/USDT", lookback=2)
        result = strategy.run_backtest([_kline(100.0, t) for t in ts])
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0

    def test_from_config(self) -> None:
        config = EngineStrategyConfig(
            id="xsec-e2e",
            name="Cross Sectional E2E",
            type="custom",
            symbols=["BTC/USDT"],
            interval="1d",
            parameters={
                "lookback": 10,
                "skip_bars": 1,
                "top_quantile": 0.1,
                "bottom_quantile": 0.05,
                "enable_short": True,
            },
        )
        strategy = create_cross_sectional_momentum_strategy(config)
        assert isinstance(strategy, CrossSectionalMomentumStrategy)
        assert strategy.symbol == "BTC/USDT"
        assert strategy.lookback == 10
        assert strategy.skip_bars == 1
        assert strategy.top_quantile == 0.1
        assert strategy.bottom_quantile == 0.05
        assert strategy.enable_short is True
        assert strategy.long_only is False
