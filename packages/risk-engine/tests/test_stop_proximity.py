"""Tests for :mod:`selene_space_risk.stop_proximity` (advisory stop proximity).

Locks the banded severity vocabulary (near -> WARNING, at/through stop ->
DANGEROUS, never CRITICAL), the faithful upstream cost path (loss-from-cost
vs ``stop_loss_pct`` / ``near_ratio``), the Selene stop path (distance from
current price to a provided dynamic stop), worst-first ordering, one alert
per symbol with highest severity winning, and inertness of NaN/flat inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from selene_space_risk.stop_proximity import (
    NEAR_THRESHOLD_PCT,
    STOP_LOSS_ALERT_PCT,
    STOP_LOSS_NEAR_RATIO,
    StaticStopPriceProvider,
    audit_stop_proximity,
)
from selene_space_types.risk import RiskLevel


@dataclass
class _Pos:
    """Minimal position duck-type (crypto or A-share attribute names)."""

    symbol: str
    avg_cost: float = 0.0
    last_price: float = 0.0
    avg_entry_price: float | None = None
    current_price: float | None = None


def _crypto(symbol: str, avg_cost: float, last_price: float) -> _Pos:
    return _Pos(symbol=symbol, avg_cost=avg_cost, last_price=last_price)


def _ids(alerts):
    return [(a.rule_id, a.level, a.context["symbol"]) for a in alerts]


# --------------------------------------------------------------------------
# Threshold constants
# --------------------------------------------------------------------------
def test_threshold_constants() -> None:
    assert STOP_LOSS_ALERT_PCT == 10.0
    assert STOP_LOSS_NEAR_RATIO == 0.8
    assert NEAR_THRESHOLD_PCT == pytest.approx(8.0)


# --------------------------------------------------------------------------
# Cost path (faithful upstream port)
# --------------------------------------------------------------------------
def test_cost_path_below_near_band_silent() -> None:
    # loss 5% < near threshold 8% -> no alert.
    assert audit_stop_proximity([_crypto("A", 100.0, 95.0)]) == []


def test_cost_path_just_below_near_boundary_silent() -> None:
    # loss 7.999% — just below the 8% near threshold -> silent.
    assert audit_stop_proximity([_crypto("A", 100.0, 92.001)]) == []


def test_cost_path_just_at_near_boundary_warning() -> None:
    # loss exactly 8% -> enters the near band -> WARNING.
    alerts = audit_stop_proximity([_crypto("A", 100.0, 92.0)])
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.WARNING, "A")]
    assert alerts[0].context["loss_pct"] == pytest.approx(8.0)
    assert alerts[0].context["near_threshold_pct"] == pytest.approx(8.0)
    assert alerts[0].context["is_triggered"] is False


def test_cost_path_at_stop_threshold_dangerous() -> None:
    # loss exactly 10% -> through the stop -> DANGEROUS.
    alerts = audit_stop_proximity([_crypto("A", 100.0, 90.0)])
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.DANGEROUS, "A")]
    assert alerts[0].context["is_triggered"] is True


def test_cost_path_profit_clamped_to_zero() -> None:
    # last_price above avg_cost: raw formula negative; clamped to 0 -> silent.
    assert audit_stop_proximity([_crypto("A", 100.0, 120.0)]) == []


def test_cost_path_upstream_field_conventions() -> None:
    alerts = audit_stop_proximity([_crypto("BTC/USDT", 50.0, 45.0)])
    ctx = alerts[0].context
    assert ctx["avg_cost"] == pytest.approx(50.0)
    assert ctx["last_price"] == pytest.approx(45.0)
    assert ctx["loss_pct"] == pytest.approx(10.0)
    # rounding conventions carried over from upstream
    assert ctx["loss_pct"] == round(ctx["loss_pct"], 4)
    assert ctx["near_threshold_pct"] == round(ctx["near_threshold_pct"], 4)


# --------------------------------------------------------------------------
# Stop path (Selene's dynamic-stop enhancement)
# --------------------------------------------------------------------------
def test_stop_path_missing_falls_back_to_cost() -> None:
    provider = StaticStopPriceProvider({"OTHER": 95.0})
    alerts = audit_stop_proximity([_crypto("A", 100.0, 90.0)], provider)
    # No stop for "A" -> cost path: loss 10% -> DANGEROUS, no stop_price key.
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.DANGEROUS, "A")]
    assert "stop_price" not in alerts[0].context
    assert "loss_pct" in alerts[0].context


def test_stop_path_at_through_stop_dangerous() -> None:
    provider = StaticStopPriceProvider({"A": 95.0})
    # last exactly at the stop -> distance 0 -> DANGEROUS.
    alerts = audit_stop_proximity([_crypto("A", 100.0, 95.0)], provider)
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.DANGEROUS, "A")]
    assert alerts[0].context["stop_distance_pct"] == pytest.approx(0.0)
    assert alerts[0].context["is_triggered"] is True
    # through the stop (negative distance) -> still DANGEROUS.
    alerts = audit_stop_proximity([_crypto("A", 100.0, 94.0)], provider)
    assert alerts[0].level == RiskLevel.DANGEROUS
    assert alerts[0].context["stop_distance_pct"] < 0


def test_stop_path_within_band_warning_even_when_in_profit() -> None:
    provider = StaticStopPriceProvider({"A": 95.0})
    # last 97 (2.1% above stop, still above cost 100) -> WARNING despite no loss.
    alerts = audit_stop_proximity([_crypto("A", 100.0, 97.0)], provider)
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.WARNING, "A")]


def test_stop_path_far_above_stop_silent() -> None:
    provider = StaticStopPriceProvider({"A": 80.0})
    # last 100 is 25% above the stop -> outside the 8% band -> silent.
    assert audit_stop_proximity([_crypto("A", 100.0, 100.0)], provider) == []


def test_stop_path_band_boundary() -> None:
    # Band width == cost-path near threshold (8%). Exactly 8% above the stop
    # -> WARNING (inclusive); just above -> silent.
    provider = StaticStopPriceProvider({"A": 100.0})
    alerts = audit_stop_proximity([_crypto("A", 200.0, 108.0)], provider)
    assert len(alerts) == 1
    assert alerts[0].level == RiskLevel.WARNING
    assert alerts[0].context["stop_distance_pct"] == pytest.approx(8.0)
    assert audit_stop_proximity([_crypto("A", 200.0, 108.1)], provider) == []


def test_stop_path_overrides_cost_path_for_same_symbol() -> None:
    # Cost says 12% loss (would be DANGEROUS) but the real stop is just below
    # the price (volatility-scaled): the stop path is the better proxy.
    provider = StaticStopPriceProvider({"A": 85.0})
    alerts = audit_stop_proximity([_crypto("A", 100.0, 88.0)], provider)
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.WARNING, "A")]


def test_stop_path_non_positive_or_nan_stop_ignored() -> None:
    provider = StaticStopPriceProvider({"A": 0.0, "B": float("nan")})
    # A: stop 0 -> invalid -> cost path (loss 0% -> silent anyway).
    # B: NaN stop -> invalid -> cost path, loss 12% -> DANGEROUS.
    alerts = audit_stop_proximity(
        [_crypto("A", 100.0, 99.0), _crypto("B", 100.0, 88.0)], provider
    )
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.DANGEROUS, "B")]


# --------------------------------------------------------------------------
# Provider contract
# --------------------------------------------------------------------------
def test_static_provider_returns_fresh_copy() -> None:
    source = {"A": 95.0, "B": 90.0}
    provider = StaticStopPriceProvider(source)
    got = provider.get_stop_prices()
    assert got == {"A": 95.0, "B": 90.0}
    # Mutating the returned copy never leaks into the provider...
    got["A"] = 1.0
    got["C"] = 2.0
    assert provider.get_stop_prices() == {"A": 95.0, "B": 90.0}
    # ...and the caller's original mapping is untouched.
    assert source == {"A": 95.0, "B": 90.0}


def test_none_provider_uses_cost_path() -> None:
    alerts = audit_stop_proximity([_crypto("A", 100.0, 90.0)], None)
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.DANGEROUS, "A")]


# --------------------------------------------------------------------------
# Input inertness
# --------------------------------------------------------------------------
def test_empty_positions() -> None:
    assert audit_stop_proximity([], StaticStopPriceProvider({"A": 90.0})) == []
    assert audit_stop_proximity([]) == []


def test_flat_and_zero_cost_positions_skipped() -> None:
    # avg_cost <= 0 -> skipped (flat / unknown-cost holdings are inert).
    assert audit_stop_proximity([_crypto("A", 0.0, 90.0)]) == []
    assert audit_stop_proximity([_crypto("A", -1.0, 90.0)]) == []


def test_zero_last_price_cost_path_triggers() -> None:
    # avg_cost > 0, last 0: loss 100% -> DANGEROUS (upstream formula does the
    # same; only avg_cost <= 0 is skipped).
    alerts = audit_stop_proximity([_crypto("A", 100.0, 0.0)])
    assert len(alerts) == 1
    assert alerts[0].level == RiskLevel.DANGEROUS


def test_blank_symbol_skipped() -> None:
    assert audit_stop_proximity([_crypto("", 100.0, 50.0)]) == []


def test_nan_inputs_inert() -> None:
    nan = float("nan")
    # NaN prices: cost-path loss is NaN, every comparison False -> silent.
    assert audit_stop_proximity([_crypto("A", nan, 90.0)]) == []
    assert audit_stop_proximity([_crypto("A", 100.0, nan)]) == []
    # NaN stop with NaN price still inert on the stop path... stop invalid ->
    # cost path -> inert. Plain NaN stop, finite price -> cost path applies.
    provider = StaticStopPriceProvider({"A": nan})
    assert audit_stop_proximity([_crypto("A", 100.0, 95.0)], provider) == []


def test_crypto_attribute_fallbacks() -> None:
    # Position objects using Selene's crypto attribute names still work when
    # the crypto-style attributes are absent entirely.
    class _CryptoPos:
        symbol = "A"
        avg_entry_price = 100.0
        current_price = 90.0

    alerts = audit_stop_proximity([_CryptoPos()])
    assert _ids(alerts) == [("stop_loss_proximity", RiskLevel.DANGEROUS, "A")]
    assert alerts[0].context["avg_cost"] == pytest.approx(100.0)
    assert alerts[0].context["last_price"] == pytest.approx(90.0)


# --------------------------------------------------------------------------
# Ordering and per-symbol precedence
# --------------------------------------------------------------------------
def test_descending_sort_worst_first() -> None:
    positions = [
        _crypto("NEAR", 100.0, 93.0),   # 7% loss -> silent
        _crypto("TRIG", 100.0, 85.0),   # 15% loss -> DANGEROUS
        _crypto("WARN", 100.0, 90.0),   # 10% loss -> DANGEROUS
        _crypto("NEAR2", 100.0, 91.0),  # 9% loss -> WARNING
    ]
    alerts = audit_stop_proximity(positions)
    symbols = [a.context["symbol"] for a in alerts]
    # DANGEROUS block first (15% then 10%), then WARNING (9%).
    assert symbols == ["TRIG", "WARN", "NEAR2"]
    losses = [a.context["loss_pct"] for a in alerts]
    assert losses == sorted(losses, reverse=True)


def test_one_alert_per_symbol_highest_severity_wins() -> None:
    # Duplicate symbols across "accounts": the DANGEROUS one must win.
    positions = [
        _crypto("A", 100.0, 92.0),  # 8% -> WARNING
        _crypto("A", 100.0, 85.0),  # 15% -> DANGEROUS
        _crypto("B", 100.0, 85.0),  # 15% -> DANGEROUS
        _crypto("B", 100.0, 92.0),  # 8% -> WARNING (must not replace it)
    ]
    alerts = audit_stop_proximity(positions)
    by_symbol = {a.context["symbol"]: a for a in alerts}
    assert set(by_symbol) == {"A", "B"}
    assert all(a.level == RiskLevel.DANGEROUS for a in by_symbol.values())


def test_never_emits_critical() -> None:
    positions = [_crypto("A", 100.0, 0.0), _crypto("B", 100.0, 10.0)]
    alerts = audit_stop_proximity(
        positions, StaticStopPriceProvider({"B": 10.0})
    )
    assert alerts
    assert all(a.level != RiskLevel.CRITICAL for a in alerts)
