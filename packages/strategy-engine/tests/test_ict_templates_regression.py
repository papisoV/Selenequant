"""Regression tests for the 3 ICT YAML templates (directional rewrite).

The templates must compile, run through the runtime, and actually produce a
signal on purpose-built data: each entry leg is direction-explicit
(``greater_than 0`` / ``less_than 0`` against the +1/-1 detector output), so
a template only fires when every leg of its rule set holds on the same bar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, overload

import numpy as np

from selene_space_strategy.compiler import compile
from selene_space_strategy.models import load_strategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "selene_space_strategy" / "templates"


def _kline(bar: tuple[float, float, float, float, float], i: int) -> KlineEvent:
    o, h, low, c, v = bar
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=i * 4 * 3600_000,
        interval="4h",
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
        closed=True,
    )


def _run(
    template: str, bars: list[tuple[float, float, float, float, float]]
) -> list[tuple[int, str, str]]:
    """Feed the bars to a fresh runtime, return (bar, side, reason) per signal."""
    runtime = StrategyRuntime(compile(load_strategy(_templates_dir() / template)))
    signals: list[tuple[int, str, str]] = []
    for i, bar in enumerate(bars):
        sig = runtime.on_kline(_kline(bar, i))
        if sig is not None:
            signals.append((i, str(sig.side), sig.reason))
    return signals


class TestICTFVGTemplate:
    def test_fires_long_on_a_bullish_gap(self) -> None:
        """Quiet drift, then one bar whose low gaps far above the high two
        bars back: the ict_fvg long leg (fvg > 0) must produce a BUY."""
        bars: list[tuple[float, float, float, float, float]] = []
        price = 50.0
        for _ in range(20):
            bars.append((price - 0.2, price + 0.2, price - 0.5, price + 0.1, 100.0))
            price += 0.3
        # Gap bar: low sits well above the high of two bars ago.
        bars.append((price + 1.0, price + 2.0, price + 0.8, price + 1.5, 100.0))
        price += 1.5
        for _ in range(19):
            bars.append((price - 0.2, price + 0.2, price - 0.5, price + 0.1, 100.0))
            price += 0.3

        signals = _run("ict_fvg.yaml", bars)
        entries = [s for s in signals if "entry" in s[2]]
        assert entries, "ict_fvg template produced no entry on a bullish gap"
        assert all("BUY" in side for _, side, _ in entries)

    def test_short_leg_is_met_on_a_bearish_gap(self) -> None:
        """Mirror of the long case: a gap down must satisfy the short entry
        rules. (The runtime defaults to LONG_ONLY for templates without a
        ``direction`` field, so the SELL order itself is gated; here we
        assert the short leg conditions genuinely hold on the gap bar.)"""
        bars: list[tuple[float, float, float, float, float]] = []
        price = 50.0
        for _ in range(20):
            bars.append((price - 0.2, price + 0.2, price - 0.5, price + 0.1, 100.0))
            price += 0.3
        # Bearish gap bar: high sits well below the low of two bars ago.
        bars.append((price - 3.5, price - 2.8, price - 4.0, price - 3.0, 100.0))
        price -= 1.5
        for _ in range(19):
            bars.append((price - 0.2, price + 0.2, price - 0.5, price + 0.1, 100.0))
            price += 0.3

        compiled = compile(load_strategy(_templates_dir() / "ict_fvg.yaml"))
        runtime = StrategyRuntime(compiled)
        for i, bar in enumerate(bars):
            runtime.on_kline(_kline(bar, i))
            if i == 20:
                frame = runtime._states["BTC/USDT"].bars
                from selene_space_strategy.runtime import (
                    _attach_indicator_columns,
                    _indicator_frame,
                )

                df = _attach_indicator_columns(
                    _indicator_frame(frame), compiled.indicators
                )
                assert all(
                    runtime._evaluate_condition(df, cond)
                    for cond in compiled.short_entry_conditions
                ), "short entry conditions not met on the bearish gap bar"


class TestICTSweepTemplate:
    def test_fires_long_on_a_confirmed_low_raid(self) -> None:
        """Two consecutive volume-spike raids of the range lows followed by a
        reclaim: the ict_sweep long leg (sweep > 0, structure >= 0) must fire
        a BUY on the second confirmation bar."""
        o, h, low, c, v = [], [], [], [], []
        price = 100.0
        for _ in range(30):  # flat range
            h.append(price + 0.4)
            low.append(price - 0.4)
            c.append(price + 0.1)
            v.append(100.0)
            o.append(price)
            price += 0.1
        for _ in range(2):  # two consecutive raids on heavy volume
            h.append(price + 0.2)
            low.append(price - 6.0)
            c.append(price - 0.5)
            v.append(600.0)
            o.append(price)
            price -= 0.1
        for _ in range(3):  # fast reclaim
            h.append(price + 0.8)
            low.append(price - 0.2)
            c.append(price + 0.6)
            v.append(100.0)
            o.append(price)
            price += 1.0
        for _ in range(20):  # drift away
            h.append(price + 0.4)
            low.append(price - 0.4)
            c.append(price + 0.1)
            v.append(100.0)
            o.append(price)
            price += 0.1

        bars = list(zip(o, h, low, c, v))
        signals = _run("ict_sweep.yaml", bars)
        entries = [s for s in signals if "entry" in s[2]]
        assert entries, "ict_sweep template produced no entry on a confirmed low raid"
        assert all("BUY" in side for _, side, _ in entries)


class TestICTOBTemplate:
    def test_fires_long_on_displacement_with_gap_in_uptrend(self) -> None:
        """An up-displacement that also opens a bullish FVG while market
        structure is +1: the ict_ob long leg (ob > 0, fvg > 0, ms > 0) must
        fire a BUY on the displacement bar."""
        rng = np.random.default_rng(0)
        n = 200
        price = 100 + np.cumsum(rng.normal(0, 0.5, n))
        highs = price + rng.uniform(0.1, 1.0, n)
        lows = price - rng.uniform(0.1, 1.0, n)
        opens = (highs + lows) / 2
        closes = opens.copy()

        # Seed 0's random walk carries a sustained market_structure +1 run
        # around bar 74 (verified while constructing this fixture).
        t = 74
        opens[t - 1] = closes[t - 1] + 1.0  # opposing down candle before the impulse
        opens[t] = closes[t - 1] + 2.0
        closes[t] = closes[t - 1] + 12.0  # displacement >= 2x average range
        highs[t] = closes[t] + 0.5
        lows[t] = highs[t - 2] + 3.0  # bullish gap over the high two bars back
        highs[t - 1] = max(highs[t - 1], opens[t - 1]) + 0.2
        lows[t - 1] = min(lows[t - 1], closes[t - 1]) - 0.2

        bars = [
            (float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i]), 100.0)
            for i in range(n)
        ]
        signals = _run("ict_ob.yaml", bars)
        entries = [s for s in signals if "entry" in s[2]]
        assert (
            entries
        ), "ict_ob template produced no entry on displacement + gap + uptrend"
        assert all("BUY" in side for _, side, _ in entries)


class TestTemplatesCompile:
    def test_all_three_templates_load_and_compile(self) -> None:
        for name in ("ict_fvg.yaml", "ict_ob.yaml", "ict_sweep.yaml"):
            config = load_strategy(_templates_dir() / name)
            compiled = compile(config)
            assert compiled.long_entry_conditions or compiled.short_entry_conditions

    def test_fvg_template_has_no_order_block_condition(self) -> None:
        """The FVG∧OB cross-detector intersection is conceptually empty: the
        confirmation rule was removed and must stay removed."""
        config = load_strategy(_templates_dir() / "ict_fvg.yaml")
        compiled = compile(config)
        for condition in (
            compiled.long_entry_conditions + compiled.short_entry_conditions
        ):
            assert "order_block" not in condition.column_name

    def test_fvg_template_legs_are_direction_opposed(self) -> None:
        """Long and short legs must be genuinely different rules."""
        config = load_strategy(_templates_dir() / "ict_fvg.yaml")
        compiled = compile(config)
        long_ops = [c.operator for c in compiled.long_entry_conditions]
        short_ops = [c.operator for c in compiled.short_entry_conditions]
        assert long_ops == ["greater_than"]
        assert short_ops == ["less_than"]


def _premium_bars() -> list[tuple[float, float, float, float, float]]:
    """Bars forming a zigzag that produces confirmed fractal swings.

    A monotone leg produces no swing at all: :class:`MarketStructure` needs a
    bar whose high (or low) exceeds the ``period`` bars on *both* sides, and on
    a straight line only the endpoints qualify — too few to build a range. So
    oscillate around slow waves, with wave amplitude well above the per-bar
    noise, and let the price traverse the full height of each range.
    """
    import math

    bars: list[tuple[float, float, float, float, float]] = []
    for i in range(300):
        mid = 100.0 + 18.0 * math.sin(i / 14.0) + 8.0 * math.sin(i / 5.0)
        nxt = 100.0 + 18.0 * math.sin((i + 1) / 14.0) + 8.0 * math.sin((i + 1) / 5.0)
        high = max(mid, nxt) + 0.25
        low = min(mid, nxt) - 0.25
        bars.append((mid, high, low, nxt, 100.0))
    return bars


@overload
def _drive(
    runtime: StrategyRuntime,
    bars: list[tuple[float, float, float, float, float]],
    start_ms: int,
    step_ms: int,
    *,
    holds: Literal[False] = False,
) -> tuple[set[str], int]: ...


@overload
def _drive(
    runtime: StrategyRuntime,
    bars: list[tuple[float, float, float, float, float]],
    start_ms: int,
    step_ms: int,
    *,
    holds: Literal[True],
) -> tuple[set[str], int, list[int]]: ...


def _drive(
    runtime: StrategyRuntime,
    bars: list[tuple[float, float, float, float, float]],
    start_ms: int,
    step_ms: int,
    *,
    holds: bool = False,
) -> tuple[set[str], int] | tuple[set[str], int, list[int]]:
    """Feed ``bars`` through the runtime and summarize what it did.

    Returns the set of order sides seen, the number of opening signals, and
    (only when ``holds`` is set) how many bars each completed round trip ran
    for. Bracelet-triggered exits count as exits, not as a second entry.
    """
    sides: set[str] = set()
    entries = 0
    open_at: int | None = None
    lengths: list[int] = []

    for i, (o, h, low, c, v) in enumerate(bars):
        signal = runtime.on_kline(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=start_ms + i * step_ms,
                interval="1h",
                open=o,
                high=h,
                low=low,
                close=c,
                volume=v,
                closed=True,
            )
        )
        if signal is None:
            continue
        opening = "entry conditions met" in signal.reason
        if opening:
            entries += 1
            sides.add(signal.side.value)
            open_at = i
        elif open_at is not None:
            lengths.append(i - open_at)
            open_at = None

    if holds:
        return sides, entries, lengths
    return sides, entries


class TestICTKillzoneDiscountTemplate:
    def test_compiles_and_trades_both_legs(self) -> None:
        """The template must compile, trade, and reach *both* legs.

        A template that compiles but never fires is indistinguishable from a
        deleted one, and a short leg the runtime silently gates off is dead
        configuration. ``direction: neutral`` in the YAML is what makes the
        short side reachable — the runtime default is long_only.
        """
        template = _templates_dir() / "ict_killzone_discount.yaml"
        config = load_strategy(template)
        compiled = compile(config)
        assert compiled.long_entry_conditions
        assert compiled.short_entry_conditions
        assert len(compiled.long_entry_conditions) == 2
        assert config.direction.value == "neutral"

        # 1h bars starting at 00:00 UTC so London (02:00-05:00 at offset -5,
        # i.e. 07:00-10:00 UTC) is reachable within the sample.
        runtime = StrategyRuntime(compiled, direction=config.direction)
        sides, entries = _drive(runtime, _premium_bars(), 1_735_689_600_000, 3_600_000)
        assert entries > 0, "killzone-discount template never traded on a trending leg"
        assert sides == {"buy", "sell"}, f"only one leg traded: {sides}"


class TestICTPremiumDiscountTemplate:
    def test_both_legs_reach_both_zones(self) -> None:
        template = _templates_dir() / "ict_premium_discount.yaml"
        config = load_strategy(template)
        compiled = compile(config)
        runtime = StrategyRuntime(compiled, direction=config.direction)

        from selene_space_strategy.runtime import (
            _attach_indicator_columns,
            _indicator_frame,
        )

        bars = _premium_bars()
        seen_zone: set[float] = set()
        for i, (o, h, low, c, v) in enumerate(bars):
            runtime.on_kline(
                KlineEvent(
                    exchange="binance",
                    symbol="BTC/USDT",
                    timestamp=1_735_689_600_000 + i * 3_600_000,
                    interval="1h",
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=v,
                    closed=True,
                )
            )
            bucket = runtime._states["BTC/USDT"]
            if len(bucket.bars) < 2:
                continue
            df = _attach_indicator_columns(
                _indicator_frame(bucket.bars), compiled.indicators
            )
            for column in compiled.indicators:
                value = df[column].iloc[-1]
                if value == value:  # not NaN
                    seen_zone.add(float(value))

        assert 1.0 in seen_zone, "discount third never reached"
        assert -1.0 in seen_zone, "premium third never reached"

    def test_exit_does_not_overlap_the_entry(self) -> None:
        """Regression: ``close_long >= 0`` fired the bar right after entry.

        ``premium_discount`` is -1/0/+1 only, so a ``>= 0`` exit is satisfied
        by equilibrium — one bar after a discount entry price ticked back
        through the mean. Measured on these bars, the overlapping rules closed
        98% of trades in a single bar; a real exit condition leaves most of
        them running.
        """
        template = _templates_dir() / "ict_premium_discount.yaml"
        config = load_strategy(template)
        runtime = StrategyRuntime(compile(config), direction=config.direction)
        sides, entries, holds = _drive(
            runtime, _premium_bars(), 1_735_689_600_000, 3_600_000, holds=True
        )
        assert entries > 0
        assert sides == {"buy", "sell"}, f"only one leg traded: {sides}"
        assert holds, "no round trip completed"
        one_bar = sum(1 for h in holds if h == 1)
        assert one_bar / len(holds) < 0.5, (
            f"{one_bar}/{len(holds)} trades closed on the next bar — the exit "
            "condition overlaps the entry again"
        )
