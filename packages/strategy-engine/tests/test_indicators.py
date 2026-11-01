"""Indicator suite — ABC contract plus SMA/EMA/RSI/MACD numeric checks
against a fixed 30-point price path and hand-computed anchors."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_strategy.indicators.base import Indicator
from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.macd import MACD, MACDResult
from selene_space_strategy.indicators.rsi import RSI
from selene_space_strategy.indicators.sma import SMA

# Simulated BTC/USDT daily closes — noisy but trending upward.
CLOSE_PRICES = [
    100.0,
    102.0,
    101.0,
    103.0,
    105.0,
    104.0,
    106.0,
    108.0,
    107.0,
    109.0,
    111.0,
    110.0,
    112.0,
    114.0,
    113.0,
    115.0,
    117.0,
    116.0,
    118.0,
    120.0,
    119.0,
    121.0,
    123.0,
    122.0,
    124.0,
    126.0,
    125.0,
    127.0,
    129.0,
    128.0,
]


def _frame(values) -> pd.DataFrame:
    return pd.DataFrame({"close": list(values)})


@pytest.fixture
def price_df() -> pd.DataFrame:
    """Frame over the fixed close series."""
    return _frame(CLOSE_PRICES)


class TestIndicatorAbc:
    """Abstract-base contract."""

    def test_direct_construction_forbidden(self) -> None:
        with pytest.raises(TypeError):
            Indicator(period=10)  # type: ignore[abstract]

    def test_zero_and_negative_periods_refused(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            SMA(period=0)
        with pytest.raises(ValueError, match="positive"):
            SMA(period=-5)

    def test_repr_shows_type_and_period(self) -> None:
        text = repr(SMA(period=20))
        assert "SMA" in text and "20" in text

    def test_concrete_instance_identity(self) -> None:
        sma = SMA(period=10)
        assert isinstance(sma, Indicator)
        assert sma.name == "sma"
        assert sma.period == 10


class TestSmaBehaviour:
    """Simple moving average."""

    def test_warmup_and_two_anchor_values(self, price_df: pd.DataFrame) -> None:
        out = SMA(period=5).calculate(price_df)

        assert isinstance(out, pd.Series)
        assert len(out) == len(price_df)
        assert all(pd.isna(out.iloc[i]) for i in range(4))

        # mean(100, 102, 101, 103, 105)
        assert out.iloc[4] == pytest.approx(102.2, abs=0.01)
        # mean(104, 106, 108, 107, 109)
        assert out.iloc[9] == pytest.approx(106.8, abs=0.01)

    def test_indicator_name(self) -> None:
        assert SMA(period=20).name == "sma"


class TestEmaBehaviour:
    """Exponential moving average."""

    def test_warmup_then_first_value_present(self, price_df: pd.DataFrame) -> None:
        out = EMA(period=5).calculate(price_df)

        assert isinstance(out, pd.Series)
        assert all(pd.isna(out.iloc[i]) for i in range(4))
        assert not pd.isna(out.iloc[4])

    def test_flat_series_collapses_to_constant(self, price_df: pd.DataFrame) -> None:
        out = EMA(period=5).calculate(_frame([50.0] * 30))
        for val in out.iloc[4:]:
            assert val == pytest.approx(50.0, abs=0.01)

    def test_indicator_name(self) -> None:
        assert EMA(period=10).name == "ema"

    def test_outpaces_sma_when_rising(self, price_df: pd.DataFrame) -> None:
        sma = SMA(period=10).calculate(price_df)
        ema = EMA(period=10).calculate(price_df)
        # Recency weighting puts the EMA nearer the higher recent prints.
        for i in range(-5, 0):
            if not pd.isna(sma.iloc[i]) and not pd.isna(ema.iloc[i]):
                assert ema.iloc[i] >= sma.iloc[i] - 1


class TestRsiBehaviour:
    """Wilder-smoothed oscillator."""

    def test_period14_warmup_and_unit_bounds(self, price_df: pd.DataFrame) -> None:
        out = RSI(period=14).calculate(price_df)

        assert isinstance(out, pd.Series)
        assert all(pd.isna(out.iloc[i]) for i in range(14))
        valid = out.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_pure_uptrend_saturates_high(self) -> None:
        valid = RSI(period=14).calculate(_frame(range(1, 31))).dropna()
        assert (valid > 90).all()

    def test_pure_downtrend_saturates_low(self) -> None:
        valid = RSI(period=14).calculate(_frame(range(30, 0, -1))).dropna()
        assert (valid < 10).all()

    def test_ping_pong_series_hugs_midpoint(self) -> None:
        df = _frame(100.0 + (i % 2) for i in range(50))
        last = RSI(period=14).calculate(df).iloc[-1]
        assert 40 < last < 60

    def test_indicator_name(self) -> None:
        assert RSI(period=14).name == "rsi"

    def test_rising_market_reads_above_50(self, price_df: pd.DataFrame) -> None:
        valid = RSI(period=14).calculate(price_df).dropna()
        assert (valid > 50).all()


class TestMacdBehaviour:
    """MACD trio: line, signal, histogram."""

    def test_default_output_matches_input_length(self, price_df: pd.DataFrame) -> None:
        out = MACD().calculate(price_df)
        assert isinstance(out, pd.Series)
        assert len(out) == len(price_df)

    def test_full_result_aligns_three_series(self, price_df: pd.DataFrame) -> None:
        full = MACD().calculate_full(price_df)
        assert isinstance(full, MACDResult)
        assert len(full.macd_line) == len(price_df)
        assert len(full.signal_line) == len(price_df)
        assert len(full.histogram) == len(price_df)

    def test_histogram_is_line_minus_signal(self, price_df: pd.DataFrame) -> None:
        full = MACD().calculate_full(price_df)
        mask = full.macd_line.notna()
        expected = full.macd_line[mask] - full.signal_line[mask]
        np.testing.assert_array_almost_equal(
            full.histogram[mask].values, expected.values, decimal=10
        )

    def test_trending_market_drives_line_positive(self, price_df: pd.DataFrame) -> None:
        line = MACD(period=26, fast_period=12, signal_period=9).calculate_full(
            price_df
        ).macd_line.dropna()
        assert (line > 0).all() or (line.iloc[-1] > 0)

    def test_period_validation_rules(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            MACD(period=26, fast_period=0)
        with pytest.raises(ValueError, match="positive"):
            MACD(period=26, signal_period=-1)
        with pytest.raises(ValueError, match="less than"):
            MACD(period=12, fast_period=12)

    def test_indicator_name(self) -> None:
        assert MACD().name == "macd"

    def test_repr_embeds_each_period(self) -> None:
        text = repr(MACD(period=26, fast_period=12, signal_period=9))
        for token in ("12", "26", "9"):
            assert token in text

    def test_nonstandard_periods_still_compute(self, price_df: pd.DataFrame) -> None:
        full = MACD(period=20, fast_period=8, signal_period=5).calculate_full(price_df)
        assert full.macd_line.notna().sum() > 0


class TestHandComputedAnchors:
    """Values cross-checked against TradingView on fixed inputs."""

    def test_sma10_at_indices_9_and_19(self) -> None:
        out = SMA(period=10).calculate(_frame(CLOSE_PRICES))
        # index 9: mean of closes 0..9
        assert out.iloc[9] == pytest.approx(104.5, abs=0.01)
        # index 19: mean of closes 10..19
        assert out.iloc[19] == pytest.approx(114.6, abs=0.01)

    def test_rsi14_on_random_walk_stays_bounded(self) -> None:
        rng = np.random.default_rng(42)
        prices = 100 + np.cumsum(rng.standard_normal(100))
        valid = RSI(period=14).calculate(_frame(prices)).dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()
