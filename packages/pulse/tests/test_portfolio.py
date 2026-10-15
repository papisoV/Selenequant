from __future__ import annotations

from ai_market_pulse.models import Asset, AssetAnalysis, NewsItem, PriceSnapshot, SignalScore
from ai_market_pulse.portfolio import enrich_portfolio


def test_enrich_portfolio_skips_asset_with_no_quantity() -> None:
    analysis = _analysis(symbol="ABC", quantity=None, cost_basis=90, last_close=120, previous_close=115)

    enriched, summary = enrich_portfolio([analysis])

    assert enriched[0].position is None
    assert summary == []


def test_enrich_portfolio_skips_asset_with_zero_quantity() -> None:
    analysis = _analysis(symbol="ABC", quantity=0, cost_basis=90, last_close=120, previous_close=115)

    enriched, summary = enrich_portfolio([analysis])

    assert enriched[0].position is None
    assert summary == []


def test_enrich_portfolio_handles_missing_cost_basis() -> None:
    analysis = _analysis(symbol="ABC", quantity=10, cost_basis=None, last_close=120, previous_close=115)

    enriched, _summary = enrich_portfolio([analysis])

    position = enriched[0].position
    assert position is not None
    assert position.cost_value is None
    assert position.unrealized_pnl is None
    assert position.unrealized_pnl_pct is None
    assert position.market_value == 1200
    assert position.day_pnl == 50


def test_enrich_portfolio_allocation_pct_is_independent_per_currency() -> None:
    usd_analysis = _analysis(
        symbol="ABC", quantity=10, cost_basis=90, last_close=120, previous_close=115, currency="USD"
    )
    eur_analysis = _analysis(
        symbol="XYZ", quantity=5, cost_basis=40, last_close=50, previous_close=48, currency="EUR"
    )

    enriched, summary = enrich_portfolio([usd_analysis, eur_analysis])

    usd_position = enriched[0].position
    eur_position = enriched[1].position
    assert usd_position is not None
    assert eur_position is not None
    assert usd_position.allocation_pct == 1
    assert eur_position.allocation_pct == 1
    assert len(summary) == 2


def test_enrich_portfolio_offsetting_positions_get_gross_exposure_allocation() -> None:
    long_analysis = _analysis(
        symbol="ABC", quantity=10, cost_basis=90, last_close=10, previous_close=9, currency="USD"
    )
    short_analysis = _analysis(
        symbol="XYZ", quantity=-10, cost_basis=90, last_close=10, previous_close=9, currency="USD"
    )

    enriched, summary = enrich_portfolio([long_analysis, short_analysis])

    long_position = enriched[0].position
    short_position = enriched[1].position
    assert long_position is not None
    assert short_position is not None
    assert long_position.market_value == 100
    assert short_position.market_value == -100
    assert long_position.allocation_pct == 0.5
    assert short_position.allocation_pct == -0.5
    assert len(summary) == 1


def _analysis(
    symbol: str,
    quantity: float | None,
    cost_basis: float | None,
    last_close: float,
    previous_close: float,
    currency: str = "USD",
) -> AssetAnalysis:
    return AssetAnalysis(
        asset=Asset(symbol=symbol, name=f"{symbol} Corp", quantity=quantity, cost_basis=cost_basis),
        snapshot=PriceSnapshot(
            symbol=symbol,
            name=f"{symbol} Corp",
            currency=currency,
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
