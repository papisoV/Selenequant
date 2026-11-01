"""Scoring engine for Hyperliquid address classification."""

from __future__ import annotations

import logging

from app.models.hyperliquid import (
    CopySignal,
    DataQuality,
    LeaderboardInfo,
    ScreenedAddress,
    ScreenedAddressDimensions,
    ScreenedAddressMetrics,
)

logger = logging.getLogger(__name__)


def classify_address(pnl: float, roi: float, win_rate: float) -> str:
    """Classify address as elite/inverse/neutral based on performance."""
    if pnl > 0 and roi > 0.1 and win_rate > 0.55:
        return "elite"
    if pnl < 0 and roi < -0.1:
        return "inverse"
    return "neutral"


def compute_score(
    pnl: float,
    roi: float,
    win_rate: float,
    profit_factor: float,
    max_drawdown: float,
    curve_stability: int,
) -> int:
    """Compute 0-100 composite score."""
    pnl_score = min(100, max(0, 50 + roi * 500))
    win_score = win_rate * 100
    pf_score = min(100, profit_factor * 30) if profit_factor > 0 else 0
    dd_score = max(0, 100 - max_drawdown * 1000)
    stability_score = curve_stability

    composite = pnl_score * 0.35 + win_score * 0.25 + pf_score * 0.2 + dd_score * 0.1 + stability_score * 0.1
    return max(0, min(100, int(composite)))


def compute_dimensions(
    pnl: float,
    roi: float,
    win_rate: float,
    profit_factor: float,
    max_drawdown: float,
    closed_trades: int,
    curve_stability: int,
) -> ScreenedAddressDimensions:
    profitability = min(100, max(0, int(50 + roi * 500)))
    consistency = min(100, max(0, int(curve_stability)))
    risk = min(100, max(0, int(100 - max_drawdown * 1000)))
    sample_quality = min(100, max(0, int(closed_trades / 30 * 100)))
    copyability = min(100, max(0, int((profitability + consistency + risk) / 3)))
    return ScreenedAddressDimensions(
        profitability=profitability,
        consistency=consistency,
        risk=risk,
        sample_quality=sample_quality,
        copyability=copyability,
    )


def compute_copy_signal(
    address: str, classification: str, score: int, risk_flags: list[str]
) -> CopySignal:
    direction = {"elite": "follow", "inverse": "invert"}.get(classification, "ignore")
    return CopySignal(
        address=address,
        classification=classification,
        score=score,
        copy_direction=direction,
        risk_flags=risk_flags,
    )


def build_screened_address(
    address: str,
    leaderboard: dict | None = None,
    fills: list[dict] | None = None,
    window_days: int = 7,
) -> ScreenedAddress:
    """Build a ScreenedAddress from raw Hyperliquid data."""
    pnl = 0.0
    roi = 0.0
    account_value = 0.0
    volume = 0.0
    display_name = None

    if leaderboard:
        pnl = float(leaderboard.get("windowPnl", 0) or 0)
        roi = float(leaderboard.get("windowRoi", 0) or 0)
        account_value = float(leaderboard.get("accountValue", 0) or 0)
        volume = float(leaderboard.get("windowVolume", 0) or 0)
        display_name = leaderboard.get("displayName")

    # Compute metrics from fills
    closed_trades = len(fills) if fills else 0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    total_fees = 0.0

    if fills:
        for fill in fills:
            fill_pnl = float(fill.get("closedPnl", 0) or 0)
            fee = abs(float(fill.get("fee", 0) or 0))
            total_fees += fee
            if fill_pnl > 0:
                gross_profit += fill_pnl
                wins += 1
            elif fill_pnl < 0:
                gross_loss += abs(fill_pnl)
                losses += 1

    win_rate = wins / closed_trades if closed_trades > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (1.0 if gross_profit > 0 else 0.0)
    max_drawdown = 0.0  # Requires equity curve; approximate from losses
    if gross_loss > 0 and account_value > 0:
        max_drawdown = min(1.0, gross_loss / account_value)

    classification = classify_address(pnl, roi, win_rate)
    score = compute_score(pnl, roi, win_rate, profit_factor, max_drawdown, 50)
    dimensions = compute_dimensions(pnl, roi, win_rate, profit_factor, max_drawdown, closed_trades, 50)

    min_trades = 30
    insufficient = closed_trades < min_trades
    data_quality = DataQuality(
        insufficient=insufficient,
        leaderboard_only=closed_trades == 0,
        closed_trades=closed_trades,
        min_trades=min_trades,
    )

    risk_flags = []
    if insufficient:
        risk_flags.append("insufficient_sample")
    if max_drawdown > 0.5:
        risk_flags.append("high_drawdown")

    metrics = ScreenedAddressMetrics(
        closed_trades=closed_trades,
        realized_pnl=pnl,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        win_rate=win_rate,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        volume=volume,
        fees=total_fees,
        losing_trades=losses,
        data_quality=data_quality,
    )

    lb_info = None
    if leaderboard:
        lb_info = LeaderboardInfo(
            account_value=account_value,
            window_pnl=pnl,
            window_roi=roi,
            window_volume=volume,
            all_time_pnl=float(leaderboard.get("allTimePnl", 0) or 0),
            all_time_roi=float(leaderboard.get("allTimeRoi", 0) or 0),
            display_name=display_name,
        )

    copy_signal = compute_copy_signal(address, classification, score, risk_flags)

    return ScreenedAddress(
        address=address,
        classification=classification,
        score=score,
        metrics=metrics,
        dimensions=dimensions,
        reasons=[f"{classification}_strategy"],
        risk_flags=risk_flags,
        data_quality=data_quality,
        leaderboard=lb_info,
        copy_signal=copy_signal,
    )
