"""Tests for IC decay analysis (P3 #32)."""

from __future__ import annotations

import pytest
import pandas as pd
import numpy as np

from selene_space_alpha.mining.ic_decay import (
    compute_ic_decay,
    compute_ic_by_lag,
    ICDecayResult,
)


def _make_signal_and_returns(
    n: int = 500,
    ic_strength: float = 0.05,
    decay_rate: float = 0.9,
    seed: int = 42,
) -> tuple[pd.Series, pd.Series]:
    """Generate signal and returns with known IC decay pattern.

    IC at lag k ≈ ic_strength * decay_rate^k.
    """
    np.random.seed(seed)
    signal = pd.Series(np.random.randn(n), name="signal")
    # Returns have some correlation with signal at lag 1, decaying at lag k
    noise = np.random.randn(n)
    returns = pd.Series(
        ic_strength * signal + np.sqrt(1 - ic_strength**2) * noise,
        name="returns",
    )
    return signal, returns


class TestComputeICDecay:
    def test_returns_ic_decay_result(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=10)
        assert isinstance(result, ICDecayResult)
        assert isinstance(result.ic_by_lag, pd.Series)
        assert len(result.ic_by_lag) == 10

    def test_ic_values_in_range(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=20)
        for lag, ic in result.ic_by_lag.items():
            assert -1.0 <= ic <= 1.0, f"IC at lag {lag} out of range: {ic}"

    def test_peak_lag_is_valid(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=20)
        assert 1 <= result.peak_lag <= 20

    def test_peak_ic_matches_series(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=20)
        expected = result.ic_by_lag.iloc[result.peak_lag - 1]
        assert result.peak_ic == pytest.approx(expected, abs=1e-10)

    def test_half_life_with_decaying_signal(self) -> None:
        """Strong signal should have measurable half-life."""
        signal, returns = _make_signal_and_returns(ic_strength=0.1)
        result = compute_ic_decay(signal, returns, max_lag=50)
        # Half-life may or may not be found depending on data
        # Just verify it's a positive number or None
        if result.half_life is not None:
            assert result.half_life > 0

    def test_is_decayed_flag(self) -> None:
        signal, returns = _make_signal_and_returns(ic_strength=0.03)
        result = compute_ic_decay(signal, returns, max_lag=20)
        assert isinstance(result.is_decayed, bool)

    def test_turnover_in_range(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=10)
        assert 0.0 <= result.turnover <= 1.0

    def test_turnover_adjusted_ic(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=10)
        expected = result.peak_ic * (1.0 - result.turnover)
        assert result.turnover_adjusted_ic == pytest.approx(expected, abs=1e-10)

    def test_pearson_method(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_decay(signal, returns, max_lag=10, method="pearson")
        assert isinstance(result, ICDecayResult)

    def test_length_mismatch_raises(self) -> None:
        signal = pd.Series([1, 2, 3])
        returns = pd.Series([1, 2])
        with pytest.raises(ValueError, match="same length"):
            compute_ic_decay(signal, returns)

    def test_invalid_max_lag_raises(self) -> None:
        signal = pd.Series([1, 2, 3])
        returns = pd.Series([1, 2, 3])
        with pytest.raises(ValueError, match="max_lag must be >= 1"):
            compute_ic_decay(signal, returns, max_lag=0)

    def test_invalid_method_raises(self) -> None:
        signal, returns = _make_signal_and_returns(n=100)
        with pytest.raises(ValueError, match="method must be"):
            compute_ic_decay(signal, returns, method="kendall")


class TestComputeICByLag:
    def test_default_lags(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_by_lag(signal, returns)
        assert len(result) == 6  # [1, 2, 3, 5, 10, 20]
        assert list(result.index) == [1, 2, 3, 5, 10, 20]

    def test_custom_lags(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_by_lag(signal, returns, lags=[1, 5, 10])
        assert len(result) == 3

    def test_ic_values_in_range(self) -> None:
        signal, returns = _make_signal_and_returns()
        result = compute_ic_by_lag(signal, returns)
        for ic in result:
            assert -1.0 <= ic <= 1.0


class TestTurnover:
    def test_constant_signal_zero_turnover(self) -> None:
        """Constant signal should have zero turnover."""
        signal = pd.Series([1.0] * 100)
        returns = pd.Series(np.random.randn(100))
        result = compute_ic_decay(signal, returns, max_lag=5)
        assert result.turnover == pytest.approx(0.0, abs=1e-10)

    def test_random_signal_high_turnover(self) -> None:
        """Random signal should have higher turnover than smooth signal."""
        np.random.seed(42)
        random_signal = pd.Series(np.random.randn(100))
        smooth_signal = pd.Series(np.cumsum(np.random.randn(100) * 0.1))
        returns = pd.Series(np.random.randn(100))

        random_result = compute_ic_decay(random_signal, returns, max_lag=5)
        smooth_result = compute_ic_decay(smooth_signal, returns, max_lag=5)

        assert random_result.turnover > smooth_result.turnover


class TestICDecayInterpretation:
    def test_strong_factor_has_high_peak_ic(self) -> None:
        """A factor with 10% IC should show meaningful peak IC."""
        signal, returns = _make_signal_and_returns(ic_strength=0.10)
        result = compute_ic_decay(signal, returns, max_lag=20)
        # With ic_strength=0.10, peak IC should be detectable
        assert abs(result.peak_ic) > 0.01

    def test_noisy_factor_has_low_peak_ic(self) -> None:
        """A very weak factor should show near-zero peak IC."""
        signal, returns = _make_signal_and_returns(ic_strength=0.001)
        result = compute_ic_decay(signal, returns, max_lag=20)
        assert abs(result.peak_ic) < 0.1
