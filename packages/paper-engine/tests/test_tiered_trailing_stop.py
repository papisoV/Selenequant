"""Tests for TieredTrailingStopTracker — four-tier ratcheting stop-loss."""

from __future__ import annotations

import time
from dataclasses import FrozenInstanceError

import pytest

from selene_space_paper.tiered_trailing_stop import (
    Tier,
    TieredTrailingConfig,
    TieredTrailingStopState,
    TieredTrailingStopTracker,
)
from selene_space_types.trading import OrderSide


class TestConfigAndStateImmutable:
    """[US-A1-03] frozen dataclasses."""

    def test_config_is_frozen(self) -> None:
        cfg = TieredTrailingConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.hard_stop_pct = 0.05  # type: ignore[misc]

    def test_state_is_frozen(self) -> None:
        st = TieredTrailingStopState(
            symbol="BTC/USDT",
            entry_price=100.0,
            peak_price=100.0,
            last_quantity=0.1,
        )
        with pytest.raises(FrozenInstanceError):
            st.peak_price = 105.0  # type: ignore[misc]


class TestOnFill:
    """[US-A1-02] on_fill method — fill-event driven, sync, no I/O."""

    def test_first_buy_initializes_state(self) -> None:
        tracker = TieredTrailingStopTracker()
        state = tracker.on_fill("BTC/USDT", 0.1, 100.0)
        assert state is not None
        assert state.symbol == "BTC/USDT"
        assert state.entry_price == 100.0
        assert state.peak_price == 100.0
        assert state.last_quantity == 0.1
        assert state.current_tier == Tier.NONE
        assert state.closed is False

    def test_addon_buy_resets_peak_and_tier(self) -> None:
        """design review fill_qty > last_quantity triggers re-anchor."""
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        # Move peak up to T3.
        tracker.on_tick("BTC/USDT", 110.0, 0.1)  # +10% → T3
        pre = tracker.status("BTC/USDT")
        assert pre is not None
        assert pre["current_tier"] == Tier.T3_TRAIL.value
        # Add-on fill at higher qty re-anchors entry+peak, resets tier.
        state = tracker.on_fill("BTC/USDT", 0.2, 108.0)
        assert state is not None
        assert state.entry_price == 108.0
        assert state.peak_price == 108.0
        assert state.last_quantity == 0.2
        assert state.current_tier == Tier.NONE

    def test_partial_close_does_not_reset_peak(self) -> None:
        """design review fill_qty <= last_quantity keeps peak/tier."""
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.2, 100.0)
        tracker.on_tick("BTC/USDT", 108.0, 0.2)  # +8% → T3
        # Partial close: qty 0.1 < 0.2.
        state = tracker.on_fill("BTC/USDT", 0.1, 105.0)
        assert state is not None
        assert state.peak_price == 108.0  # unchanged
        assert state.current_tier == Tier.T3_TRAIL  # unchanged
        assert state.last_quantity == 0.1
        assert state.entry_price == 100.0  # unchanged

    def test_fill_is_synchronous_under_1ms(self) -> None:
        """[US-A1-07] on_fill p99 < 1ms — no I/O, pure dict+dataclass."""
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        # Warm up.
        for _ in range(100):
            tracker.on_fill("BTC/USDT", 0.1, 101.0)
        # Measure.
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            tracker.on_fill("BTC/USDT", 0.1, 101.0)
        elapsed = time.perf_counter() - start
        p99 = elapsed / iterations  # avg as p99 proxy for sync no-io
        assert p99 < 1e-3  # < 1ms


class TestOnTick:
    """[US-A1-01] on_tick method — peak ratchet + tier ratchet + close."""

    def test_no_position_returns_none(self) -> None:
        tracker = TieredTrailingStopTracker()
        assert tracker.on_tick("BTC/USDT", 67000.0, 0.1) is None

    def test_zero_price_returns_none(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        assert tracker.on_tick("BTC/USDT", 0.0, 0.1) is None

    def test_zero_quantity_clears_state(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        assert tracker.on_tick("BTC/USDT", 105.0, 0.0) is None
        assert tracker.status("BTC/USDT") is None

    def test_peak_ratchets_up_only(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 105.0, 0.1)
        tracker.on_tick("BTC/USDT", 102.0, 0.1)  # dip does NOT lower peak
        st = tracker.status("BTC/USDT")
        assert st is not None
        assert st["peak_price"] == 105.0

    def test_entry_price_unchanged_after_tick(self) -> None:
        """design review replace does not mutate entry_price."""
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 110.0, 0.1)
        tracker.on_tick("BTC/USDT", 95.0, 0.1)
        st = tracker.status("BTC/USDT")
        assert st is not None
        assert st["entry_price"] == 100.0


class TestTierRatchet:
    """design review T1 → T2 → T3 ratchet, only ascends."""

    def test_t1_t2_t3_progression(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        # +1.5% → T1.
        st = tracker.on_tick("BTC/USDT", 101.5, 0.1)
        assert st is not None and st.current_tier == Tier.T1_PROTECT
        # +3.0% → T2.
        st = tracker.on_tick("BTC/USDT", 103.0, 0.1)
        assert st is not None and st.current_tier == Tier.T2_LOCK
        # +6.0% → T3.
        st = tracker.on_tick("BTC/USDT", 106.0, 0.1)
        assert st is not None and st.current_tier == Tier.T3_TRAIL

    def test_tier_does_not_downgrade_on_dip(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 106.0, 0.1)  # T3
        # Pull back but still profitable enough to keep T3.
        st = tracker.on_tick("BTC/USDT", 104.0, 0.1)
        assert st is not None
        # Tier must stay T3 even though profit dropped to T2 range.
        assert st.current_tier == Tier.T3_TRAIL


class TestStopLevelAndClose:
    """design review T1/T2/T3 stop levels + hard-stop floor."""

    def test_t1_stop_level(self) -> None:
        cfg = TieredTrailingConfig(
            low_tier_threshold=0.015,
            low_tier_drop_pct=0.5,
        )
        tracker = TieredTrailingStopTracker(cfg)
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 101.5, 0.1)  # T1
        # Stop should be peak * (1 - 0.5) = 101.5 * 0.5 = 50.75.
        st = tracker.status("BTC/USDT")
        assert st is not None
        assert abs(st["stop_level"] - 50.75) < 1e-6

    def test_t2_stop_level(self) -> None:
        cfg = TieredTrailingConfig(
            first_tier_threshold=0.03,
            first_tier_drop_pct=0.30,
        )
        tracker = TieredTrailingStopTracker(cfg)
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 103.0, 0.1)  # T2
        # Stop = 103.0 * (1 - 0.30) = 72.1.
        st = tracker.status("BTC/USDT")
        assert st is not None
        assert abs(st["stop_level"] - 72.1) < 1e-6

    def test_t3_stop_level(self) -> None:
        cfg = TieredTrailingConfig(
            second_tier_threshold=0.06,
            second_tier_drop_pct=0.20,
        )
        tracker = TieredTrailingStopTracker(cfg)
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 106.0, 0.1)  # T3
        # Stop = 106.0 * (1 - 0.20) = 84.8.
        st = tracker.status("BTC/USDT")
        assert st is not None
        assert abs(st["stop_level"] - 84.8) < 1e-6

    def test_hard_stop_closes_below_floor(self) -> None:
        """profit_pct <= -hard_stop_pct closes position."""
        cfg = TieredTrailingConfig(hard_stop_pct=0.02)
        tracker = TieredTrailingStopTracker(cfg)
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        # Drop 3% → -3% <= -2% → closed.
        st = tracker.on_tick("BTC/USDT", 97.0, 0.1)
        assert st is not None
        assert st.closed is True

    def test_t3_breach_closes(self) -> None:
        """In T3, price below T3 stop level triggers closed."""
        cfg = TieredTrailingConfig(
            second_tier_threshold=0.06,
            second_tier_drop_pct=0.20,
        )
        tracker = TieredTrailingStopTracker(cfg)
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 106.0, 0.1)  # T3, stop = 84.8
        # Drop below 84.8.
        st = tracker.on_tick("BTC/USDT", 84.0, 0.1)
        assert st is not None
        assert st.closed is True


class TestBuildSellSignal:
    def test_emits_sell_on_closed_state(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        st = tracker.on_tick("BTC/USDT", 97.0, 0.1)  # hard stop
        assert st is not None and st.closed
        sig = tracker.build_sell_signal(st, position_quantity=0.1, now_epoch=1000.0)
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert sig.quantity == 0.1
        assert "tiered_trailing_stop" in sig.reason

    def test_no_sell_on_open_state(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        st = tracker.on_tick("BTC/USDT", 101.0, 0.1)
        assert st is not None and not st.closed
        assert tracker.build_sell_signal(st, position_quantity=0.1) is None

    def test_emit_throttle_prevents_spam(self) -> None:
        tracker = TieredTrailingStopTracker(
            TieredTrailingConfig(min_emit_interval_seconds=10.0)
        )
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_tick("BTC/USDT", 97.0, 0.1, now_epoch=1000.0)
        # status returns a dict, so re-fetch state via internal dict.
        state = tracker._peaks["BTC/USDT"]
        sig1 = tracker.build_sell_signal(state, position_quantity=0.1, now_epoch=1001.0)
        assert sig1 is not None
        # Within throttle window — second emit should be None.
        state = tracker._peaks["BTC/USDT"]
        sig2 = tracker.build_sell_signal(state, position_quantity=0.1, now_epoch=1002.0)
        assert sig2 is None


class TestPipelineIntegration:
    """[US-A1-04, US-A1-05, US-A1-06] LiveDecisionPipeline wiring."""

    def test_pipeline_has_check_tiered_trailing_stop(self) -> None:
        from selene_space_paper.live_decision_pipeline import LiveDecisionPipeline

        assert hasattr(LiveDecisionPipeline, "check_tiered_trailing_stop")
        assert callable(getattr(LiveDecisionPipeline, "check_tiered_trailing_stop"))

    def test_pipeline_has_notify_fill_tiered(self) -> None:
        from selene_space_paper.live_decision_pipeline import LiveDecisionPipeline

        assert hasattr(LiveDecisionPipeline, "notify_fill_tiered")

    def test_check_tiered_returns_none_without_tracker(self) -> None:
        from selene_space_paper.live_decision_pipeline import LiveDecisionPipeline

        # Construct minimal pipeline without tiered tracker.
        # Use duck-typed stubs to avoid heavy dependencies.
        class _StubRisk:
            def pre_check(self, intent, ctx):
                class _D:
                    allowed = True
                    reason = ""
                    alerts = []

                return _D()

        class _StubOrderMgr:
            def place_order(self, *args, **kwargs):
                class _O:
                    id = "stub-1"

                return _O()

        class _StubPipelineConfig:
            pass

        # Bypass __init__ — we only need the method, not full wiring.
        pipeline = LiveDecisionPipeline.__new__(LiveDecisionPipeline)
        pipeline._tiered_trailing_stop = None
        result = pipeline.check_tiered_trailing_stop("BTC/USDT", 100.0, 0.1)
        assert result is None

    def test_check_tiered_calls_on_tick_not_update(self) -> None:
        """[US-A1-05] verify method name in source."""
        import inspect

        from selene_space_paper.live_decision_pipeline import LiveDecisionPipeline

        src = inspect.getsource(LiveDecisionPipeline.check_tiered_trailing_stop)
        assert "on_tick(" in src
        assert ".update(" not in src

    def test_process_signal_stages_unchanged(self) -> None:
        """[US-A1-06] process_signal stage 1-7 markers intact."""
        import inspect

        from selene_space_paper.live_decision_pipeline import LiveDecisionPipeline

        src = inspect.getsource(LiveDecisionPipeline.process_signal)
        for stage in (
            "Stage 1",
            "Stage 2",
            "Stage 3",
            "Stage 4",
            "Stage 5",
            "Stage 6",
            "Stage 7",
        ):
            assert stage in src, f"missing {stage}"


class TestSymbols:
    """symbols() — read-only view of tracked symbols for the status API."""

    def test_symbols_reflect_tracked_state(self) -> None:
        tracker = TieredTrailingStopTracker()
        assert tracker.symbols() == []
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.on_fill("ETH/USDT", 1.0, 50.0)
        assert sorted(tracker.symbols()) == ["BTC/USDT", "ETH/USDT"]
        # Returned list is a copy — mutating it must not affect the tracker.
        symbols = tracker.symbols()
        symbols.clear()
        assert tracker.symbols() == ["BTC/USDT", "ETH/USDT"]

    def test_symbols_after_clear(self) -> None:
        tracker = TieredTrailingStopTracker()
        tracker.on_fill("BTC/USDT", 0.1, 100.0)
        tracker.clear("BTC/USDT")
        assert tracker.symbols() == []
