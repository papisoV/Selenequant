"""Tests for ICT indicators (FVG, OrderBlockDetector, LiquiditySweepDetector).

Verifies the incremental (non-cumulative) signal contract — the fix for the
'exists operator always true after first trigger' bug — plus the directional
encoding (+1 bullish / -1 bearish) and the NaN warm-up policy required by the
Indicator ABC.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.ict import (
    FVG,
    LiquiditySweepDetector,
    OrderBlockDetector,
)


def _df(highs, lows, opens=None, closes=None, volumes=None):
    """Build a DataFrame with OHLCV columns."""
    if opens is None:
        opens = [h - 1 for h in highs]
    if closes is None:
        closes = [h - 0.5 for h in highs]
    data = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
    }
    if volumes is not None:
        data["volume"] = volumes
    return pd.DataFrame(data)


class TestFVGIncremental:
    def test_fvg_bullish_incremental(self) -> None:
        """3 bars: K1.high=10, K3.low=50 → bullish FVG (K1.high < K3.low).
        result.iloc[2] should be 1 (new zone at bar 2), result.iloc[0:2] should be 0.
        """
        highs = [10, 20, 50]
        lows = [5, 15, 45]
        df = _df(highs, lows)
        fvg = FVG(period=3, min_gap_atr=0.1)
        result = fvg.calculate(df)
        assert result.iloc[2] == 1
        # Warm-up rows are NaN per the Indicator contract, not 0.
        assert result.iloc[:2].isna().all()

    def test_fvg_no_false_positive(self) -> None:
        """No gap (consecutive bars where K1.high >= K3.low and K1.low <= K3.high) → result all 0.
        Bars overlap: bar i's high is below bar i+2's low would be a gap.
        Use bars where K1.high >= K3.low (no bullish gap) and K1.low <= K3.high (no bearish gap).
        """
        # Monotonically increasing with overlap: each bar's range overlaps the next
        highs = [11, 12, 13, 14, 15]
        lows = [9, 10, 11, 12, 13]
        # At i=2: k1=bar0 (high=11, low=9), k3=bar2 (high=13, low=11)
        # k1_high(11) < k3_low(11)? No (11 is not < 11) → no bullish FVG
        # k1_low(9) > k3_high(13)? No → no bearish FVG
        df = _df(highs, lows)
        fvg = FVG(period=3, min_gap_atr=0.1)
        result = fvg.calculate(df)
        assert (result.dropna() == 0).all()

    def test_fvg_bearish_incremental(self) -> None:
        """Bearish FVG: K1.low > K3.high (gap down).
        K1: high=50, low=45. K2: high=30, low=25. K3: high=10, low=5.
        K1.low(45) > K3.high(10) → bearish FVG at bar 2 → -1.
        """
        highs = [50, 30, 10]
        lows = [45, 25, 5]
        df = _df(highs, lows)
        fvg = FVG(period=3, min_gap_atr=0.1)
        result = fvg.calculate(df)
        assert result.iloc[2] == -1

    def test_fvg_subsequent_bars_zero(self) -> None:
        """After a bullish FVG at bar 2, bars 3+ with no new gap should be 0.
        This is the core fix: previously result was cumulative count, now incremental.
        Use overlapping bars after the gap so no new FVG forms.
        """
        # Bar 2 has bullish FVG (K0.high=10 < K2.low=50)
        # Bars 3,4,5: make K(i-2).high >= K(i).low so no new bullish FVG
        # Bar 3: K1=bar1 (high=20), K3=bar3 (low must be <= 20)
        # Bar 4: K2=bar2 (high=50), K4=bar4 (low must be <= 50)
        # Bar 5: K3=bar3 (high=51), K5=bar5 (low must be <= 51)
        highs = [10, 20, 50, 51, 52, 53]
        lows = [5, 15, 45, 20, 50, 51]  # overlapping lows to prevent new bullish FVG
        df = _df(highs, lows)
        fvg = FVG(period=3, min_gap_atr=0.1)
        result = fvg.calculate(df)
        assert result.iloc[2] == 1
        # Subsequent bars: no new gap (overlap prevents it)
        assert result.iloc[3] == 0
        assert result.iloc[4] == 0
        assert result.iloc[5] == 0


class TestOrderBlockIncremental:
    def test_ob_incremental_signal(self) -> None:
        """Down candle at j, then strong up move at i → OB detected at bar i.
        result.iloc[i] == 1 (incremental), not cumulative count.
        """
        # 20 bars: bar 5 is down candle, bar 6 has strong up move
        closes = [100.0] * 20
        opens = [99.0] * 20
        # Make bar 5 a down candle
        opens[5] = 105
        closes[5] = 100
        # Make bar 6 a strong up move (close >> prev_close)
        closes[6] = 120
        opens[6] = 101
        highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
        df = _df(highs, lows, opens, closes)
        ob = OrderBlockDetector(period=5, move_threshold=2.0)
        result = ob.calculate(df)
        # Bar 6 should have OB signal (incremental = 1)
        assert result.iloc[6] == 1
        # Bar 5 should not have signal
        assert result.iloc[5] == 0

    def test_ob_no_signal_without_move(self) -> None:
        """No significant move → result all 0."""
        closes = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0, 103.5]
        opens = [99.5, 100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 103.0]
        highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
        df = _df(highs, lows, opens, closes)
        ob = OrderBlockDetector(period=5, move_threshold=2.0)
        result = ob.calculate(df)
        assert (result.dropna() == 0).all()


class TestLiquiditySweepIncremental:
    def test_sweep_incremental_signal(self) -> None:
        """A textbook stop-hunt must actually produce a sweep signal.

        The old version of this test only asserted "every value is 0 or 1",
        which the all-zero series satisfies trivially — it stayed green even
        while the detector was mathematically dead for period >= 3. The raid
        below pierces the range low on 5x volume and reclaims within the
        confirmation window, so exactly one non-zero bar is expected.
        """
        n = 60
        highs = [100.5] * n
        lows = [99.5] * n
        closes = [100.0] * n
        volumes = [100.0] * n
        # Raid the lows on heavy volume at bar 40, then reclaim above the
        # prior 20-bar high within the 3-bar confirmation window.
        lows[40] = 90.0
        volumes[40] = 500.0
        for k in range(1, 4):
            highs[40 + k] = 103.0 + k
            lows[40 + k] = 101.0
            closes[40 + k] = 102.0 + k

        df = _df(highs, lows, closes=closes, volumes=volumes)
        sweep = LiquiditySweepDetector(period=20, volume_threshold=1.5, reversal_bars=3)
        result = sweep.calculate(df)

        assert result.sum() > 0, "textbook low raid produced no sweep signal"
        assert result.dropna().isin([0, 1, -1]).all()
        fired = result.dropna()
        fired = fired[fired != 0]
        assert len(fired) == 1
        assert fired.iloc[0] == 1.0

    def test_sweep_no_signal_without_volume(self) -> None:
        """No volume spike → result all 0 (after warm-up)."""
        n = 25
        closes = [100.0] * n
        highs = [101.0] * n
        lows = [99.0] * n
        volumes = [100] * n  # constant volume, no spike
        lows[5] = 90
        lows[20] = 88
        df = _df(highs, lows, closes=closes, volumes=volumes)
        sweep = LiquiditySweepDetector(period=5, volume_threshold=1.5, reversal_bars=3)
        result = sweep.calculate(df)
        assert (result.dropna() == 0).all()
