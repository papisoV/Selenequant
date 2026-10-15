from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ai_market_pulse.models import (
    Asset,
    AssetAnalysis,
    AttentionItem,
    BenchmarkSnapshot,
    ChecklistItem,
    ContributionItem,
    DailyReport,
    DataFreshness,
    HistoryPoint,
    InsightSummary,
    NewsItem,
    PortfolioSummary,
    PriceSnapshot,
    RiskFinding,
    SignalScore,
)


def _analysis() -> AssetAnalysis:
    return AssetAnalysis(
        asset=Asset(symbol="ABC", name="ABC Corp", quantity=10, cost_basis=90),
        snapshot=PriceSnapshot(
            symbol="ABC",
            name="ABC Corp",
            currency="USD",
            last_close=120,
            previous_close=115,
            change_pct=0.0435,
            start_date="2026-01-01",
            end_date="2026-07-07",
            rows=100,
        ),
        metrics={"return_20d": 0.1, "rsi14": 55},
        signal=SignalScore(score=60, stance="watch bullish", risk_level="low", reasons=["reason"]),
        news=[NewsItem(title="news", link="https://example.com")],
    )


def _history_point() -> HistoryPoint:
    return HistoryPoint(
        date="2026-07-07",
        symbol="ABC",
        close=120,
        score=60,
        stance="watch bullish",
        risk_level="low",
    )


def _freshness() -> DataFreshness:
    return DataFreshness(
        latest_date="2026-07-08",
        age_days=0,
        source="yfinance",
        rows=220,
        status="fresh",
        message="ok",
    )


def _benchmark_snapshot() -> BenchmarkSnapshot:
    return BenchmarkSnapshot(
        symbol="SPY",
        name="S&P 500 ETF",
        market="US",
        currency="USD",
        last_close=741.38,
        change_pct=-0.0085,
        return_20d=0.0029,
        return_60d=0.0911,
        source="yfinance",
        freshness=_freshness(),
    )


def _insight_summary() -> InsightSummary:
    return InsightSummary(
        attention=[
            AttentionItem(
                symbol="ABC",
                priority=1,
                reason="RSI overbought",
                has_position=True,
                risk_level="high",
            )
        ],
        risk_findings=[
            RiskFinding(symbol="ABC", severity="high", rule="rsi", message="RSI too high", value=80)
        ],
        day_contributors=[
            ContributionItem(
                symbol="ABC",
                currency="USD",
                day_pnl=50,
                unrealized_pnl=300,
                market_value=1200,
                allocation_pct=1,
            )
        ],
        unrealized_contributors=[
            ContributionItem(
                symbol="ABC",
                currency="USD",
                day_pnl=50,
                unrealized_pnl=300,
                market_value=1200,
                allocation_pct=1,
            )
        ],
        checklist=[ChecklistItem(text="Review ABC position", priority="high", symbol="ABC")],
    )


def _report() -> DailyReport:
    return DailyReport(
        title="Daily Pulse",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        analyses=[_analysis()],
        market_brief="brief",
        portfolio=[
            PortfolioSummary(
                currency="USD",
                positions=1,
                market_value=1200,
                cost_value=900,
                day_pnl=50,
                unrealized_pnl=300,
                unrealized_pnl_pct=0.333,
            )
        ],
        history={"ABC": [_history_point()]},
        insights=_insight_summary(),
        benchmarks=[_benchmark_snapshot()],
        portfolio_ai_summary="Portfolio state: calm.",
    )


def _assert_json_serializable(value: Any) -> None:
    json.dumps(value)


def test_to_jsonable_returns_json_serializable_structure() -> None:
    report = _report()

    result = report.model_dump(mode="json")

    _assert_json_serializable(result)


def test_to_jsonable_round_trips_top_level_fields() -> None:
    report = _report()

    result = report.model_dump(mode="json")

    assert result["title"] == "Daily Pulse"
    assert result["generated_at"] == "2026-07-08T08:00:00"
    assert result["timezone"] == "UTC"
    assert result["language"] == "en-US"
    assert result["market_brief"] == "brief"
    assert result["portfolio_ai_summary"] == "Portfolio state: calm."


def test_to_jsonable_round_trips_history() -> None:
    report = _report()

    result = report.model_dump(mode="json")

    assert "ABC" in result["history"]
    history_points = result["history"]["ABC"]
    assert len(history_points) == 1
    assert history_points[0]["symbol"] == "ABC"
    assert history_points[0]["date"] == "2026-07-07"
    assert history_points[0]["close"] == 120


def test_to_jsonable_round_trips_insights() -> None:
    report = _report()

    result = report.model_dump(mode="json")

    insights = result["insights"]
    assert insights["attention"][0]["symbol"] == "ABC"
    assert insights["attention"][0]["reason"] == "RSI overbought"
    assert insights["risk_findings"][0]["rule"] == "rsi"
    assert insights["day_contributors"][0]["market_value"] == 1200
    assert insights["unrealized_contributors"][0]["unrealized_pnl"] == 300
    assert insights["checklist"][0]["text"] == "Review ABC position"


def test_to_jsonable_round_trips_benchmarks_with_nested_freshness() -> None:
    report = _report()

    result = report.model_dump(mode="json")

    benchmark = result["benchmarks"][0]
    assert benchmark["symbol"] == "SPY"
    assert benchmark["freshness"]["status"] == "fresh"
    assert benchmark["freshness"]["source"] == "yfinance"


def test_to_jsonable_round_trips_analyses_and_portfolio() -> None:
    report = _report()

    result = report.model_dump(mode="json")

    analysis = result["analyses"][0]
    assert analysis["asset"]["symbol"] == "ABC"
    assert analysis["snapshot"]["last_close"] == 120
    assert analysis["signal"]["stance"] == "watch bullish"
    assert analysis["news"][0]["title"] == "news"
    assert analysis["position"] is None
    assert analysis["benchmark"] is None
    assert analysis["freshness"] is None

    portfolio = result["portfolio"][0]
    assert portfolio["market_value"] == 1200
    assert portfolio["currency"] == "USD"


def test_to_jsonable_handles_empty_report() -> None:
    report = DailyReport(
        title="Empty",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        analyses=[],
        market_brief="",
    )

    result = report.model_dump(mode="json")

    _assert_json_serializable(result)
    assert result["portfolio"] == []
    assert result["history"] == {}
    assert result["benchmarks"] == []
    assert result["analyses"] == []
    assert result["insights"]["attention"] == []
