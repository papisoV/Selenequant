"""Tests for VolatilityScaledKellyRule (M3) — Kelly × inverse realized vol."""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_risk.advanced_rules import VolatilityScaledKellyRule
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _make_intent(**overrides: Any) -> TradeIntent:
    defaults = {
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "quantity": 1.0,
        "price": 67000.0,
        "order_type": OrderType.MARKET,
        "reasoning": "test",
        "confidence": 0.8,
        "risk_params": {"position_fraction": 0.1},
        "timestamp": 0,
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


def _winning_trade_history(n_wins: int = 8, n_losses: int = 4) -> list[dict]:
    """Win rate = 8/12 = 0.667, avg_win=100, avg_loss=50, odds=2.0."""
    wins = [{"pnl": 100.0} for _ in range(n_wins)]
    losses = [{"pnl": -50.0} for _ in range(n_losses)]
    return wins + losses


def _constant_returns(n: int, per_period: float) -> list[float]:
    """Returns with zero stdev — degenerate; used to probe vol=0 path."""
    return [per_period] * n


def _noisy_returns(n: int, amp: float) -> list[float]:
    """Oscillating returns with given amplitude; stdev ≈ amp / sqrt(2).

    Used to dial realized vol up or down deterministically.
    """
    return [amp if i % 2 == 0 else -amp for i in range(n)]


class TestVolScaledKellyBasics:
    def test_no_equity_returns_none(self) -> None:
        rule = VolatilityScaledKellyRule()
        ctx = {
            "total_equity": 0.0,
            "trade_history": _winning_trade_history(),
            "returns": _noisy_returns(30, 0.01),
        }
        assert rule.check(_make_intent(), ctx) is None

    def test_insufficient_trade_history_returns_none(self) -> None:
        rule = VolatilityScaledKellyRule(min_trades_for_estimation=100)
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": _noisy_returns(30, 0.01),
        }
        assert rule.check(_make_intent(), ctx) is None

    def test_no_wins_or_no_losses_returns_none(self) -> None:
        rule = VolatilityScaledKellyRule()
        # All wins — no losses → can't compute odds.
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": [{"pnl": 100.0}] * 12,
            "returns": _noisy_returns(30, 0.01),
        }
        assert rule.check(_make_intent(), ctx) is None

    def test_negative_kelly_emits_warning(self) -> None:
        """Losing strategy: kelly_f < 0 → WARNING alert, no sizing."""
        rule = VolatilityScaledKellyRule()
        # 4 wins @ +50, 8 losses @ -100: odds=0.5, win_rate=0.333
        # kelly_f = (0.5*0.333 - 0.667) / 0.5 = -1.0
        history = [{"pnl": 50.0}] * 4 + [{"pnl": -100.0}] * 8
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": history,
            "returns": _noisy_returns(30, 0.01),
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.level == RiskLevel.WARNING


class TestVolScalingFactor:
    """Verify the vol_scale = baseline / realized clamp logic."""

    def test_high_realized_vol_shrinks_position(self) -> None:
        """realized_vol=40% vs baseline=20% → scale=0.5 (clamped above min)."""
        rule = VolatilityScaledKellyRule(
            baseline_vol=0.20, min_scale=0.25, max_scale=1.5,
        )
        # amp=0.04 → stdev ≈ 0.04, annualized ≈ 0.04 * sqrt(252) ≈ 0.635
        # Actually realized_vol ≈ 0.635 — way above baseline → scale clamps to 0.25.
        # Use amp=0.01 → stdev ≈ 0.01, annualized ≈ 0.1587 → realized ≈ 15.87%
        # Wait we want HIGH vol. Use amp=0.02 → realized ≈ 31.7% > baseline 20%.
        returns = _noisy_returns(30, 0.02)
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": returns,
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.level == RiskLevel.INFO
        # realized_vol ≈ 0.317, scale = 0.20/0.317 ≈ 0.631
        # scaled_f = adjusted_f * 0.631, adjusted_f = half_kelly = 0.5 * kelly_f
        # kelly_f = (2 * 0.667 - 0.333) / 2 = 0.500 → adjusted_f = 0.25 (capped)
        # scaled_f = 0.25 * 0.631 = 0.158
        assert result.context["vol_scale"] < 1.0
        assert result.context["scaled_f"] < result.context["adjusted_f"]

    def test_low_realized_vol_grows_position(self) -> None:
        """realized_vol=5% vs baseline=20% → scale would be 4.0 but clamps to 1.5."""
        rule = VolatilityScaledKellyRule(
            baseline_vol=0.20, min_scale=0.25, max_scale=1.5,
        )
        # amp=0.002 → stdev ≈ 0.002, annualized ≈ 0.0317 → realized ≈ 3.2%
        # scale = 0.20 / 0.032 = 6.25, clamps to max_scale=1.5
        returns = _noisy_returns(30, 0.002)
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": returns,
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["vol_scale"] == pytest.approx(1.5)
        assert result.context["scaled_f"] > result.context["adjusted_f"]

    def test_clamped_at_min_scale(self) -> None:
        """Extreme vol: scale clamps to min_scale."""
        rule = VolatilityScaledKellyRule(
            baseline_vol=0.20, min_scale=0.25, max_scale=1.5,
        )
        # amp=0.05 → realized ≈ 79% → scale = 0.20/0.79 = 0.253, barely above min
        # Use larger amp to force clamp:
        returns = _noisy_returns(30, 0.10)  # realized ≈ 158%
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": returns,
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["vol_scale"] == pytest.approx(0.25)

    def test_no_returns_data_falls_back_to_unscaled(self) -> None:
        """Empty returns → realized_vol=None → scale=1.0 (unscaled Kelly)."""
        rule = VolatilityScaledKellyRule()
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": [],
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["vol_scale"] == 1.0
        assert result.context["realized_vol"] is None
        # scaled_f == adjusted_f when scale=1.0
        assert result.context["scaled_f"] == pytest.approx(
            result.context["adjusted_f"]
        )

    def test_insufficient_returns_falls_back_to_unscaled(self) -> None:
        """< min_returns_for_vol samples → can't compute vol → scale=1.0."""
        rule = VolatilityScaledKellyRule(min_returns_for_vol=50)
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": _noisy_returns(10, 0.01),  # only 10 samples
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["vol_scale"] == 1.0
        assert result.context["realized_vol"] is None

    def test_constant_returns_zero_stdev_falls_back_to_unscaled(self) -> None:
        """All-zero returns → realized_vol=0 → scale=1.0 (degenerate case)."""
        rule = VolatilityScaledKellyRule()
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": [0.0] * 30,
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        # stdev = 0 → realized_vol = 0 → fallback to scale=1.0
        assert result.context["vol_scale"] == 1.0


class TestKellyMath:
    """Verify Kelly formula correctness (same math as KellyPositionSizingRule)."""

    def test_kelly_f_formula(self) -> None:
        """win_rate=0.667, odds=2.0 → kelly_f = (2*0.667 - 0.333) / 2 = 0.5."""
        rule = VolatilityScaledKellyRule(kelly_fraction=1.0)  # full Kelly
        # No returns → scale=1.0, so scaled_f = kelly_f * 1.0 = 0.5
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": [],
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["kelly_f"] == pytest.approx(0.5, abs=1e-3)

    def test_half_kelly_default(self) -> None:
        rule = VolatilityScaledKellyRule(kelly_fraction=0.5)
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": [],
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        # adjusted_f = kelly_f * 0.5 = 0.25
        assert result.context["adjusted_f"] == pytest.approx(0.25, abs=1e-3)

    def test_hard_ceiling_at_25_pct(self) -> None:
        """Even with huge kelly_f, scaled_f capped at 0.25."""
        rule = VolatilityScaledKellyRule(
            kelly_fraction=1.0, max_scale=10.0,  # allow big scaling
        )
        # 10 wins @ 1000, 1 loss @ 10 → odds=100, win_rate=10/11=0.909
        # kelly_f = (100 * 0.909 - 0.091) / 100 ≈ 0.908 → adjusted_f * 10 → capped at 0.25
        history = [{"pnl": 1000.0}] * 10 + [{"pnl": -10.0}] * 1
        returns = _noisy_returns(30, 0.001)  # low vol → big scale, clamped
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": history,
            "returns": returns,
        }
        result = rule.check(_make_intent(), ctx)
        assert isinstance(result, RiskAlert)
        assert result.context["scaled_f"] <= 0.25


class TestRuleProtocol:
    def test_rule_id(self) -> None:
        assert VolatilityScaledKellyRule().rule_id == "vol_scaled_kelly"

    def test_enabled_flag(self) -> None:
        rule = VolatilityScaledKellyRule()
        assert rule.enabled is True
        rule.enabled = False
        assert rule.enabled is False

    def test_disabled_via_property_no_change_to_check(self) -> None:
        """enabled is a flag — check() still runs if called. Engine layer
        honors .enabled. Here just verify the setter works."""
        rule = VolatilityScaledKellyRule()
        rule.enabled = False
        ctx = {
            "total_equity": 10_000.0,
            "trade_history": _winning_trade_history(),
            "returns": [],
        }
        # Rule itself doesn't gate on .enabled — engine does.
        assert rule.check(_make_intent(), ctx) is not None
