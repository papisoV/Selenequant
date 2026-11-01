"""Daily risk report — aggregates VaR/CVaR + portfolio exposure metrics.

Produces a point-in-time snapshot of portfolio risk suitable for:
  - End-of-day risk review
  - Pre-market open sanity check
  - Dashboard / analytics endpoint backing

Combines three risk dimensions:
  1. Statistical VaR/CVaR (historical / parametric / monte carlo)
  2. Portfolio exposure ratios (gross / net / margin utilization)
  3. Rule-based alerts from the three portfolio rules

The report is read-only — it does not mutate state or activate kill switch.
Callers (dashboard, scheduler) decide how to surface the report.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from selene_space_risk.portfolio_rules import (
    MarginUtilizationRule,
    NetExposureRule,
    TotalExposureRule,
)
from selene_space_risk.var.cvar import compute_cvar
from selene_space_risk.var.historical_var import compute_historical_var
from selene_space_risk.var.monte_carlo_var import compute_monte_carlo_var
from selene_space_risk.var.parametric_var import compute_parametric_var
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


_DEFAULT_CONFIDENCES: tuple[float, ...] = (0.95, 0.99)


class VarEstimate(BaseModel):
    """VaR/CVaR pair at a single confidence level, across all methods."""

    confidence: float = Field(..., description="Confidence level (e.g. 0.95)")
    historical_var: float = Field(..., description="Historical VaR (negative = loss)")
    parametric_var: float = Field(..., description="Parametric (variance-covariance) VaR")
    monte_carlo_var: float = Field(..., description="Monte Carlo VaR")
    cvar: float = Field(..., description="CVaR / expected shortfall (historical)")

    model_config = ConfigDict(frozen=True)


class PortfolioExposure(BaseModel):
    """Aggregate exposure ratios — mirrors the three portfolio rules."""

    gross_exposure_pct: float = Field(0.0, description="sum(|notional| * leverage) / equity")
    net_exposure_pct: float = Field(0.0, description="(longs - shorts) / equity")
    margin_utilization_pct: float = Field(0.0, description="total_margin / equity")
    position_count: int = Field(0, description="Number of open positions")
    total_equity: float = Field(0.0, description="Account equity used as denominator")

    model_config = ConfigDict(frozen=True)


class DailyRiskReport(BaseModel):
    """End-of-day (or any point-in-time) portfolio risk snapshot.

    Combines statistical VaR/CVaR with portfolio exposure metrics and
    rule-triggered alerts. All fields are read-only — this is a snapshot,
    not a mutation request.
    """

    timestamp: int = Field(..., description="Report generation time (ms epoch)")
    total_equity: float = Field(..., description="Account equity at report time")
    var_estimates: list[VarEstimate] = Field(
        default_factory=list,
        description="VaR/CVaR at each requested confidence level",
    )
    exposure: PortfolioExposure = Field(
        default_factory=PortfolioExposure,
        description="Aggregate portfolio exposure ratios",
    )
    alerts: list[RiskAlert] = Field(
        default_factory=list,
        description="Rule alerts triggered by current portfolio state",
    )
    returns_sample_size: int = Field(0, description="Number of return observations used for VaR")

    model_config = ConfigDict(frozen=True)

    @property
    def worst_alert_level(self) -> RiskLevel | None:
        """Highest severity among alerts, or None if no alerts."""
        if not self.alerts:
            return None
        severity_order = {
            RiskLevel.INFO: 0,
            RiskLevel.WARNING: 1,
            RiskLevel.RESTRICTED: 2,
            RiskLevel.DANGEROUS: 3,
            RiskLevel.CRITICAL: 4,
        }
        return max(self.alerts, key=lambda a: severity_order.get(a.level, 0)).level


def _build_nominal_intent() -> TradeIntent:
    """Construct a zero-quantity intent for portfolio rule evaluation.

    Portfolio rules evaluate ``intent + existing positions``. For a daily
    snapshot we have no new intent — we only want to assess the current
    portfolio. A zero-quantity, zero-price intent ensures the rules measure
    existing exposure without adding any new-trade contribution.
    """
    return TradeIntent(
        symbol="__PORTFOLIO_SNAPSHOT__",
        side=OrderSide.BUY,
        quantity=1e-10,  # gt=0 constraint requires non-zero; negligible vs any real position
        price=0.0,
        order_type=OrderType.MARKET,
        reasoning="daily_risk_report.snapshot",
        confidence=1.0,
        risk_params={"stop_loss": 0.0, "take_profit": 0.0, "leverage": 1.0},
        timestamp=int(time.time() * 1000),
    )


def _compute_exposure(
    equity: float,
    positions: dict[str, Any],
) -> PortfolioExposure:
    """Compute aggregate exposure ratios without running the rules.

    Mirrors the math in TotalExposureRule / NetExposureRule /
    MarginUtilizationRule but returns raw ratios rather than alerts.
    Useful for the report's ``exposure`` field even when no rule fires.
    """
    if equity <= 0:
        return PortfolioExposure(
            gross_exposure_pct=0.0,
            net_exposure_pct=0.0,
            margin_utilization_pct=0.0,
            position_count=len(positions),
            total_equity=equity,
        )

    gross = 0.0
    net = 0.0
    margin = 0.0
    default_leverage = 10.0

    for symbol, pos in positions.items():
        if isinstance(pos, dict):
            notional = abs(float(pos.get("notional", 0.0)))
            leverage = float(pos.get("leverage", 1.0))
            signed_notional = float(pos.get("notional", 0.0))
            side = pos.get("side", "")
            if side in ("sell", "short"):
                signed_notional = -abs(signed_notional)
            if "margin" in pos:
                pos_margin = float(pos["margin"])
            else:
                pos_margin = notional / leverage if leverage > 0 else notional
        else:
            notional = abs(float(pos))
            leverage = 1.0
            signed_notional = float(pos)
            pos_margin = notional / default_leverage

        gross += notional * leverage
        net += signed_notional
        margin += pos_margin

    return PortfolioExposure(
        gross_exposure_pct=gross / equity,
        net_exposure_pct=net / equity,
        margin_utilization_pct=margin / equity,
        position_count=len(positions),
        total_equity=equity,
    )


def generate_daily_risk_report(
    *,
    equity: float,
    positions: dict[str, Any],
    returns: list[float] | np.ndarray,
    confidences: tuple[float, ...] = _DEFAULT_CONFIDENCES,
    monte_carlo_seed: int | None = None,
    timestamp_ms: int | None = None,
) -> DailyRiskReport:
    """Generate a daily portfolio risk report.

    Args:
        equity: Total account equity (cash + unrealized PnL).
        positions: symbol → notional (float) or symbol → dict
            (notional/leverage/margin/side). Same shape accepted by
            portfolio_rules.
        returns: Historical return series for VaR/CVaR estimation.
            Typically daily portfolio returns over the last N days.
        confidences: Confidence levels to compute (default 95% + 99%).
        monte_carlo_seed: RNG seed for reproducible MC VaR.
        timestamp_ms: Override report timestamp (default: now).

    Returns:
        DailyRiskReport with VaR estimates, exposure ratios, and alerts.

    Raises:
        ValueError: If returns is empty or confidences is empty.
    """
    if len(returns) == 0:
        raise ValueError("returns must not be empty — need historical series for VaR")
    if len(confidences) == 0:
        raise ValueError("confidences must not be empty")

    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    returns_arr = np.asarray(returns, dtype=np.float64)

    var_estimates: list[VarEstimate] = []
    for conf in confidences:
        var_estimates.append(
            VarEstimate(
                confidence=conf,
                historical_var=compute_historical_var(returns_arr, confidence=conf),
                parametric_var=compute_parametric_var(returns_arr, confidence=conf),
                monte_carlo_var=compute_monte_carlo_var(
                    returns_arr,
                    confidence=conf,
                    seed=monte_carlo_seed,
                ),
                cvar=compute_cvar(returns_arr, confidence=conf),
            )
        )

    exposure = _compute_exposure(equity, positions)

    # Run portfolio rules with a nominal zero-quantity intent to surface
    # alerts on the current portfolio state alone.
    intent = _build_nominal_intent()
    context = {"total_equity": equity, "positions": positions}
    alerts: list[RiskAlert] = []
    for rule in (TotalExposureRule(), NetExposureRule(), MarginUtilizationRule()):
        alert = rule.check(intent, context)
        if alert is not None:
            alerts.append(alert)

    return DailyRiskReport(
        timestamp=ts,
        total_equity=equity,
        var_estimates=var_estimates,
        exposure=exposure,
        alerts=alerts,
        returns_sample_size=len(returns_arr),
    )
