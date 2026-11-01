"""Equivalence + effectiveness comparison for the liquidity-aware stop.

Same price series driven through the tracker twice:
  - A-path: liquidity=None  (legacy pure peak-pct behavior)
  - B-path: LiquidityContext injected (stop-hunt avoidance)

Equivalence asserts the None path is untouched by the feature.
Effectiveness asserts the injected path actually lowers the stop when it
would land in a stop-hunt pocket.
"""

from __future__ import annotations

import pytest

from selene_space_paper.tiered_trailing_stop import (
    LiquidityContext,
    TieredTrailingStopTracker,
)

SYMBOL = "BTC/USDT"


def _drive(
    prices: list[float],
    liquidity: LiquidityContext | None,
) -> list[dict]:
    """Replay the price series through a fresh tracker; return status snapshots."""
    tracker = TieredTrailingStopTracker()
    tracker.on_fill(SYMBOL, 0.1, prices[0])
    frames = []
    for price in prices:
        tracker.on_tick(SYMBOL, price, 0.1, liquidity=liquidity)
        status = tracker.status(SYMBOL)
        if status is not None:
            frames.append(
                {
                    "price": price,
                    "peak": status["peak_price"],
                    "tier": status["current_tier"],
                    "stop": status["stop_level"],
                    "closed": status["closed"],
                }
            )
    return frames


def _series() -> list[float]:
    """Entry 100 → rally to 120 (T3, raw stop 96) → dip → recover."""
    return [100.0, 105.0, 112.0, 120.0, 118.0, 116.0, 121.0, 119.0, 117.0]


class TestEquivalence:
    def test_none_path_identical_across_runs(self) -> None:
        """liquidity=None replay equals a second None replay (determinism)."""
        a = _drive(_series(), None)
        b = _drive(_series(), None)
        assert a == b

    def test_empty_context_path_equals_none(self) -> None:
        """Empty LiquidityContext (no zones) behaves exactly like None."""
        none_frames = _drive(_series(), None)
        empty = LiquidityContext(hvn_prices=(), round_step=0.0)
        empty_frames = _drive(_series(), empty)
        assert none_frames == empty_frames


class TestEffectiveness:
    def test_hvn_pocket_lowers_stop(self) -> None:
        """Rally peak 120 → raw T3 stop 96.0. HVN at 100 with atr=8,
        buffer 0.5*8=4 → pocket (96, 100]. Raw stop 96 is NOT inside
        (96 exclusive); after peak 121 → raw stop 96.8 sits inside the
        pocket → nudged to 96.0."""
        liq = LiquidityContext(hvn_prices=(100.0,), atr=8.0, buffer_atr=0.5)
        frames = _drive([100.0, 112.0, 120.0, 121.0], liq)
        raw_frames = _drive([100.0, 112.0, 120.0, 121.0], None)
        # At peak 121: raw = 96.8 (in pocket), effective = 96.0.
        assert raw_frames[-1]["stop"] == pytest.approx(121.0 * 0.80)
        assert frames[-1]["stop"] == pytest.approx(96.0)
        assert frames[-1]["stop"] < raw_frames[-1]["stop"]

    def test_closed_signal_identical_when_no_zone_conflict(self) -> None:
        """Without pocket conflicts, injected path closes identically."""
        liq = LiquidityContext(hvn_prices=(50.0,), round_step=10.0, atr=1.0)
        a = _drive([100.0, 112.0, 89.0, 88.0], None)
        b = _drive([100.0, 112.0, 89.0, 88.0], liq)
        assert [f["closed"] for f in a] == [f["closed"] for f in b]
        assert a[-1]["closed"] is True  # sanity: dip below T3 stop (89.6) closes
