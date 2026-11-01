"""US-006: KellyAllocator fractional normalization-then-multiply (B6).

The scale factor must be applied AFTER normalization: multiplying before the
``v / total`` step is a math no-op (the same scalar cancels in the ratio), so
``fractional=0.25`` must produce absolute weights summing to ~0.25 that are
proportional to the full-Kelly weights.
"""

from __future__ import annotations

import pytest

from selene_space_alpha.allocator.kelly_allocator import KellyAllocator


@pytest.fixture
def strategies() -> dict[str, dict]:
    return {
        "strat_a": {"sharpe": 2.0, "volatility": 0.15},
        "strat_b": {"sharpe": 1.0, "volatility": 0.15},
        "strat_c": {"sharpe": 0.5, "volatility": 0.15},
    }


class TestFractionalSignature:
    def test_signature_has_fractional_default_one(self) -> None:
        allocator = KellyAllocator()
        assert allocator.fractional == 1.0

    def test_fractional_positive_validation(self) -> None:
        with pytest.raises(ValueError, match="fractional"):
            KellyAllocator(fractional=0.0)
        with pytest.raises(ValueError, match="fractional"):
            KellyAllocator(fractional=-0.5)


class TestFractionalScaling:
    def test_default_full_kelly_sum_to_one(self, strategies) -> None:
        allocator = KellyAllocator()
        weights = allocator.allocate(strategies)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_quarter_kelly_scales_after_normalization(self, strategies) -> None:
        """fractional=0.25 -> absolute weights proportional to full Kelly."""
        full = KellyAllocator().allocate(strategies)
        quarter = KellyAllocator(fractional=0.25).allocate(strategies)

        # Sum of absolute weights ~= 0.25 (normalization THEN scale).
        assert sum(quarter.values()) == pytest.approx(0.25, abs=1e-6)

        # Same relative proportions as full Kelly.
        for sid in strategies:
            ratio_full = full[sid] / full["strat_a"]
            ratio_quarter = quarter[sid] / quarter["strat_a"]
            assert ratio_quarter == pytest.approx(ratio_full)

        # Scaling is a pure shrink, not a re-ranking.
        assert quarter["strat_a"] > quarter["strat_c"]

    def test_half_kelly_each_weight_half(self, strategies) -> None:
        full = KellyAllocator().allocate(strategies)
        half = KellyAllocator(fractional=0.5).allocate(strategies)
        for sid in strategies:
            assert half[sid] == pytest.approx(full[sid] * 0.5, abs=1e-6)

    def test_fractional_greater_than_one_allowed(self, strategies) -> None:
        """Leveraged Kelly: scale > 1 amplifies full weights proportionally."""
        full = KellyAllocator().allocate(strategies)
        boosted = KellyAllocator(fractional=2.0).allocate(strategies)
        for sid in strategies:
            assert boosted[sid] == pytest.approx(full[sid] * 2.0, abs=1e-6)


class TestFractionalEdgeCases:
    def test_quarter_kelly_single_strategy(self) -> None:
        allocator = KellyAllocator(fractional=0.25)
        weights = allocator.allocate({"only": {"sharpe": 1.0, "volatility": 0.10}})
        assert weights["only"] == pytest.approx(0.25)

    def test_quarter_kelly_zero_sharpe_still_zero(self) -> None:
        allocator = KellyAllocator(fractional=0.25)
        weights = allocator.allocate({
            "good": {"sharpe": 1.5, "volatility": 0.10},
            "bad": {"sharpe": -0.5, "volatility": 0.10},
        })
        assert weights.get("bad", 0.0) == 0.0

    def test_empty_strategies(self) -> None:
        assert KellyAllocator(fractional=0.25).allocate({}) == {}
