"""P2-4: TWAP slicer splits parent order into evenly-spaced children.

Verifies:
- N slices produced, evenly spaced across duration.
- Total quantity equals parent_qty (rounding absorbed by last slice).
- Validation: non-positive parent_qty / duration / n_slices raises.
- Single-slice edge case (degenerate TWAP, but valid).
- Quantities are equal for even divisions (e.g. 10 / 5 = 2.0 each).
- Uneven division: 10 / 3 → last slice absorbs remainder.
"""

from __future__ import annotations

import math

import pytest

from selene_space_exchange.execution.slicer import TWAPSlice, twap_slice


class TestTWAPSliceBasic:
    def test_n_slices_produced(self) -> None:
        slices = twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=5)
        assert len(slices) == 5

    def test_total_quantity_equals_parent(self) -> None:
        """Slices must sum to parent_qty exactly (rounding in last slice)."""
        for parent_qty in (10.0, 7.5, 1.0, 0.1, 100.0):
            for n in (1, 3, 7, 11):
                slices = twap_slice(
                    parent_qty=parent_qty, duration_seconds=300, n_slices=n
                )
                total = sum(s.quantity for s in slices)
                assert math.isclose(total, parent_qty, rel_tol=1e-12), (
                    f"parent={parent_qty} n={n}: total={total} != parent"
                )

    def test_even_spacing(self) -> None:
        """Slices are evenly spaced at duration / n_slices intervals."""
        slices = twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=5)
        # Expected offsets: 0, 60, 120, 180, 240 (interval = 60s).
        expected = [0.0, 60.0, 120.0, 180.0, 240.0]
        assert [s.start_offset_s for s in slices] == expected

    def test_quantities_equal_when_evenly_divisible(self) -> None:
        """10.0 / 5 → each slice gets exactly 2.0."""
        slices = twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=5)
        for s in slices:
            assert math.isclose(s.quantity, 2.0, rel_tol=1e-12)

    def test_last_slice_absorbs_remainder(self) -> None:
        """10.0 / 3 → first two slices get 3.333..., last absorbs the rest."""
        slices = twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=3)
        base = 10.0 / 3
        assert math.isclose(slices[0].quantity, base, rel_tol=1e-12)
        assert math.isclose(slices[1].quantity, base, rel_tol=1e-12)
        # Last slice = 10 - (2 * 10/3) = 10/3 exactly (same as base in this case).
        assert math.isclose(slices[2].quantity, 10.0 - 2 * base, rel_tol=1e-12)

    def test_single_slice_edge_case(self) -> None:
        """n_slices=1 → one slice at t=0 with full parent_qty."""
        slices = twap_slice(parent_qty=5.0, duration_seconds=60, n_slices=1)
        assert len(slices) == 1
        assert math.isclose(slices[0].quantity, 5.0, rel_tol=1e-12)
        assert slices[0].start_offset_s == 0.0

    def test_indices_are_sequential(self) -> None:
        slices = twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=5)
        assert [s.index for s in slices] == [0, 1, 2, 3, 4]

    def test_offset_increases_monotonically(self) -> None:
        slices = twap_slice(parent_qty=10.0, duration_seconds=600, n_slices=10)
        offsets = [s.start_offset_s for s in slices]
        assert offsets == sorted(offsets)


class TestTWAPValidation:
    def test_non_positive_parent_qty_raises(self) -> None:
        with pytest.raises(ValueError, match="parent_qty"):
            twap_slice(parent_qty=0.0, duration_seconds=300, n_slices=5)
        with pytest.raises(ValueError, match="parent_qty"):
            twap_slice(parent_qty=-1.0, duration_seconds=300, n_slices=5)

    def test_non_positive_duration_raises(self) -> None:
        with pytest.raises(ValueError, match="duration_seconds"):
            twap_slice(parent_qty=10.0, duration_seconds=0.0, n_slices=5)
        with pytest.raises(ValueError, match="duration_seconds"):
            twap_slice(parent_qty=10.0, duration_seconds=-1.0, n_slices=5)

    def test_zero_slices_raises(self) -> None:
        with pytest.raises(ValueError, match="n_slices"):
            twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=0)

    def test_negative_slices_raises(self) -> None:
        with pytest.raises(ValueError, match="n_slices"):
            twap_slice(parent_qty=10.0, duration_seconds=300, n_slices=-1)


class TestTWAPSlice:
    def test_dataclass_is_frozen(self) -> None:
        """TWAPSlice must be immutable (frozen=True)."""
        s = TWAPSlice(index=0, start_offset_s=0.0, quantity=1.0)
        with pytest.raises(Exception):
            s.quantity = 2.0  # type: ignore[misc]
