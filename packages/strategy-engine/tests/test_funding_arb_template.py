"""Tests for FundingArbStrategy — long spot + short perp funding rate capture."""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import SignalType
from selene_space_strategy.templates.presets.derivatives.funding_arb import FundingArbStrategy


def _make_spot_ohlc(rows: int = 100, start_price: float = 50000.0) -> pd.DataFrame:
    """Build a synthetic spot OHLCV DataFrame with DatetimeIndex."""
    idx = pd.date_range("2026-01-01", periods=rows, freq="1h", tz="UTC")
    # Constant price — strategy should still fire on funding signal alone
    return pd.DataFrame(
        {
            "open": [start_price] * rows,
            "high": [start_price] * rows,
            "low": [start_price] * rows,
            "close": [start_price] * rows,
            "volume": [1.0] * rows,
        },
        index=idx,
    )


def _make_perp_with_funding(funding_rates: list[float], spot_df: pd.DataFrame) -> pd.DataFrame:
    """Build a perp DataFrame with funding_rate column aligned to spot_df index.

    If funding_rates is shorter than spot_df, the last value is broadcast to fill.
    """
    n = len(spot_df)
    if len(funding_rates) < n:
        if len(funding_rates) == 0:
            funding_rates = [0.0] * n
        else:
            funding_rates = funding_rates + [funding_rates[-1]] * (n - len(funding_rates))
    elif len(funding_rates) > n:
        funding_rates = funding_rates[:n]
    return pd.DataFrame({"funding_rate": funding_rates}, index=spot_df.index)


def _instantiate(**overrides: Any) -> FundingArbStrategy:
    defaults: dict[str, Any] = {
        "symbol": "BTC/USDT",
        "funding_threshold": 0.0005,
        "exit_threshold": 0.0001,
        "max_hold_bars": 60,
        "initial_capital": 10000,
    }
    defaults.update(overrides)
    return FundingArbStrategy(**defaults)


def test_funding_threshold_triggers_entry() -> None:
    strategy = _instantiate()
    spot = _make_spot_ohlc()
    perp = _make_perp_with_funding([0.001], spot)  # 0.1% per 8h > threshold
    strategy.set_secondary_data(perp)
    signal = strategy.get_signal(spot)
    assert signal == SignalType.BUY


def test_below_threshold_holds() -> None:
    strategy = _instantiate()
    spot = _make_spot_ohlc()
    perp = _make_perp_with_funding([0.0003], spot)  # < 0.0005 threshold
    strategy.set_secondary_data(perp)
    signal = strategy.get_signal(spot)
    assert signal == SignalType.HOLD


def test_exit_on_revert() -> None:
    strategy = _instantiate()
    spot = _make_spot_ohlc()
    perp = _make_perp_with_funding([0.001, 0.00005], spot)  # 2nd bar below exit_threshold
    strategy.set_secondary_data(perp)
    # Force into long position
    strategy._position = "long"
    strategy._entry_price = 50000.0
    strategy._bars_held = 5
    signal = strategy.get_signal(spot)
    assert signal == SignalType.CLOSE_LONG


def test_max_hold_bars_exit() -> None:
    strategy = _instantiate(max_hold_bars=3)
    spot = _make_spot_ohlc()
    perp = _make_perp_with_funding([0.001] * 5, spot)  # funding stays high
    strategy.set_secondary_data(perp)
    strategy._position = "long"
    strategy._entry_price = 50000.0
    strategy._bars_held = 3
    signal = strategy.get_signal(spot)
    assert signal == SignalType.CLOSE_LONG


def test_missing_perp_data_holds() -> None:
    """No set_secondary_data called → strategy should HOLD, not crash."""
    strategy = _instantiate()
    spot = _make_spot_ohlc()
    signal = strategy.get_signal(spot)
    assert signal == SignalType.HOLD


def test_missing_funding_rate_column_holds() -> None:
    """Perp data provided but no funding_rate column → HOLD."""
    strategy = _instantiate()
    spot = _make_spot_ohlc()
    perp = pd.DataFrame({"close": [50000]}, index=spot.index)
    strategy.set_secondary_data(perp)
    signal = strategy.get_signal(spot)
    assert signal == SignalType.HOLD


def test_perp_symbol_default() -> None:
    strategy = FundingArbStrategy(symbol="ETH/USDT")
    assert strategy.perp_symbol == "ETH/USDT-SWAP"


def test_get_parameters_exposes_funding_threshold() -> None:
    strategy = _instantiate(funding_threshold=0.0007)
    params = strategy.get_parameters()
    assert params["funding_threshold"] == 0.0007
    assert params["perp_symbol"] == "BTC/USDT-SWAP"
