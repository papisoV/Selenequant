"""Tests for liquidity-aware stop-hunt avoidance in TieredTrailingStopTracker.

The liquidity nudge is opt-in: ``on_tick(..., liquidity=None)`` (default)
must behave byte-for-byte like the legacy pure peak-pct tracker. When a
LiquidityContext is injected, a candidate stop landing just beneath an
HVN or round-number level is pushed to the pocket's lower edge.
"""

from __future__ import annotations

import pytest

from selene_space_paper.tiered_trailing_stop import (
    LiquidityContext,
    Tier,
    TieredTrailingStopTracker,
)


@pytest.fixture()
def tracker_with_entry() -> TieredTrailingStopTracker:
    tracker = TieredTrailingStopTracker()
    tracker.on_fill("BTC/USDT", 0.1, 100.0)
    tracker.on_tick("BTC/USDT", 110.0, 0.1)  # +10% → T3_TRAIL
    return tracker


def _t3_stop(tracker: TieredTrailingStopTracker) -> float:
    status = tracker.status("BTC/USDT")
    assert status is not None
    return float(status["stop_level"])


class TestBackwardCompat:
    def test_liquidity_none_matches_legacy(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """liquidity=None → stop identical to the un-injected call."""
        tracker = tracker_with_entry
        legacy = _t3_stop(tracker)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=None)
        assert _t3_stop(tracker) == legacy

    def test_empty_context_is_noop(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """Empty hvn + round_step=0 → no nudge even when injected."""
        tracker = tracker_with_entry
        legacy = _t3_stop(tracker)
        empty = LiquidityContext()
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=empty)
        assert _t3_stop(tracker) == legacy

    def test_zero_buffer_is_noop(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """atr=0 + min_buffer_pct=0 → zero-width pockets, no nudge."""
        tracker = tracker_with_entry
        legacy = _t3_stop(tracker)
        flat = LiquidityContext(hvn_prices=(105.0,), atr=0.0, min_buffer_pct=0.0)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=flat)
        assert _t3_stop(tracker) == legacy

    def test_addon_fill_resets_effective_stop(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """Add-on re-anchors entry/peak — any stale effective_stop from
        before the fill must not leak into status()."""
        tracker = tracker_with_entry  # peak 110, effective_stop 88.0
        tracker.on_fill("BTC/USDT", 0.2, 108.0)  # add-on → re-anchor at 108
        status = tracker.status("BTC/USDT")
        assert status is not None
        # Fresh re-anchored state: stop must be recomputed (108*0.98 hard
        # floor at Tier.NONE), not the stale 88.0.
        assert status["stop_level"] == pytest.approx(108.0 * (1.0 - 0.02))


class TestAvoidance:
    def test_hvn_pocket_nudge(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """T3 stop = 110*(1-0.20) = 88.0; HVN at 90 with buffer 0.25*atr=2.5.

        Pocket [87.5, 90] contains 88.0 → stop drops to 87.5.
        """
        tracker = tracker_with_entry
        liq = LiquidityContext(hvn_prices=(90.0,), atr=10.0, buffer_atr=0.25)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=liq)
        assert _t3_stop(tracker) == pytest.approx(87.5)

    def test_round_number_pocket_nudge(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """Stop 88.0 with round_step=10 → nearest level 90, pocket [89, 90].

        88.0 is NOT in that pocket → unchanged (round level below pocket
        floor must not be hit by 88.0).
        """
        tracker = tracker_with_entry
        liq = LiquidityContext(round_step=10.0, atr=4.0, buffer_atr=0.25)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=liq)
        # level 90, width = max(1.0, 0.09) = 1.0 → pocket (89, 90]; 88 outside.
        assert _t3_stop(tracker) == pytest.approx(88.0)

        # Now a stop that DOES sit in the pocket: raise peak to 112.5 →
        # T3 stop = 112.5*0.80 = 90.0 exactly → pocket lower edge 89.0.
        tracker.on_tick("BTC/USDT", 112.5, 0.1, liquidity=liq)
        assert _t3_stop(tracker) == pytest.approx(89.0)

    def test_no_conflict_unchanged(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """Stop far from any zone → unchanged."""
        tracker = tracker_with_entry
        liq = LiquidityContext(hvn_prices=(70.0,), round_step=100.0, atr=5.0)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=liq)
        assert _t3_stop(tracker) == pytest.approx(88.0)

    def test_multiple_hvns_nearest_lower_edge(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """Two pockets: sweep must land clear of both.

        Stop 88.0; HVN 90 (width 2.5) and HVN 86 (width max(2.5, 0.086)).
        88 in [87.5, 90] → drop to 87.5; 87.5 in [83.5, 86]? No → done.
        """
        tracker = tracker_with_entry
        liq = LiquidityContext(hvn_prices=(90.0, 86.0), atr=10.0)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=liq)
        assert _t3_stop(tracker) == pytest.approx(87.5)

    def test_cascade_sweep_lands_clear(self) -> None:
        """Overlapping pockets cascade until the stop is clear of all."""
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 125.0, 0.1)  # +25% → T3, stop = 100.0
        liq = LiquidityContext(hvn_prices=(100.0, 97.0), atr=4.0, buffer_atr=0.5)
        tracker.on_tick("BTC/USDT", 125.0, 0.1, liquidity=liq)
        # 100 in (98, 100] → 98; 98 in (95, 97]? No → 98.
        assert _t3_stop(tracker) == pytest.approx(98.0)

    def test_nudge_never_above_raw_stop(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """The nudge only ever moves the stop down, never up."""
        tracker = tracker_with_entry
        raw = _t3_stop(tracker)
        liq = LiquidityContext(hvn_prices=(200.0,), atr=10.0)
        tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=liq)
        assert _t3_stop(tracker) == pytest.approx(raw)

    def test_tier_ratchet_unaffected(
        self, tracker_with_entry: TieredTrailingStopTracker
    ) -> None:
        """Liquidity injection must not alter tier promotion."""
        tracker = tracker_with_entry
        liq = LiquidityContext(hvn_prices=(90.0,), atr=10.0)
        state = tracker.on_tick("BTC/USDT", 110.0, 0.1, liquidity=liq)
        assert state is not None
        assert state.current_tier == Tier.T3_TRAIL

    def test_frozen_context(self) -> None:
        from dataclasses import FrozenInstanceError

        liq = LiquidityContext()
        with pytest.raises(FrozenInstanceError):
            liq.atr = 5.0  # type: ignore[misc]
