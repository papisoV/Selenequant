"""US-004: PPO / CHOP / Efficiency Ratio / Divergence indicator tests.

Each indicator must:
- subclass Indicator (base.py L10) and implement ``calculate`` returning a
  Series aligned with the input index
- be registered in IndicatorRegistry via the register API (registry.py L27)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from selene_space_strategy.indicators.chop import ChoppinessIndex
from selene_space_strategy.indicators.divergence import Divergence
from selene_space_strategy.indicators.efficiency_ratio import EfficiencyRatio
from selene_space_strategy.indicators.ppo import PPO, PPOResult
from selene_space_strategy.indicators.registry import IndicatorRegistry, indicator_registry


class TestPPO:
    """PPO = percentage normalization of the MACD fast/slow spread."""

    def test_calculation_matches_definition(self) -> None:
        close = pd.Series(np.arange(100.0, 200.0, 1.0))
        df = pd.DataFrame({"close": close})

        ppo = PPO(period=10, fast_period=4, signal_period=3).calculate(df)

        fast = close.ewm(span=4, adjust=False).mean()
        slow = close.ewm(span=10, adjust=False).mean()
        expected = (fast - slow) / slow * 100.0

        # Implementation masks the first period-1 values as NaN; compare the
        # computed tail against the raw formula.
        pd.testing.assert_series_equal(ppo.iloc[9:], expected.iloc[9:], check_names=False)
        assert ppo.iloc[:9].isna().all()

    def test_is_macd_division_not_subtraction(self) -> None:
        """PPO must equal MACD's subtraction normalized by slow EMA (% formula)."""
        from selene_space_strategy.indicators.macd import MACD

        close = pd.Series(np.arange(50.0, 200.0, 2.0))
        df = pd.DataFrame({"close": close})

        ppo = PPO(period=8, fast_period=3, signal_period=2).calculate(df)
        macd = MACD(period=8, fast_period=3, signal_period=2).calculate(df)

        # ppo == macd_line / slow_ema * 100 — the same subtraction, but divided.
        expected = macd / (close.ewm(span=8, adjust=False).mean()) * 100.0
        pd.testing.assert_series_equal(ppo, expected, check_names=False, check_index_type=False)

        mid = len(close) // 2
        assert ppo.iloc[mid] > 0.0  # same sign as raw MACD for a rising market

    def test_full_result(self) -> None:
        df = pd.DataFrame({"close": np.arange(100.0, 200.0, 1.0)})
        res = PPO(period=10, fast_period=4, signal_period=3).calculate_full(df)
        assert isinstance(res, PPOResult)
        assert res.ppo_line.equals(res.histogram + res.signal_line)

    def test_aligned_index_and_nan_warmup(self) -> None:
        idx = pd.RangeIndex(37)
        df = pd.DataFrame({"close": np.arange(100.0, 137.0, 1.0)}, index=idx)
        period = 10
        ppo = PPO(period=period, fast_period=4, signal_period=3).calculate(df)
        assert ppo.index.equals(idx)
        assert ppo.iloc[: period - 1].isna().all()
        assert ppo.iloc[period - 1 :].notna().all()

    def test_param_validation(self) -> None:
        with pytest.raises(ValueError, match="fast_period"):
            PPO(period=10, fast_period=0)
        with pytest.raises(ValueError, match="fast_period.*less than slow"):
            PPO(period=4, fast_period=10)
        with pytest.raises(ValueError, match="signal_period"):
            PPO(period=10, signal_period=0)

    def test_name(self) -> None:
        assert PPO().name == "ppo"


class TestChoppinessIndex:
    """CHOP < 38.2 -> trending; CHOP > 61.8 -> ranging/choppy."""

    def test_trending_is_low(self) -> None:
        # Steep, clean uptrend: big range relative to true-range path.
        n = 60
        c = 100.0 + 3.0 * np.arange(n)
        df = pd.DataFrame(
            {"high": c + 1.0, "low": c - 1.0, "close": c}
        )
        chop = ChoppinessIndex(period=14).calculate(df)
        tail = chop.dropna()
        assert (tail > 0).all() and (tail <= 100).all()
        assert tail.iloc[-1] < ChoppinessIndex.TREND_THRESHOLD  # 38.2

    def test_choppy_is_high(self) -> None:
        # Tight oscillation: small range but heavy churn -> high choppiness.
        n = 60
        c = 100.0 + (np.arange(n) % 4 - 1.5) * 2.0
        df = pd.DataFrame(
            {"high": c + 0.5, "low": c - 0.5, "close": c}
        )
        chop = ChoppinessIndex(period=14).calculate(df)
        tail = chop.dropna()
        assert tail.iloc[-1] > ChoppinessIndex.CHOPPY_THRESHOLD  # 61.8

    def test_known_values(self) -> None:
        # period=3 hand-computed: at i=2 ratio=8/6 -> CHOP ~26.19.
        df = pd.DataFrame(
            {
                "high": [10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
                "low": [8.0, 10.0, 12.0, 14.0, 16.0, 18.0],
                "close": [9.0, 11.0, 13.0, 15.0, 17.0, 19.0],
            }
        )
        chop = ChoppinessIndex(period=3).calculate(df)
        expected = 100.0 * math.log10(8.0 / 6.0) / math.log10(3.0)
        assert chop.iloc[2] == pytest.approx(expected, rel=1e-9)
        assert chop.iloc[:2].isna().all()

    def test_aligned_index(self) -> None:
        df = pd.DataFrame(
            {
                "high": np.arange(10.0, 40.0),
                "low": np.arange(8.0, 38.0),
                "close": np.arange(9.0, 39.0),
            }
        )
        chop = ChoppinessIndex(period=10).calculate(df)
        assert len(chop) == len(df)
        assert chop.iloc[:9].isna().all()

    def test_flat_market_is_nan_not_inf(self) -> None:
        df = pd.DataFrame({"high": [10.0] * 20, "low": [10.0] * 20, "close": [10.0] * 20})
        chop = ChoppinessIndex(period=5).calculate(df)
        assert np.isfinite(chop.dropna()).all()

    def test_name(self) -> None:
        assert ChoppinessIndex().name == "chop"


class TestEfficiencyRatio:
    """ER = net displacement / total path length, in [0, 1]."""

    def test_known_values(self) -> None:
        close = pd.Series([10.0, 20.0, 19.0, 30.0, 29.0, 40.0])
        df = pd.DataFrame({"close": close})
        er = EfficiencyRatio(period=3).calculate(df)
        # i=3: |30-10| / (|20-10| + |19-20| + |30-19|) = 20/22
        assert er.iloc[3] == pytest.approx(20.0 / 22.0, rel=1e-9)
        assert er.iloc[:3].isna().all()

    def test_straight_line_near_one(self) -> None:
        c = 100.0 + 5.0 * np.arange(40)
        df = pd.DataFrame({"close": c})
        er = EfficiencyRatio(period=14).calculate(df)
        assert er.dropna().iloc[-1] == pytest.approx(1.0, abs=1e-9)

    def test_oscillation_near_zero(self) -> None:
        c = 100.0 + (np.arange(40) % 2) * 4.0
        df = pd.DataFrame({"close": c})
        er = EfficiencyRatio(period=14).calculate(df)
        assert er.dropna().iloc[-1] <= 0.5

    def test_aligned_index_and_bounds(self) -> None:
        df = pd.DataFrame({"close": np.arange(50.0, 100.0, 1.0)})
        er = EfficiencyRatio(period=14).calculate(df)
        assert len(er) == len(df)
        valid = er.dropna()
        assert ((valid >= 0.0) & (valid <= 1.0)).all()

    def test_name(self) -> None:
        assert EfficiencyRatio().name == "efficiency_ratio"


BEARISH_PRICES = [80.0, 85.0, 90.0, 95.0, 110.0, 98.0, 96.0, 94.0, 92.0, 90.0, 120.0, 105.0, 100.0, 95.0, 90.0]
BEARISH_OSC = [50.0, 50.0, 50.0, 50.0, 80.0, 50.0, 50.0, 50.0, 50.0, 50.0, 60.0, 50.0, 50.0, 50.0, 50.0]
BULLISH_PRICES = [120.0, 115.0, 110.0, 105.0, 90.0, 97.0, 99.0, 101.0, 103.0, 105.0, 80.0, 95.0, 100.0, 105.0, 110.0]
BULLISH_OSC = [50.0, 50.0, 50.0, 50.0, 20.0, 50.0, 50.0, 50.0, 50.0, 50.0, 40.0, 50.0, 50.0, 50.0, 50.0]


def _div_df(prices: list[float], osc: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": prices, "oscillator": osc})


class TestDivergence:
    """State filter (osc>50 bearish / osc<50 bullish) + strength scoring."""

    def test_bearish_divergence_negative_strength(self) -> None:
        # Higher high (110 -> 120) with lower oscillator high (80 -> 60).
        scores = Divergence(period=14, pivot=2).calculate(_div_df(BEARISH_PRICES, BEARISH_OSC))
        assert scores.iloc[10] == pytest.approx(-0.2)  # -(80-60)/100
        assert scores.iloc[4] == 0.0
        assert (scores.iloc[5:10] == 0.0).all()  # no score between the pivots
        assert scores.iloc[:2].isna().all()

    def test_bullish_divergence_positive_strength(self) -> None:
        # Lower low (90 -> 80) with higher oscillator low (20 -> 40).
        scores = Divergence(period=14, pivot=2).calculate(_div_df(BULLISH_PRICES, BULLISH_OSC))
        assert scores.iloc[10] == pytest.approx(0.2)  # +(40-20)/100
        assert scores.iloc[4] == 0.0

    def test_state_filter_rejects_low_zone_bearish(self) -> None:
        # Same price shape, but both oscillator pivots < 50 -> filtered out.
        osc = [30.0, 30.0, 30.0, 30.0, 40.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0]
        scores = Divergence(period=14, pivot=2).calculate(_div_df(BEARISH_PRICES, osc))
        assert (scores.dropna() == 0.0).all()

    def test_strong_divergence_uses_min_strength(self) -> None:
        d = Divergence(period=14, pivot=2, min_strength=0.3)
        scores = d.calculate(_div_df(BEARISH_PRICES, BEARISH_OSC))
        # strength 0.2 < min 0.3 -> suppressed
        assert (scores.dropna() == 0.0).all()
        assert d.min_strength == 0.3

    def test_oscillator_defaults_to_rsi(self) -> None:
        df = pd.DataFrame({"close": np.arange(100.0, 150.0, 1.0)})
        scores = Divergence(period=14, pivot=2).calculate(df)
        assert len(scores) == len(df)
        assert scores.iloc[:2].isna().all()

    def test_param_validation(self) -> None:
        with pytest.raises(ValueError, match="pivot"):
            Divergence(pivot=0)
        with pytest.raises(ValueError, match="min_strength"):
            Divergence(min_strength=2.0)

    def test_name(self) -> None:
        assert Divergence().name == "divergence"


class TestRegistry:
    """All four P1 indicators registered via IndicatorRegistry.register."""

    def test_preregistered_in_default_registry(self) -> None:
        for name in ("ppo", "chop", "efficiency_ratio", "divergence"):
            assert indicator_registry.has(name), f"{name} not in default registry"
            cls = indicator_registry.get(name)
            assert issubclass(cls, object)

    def test_register_on_fresh_registry(self) -> None:
        reg = IndicatorRegistry()
        reg.register("ppo", PPO)
        reg.register("chop", ChoppinessIndex)
        reg.register("efficiency_ratio", EfficiencyRatio)
        reg.register("divergence", Divergence)
        assert sorted(reg.list())[-4:] == [
            "chop", "divergence", "efficiency_ratio", "ppo",
        ]
