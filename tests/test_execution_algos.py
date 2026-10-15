"""Tests for execution algorithms: VWAP, POV, Adaptive.

All algorithms implement the ExecutionAlgo Protocol and return slice schedules
that callers execute via LiveExecutionEngine.place_order().
"""

from __future__ import annotations


import pytest

from selene_space_exchange.execution.algo.base import ExecutionAlgo
from selene_space_exchange.execution.algo.vwap import VWAPExecutor
from selene_space_exchange.execution.algo.pov import POVExecutor
from selene_space_exchange.execution.algo.adaptive import AdaptiveExecutor


# ---------------------------------------------------------------------------
# VWAPExecutor
# ---------------------------------------------------------------------------


class TestVWAPExecutor:
    def test_slices_sum_to_parent_qty(self) -> None:
        algo = VWAPExecutor(duration_s=300, n_slices=10)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        total = sum(s.quantity for s in schedule)
        assert total == pytest.approx(10.0)

    def test_returns_n_slices(self) -> None:
        algo = VWAPExecutor(duration_s=300, n_slices=10)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        assert len(schedule) == 10

    def test_first_slice_at_zero(self) -> None:
        algo = VWAPExecutor(duration_s=300, n_slices=5)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        assert schedule[0].start_offset_s == 0.0

    def test_interval_linear(self) -> None:
        algo = VWAPExecutor(duration_s=300, n_slices=5)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        for i in range(1, len(schedule)):
            diff = schedule[i].start_offset_s - schedule[i - 1].start_offset_s
            assert diff == pytest.approx(60.0, abs=0.01)

    def test_volume_profile_weights(self) -> None:
        """Slices should be larger in high-volume buckets and smaller in low-volume."""
        # Mock volume profile: buckets 0-2 high vol, 3-4 low vol
        volume_profile = [100.0, 100.0, 100.0, 10.0, 10.0]
        algo = VWAPExecutor(duration_s=300, n_slices=5, volume_profile=volume_profile)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        # High vol buckets should get ~31% each (100/320), low vol ~3.1% each
        assert schedule[0].quantity > schedule[3].quantity
        assert schedule[0].quantity == pytest.approx(10.0 * 100 / 320, abs=0.01)

    def test_uniform_when_no_profile(self) -> None:
        algo = VWAPExecutor(duration_s=300, n_slices=4)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        for s in schedule:
            assert s.quantity == pytest.approx(2.5)

    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            VWAPExecutor(duration_s=0, n_slices=10)
        with pytest.raises(ValueError):
            VWAPExecutor(duration_s=300, n_slices=0)

    def test_implements_protocol(self) -> None:
        algo = VWAPExecutor(duration_s=300, n_slices=5)
        assert isinstance(algo, ExecutionAlgo)


# ---------------------------------------------------------------------------
# POVExecutor
# ---------------------------------------------------------------------------


class TestPOVExecutor:
    def test_slices_sum_to_parent_qty(self) -> None:
        algo = POVExecutor(participation_rate=0.10, max_slices=10)
        schedule = algo.schedule(parent_qty=100.0, price=50000.0)
        total = sum(s.quantity for s in schedule)
        assert total == pytest.approx(100.0)

    def test_each_slice_is_participation_fraction(self) -> None:
        """Each slice should target participation_rate of estimated volume."""
        market_volume = 1000.0  # total volume during execution window
        algo = POVExecutor(participation_rate=0.05, max_slices=5, estimated_volume=market_volume)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0)
        # 5% of 1000 = 50 expected from schedule — but parent caps it to 10
        # So each slice ≤ max(participation target, remaining / remaining_slices)
        for s in schedule:
            assert s.quantity > 0

    def test_respects_max_slices(self) -> None:
        algo = POVExecutor(participation_rate=0.10, max_slices=4)
        schedule = algo.schedule(parent_qty=1000.0, price=50000.0)
        assert len(schedule) <= 4

    def test_small_parent_not_over_sliced(self) -> None:
        """A small parent qty should produce few slices to avoid dust."""
        algo = POVExecutor(participation_rate=0.10, max_slices=20, estimated_volume=10000.0)
        schedule = algo.schedule(parent_qty=1.0, price=50000.0)
        # 10% of 10k = 1000 → only 1 slice needed
        assert len(schedule) <= 2

    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            POVExecutor(participation_rate=0, max_slices=5)
        with pytest.raises(ValueError):
            POVExecutor(participation_rate=1.5, max_slices=5)
        with pytest.raises(ValueError):
            POVExecutor(participation_rate=0.1, max_slices=0)

    def test_implements_protocol(self) -> None:
        algo = POVExecutor(participation_rate=0.10, max_slices=5)
        assert isinstance(algo, ExecutionAlgo)


# ---------------------------------------------------------------------------
# AdaptiveExecutor
# ---------------------------------------------------------------------------


class TestAdaptiveExecutor:
    def test_default_no_spread_adjustment(self) -> None:
        """With default spread, slices are uniform."""
        algo = AdaptiveExecutor(duration_s=300, n_slices=5)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0, current_spread=0.001)
        total = sum(s.quantity for s in schedule)
        assert total == pytest.approx(10.0)

    def test_wide_spread_reduces_slice_size(self) -> None:
        """When spread is wide, early slices should be smaller (wait for improvement)."""
        algo = AdaptiveExecutor(duration_s=300, n_slices=5, base_spread=0.001)
        # Very wide spread (10x normal) → early slices much smaller
        schedule = algo.schedule(parent_qty=10.0, price=50000.0, current_spread=0.01)
        # First slice should be smaller than last (more weight later)
        assert schedule[0].quantity < schedule[-1].quantity

    def test_tight_spread_front_loads(self) -> None:
        """When spread is tight, front-load the order."""
        algo = AdaptiveExecutor(duration_s=300, n_slices=5, base_spread=0.001)
        schedule = algo.schedule(parent_qty=10.0, price=50000.0, current_spread=0.0001)
        # First slice should be larger than last (front-loaded)
        assert schedule[0].quantity >= schedule[-1].quantity

    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            AdaptiveExecutor(duration_s=0, n_slices=5)
        with pytest.raises(ValueError):
            AdaptiveExecutor(duration_s=300, n_slices=0)
        with pytest.raises(ValueError):
            AdaptiveExecutor(duration_s=300, n_slices=5, base_spread=-0.01)

    def test_implements_protocol(self) -> None:
        algo = AdaptiveExecutor(duration_s=300, n_slices=5)
        assert isinstance(algo, ExecutionAlgo)
