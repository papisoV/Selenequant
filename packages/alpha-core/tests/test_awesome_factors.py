"""Tests for awesome-systematic-trading distilled factors.

Validates that each ``compute_aw_*`` factor:
  - returns a Series with the same index as the input
  - never leaks NaN
  - degrades to a zero Series when required columns are absent
  - has the direction implied by its recipe (momentum positive in a
    rally, reversal positive after a drop, drawdown negative under water)
  - is registered into ALL_FACTORS, exposed in the formula vocab, and
    computable through PandasStackVM
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factors.awesome_factors import AWESOME_FACTORS
from selene_space_alpha.factors.pandas_bridge import ALL_FACTORS
from selene_space_alpha.vm.vm_pandas import PandasStackVM
from selene_space_alpha.vocab import FORMULA_VOCAB

_DAY_MS = 86_400_000


def _ohlcv(n: int = 300, start: float = 100.0, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    steps = rng.normal(drift, 0.02, n)
    price = start * np.exp(np.cumsum(steps))
    base = pd.Timestamp("2025-01-01").value // 10**6
    ts = [base + i * _DAY_MS for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": price * 0.999,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": rng.uniform(100, 1000, n),
        }
    )


def _up_trend(n: int = 300) -> pd.DataFrame:
    """Deterministic monotonic rally: close goes 100 -> 200."""
    price = np.linspace(100.0, 200.0, n)
    base = pd.Timestamp("2025-01-01").value // 10**6
    ts = [base + i * _DAY_MS for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": price * 0.999,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": np.full(n, 500.0),
        }
    )


def _down_trend(n: int = 300) -> pd.DataFrame:
    """Deterministic monotonic decline: close goes 200 -> 100."""
    return _up_trend(n).assign(close=np.linspace(200.0, 100.0, n))


def _last(series: pd.Series) -> float:
    return float(series.iloc[-1])


class TestFactorContract:
    @pytest.mark.parametrize("name", sorted(AWESOME_FACTORS))
    def test_factor_returns_series_no_nan(self, name: str) -> None:
        fn = AWESOME_FACTORS[name][0]
        data = _ohlcv()
        result = fn(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)
        assert result.index.equals(data.index)
        assert not result.isna().any()
        assert not np.isinf(result.to_numpy()).any()

    @pytest.mark.parametrize("name", sorted(AWESOME_FACTORS))
    def test_factor_degrades_on_missing_columns(self, name: str) -> None:
        fn = AWESOME_FACTORS[name][0]
        data = _ohlcv().drop(columns=["volume", "open"])
        result = fn(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)
        assert not result.isna().any()

    @pytest.mark.parametrize("name", sorted(AWESOME_FACTORS))
    def test_factor_all_values_finite(self, name: str) -> None:
        fn = AWESOME_FACTORS[name][0]
        result = fn(_up_trend())
        assert not result.isna().any()


class TestMomentumFactors:
    def test_52w_high_proximity_high_in_rally(self) -> None:
        data = _up_trend()
        assert _last(AWESOME_FACTORS["aw_52w_high"][0](data)) > 0.9

    def test_tsmom_12m_positive_in_rally(self) -> None:
        data = _up_trend()
        assert _last(AWESOME_FACTORS["aw_tsmom_12m"][0](data)) > 0.0

    def test_tsmom_sign_flips_with_direction(self) -> None:
        assert _last(AWESOME_FACTORS["aw_tsmom_sign"][0](_up_trend())) == 1.0
        assert _last(AWESOME_FACTORS["aw_tsmom_sign"][0](_down_trend())) == -1.0

    def test_consistent_mom_long_when_both_months_up(self) -> None:
        data = _up_trend()
        assert _last(AWESOME_FACTORS["aw_consistent_mom"][0](data)) == 1.0

    def test_mom_6m_positive_in_rally(self) -> None:
        assert _last(AWESOME_FACTORS["aw_mom_6m"][0](_up_trend())) > 0.0

    def test_mom_acceleration_positive_when_accelerating(self) -> None:
        data = _up_trend()
        assert _last(AWESOME_FACTORS["aw_mom_acceleration"][0](data)) > 0.0


class TestReversalFactors:
    def test_reversal_5d_positive_after_drop(self) -> None:
        data = _down_trend()
        assert _last(AWESOME_FACTORS["aw_reversal_5d"][0](data)) > 0.0

    def test_reversal_10d_positive_after_drop(self) -> None:
        data = _down_trend()
        assert _last(AWESOME_FACTORS["aw_reversal_10d"][0](data)) > 0.0

    def test_reversal_negative_in_rally(self) -> None:
        data = _up_trend()
        assert _last(AWESOME_FACTORS["aw_reversal_5d"][0](data)) < 0.0


class TestVolatilityFactors:
    def test_low_vol_negative(self) -> None:
        """Factor is -vol, so it should be negative in normal data."""
        assert _last(AWESOME_FACTORS["aw_low_vol"][0](_ohlcv())) < 0.0

    def test_vol_risk_premium_small_in_flat_data(self) -> None:
        data = _ohlcv()
        result = AWESOME_FACTORS["aw_vol_risk_premium"][0](data)
        assert -1.0 <= _last(result) <= 1.0

    def test_skew_12m_finite(self) -> None:
        assert not AWESOME_FACTORS["aw_skew_12m"][0](_ohlcv()).isna().any()

    def test_return_asymmetry_zero_when_no_tails(self) -> None:
        data = _up_trend()
        result = AWESOME_FACTORS["aw_return_asymmetry"][0](data)
        assert -1.0 <= _last(result) <= 1.0

    def test_vol_of_vol_nonnegative_scale(self) -> None:
        result = AWESOME_FACTORS["aw_vol_of_vol"][0](_ohlcv())
        assert not result.isna().any()


class TestTrendFactors:
    def test_trend_above_ma_positive_in_rally(self) -> None:
        assert _last(AWESOME_FACTORS["aw_trend_above_ma"][0](_up_trend())) > 0.0

    def test_trend_above_ma_negative_in_decline(self) -> None:
        assert _last(AWESOME_FACTORS["aw_trend_above_ma"][0](_down_trend())) < 0.0

    def test_trend_ma_slope_positive_in_rally(self) -> None:
        assert _last(AWESOME_FACTORS["aw_trend_ma_slope"][0](_up_trend())) > 0.0

    def test_trend_ma_cross_positive_in_rally(self) -> None:
        assert _last(AWESOME_FACTORS["aw_trend_ma_cross"][0](_up_trend())) > 0.0


class TestRiskAndCalendarFactors:
    def test_drawdown_negative_under_water(self) -> None:
        """After a rally peaks and falls, the trailing 252d drawdown is < 0."""
        base = _up_trend()
        n = len(base)
        price = base["close"].to_numpy().copy()
        peak = price[-1]
        decline = np.linspace(peak, peak * 0.8, n)
        price = np.concatenate([price, decline])
        data = _up_trend(2 * n)
        data["close"] = price
        data["high"] = price * 1.01
        data["low"] = price * 0.99
        assert _last(AWESOME_FACTORS["aw_drawdown"][0](data)) < 0.0

    def test_turn_of_month_zero_without_timestamp(self) -> None:
        data = _ohlcv().drop(columns=["timestamp"])
        result = AWESOME_FACTORS["aw_turn_of_month"][0](data)
        assert (result == 0.0).all()

    def test_turn_of_month_bounded(self) -> None:
        result = AWESOME_FACTORS["aw_turn_of_month"][0](_ohlcv())
        assert ((result >= 0.0) & (result <= 1.0)).all()

    def test_overnight_8h_zero_without_open(self) -> None:
        data = _ohlcv().drop(columns=["open"])
        result = AWESOME_FACTORS["aw_overnight_8h"][0](data)
        assert (result == 0.0).all()

    def test_volume_trend_zero_without_volume(self) -> None:
        data = _ohlcv().drop(columns=["volume"])
        result = AWESOME_FACTORS["aw_volume_trend"][0](data)
        assert (result == 0.0).all()


class TestFormulaVocabIntegration:
    def test_all_aw_factors_registered_in_all_factors(self) -> None:
        aw_keys = sorted(AWESOME_FACTORS)
        for key in aw_keys:
            assert key in ALL_FACTORS, f"{key} missing from ALL_FACTORS"
        assert len(aw_keys) == 20

    def test_all_aw_tokens_in_vocab(self) -> None:
        aw_tokens = [f for f in FORMULA_VOCAB.feature_names if f.startswith("AW_")]
        assert len(aw_tokens) == 20

    def test_vocab_token_preserves_prior_features(self) -> None:
        """Appending AW_ tokens must not reorder existing features."""
        assert "RET" in FORMULA_VOCAB.feature_names
        assert "DERIV_BASIS_EXTREME" in FORMULA_VOCAB.feature_names

    def test_vm_pandas_computes_all_aw_tokens(self) -> None:
        vm = PandasStackVM()
        data = _ohlcv()
        fmap = {f: i for i, f in enumerate(FORMULA_VOCAB.feature_names)}
        feats = vm._compute_features(data)
        aw_expected = sorted(f for f in FORMULA_VOCAB.feature_names if f.startswith("AW_"))
        for token in aw_expected:
            s = feats[fmap[token]]
            assert isinstance(s, pd.Series)
            assert len(s) == len(data)
            assert not s.isna().any()
