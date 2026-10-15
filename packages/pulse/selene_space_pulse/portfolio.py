from __future__ import annotations

from .models import AssetAnalysis, PortfolioSummary, PositionMetrics


def enrich_portfolio(analyses: list[AssetAnalysis]) -> tuple[list[AssetAnalysis], list[PortfolioSummary]]:
    positions = [_build_position(item) for item in analyses]
    totals_by_currency: dict[str, float] = {}
    for position in positions:
        if position:
            totals_by_currency[position.currency] = totals_by_currency.get(position.currency, 0) + abs(
                position.market_value
            )

    enriched: list[AssetAnalysis] = []
    for analysis, position in zip(analyses, positions):
        if position:
            total = totals_by_currency.get(position.currency, 0)
            allocation = position.market_value / total if total else None
            position = position.model_copy(update={"allocation_pct": _round(allocation)})
        enriched.append(analysis.model_copy(update={"position": position}))

    return enriched, _summaries([item.position for item in enriched if item.position])


def _build_position(analysis: AssetAnalysis) -> PositionMetrics | None:
    quantity = analysis.asset.quantity
    if quantity in (None, 0) or analysis.snapshot.rows <= 0 or analysis.snapshot.last_close <= 0:
        return None

    currency = analysis.snapshot.currency or analysis.asset.currency or "UNKNOWN"
    last_close = float(analysis.snapshot.last_close)
    previous_close = analysis.snapshot.previous_close
    cost_basis = analysis.asset.cost_basis
    market_value = float(quantity) * last_close
    cost_value = float(quantity) * float(cost_basis) if cost_basis is not None else None
    day_pnl = None
    if previous_close not in (None, 0):
        day_pnl = float(quantity) * (last_close - float(previous_close))
    unrealized_pnl = market_value - cost_value if cost_value is not None else None
    unrealized_pnl_pct = unrealized_pnl / cost_value if cost_value else None

    return PositionMetrics(
        symbol=analysis.asset.symbol,
        currency=currency,
        quantity=float(quantity),
        cost_basis=_round(cost_basis),
        cost_value=_round(cost_value),
        market_value=_round(market_value) or 0,
        day_pnl=_round(day_pnl),
        unrealized_pnl=_round(unrealized_pnl),
        unrealized_pnl_pct=_round(unrealized_pnl_pct),
    )


def _summaries(positions: list[PositionMetrics]) -> list[PortfolioSummary]:
    grouped: dict[str, list[PositionMetrics]] = {}
    for position in positions:
        grouped.setdefault(position.currency, []).append(position)

    summaries: list[PortfolioSummary] = []
    for currency, currency_positions in sorted(grouped.items()):
        market_value = sum(item.market_value for item in currency_positions)
        cost_values = [item.cost_value for item in currency_positions if item.cost_value is not None]
        day_values = [item.day_pnl for item in currency_positions if item.day_pnl is not None]
        unrealized_values = [item.unrealized_pnl for item in currency_positions if item.unrealized_pnl is not None]
        cost_value = sum(cost_values) if cost_values else None
        day_pnl = sum(day_values) if day_values else None
        unrealized_pnl = sum(unrealized_values) if unrealized_values else None
        unrealized_pnl_pct = unrealized_pnl / cost_value if cost_value and unrealized_pnl is not None else None
        summaries.append(
            PortfolioSummary(
                currency=currency,
                positions=len(currency_positions),
                market_value=_round(market_value) or 0,
                cost_value=_round(cost_value),
                day_pnl=_round(day_pnl),
                unrealized_pnl=_round(unrealized_pnl),
                unrealized_pnl_pct=_round(unrealized_pnl_pct),
            )
        )
    return summaries


def _round(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)
