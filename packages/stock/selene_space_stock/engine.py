"""Main analysis engine — orchestrates data fetching, scoring, and report generation.

Migrated from ai-market-pulse.  Accepts ``StockConfig`` instead of
``AppConfig`` and uses ``model_copy(update={...})`` instead of
``dataclasses.replace()``.

NOTE: The ``run_analysis`` function depends on optional modules (``news``,
``llm``) that are not yet migrated.  It is guarded with a try/except import
in ``__init__.py`` so the rest of the package remains usable without them.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from selene_space_types.stock import (
    AssetAnalysis,
    DataFreshness,
    DailyReport,
    PriceSnapshot,
    StockAsset,
)

from selene_space_stock.benchmarks import attach_benchmark_comparisons, build_data_freshness, fetch_benchmarks
from selene_space_stock.config import StockConfig
from selene_space_stock.indicators import calculate_indicators
from selene_space_stock.insights import build_insights
from selene_space_stock.market_data import MarketDataError, fetch_history
from selene_space_stock.portfolio import enrich_portfolio
from selene_space_stock.scoring import score_asset


def run_analysis(config: StockConfig, config_path: str | None = None) -> DailyReport:
    """Run the full daily analysis pipeline and return a ``DailyReport``."""
    generated_at = datetime.now(ZoneInfo(config.timezone))
    analyses: list[AssetAnalysis] = []
    for asset in config.assets:
        try:
            hydrated_asset, snapshot, history = fetch_history(
                asset,
                config.analysis.lookback_days,
                config.data.providers,
                as_of=generated_at.date(),
            )
            metrics = calculate_indicators(history)
            freshness = build_data_freshness(snapshot, generated_at.date(), config.benchmarks.stale_after_days)
            warnings = _warnings(snapshot, config.analysis.min_history_rows, freshness)
            signal = score_asset(metrics)
            # NOTE: news and LLM summarization are not yet migrated;
            # placeholder empty lists / None values are used.
            news: list = []
            analysis = AssetAnalysis(
                asset=hydrated_asset,
                snapshot=snapshot,
                metrics=metrics,
                signal=signal,
                news=news,
                warnings=warnings,
                freshness=freshness,
            )
            analyses.append(analysis)
        except MarketDataError as exc:
            analyses.append(_failed_analysis(asset, str(exc)))

    benchmark_data, benchmark_snapshots = fetch_benchmarks(
        [analysis.asset for analysis in analyses],
        config.benchmarks,
        config.data.providers,
        config.analysis.lookback_days,
        generated_at.date(),
    )
    analyses = attach_benchmark_comparisons(analyses, benchmark_data, config.benchmarks)
    analyses, portfolio = enrich_portfolio(analyses)

    report = DailyReport(
        title=config.title,
        generated_at=generated_at,
        timezone=config.timezone,
        language=config.analysis.language,
        analyses=analyses,
        market_brief=_market_brief(analyses),
        portfolio=portfolio,
        insights=build_insights(analyses),
        benchmarks=benchmark_snapshots,
        config_path=config_path,
    )
    return report


def _warnings(snapshot: PriceSnapshot, min_rows: int, freshness: DataFreshness | None = None) -> list[str]:
    warnings: list[str] = []
    if snapshot.rows < min_rows:
        warnings.append(f"Only {snapshot.rows} rows of price history are available.")
    if snapshot.change_pct is not None and abs(snapshot.change_pct) > 0.08:
        warnings.append("Single-day move is unusually large; verify corporate actions or data quality.")
    if freshness and freshness.status in {"stale", "missing", "unknown"}:
        warnings.append(f"Data freshness: {freshness.message}")
    return warnings


def _failed_analysis(asset: StockAsset, message: str) -> AssetAnalysis:
    freshness = DataFreshness(
        latest_date="",
        age_days=None,
        source=None,
        rows=0,
        status="missing",
        message=message,
    )
    snapshot = PriceSnapshot(
        symbol=asset.symbol,
        name=asset.name or asset.symbol,
        currency=asset.currency,
        last_close=0,
        previous_close=None,
        change_pct=None,
        start_date="",
        end_date="",
        rows=0,
    )
    return AssetAnalysis(
        asset=asset,
        snapshot=snapshot,
        metrics={},
        signal=score_asset({}),
        news=[],
        ai_summary=None,
        warnings=[message],
        freshness=freshness,
    )


def _market_brief(analyses: list[AssetAnalysis]) -> str:
    valid = [item for item in analyses if item.snapshot.rows > 0]
    if not valid:
        return "No valid market data was available."
    average_score = sum(item.signal.score for item in valid) / len(valid)
    high_risk = sum(1 for item in valid if item.signal.risk_level == "high")
    constructive = sum(1 for item in valid if item.signal.score >= 60)
    return (
        f"{len(valid)} assets analyzed. Average signal score is {average_score:.1f}/100. "
        f"{constructive} assets are constructive or watch-bullish. "
        f"{high_risk} assets are flagged as high risk."
    )
