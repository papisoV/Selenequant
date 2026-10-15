"""Tests for BasisCarryStrategy — long spot + short quarterly futures basis capture."""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import SignalType
from selene_space_strategy.templates.presets.derivatives.basis_carry import BasisCarryStrategy


def _make_spot_with_basis(basis_rates: list[float], prices: list[float] | None = None) -> pd.DataFrame:
    """Build a spot DataFrame with DERIV_basis_rate column merged in."""
    n = len(basis_rates)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    if prices is None:
        prices = [50000.0] * n
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1.0] * n,
            "DERIV_basis_rate": basis_rates,
        },
        index=idx,
    )


def _instantiate(**overrides: Any) -> BasisCarryStrategy:
    defaults: dict[str, Any] = {
        "symbol": "BTC/USDT",
        "basis_threshold": 0.02,
        "exit_basis": 0.005,
        "initial_capital": 10000,
    }
    defaults.update(overrides)
    return BasisCarryStrategy(**defaults)


def test_basis_threshold_triggers_entry() -> None:
    strategy = _instantiate()
    data = _make_spot_with_basis([0.03])  # 3% > 2% threshold
    signal = strategy.get_signal(data)
    assert signal == SignalType.BUY


def test_exit_on_basis_reversal() -> None:
    strategy = _instantiate()
    data = _make_spot_with_basis([0.03, 0.003])  # 2nd bar below exit_basis
    strategy._position = "long"
    strategy._entry_price = 50000.0
    signal = strategy.get_signal(data)
    assert signal == SignalType.CLOSE_LONG


def test_exit_at_expiry() -> None:
    """When current timestamp >= expiry_ts, close long."""
    strategy = _instantiate(expiry_ts=1000)
    # Use timestamps well past expiry
    idx = pd.date_range("2026-06-01", periods=3, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {
            "open": [50000.0] * 3,
            "high": [50000.0] * 3,
            "low": [50000.0] * 3,
            "close": [50000.0] * 3,
            "volume": [1.0] * 3,
            "DERIV_basis_rate": [0.05, 0.05, 0.05],
            "timestamp": [2_000_000, 2_000_001, 2_000_002],  # ms epoch, all > 1000
        },
        index=idx,
    )
    strategy._position = "long"
    strategy._entry_price = 50000.0
    signal = strategy.get_signal(data)
    assert signal == SignalType.CLOSE_LONG


def test_missing_basis_column_holds() -> None:
    """No DERIV_basis_rate column → HOLD, no crash."""
    strategy = _instantiate()
    data = pd.DataFrame(
        {"open": [50000], "high": [50000], "low": [50000], "close": [50000], "volume": [1.0]},
        index=pd.date_range("2026-01-01", periods=1, freq="1h", tz="UTC"),
    )
    signal = strategy.get_signal(data)
    assert signal == SignalType.HOLD


def test_below_threshold_holds() -> None:
    strategy = _instantiate()
    data = _make_spot_with_basis([0.015])  # < 0.02 threshold
    signal = strategy.get_signal(data)
    assert signal == SignalType.HOLD


def test_futures_symbol_default() -> None:
    strategy = BasisCarryStrategy(symbol="ETH/USDT")
    assert strategy.futures_symbol == "ETH/USDT-QUARTERLY"


def test_get_parameters_exposes_basis_threshold() -> None:
    strategy = _instantiate(basis_threshold=0.025, expiry_ts=1735689600000)
    params = strategy.get_parameters()
    assert params["basis_threshold"] == 0.025
    assert params["expiry_ts"] == 1735689600000
    assert params["futures_symbol"] == "BTC/USDT-QUARTERLY"


def test_no_expiry_holds_in_long() -> None:
    """Without expiry_ts, position stays long while basis is acceptable."""
    strategy = _instantiate()
    data = _make_spot_with_basis([0.03, 0.025])  # basis still above exit
    strategy._position = "long"
    strategy._entry_price = 50000.0
    signal = strategy.get_signal(data)
    assert signal == SignalType.HOLD
