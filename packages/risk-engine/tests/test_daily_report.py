"""Tests for daily risk report generator.

Covers:
  - VaR/CVaR computation at multiple confidence levels
  - Portfolio exposure ratios (gross / net / margin)
  - Rule alerts triggered by portfolio state
  - Edge cases: empty returns, no positions, hedged portfolio
"""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_risk.daily_report import (
    VarEstimate,
    generate_daily_risk_report,
)
from selene_space_types.risk import RiskLevel


def _make_returns(n: int = 500, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.02, size=n).tolist()


# ---------------------------------------------------------------------------
# VaR / CVaR fields
# ---------------------------------------------------------------------------


class TestVarFields:
    def test_report_has_var_estimates_at_default_confidences(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 50_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert len(report.var_estimates) == 2
        confs = [v.confidence for v in report.var_estimates]
        assert confs == [0.95, 0.99]

    def test_var_99_more_extreme_than_var_95(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        var95 = report.var_estimates[0].historical_var
        var99 = report.var_estimates[1].historical_var
        assert abs(var99) > abs(var95)

    def test_cvar_more_extreme_than_var_at_same_confidence(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        var95 = report.var_estimates[0].historical_var
        cvar95 = report.var_estimates[0].cvar
        assert abs(cvar95) >= abs(var95)

    def test_var_estimates_contain_all_three_methods(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        for v in report.var_estimates:
            assert isinstance(v, VarEstimate)
            # All methods produce finite losses
            assert v.historical_var < 0
            assert v.parametric_var < 0
            assert v.monte_carlo_var < 0
            assert v.cvar < 0

    def test_custom_confidence_levels(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            confidences=(0.90, 0.95, 0.99),
            monte_carlo_seed=42,
        )
        assert len(report.var_estimates) == 3
        assert report.var_estimates[0].confidence == 0.90

    def test_monte_carlo_seed_reproducible(self) -> None:
        kwargs = dict(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=123,
        )
        r1 = generate_daily_risk_report(**kwargs)
        r2 = generate_daily_risk_report(**kwargs)
        assert r1.var_estimates[0].monte_carlo_var == r2.var_estimates[0].monte_carlo_var

    def test_returns_sample_size_recorded(self) -> None:
        returns = _make_returns(n=250)
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=returns,
            monte_carlo_seed=42,
        )
        assert report.returns_sample_size == 250


# ---------------------------------------------------------------------------
# Portfolio exposure
# ---------------------------------------------------------------------------


class TestExposureMetrics:
    def test_gross_exposure_sums_abs_notional(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 50_000.0, "ETH/USDT": -30_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # |50k| + |-30k| = 80k → 80%
        assert report.exposure.gross_exposure_pct == pytest.approx(0.80, abs=0.01)

    def test_net_exposure_long_minus_short(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 50_000.0, "ETH/USDT": -30_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # 50k - 30k = 20k → 20% net long
        assert report.exposure.net_exposure_pct == pytest.approx(0.20, abs=0.01)

    def test_margin_utilization_uses_margin_field_when_present(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={
                "BTC/USDT": {"notional": 50_000.0, "margin": 5_000.0},
                "ETH/USDT": {"notional": 30_000.0, "margin": 3_000.0},
            },
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # 8k margin / 100k equity = 8%
        assert report.exposure.margin_utilization_pct == pytest.approx(0.08, abs=0.01)

    def test_margin_utilization_falls_back_to_notional_leverage_proxy(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 80_000.0},  # plain float, no leverage
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # 80k / 10x default leverage = 8k → 8%
        assert report.exposure.margin_utilization_pct == pytest.approx(0.08, abs=0.01)

    def test_leverage_amplifies_gross_exposure(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": {"notional": 50_000.0, "leverage": 3.0}},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # 50k * 3 = 150k → 150%
        assert report.exposure.gross_exposure_pct == pytest.approx(1.50, abs=0.01)

    def test_zero_equity_returns_zero_ratios(self) -> None:
        report = generate_daily_risk_report(
            equity=0.0,
            positions={"BTC/USDT": 50_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert report.exposure.gross_exposure_pct == 0.0
        assert report.exposure.net_exposure_pct == 0.0
        assert report.exposure.margin_utilization_pct == 0.0
        assert report.exposure.position_count == 1

    def test_position_count_matches_dict_size(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 10_000.0, "ETH/USDT": 5_000.0, "SOL/USDT": 3_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert report.exposure.position_count == 3

    def test_hedged_portfolio_reduces_net_exposure(self) -> None:
        """Long 50k + short 50k → net 0 even though gross is 100%."""
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 50_000.0, "ETH/USDT": -50_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert report.exposure.gross_exposure_pct == pytest.approx(1.0, abs=0.01)
        assert report.exposure.net_exposure_pct == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class TestAlerts:
    def test_no_alerts_when_within_limits(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 50_000.0},  # 50% gross, well under 200%
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert report.alerts == []

    def test_alert_when_gross_exposure_exceeds_default(self) -> None:
        """Default TotalExposureRule max_gross = 2.0. Need >200% to trigger."""
        report = generate_daily_risk_report(
            equity=10_000.0,
            positions={
                "BTC/USDT": {"notional": 15_000.0, "leverage": 4.0},  # 60k eff = 600%
            },
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        rule_ids = [a.rule_id for a in report.alerts]
        assert "total_exposure" in rule_ids

    def test_alert_when_margin_exceeds_equity(self) -> None:
        """Margin ≥ 100% of equity should trigger CRITICAL margin alert."""
        report = generate_daily_risk_report(
            equity=10_000.0,
            positions={
                "BTC/USDT": {"notional": 200_000.0, "margin": 15_000.0},
            },
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        margin_alerts = [a for a in report.alerts if a.rule_id == "margin_utilization"]
        assert len(margin_alerts) == 1
        assert margin_alerts[0].level == RiskLevel.CRITICAL

    def test_worst_alert_level_returns_highest_severity(self) -> None:
        report = generate_daily_risk_report(
            equity=10_000.0,
            positions={
                "BTC/USDT": {"notional": 200_000.0, "margin": 15_000.0},
                "ETH/USDT": {"notional": 50_000.0, "leverage": 5.0},
            },
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # Multiple alerts should fire; worst should be CRITICAL (margin ≥ 100%)
        assert report.worst_alert_level == RiskLevel.CRITICAL

    def test_worst_alert_level_none_when_no_alerts(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 10_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert report.worst_alert_level is None

    def test_alerts_use_nominal_intent_symbol(self) -> None:
        """Portfolio rules are evaluated with a snapshot intent that
        doesn't add new exposure — alerts reflect existing portfolio only."""
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 250_000.0},  # 250% gross → alert
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        # The alert context reflects the gross exposure ratio, not a trade.
        assert any(a.context.get("gross_exposure_pct", 0) > 2.0 for a in report.alerts)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_returns_raises(self) -> None:
        with pytest.raises(ValueError, match="returns must not be empty"):
            generate_daily_risk_report(
                equity=100_000.0,
                positions={"BTC/USDT": 50_000.0},
                returns=[],
            )

    def test_empty_confidences_raises(self) -> None:
        with pytest.raises(ValueError, match="confidences must not be empty"):
            generate_daily_risk_report(
                equity=100_000.0,
                positions={"BTC/USDT": 50_000.0},
                returns=_make_returns(),
                confidences=(),
            )

    def test_no_positions_still_computes_var(self) -> None:
        """VaR depends on returns, not positions. Empty positions = no alerts."""
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert len(report.var_estimates) == 2
        assert report.exposure.position_count == 0
        assert report.alerts == []
        # VaR is still computed from returns
        assert report.var_estimates[0].historical_var < 0

    def test_numpy_array_returns_accepted(self) -> None:
        returns_arr = np.array(_make_returns())
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={"BTC/USDT": 50_000.0},
            returns=returns_arr,
            monte_carlo_seed=42,
        )
        assert report.returns_sample_size == 500

    def test_custom_timestamp_used(self) -> None:
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=42,
            timestamp_ms=1700000000000,
        )
        assert report.timestamp == 1700000000000

    def test_report_is_immutable(self) -> None:
        """DailyRiskReport uses frozen=True config — snapshots are read-only."""
        report = generate_daily_risk_report(
            equity=100_000.0,
            positions={},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        with pytest.raises(Exception):
            report.total_equity = 999_999.0  # type: ignore[misc]

    def test_total_equity_propagated_to_report(self) -> None:
        report = generate_daily_risk_report(
            equity=250_000.0,
            positions={"BTC/USDT": 100_000.0},
            returns=_make_returns(),
            monte_carlo_seed=42,
        )
        assert report.total_equity == 250_000.0
        assert report.exposure.total_equity == 250_000.0
