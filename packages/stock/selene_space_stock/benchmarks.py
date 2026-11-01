"""Benchmark fetching and comparison logic.

Migrated from ai-market-pulse with Pydantic v2 model references.
Uses ``StockAsset`` instead of ``Asset`` and ``model_copy()`` instead of
``dataclasses.replace()``.
"""

from __future__ import annotations

from datetime import date


from selene_space_types.stock import (
    AssetAnalysis,
    BenchmarkComparison,
    BenchmarkSnapshot,
    DataFreshness,
    PriceSnapshot,
    StockAsset,
)

from selene_space_stock.config import BenchmarkSettings
from selene_space_stock.indicators import calculate_indicators
from selene_space_stock.market_data import MarketDataError, fetch_history


class BenchmarkData:
    """Container for a benchmark asset's fetched data and computed metrics."""

    __slots__ = ("asset", "snapshot", "metrics", "freshness")

    def __init__(
        self,
        asset: StockAsset,
        snapshot: PriceSnapshot,
        metrics: dict[str, float | int | str | None],
        freshness: DataFreshness,
    ) -> None:
        self.asset = asset
        self.snapshot = snapshot
        self.metrics = metrics
        self.freshness = freshness


def fetch_benchmarks(
    assets: list[StockAsset],
    settings: BenchmarkSettings,
    providers: list[str],
    lookback_days: int,
    today: date,
) -> tuple[dict[str, BenchmarkData], list[BenchmarkSnapshot]]:
    if not settings.enabled:
        return {}, []

    data: dict[str, BenchmarkData] = {}
    snapshots: list[BenchmarkSnapshot] = []
    for asset in _benchmark_assets(assets, settings):
        key = _symbol_key(asset.symbol)
        try:
            hydrated_asset, snapshot, history = fetch_history(asset, lookback_days, providers, as_of=today)
            metrics = calculate_indicators(history)
            freshness = build_data_freshness(snapshot, today, settings.stale_after_days)
            benchmark_data = BenchmarkData(
                asset=hydrated_asset,
                snapshot=snapshot,
                metrics=metrics,
                freshness=freshness,
            )
            data[key] = benchmark_data
            snapshots.append(_snapshot_from_data(benchmark_data))
        except MarketDataError as exc:
            freshness = DataFreshness(
                latest_date="",
                age_days=None,
                source=None,
                rows=0,
                status="missing",
                message=f"Benchmark unavailable: {exc}",
            )
            snapshots.append(
                BenchmarkSnapshot(
                    symbol=asset.symbol,
                    name=asset.name or asset.symbol,
                    market=asset.market,
                    currency=asset.currency,
                    last_close=None,
                    change_pct=None,
                    return_20d=None,
                    return_60d=None,
                    source=None,
                    freshness=freshness,
                )
            )
    return data, snapshots


def attach_benchmark_comparisons(
    analyses: list[AssetAnalysis],
    benchmarks: dict[str, BenchmarkData],
    settings: BenchmarkSettings,
) -> list[AssetAnalysis]:
    if not settings.enabled:
        return analyses

    enriched: list[AssetAnalysis] = []
    for analysis in analyses:
        benchmark_symbol = benchmark_symbol_for_asset(analysis.asset, settings)
        benchmark = benchmarks.get(_symbol_key(benchmark_symbol)) if benchmark_symbol else None
        comparison = _comparison(analysis, benchmark) if benchmark else None
        enriched.append(analysis.model_copy(update={"benchmark": comparison}))
    return enriched


def benchmark_symbol_for_asset(asset: StockAsset, settings: BenchmarkSettings) -> str | None:
    overrides = {_symbol_key(symbol): benchmark for symbol, benchmark in settings.compare.items()}
    selected = overrides.get(_symbol_key(asset.symbol))
    if not selected:
        selected = settings.default_by_market.get(asset.market.upper())
    if not selected:
        return None
    if _symbol_key(selected) == _symbol_key(asset.symbol):
        return _self_comparison_fallback(asset, settings)
    return selected


def build_data_freshness(snapshot: PriceSnapshot, today: date, stale_after_days: int = 4) -> DataFreshness:
    if snapshot.rows <= 0 or not snapshot.end_date:
        return DataFreshness(
            latest_date="",
            age_days=None,
            source=snapshot.source,
            rows=snapshot.rows,
            status="missing",
            message="No usable price history returned.",
        )
    try:
        latest = date.fromisoformat(snapshot.end_date)
        age_days = max((today - latest).days, 0)
    except ValueError:
        age_days = None
    if age_days is None:
        status = "unknown"
        message = f"Latest trading date could not be parsed from {snapshot.end_date}."
    elif age_days <= stale_after_days:
        status = "fresh"
        message = f"Latest trading day {snapshot.end_date}; source {snapshot.source or 'n/a'}."
    else:
        status = "stale"
        message = f"Latest trading day {snapshot.end_date}, {age_days} calendar days behind; verify data freshness."
    return DataFreshness(
        latest_date=snapshot.end_date,
        age_days=age_days,
        source=snapshot.source,
        rows=snapshot.rows,
        status=status,
        message=message,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _benchmark_assets(assets: list[StockAsset], settings: BenchmarkSettings) -> list[StockAsset]:
    symbols: list[str] = []
    symbols.extend(settings.symbols)
    symbols.extend(settings.compare.values())
    for asset in assets:
        selected = benchmark_symbol_for_asset(asset, settings)
        if selected:
            symbols.append(selected)

    seen: set[str] = set()
    result: list[StockAsset] = []
    for symbol in symbols:
        key = _symbol_key(symbol)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            StockAsset(
                symbol=symbol,
                name=settings.names.get(symbol) or settings.names.get(key) or symbol,
                market=_infer_market(symbol),
                tags=["benchmark"],
            )
        )
    return result


def _snapshot_from_data(data: BenchmarkData) -> BenchmarkSnapshot:
    return BenchmarkSnapshot(
        symbol=data.asset.symbol,
        name=data.asset.name or data.snapshot.name,
        market=data.asset.market,
        currency=data.snapshot.currency,
        last_close=data.snapshot.last_close,
        change_pct=data.snapshot.change_pct,
        return_20d=_float_metric(data.metrics, "return_20d"),
        return_60d=_float_metric(data.metrics, "return_60d"),
        source=data.snapshot.source,
        freshness=data.freshness,
    )


def _comparison(analysis: AssetAnalysis, benchmark: BenchmarkData) -> BenchmarkComparison:
    asset_20d = _float_metric(analysis.metrics, "return_20d")
    asset_60d = _float_metric(analysis.metrics, "return_60d")
    benchmark_20d = _float_metric(benchmark.metrics, "return_20d")
    benchmark_60d = _float_metric(benchmark.metrics, "return_60d")
    relative_20d = _relative(asset_20d, benchmark_20d)
    relative_60d = _relative(asset_60d, benchmark_60d)
    return BenchmarkComparison(
        symbol=benchmark.asset.symbol,
        name=benchmark.asset.name or benchmark.snapshot.name,
        market=benchmark.asset.market,
        source=benchmark.snapshot.source,
        latest_date=benchmark.snapshot.end_date,
        asset_return_20d=asset_20d,
        benchmark_return_20d=benchmark_20d,
        relative_return_20d=relative_20d,
        asset_return_60d=asset_60d,
        benchmark_return_60d=benchmark_60d,
        relative_return_60d=relative_60d,
        verdict=_verdict(relative_20d, relative_60d),
    )


def _relative(asset_return: float | None, benchmark_return: float | None) -> float | None:
    if asset_return is None or benchmark_return is None:
        return None
    return round(asset_return - benchmark_return, 6)


def _verdict(relative_20d: float | None, relative_60d: float | None) -> str:
    values = [value for value in [relative_20d, relative_60d] if value is not None]
    if not values:
        return "unknown"
    positive = sum(1 for value in values if value >= 0.02)
    negative = sum(1 for value in values if value <= -0.02)
    if positive and not negative:
        return "outperforming"
    if negative and not positive:
        return "underperforming"
    if all(abs(value) < 0.02 for value in values):
        return "tracking"
    return "mixed"


def _float_metric(metrics: dict[str, float | int | str | None], key: str) -> float | None:
    value = metrics.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _self_comparison_fallback(asset: StockAsset, settings: BenchmarkSettings) -> str | None:
    market = asset.market.upper()
    alternates = {
        "US": ["SPY", "QQQ"],
        "CN": ["000300.SS", "399300.SZ"],
        "HK": ["^HSI", "2800.HK"],
        "CRYPTO": ["BTC-USD", "ETH-USD"],
    }.get(market, [])
    for symbol in alternates:
        if _symbol_key(symbol) != _symbol_key(asset.symbol):
            return symbol
    return None


def _infer_market(symbol: str) -> str:
    upper = symbol.upper()
    if upper.endswith((".SS", ".SZ")) or upper.startswith(("000", "399")):
        return "CN"
    if upper.endswith(".HK") or upper.startswith("^HSI"):
        return "HK"
    if upper.endswith("-USD"):
        return "CRYPTO"
    return "US"


def _symbol_key(symbol: str | None) -> str:
    return (symbol or "").strip().upper()
