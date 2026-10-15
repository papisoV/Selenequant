from __future__ import annotations

from datetime import datetime

from ai_market_pulse.history import attach_history, records_from_report
from ai_market_pulse.models import (
    Asset,
    AssetAnalysis,
    DailyReport,
    HistoryPoint,
    NewsItem,
    PriceSnapshot,
    SignalScore,
)
from ai_market_pulse.portfolio import enrich_portfolio


def test_enrich_portfolio_computes_position_and_summary() -> None:
    analysis = _analysis(quantity=10, cost_basis=90, last_close=120, previous_close=115)

    enriched, summary = enrich_portfolio([analysis])

    assert len(summary) == 1
    assert summary[0].currency == "USD"
    assert summary[0].market_value == 1200
    assert summary[0].day_pnl == 50
    assert summary[0].unrealized_pnl == 300
    assert enriched[0].position is not None
    assert enriched[0].position.allocation_pct == 1


def test_attach_history_merges_current_report() -> None:
    analysis = _analysis(quantity=2, cost_basis=100, last_close=110, previous_close=108)
    analyses, portfolio = enrich_portfolio([analysis])
    report = DailyReport(
        title="Test",
        generated_at=datetime(2026, 7, 7, 8, 0),
        timezone="UTC",
        language="en-US",
        analyses=analyses,
        market_brief="brief",
        portfolio=portfolio,
    )
    previous = [
        HistoryPoint(
            date="2026-07-06",
            symbol="ABC",
            close=105,
            score=55,
            stance="neutral",
            risk_level="low",
        )
    ]

    with_history = attach_history(report, previous)
    records = records_from_report(report)

    assert len(records) == 1
    assert records[0].market_value == 220
    assert records[0].currency == "USD"
    assert records[0].day_pnl == 4
    assert [point.date for point in with_history.history["ABC"]] == ["2026-07-06", "2026-07-07"]


def _analysis(
    quantity: float,
    cost_basis: float,
    last_close: float,
    previous_close: float,
) -> AssetAnalysis:
    return AssetAnalysis(
        asset=Asset(symbol="ABC", name="ABC Corp", quantity=quantity, cost_basis=cost_basis),
        snapshot=PriceSnapshot(
            symbol="ABC",
            name="ABC Corp",
            currency="USD",
            last_close=last_close,
            previous_close=previous_close,
            change_pct=last_close / previous_close - 1,
            start_date="2026-01-01",
            end_date="2026-07-07",
            rows=100,
        ),
        metrics={"last_close": last_close},
        signal=SignalScore(score=60, stance="watch bullish", risk_level="low", reasons=["reason"]),
        news=[NewsItem(title="news", link="https://example.com")],
    )
