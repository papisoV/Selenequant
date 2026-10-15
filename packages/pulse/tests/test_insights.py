from __future__ import annotations

from ai_market_pulse.insights import build_insights
from ai_market_pulse.models import Asset, AssetAnalysis, PositionMetrics, PriceSnapshot, SignalScore


def test_build_insights_flags_position_loss_and_concentration() -> None:
    analysis = AssetAnalysis(
        asset=Asset(symbol="LOSS", quantity=10, cost_basis=100),
        snapshot=PriceSnapshot(
            symbol="LOSS",
            name="Loss Corp",
            currency="USD",
            last_close=80,
            previous_close=85,
            change_pct=-0.0588,
            start_date="2026-01-01",
            end_date="2026-07-08",
            rows=100,
            source="unit",
        ),
        metrics={
            "last_close": 80,
            "sma20": 90,
            "sma50": 95,
            "sma200": 100,
            "rsi14": 28,
            "drawdown_60d": -0.22,
        },
        signal=SignalScore(score=20, stance="high risk", risk_level="high", reasons=[]),
        news=[],
        position=PositionMetrics(
            symbol="LOSS",
            currency="USD",
            quantity=10,
            cost_basis=100,
            cost_value=1000,
            market_value=800,
            day_pnl=-50,
            unrealized_pnl=-200,
            unrealized_pnl_pct=-0.2,
            allocation_pct=0.75,
        ),
    )

    insights = build_insights([analysis])

    assert insights.attention
    assert insights.attention[0].symbol == "LOSS"
    assert any(item.rule == "position_loss" for item in insights.risk_findings)
    assert any(item.rule == "concentration" for item in insights.risk_findings)
    assert insights.checklist
