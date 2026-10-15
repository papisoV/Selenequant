"""Tests for MomentumRiskParityAllocation (C2 risk-parity + momentum tilt).

Covers:
  - Weights sum to 1; non-negative
  - Pure risk parity (tilt_strength=0): lower-vol asset gets higher weight
  - Momentum tilt raises positive-momentum assets above pure risk parity
  - Negative momentum reduces weight (clipped to [-1,1], no shorts)
  - Warm-up: before history accumulates, falls back to equal weights
  - Protocol conformance with backtest-engine AllocationModel
  - History accumulation across calls
"""

from __future__ import annotations

import pytest

from selene_space_alpha.allocator.momentum_rp_allocator import MomentumRiskParityAllocation
from selene_space_backtest.multi_asset.allocation_model import AllocationModel


def _feed(
    model: MomentumRiskParityAllocation,
    symbols: list[str],
    price_seqs: dict[str, list[float]],
    equity: float = 100_000.0,
) -> dict[str, float]:
    """Feed per-symbol closes one bar at a time, returning final weights."""
    n = max(len(v) for v in price_seqs.values())
    weights: dict[str, float] = {}
    for i in range(n):
        prices = {
            s: seq[i] for s, seq in price_seqs.items() if i < len(seq)
        }
        weights = model.compute_weights(symbols, equity, prices)
    return weights


def test_weights_sum_to_one() -> None:
    model = MomentumRiskParityAllocation()
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    seqs = {
        "BTC/USDT": [100.0, 105.0, 110.0, 115.0, 121.0, 127.0],
        "ETH/USDT": [100.0, 98.0, 101.0, 99.0, 103.0, 100.0],
        "SOL/USDT": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0],
    }
    weights = _feed(model, symbols, seqs)
    assert sum(weights.values()) == 1.0
    assert all(w >= 0.0 for w in weights.values())


def test_risk_parity_lower_vol_gets_higher_weight() -> None:
    """tilt_strength=0 → pure inverse-vol: stable asset weighted above volatile one."""
    model = MomentumRiskParityAllocation(tilt_strength=0.0)
    symbols = ["A/USDT", "B/USDT"]
    seqs = {
        "A/USDT": [100.0, 120.0, 145.0, 175.0, 210.0],  # volatile
        "B/USDT": [100.0, 101.0, 102.0, 103.0, 104.0],  # stable
    }
    weights = _feed(model, symbols, seqs)
    assert weights["B/USDT"] > weights["A/USDT"]


def test_positive_momentum_tilts_up() -> None:
    """Rising asset's weight is higher with momentum tilt than without it."""
    pure = MomentumRiskParityAllocation(tilt_strength=0.0)
    tilted = MomentumRiskParityAllocation(tilt_strength=1.0)
    symbols = ["A/USDT", "B/USDT"]
    seqs = {
        "A/USDT": [100.0, 105.0, 110.0, 115.0, 121.0],  # rising momentum
        "B/USDT": [100.0, 98.0, 96.0, 94.0, 92.0],  # falling
    }
    w_pure = _feed(pure, symbols, seqs)
    w_tilt = _feed(tilted, symbols, seqs)
    assert w_tilt["A/USDT"] > w_pure["A/USDT"]
    assert w_tilt["B/USDT"] < w_pure["B/USDT"]


def test_negative_momentum_never_shorts() -> None:
    """Momentum is clipped to [-1,1], so weights stay non-negative."""
    model = MomentumRiskParityAllocation(tilt_strength=5.0)
    symbols = ["A/USDT", "B/USDT"]
    seqs = {
        "A/USDT": [100.0, 110.0, 121.0, 133.0, 146.0],
        "B/USDT": [100.0, 90.0, 81.0, 73.0, 66.0],  # deeply negative momentum
    }
    weights = _feed(model, symbols, seqs)
    assert all(w >= 0.0 for w in weights.values())
    assert sum(weights.values()) == 1.0
    # Deep negative momentum → B clipped to weight ~0 (long-only tilt).
    assert weights["B/USDT"] < 0.2


def test_warmup_falls_back_to_equal_weights() -> None:
    """First call with no history returns equal weights summing to 1."""
    model = MomentumRiskParityAllocation()
    weights = model.compute_weights(
        ["A/USDT", "B/USDT", "C/USDT"], 100_000.0, None
    )
    assert all(abs(w - 1 / 3) < 1e-6 for w in weights.values())
    assert sum(weights.values()) == pytest.approx(1.0)


def test_implements_allocation_model_protocol() -> None:
    assert isinstance(MomentumRiskParityAllocation(), AllocationModel)


def test_history_accumulates_across_calls() -> None:
    """Weights evolve as more price history accumulates."""
    model = MomentumRiskParityAllocation()
    symbols = ["A/USDT", "B/USDT"]
    seqs = {
        "A/USDT": [100.0, 105.0, 110.0, 115.0, 121.0],
        "B/USDT": [100.0, 101.0, 102.0, 103.0, 104.0],
    }
    first = model.compute_weights(symbols, 100_000.0, {"A/USDT": 100.0, "B/USDT": 100.0})
    later = _feed(model, symbols, seqs)
    assert later != first


def test_empty_symbols_returns_empty() -> None:
    model = MomentumRiskParityAllocation()
    assert model.compute_weights([], 100_000.0, None) == {}


def test_partial_prices_only_accumulate_known_symbols() -> None:
    """compute_weights tolerates a prices dict missing some symbols."""
    model = MomentumRiskParityAllocation()
    symbols = ["A/USDT", "B/USDT"]
    w1 = model.compute_weights(symbols, 100_000.0, {"A/USDT": 100.0})
    assert set(w1) == set(symbols)
    assert sum(w1.values()) == 1.0


def test_history_stays_bounded() -> None:
    """History is trimmed to max(vol, momentum)_lookback + 1 closes (no unbounded growth)."""
    model = MomentumRiskParityAllocation(vol_lookback=5, momentum_lookback=10)
    symbol = "A/USDT"
    seq = [100.0 + i for i in range(30)]
    weights: dict[str, float] = {}
    for price in seq:
        weights = model.compute_weights([symbol], 100_000.0, {symbol: price})
    assert sum(weights.values()) == pytest.approx(1.0)
    hist = model._history[symbol]
    assert len(hist) == 11  # max(5, 10) + 1
    assert hist == seq[-11:]
