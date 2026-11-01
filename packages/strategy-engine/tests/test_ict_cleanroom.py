"""Behaviour locks for the ICT detector cleanroom rewrite (REWRITE_SPEC_ICT.md).

Every test here is written against the SPEC's semantics, not against any
third-party implementation. Each one is expected to be RED on the pre-rewrite
module and GREEN afterwards:

- sweep fires at the default period (it was mathematically dead for period>=3)
- detectors carry direction (+1 bullish / -1 bearish), not a bare 0/1
- OB uses the open-to-wick boundary
- MarketStructure is causal (no future swings)
- warm-up rows are NaN, per the Indicator ABC
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from selene_space_strategy.indicators.ict import (
    FVG,
    LiquiditySweepDetector,
    MarketStructure,
    OrderBlockDetector,
)


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


def _frame(highs, lows, opens=None, closes=None, volumes=None) -> pd.DataFrame:
    """OHLCV frame; opens/closes default to values inside each bar's range."""
    if opens is None:
        opens = [(h + lo) / 2 for h, lo in zip(highs, lows)]
    if closes is None:
        closes = [(h + lo) / 2 for h, lo in zip(highs, lows)]
    data = {"open": opens, "high": highs, "low": lows, "close": closes}
    if volumes is not None:
        data["volume"] = volumes
    return pd.DataFrame(data)


def _stop_hunt_frame(n: int = 60, spike_at: int = 40) -> pd.DataFrame:
    """Textbook sell-side raid: grind up, spike the high on volume, collapse.

    The spike pierces the prior swing high on heavy volume and the following
    bars break below the prior swing low — the canonical bearish sweep.
    """
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    price = 100.0
    for i in range(n):
        if i < spike_at:
            price += 0.5
            highs.append(price + 0.4)
            lows.append(price - 0.4)
            volumes.append(100.0)
        elif i == spike_at:
            highs.append(price + 3.0)
            lows.append(price - 0.2)
            volumes.append(800.0)
        else:
            price -= 1.2 if i < spike_at + 5 else 0.1
            highs.append(price + 0.3)
            lows.append(price - 0.3)
            volumes.append(100.0)
    closes = [(h + lo) / 2 for h, lo in zip(highs, lows)]
    return _frame(highs, lows, closes=closes, volumes=volumes)


# ---------------------------------------------------------------------------
# 1-2. LiquiditySweepDetector — the dead-for-period>=3 bug and direction
# ---------------------------------------------------------------------------


class TestLiquiditySweep:
    def test_sweep_fires_at_default_period(self) -> None:
        """RED before rewrite: the centred window contained bar i, so no sweep
        could ever fire for period >= 3 (default is 20)."""
        result = LiquiditySweepDetector(period=20).calculate(_stop_hunt_frame())
        assert result.sum() != 0, "sweep detector produced no signal at period=20"

    def test_sweep_is_bearish_on_a_high_raid(self) -> None:
        """Raiding the highs then collapsing is a BEARISH sweep: -1."""
        result = LiquiditySweepDetector(period=20).calculate(_stop_hunt_frame())
        fired = result[result.notna() & (result != 0)]
        assert len(fired) > 0
        assert set(fired.unique()) == {-1}

    def test_sweep_is_bullish_on_a_low_raid(self) -> None:
        """Mirror image: raid the lows on volume, then reclaim -> +1."""
        df = _stop_hunt_frame()
        mirrored = _frame(
            highs=(-df["low"]).tolist(),
            lows=(-df["high"]).tolist(),
            closes=(-df["close"]).tolist(),
            volumes=df["volume"].tolist(),
        )
        result = LiquiditySweepDetector(period=20).calculate(mirrored)
        fired = result[result.notna() & (result != 0)]
        assert len(fired) > 0
        assert set(fired.unique()) == {1}

    def test_no_sweep_without_volume(self) -> None:
        """The raid shape with flat volume must not qualify."""
        df = _stop_hunt_frame()
        df["volume"] = 100.0
        result = LiquiditySweepDetector(period=20).calculate(df)
        fired = result[result.notna() & (result != 0)]
        assert len(fired) == 0


# ---------------------------------------------------------------------------
# 3-4. FVG — direction and incremental semantics
# ---------------------------------------------------------------------------


class TestFVGDirection:
    def test_bullish_gap_is_plus_one(self) -> None:
        df = _frame(highs=[10, 20, 50], lows=[5, 15, 45])
        result = FVG(period=3, min_gap_atr=0.1).calculate(df)
        assert result.iloc[2] == 1

    def test_bearish_gap_is_minus_one(self) -> None:
        df = _frame(highs=[50, 30, 10], lows=[45, 25, 5])
        result = FVG(period=3, min_gap_atr=0.1).calculate(df)
        assert result.iloc[2] == -1

    def test_gap_is_incremental_not_cumulative(self) -> None:
        """One gap then overlapping bars: the signal must not persist."""
        highs = [10, 20, 50, 51, 52, 53]
        lows = [5, 15, 45, 20, 50, 51]
        result = FVG(period=3, min_gap_atr=0.1).calculate(_frame(highs, lows))
        assert result.iloc[2] == 1
        assert (result.iloc[3:] == 0).all()


# ---------------------------------------------------------------------------
# 5-6. OrderBlockDetector — direction and the open-to-wick boundary
# ---------------------------------------------------------------------------


class TestOrderBlock:
    def test_upward_displacement_is_plus_one(self) -> None:
        closes = [100.0] * 20
        opens = [99.0] * 20
        opens[5], closes[5] = 105.0, 100.0  # opposing (down) candle
        opens[6], closes[6] = 101.0, 120.0  # bullish displacement
        highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
        result = OrderBlockDetector(period=5, move_threshold=2.0).calculate(
            _frame(highs, lows, opens, closes)
        )
        assert result.iloc[6] == 1

    def test_downward_displacement_is_minus_one(self) -> None:
        closes = [100.0] * 20
        opens = [101.0] * 20
        opens[5], closes[5] = 100.0, 105.0  # opposing (up) candle
        opens[6], closes[6] = 104.0, 80.0  # bearish displacement
        highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
        result = OrderBlockDetector(period=5, move_threshold=2.0).calculate(
            _frame(highs, lows, opens, closes)
        )
        assert result.iloc[6] == -1

    def test_bullish_block_uses_open_to_wick_boundary(self) -> None:
        """Distal = low of the opposing candle, proximal = its open."""
        closes = [100.0] * 20
        opens = [99.0] * 20
        opens[5], closes[5] = 105.0, 100.0
        opens[6], closes[6] = 101.0, 120.0
        highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
        detector = OrderBlockDetector(period=5, move_threshold=2.0)
        detector.calculate(_frame(highs, lows, opens, closes))

        blocks = detector.get_active_blocks(current_price=110.0, max_age=100)
        bullish = [b for b in blocks if b.type == "bullish"]
        assert len(bullish) == 1
        block = bullish[0]
        assert block.type == "bullish"
        assert block.upper == pytest.approx(opens[5])
        assert block.lower == pytest.approx(lows[5])


# ---------------------------------------------------------------------------
# 7. MarketStructure — causality
# ---------------------------------------------------------------------------


class TestMarketStructureCausality:
    def test_labels_are_not_degenerate(self) -> None:
        """Guard against the 'all zeros passes trivially' trap."""
        rng = np.random.default_rng(7)
        price = 100 + np.cumsum(rng.normal(0, 1.0, 200))
        highs = price + rng.uniform(0.1, 1.0, 200)
        lows = price - rng.uniform(0.1, 1.0, 200)
        result = MarketStructure(period=3).calculate(_frame(highs, lows))
        assert (
            result.std() > 0
        ), "structure labels are constant; causality test would be vacuous"

    def test_label_at_i_equals_prefix_only_computation(self) -> None:
        """The definitive lookahead lock.

        What a detector may report at bar ``i`` is whatever it could compute
        from ``df[:i+1]`` alone. Recomputing on that prefix must reproduce the
        full-series label at ``i``; any disagreement means bar ``i`` consumed
        bars it could not have seen yet.
        """
        rng = np.random.default_rng(11)
        price = 100 + np.cumsum(rng.normal(0, 1.0, 160))
        highs = price + rng.uniform(0.1, 1.0, 160)
        lows = price - rng.uniform(0.1, 1.0, 160)
        frame = _frame(highs, lows)
        period = 3

        full = MarketStructure(period=period).calculate(frame)
        assert full.std() > 0, "degenerate labels would make this test vacuous"

        mismatches = []
        for i in range(4 * period, len(frame)):
            prefix = MarketStructure(period=period).calculate(frame.iloc[: i + 1])
            if not _same(prefix.iloc[i], full.iloc[i]):
                mismatches.append((i, prefix.iloc[i], full.iloc[i]))

        assert (
            not mismatches
        ), f"{len(mismatches)} bars depend on future data, e.g. {mismatches[:5]}"


def _same(a: object, b: object) -> bool:
    """Equality that treats NaN as equal to NaN."""
    a_nan = a is None or (isinstance(a, float) and np.isnan(a))
    b_nan = b is None or (isinstance(b, float) and np.isnan(b))
    if a_nan or b_nan:
        return a_nan and b_nan
    return bool(a == b)


# ---------------------------------------------------------------------------
# 8. NaN warm-up contract (Indicator ABC: NaN, not half-fed estimates)
# ---------------------------------------------------------------------------


class TestWarmupContract:
    def test_fvg_warmup_is_nan(self) -> None:
        df = _frame([10, 20, 50, 51, 52], [5, 15, 45, 20, 50])
        result = FVG(period=3, min_gap_atr=0.1).calculate(df)
        assert result.iloc[:2].isna().all()

    def test_order_block_warmup_is_nan(self) -> None:
        n = 10
        closes = [100.0] * n
        opens = [99.0] * n
        highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
        result = OrderBlockDetector(period=5, move_threshold=2.0).calculate(
            _frame(highs, lows, opens, closes)
        )
        assert result.iloc[:5].isna().all()

    def test_market_structure_warmup_is_nan(self) -> None:
        rng = np.random.default_rng(3)
        price = 100 + np.cumsum(rng.normal(0, 1.0, 60))
        highs = price + 1.0
        lows = price - 1.0
        period = 5
        result = MarketStructure(period=period).calculate(_frame(highs, lows))
        assert result.iloc[: 2 * period].isna().all()

    def test_sweep_tail_is_zero_not_nan(self) -> None:
        """The trailing ``reversal_bars`` bars must be 0, never NaN.

        ``StrategyRuntime.on_kline`` reads only the newest row of each
        indicator column and returns early if it is NaN. A NaN tail would
        therefore make the sweep column NaN on *every* bar the runtime ever
        inspects, killing the template in production even though the detector
        computes real signals. The tail is "evaluated, no signal" — 0.
        """
        result = LiquiditySweepDetector(period=20, reversal_bars=3).calculate(
            _stop_hunt_frame()
        )
        assert (
            result.iloc[-3:].notna().all()
        ), "NaN tail makes the detector invisible to the runtime"
        assert (result.iloc[-3:] == 0).all()

    def test_sweep_signal_reaches_the_runtime(self) -> None:
        """End-to-end lock for the production path: the newest row must carry
        the signal value on the confirmation bar, so ``on_kline`` can see it."""
        from selene_space_strategy.compiler import compile as compile_strategy
        from selene_space_strategy.models import load_strategy
        from selene_space_strategy.runtime import (
            StrategyRuntime,
            _attach_indicator_columns,
            _indicator_frame,
        )
        from selene_space_types.market import KlineEvent

        template = (
            Path(__file__).parent.parent
            / "selene_space_strategy"
            / "templates"
            / "ict_sweep.yaml"
        )
        compiled = compile_strategy(load_strategy(template))
        column = next(iter(compiled.indicators))
        runtime = StrategyRuntime(compiled)

        frame = _stop_hunt_frame()
        newest_values: list[float] = []
        for i in range(len(frame)):
            row = frame.iloc[i]
            event = KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 4 * 3600_000,
                interval="4h",
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                closed=True,
            )
            runtime.on_kline(event)
            bars = runtime._states["BTC/USDT"].bars
            if len(bars) < 2:
                continue
            attached = _attach_indicator_columns(
                _indicator_frame(bars), compiled.indicators
            )
            newest_values.append(attached[column].iloc[-1])

        tail = newest_values[-1]
        assert not np.isnan(
            tail
        ), "newest indicator row is NaN: on_kline would never fire"
        assert (
            -1.0 in newest_values
        ), "the confirmed sweep never surfaced on the newest row"


# ---------------------------------------------------------------------------
# 9. Lifecycle age budgets count detections, not bar positions
# ---------------------------------------------------------------------------


class TestLifecycleAge:
    def test_fvg_max_age_counts_detections_not_bars(self) -> None:
        """A ``max_age`` budget must shrink with the number of *newer zones*,
        not with elapsed bar index.

        ``bar_index`` is an absolute row position while ``max_age`` is a count
        of detections. Measuring one against the other yields a negative
        "age" whenever gaps are sparse (few detections spread over many bars),
        which silently disables the budget and returns ancient zones forever.
        """
        highs: list[float] = []
        lows: list[float] = []
        volumes: list[float] = []
        price = 100.0
        # Sparse gaps: alternating quiet drift and a wide bar that opens a gap.
        for k in range(6):
            for _ in range(10):
                highs.append(price + 0.2)
                lows.append(price - 0.2)
                volumes.append(100.0)
                price += 0.1
            highs.append(price + 6.0)  # wide bar -> gap vs bar two back
            lows.append(price + 1.0)
            volumes.append(100.0)
            price += 2.0

        df = _frame(highs, lows, volumes=volumes)
        detector = FVG(period=3, min_gap_atr=0.1)
        detector.calculate(df)
        assert len(detector._zones) >= 3, "fixture must create several zones"

        # Probe above every zone so nothing is marked filled: only the age
        # budget can exclude anything, isolating the behaviour under test.
        probe = max(z.upper for z in detector._zones) + 10.0

        generous = detector.get_active_zones(probe, max_age=10_000)
        assert len(generous) == len(detector._zones)

        tight = detector.get_active_zones(probe, max_age=1)
        assert len(tight) == 2, "max_age=1 must keep only the two newest zones"
        assert tight == detector._zones[-2:]

    def test_order_block_max_age_counts_detections_not_bars(self) -> None:
        """Same budget semantics for order blocks."""
        closes = [100.0] * 60
        opens = [99.0] * 60
        # Three well-separated displacements, each preceded by an opposing bar.
        for centre in (20, 35, 50):
            opens[centre - 1], closes[centre - 1] = 103.0, 100.0
            opens[centre], closes[centre] = 101.0, 118.0
            opens[centre + 1], closes[centre + 1] = 117.0, 116.0
        highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 1 for o, c in zip(opens, closes)]

        detector = OrderBlockDetector(period=5, move_threshold=2.0)
        detector.calculate(_frame(highs, lows, opens, closes))
        assert len(detector._blocks) >= 3, "fixture must create several blocks"

        oldest = detector._blocks[0]
        probe = (oldest.lower + oldest.upper) / 2
        generous = detector.get_active_blocks(probe, max_age=10_000)
        assert len(generous) == len(detector._blocks)

        tight = detector.get_active_blocks(probe, max_age=1)
        assert len(tight) == 2
        assert tight == detector._blocks[-2:]
