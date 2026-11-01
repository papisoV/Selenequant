from __future__ import annotations

from ai_market_pulse.dashboard import build_dashboard_data, render_dashboard
from ai_market_pulse.models import HistoryPoint


def test_dashboard_data_groups_portfolio_by_currency_and_date() -> None:
    records = [
        _point("2026-07-06", "AAA", 50, "low", "USD", market_value=1000, day_pnl=10),
        _point("2026-07-06", "BBB", 40, "medium", "USD", market_value=500, day_pnl=-5),
        _point("2026-07-07", "AAA", 60, "low", "USD", market_value=1200, day_pnl=20),
        _point("2026-07-07", "BBB", 25, "high", "USD", market_value=450, day_pnl=-50),
        _point("2026-07-07", "CCC", 70, "low", "CNY", market_value=800, day_pnl=30),
    ]

    data = build_dashboard_data(records)

    usd = next(series for series in data.portfolio_series if series.currency == "USD")
    assert usd.points == [("2026-07-06", 1500), ("2026-07-07", 1650)]
    assert data.symbol_trends[0].score_delta == 10
    assert data.risk_leaders[0].symbol == "BBB"
    assert data.contribution_leaders[0].symbol == "BBB"


def test_render_dashboard_contains_main_sections() -> None:
    data = build_dashboard_data(
        [
            _point("2026-07-06", "AAA", 50, "low", "USD", market_value=1000, day_pnl=10),
            _point("2026-07-07", "AAA", 60, "medium", "USD", market_value=1100, day_pnl=25),
        ]
    )

    html = render_dashboard(data)

    assert "Portfolio Net Value" in html
    assert "Score Changes" in html
    assert "Risk Board" in html
    assert "Contribution Board" in html
    assert "AAA" in html
    assert "data-lang-choice" in html
    assert "组合研究驾驶舱" in html
    assert "data-symbol-filter" in html
    assert "data-risk-filter" in html
    assert "data-relative-filter" in html
    assert "data-detail-summary" in html
    assert "dashboard-data" in html
    assert "data-symbol-card" in html
    assert html.count("<tr data-symbol-row") == 3


def test_render_dashboard_places_score_changes_before_portfolio_net_value() -> None:
    data = build_dashboard_data(
        [
            _point("2026-07-06", "AAA", 50, "low", "USD", market_value=1000, day_pnl=10),
            _point("2026-07-07", "AAA", 60, "medium", "USD", market_value=1100, day_pnl=25),
        ]
    )

    rendered = render_dashboard(data)

    score_changes_index = rendered.index("Score Changes")
    portfolio_net_value_index = rendered.index("Portfolio Net Value")
    assert score_changes_index < portfolio_net_value_index


def _point(
    date: str,
    symbol: str,
    score: int,
    risk: str,
    currency: str,
    market_value: float,
    day_pnl: float,
) -> HistoryPoint:
    return HistoryPoint(
        date=date,
        symbol=symbol,
        close=100,
        score=score,
        stance="neutral",
        risk_level=risk,
        currency=currency,
        change_pct=0.01,
        market_value=market_value,
        day_pnl=day_pnl,
        unrealized_pnl=market_value * 0.1,
        unrealized_pnl_pct=0.1,
    )
