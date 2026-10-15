"""Stock market and A-share data models for SeleneQuant.

Converted from ai-market-pulse frozen dataclasses and 0705aiinvest dataclasses
to unified Pydantic v2 models. These types cover stock signal scoring,
daily reports, factor definitions, and A-share regime detection.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FactorCategory(str, Enum):
    """Factor classification category."""

    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    SENTIMENT = "sentiment"
    EVENT = "event"


class TrendRegime(str, Enum):
    """Market trend regime (A-share index based)."""

    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


class VolRegime(str, Enum):
    """Market volatility regime (A-share index based)."""

    LOW_VOL = "low_vol"
    NORMAL_VOL = "normal_vol"
    HIGH_VOL = "high_vol"


# ---------------------------------------------------------------------------
# Signal scoring models (from ai-market-pulse)
# ---------------------------------------------------------------------------


class StockAsset(BaseModel):
    """Stock/ETF/crypto asset in a watchlist."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"symbol": "AAPL", "market": "US"}]})

    symbol: str
    name: str | None = None
    market: str = "US"
    currency: str | None = None
    tags: list[str] = Field(default_factory=list)
    quantity: float | None = None
    cost_basis: float | None = None
    note: str | None = None


class NewsItem(BaseModel):
    """News article related to an asset."""

    title: str
    link: str
    source: str | None = None
    published: str | None = None


class PriceSnapshot(BaseModel):
    """Latest price summary for an asset."""

    symbol: str
    name: str
    currency: str | None
    last_close: float
    previous_close: float | None = None
    change_pct: float | None = None
    start_date: str
    end_date: str
    rows: int
    source: str | None = None


class DataFreshness(BaseModel):
    """Data freshness metadata."""

    latest_date: str
    age_days: int | None = None
    source: str | None = None
    rows: int
    status: str
    message: str


class SignalScore(BaseModel):
    """0-100 signal scoring result with stance and risk level."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"score": 72, "stance": "watch bullish", "risk_level": "low", "reasons": ["Price > SMA20", "RSI 45-65"]}]
        }
    )

    score: int = Field(..., ge=0, le=100)
    stance: str
    risk_level: str
    reasons: list[str] = Field(default_factory=list)


class PositionMetrics(BaseModel):
    """Position-level P&L metrics."""

    symbol: str
    currency: str
    quantity: float
    cost_basis: float | None = None
    cost_value: float | None = None
    market_value: float
    day_pnl: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    allocation_pct: float | None = None


class PortfolioSummary(BaseModel):
    """Currency-aggregated portfolio summary."""

    currency: str
    positions: int
    market_value: float
    cost_value: float | None = None
    day_pnl: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None


class HistoryPoint(BaseModel):
    """Single-day historical record for dashboard charts."""

    date: str
    symbol: str
    close: float | None = None
    score: int
    stance: str
    risk_level: str
    currency: str | None = None
    change_pct: float | None = None
    market_value: float | None = None
    day_pnl: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    benchmark_symbol: str | None = None
    relative_return_20d: float | None = None
    relative_return_60d: float | None = None
    latest_data_date: str | None = None
    data_age_days: int | None = None
    freshness_status: str | None = None


class RiskFinding(BaseModel):
    """Risk alert for an asset."""

    symbol: str
    severity: str
    rule: str
    message: str
    value: float | str | None = None


class AttentionItem(BaseModel):
    """High-priority attention item."""

    symbol: str
    priority: int
    reason: str
    has_position: bool
    risk_level: str
    day_pnl: float | None = None
    unrealized_pnl: float | None = None


class ContributionItem(BaseModel):
    """P&L contribution item."""

    symbol: str
    currency: str
    day_pnl: float | None = None
    unrealized_pnl: float | None = None
    market_value: float | None = None
    allocation_pct: float | None = None


class ChecklistItem(BaseModel):
    """Daily checklist reminder."""

    text: str
    priority: str = "normal"
    symbol: str | None = None


class InsightSummary(BaseModel):
    """Aggregated insights from daily analysis."""

    attention: list[AttentionItem] = Field(default_factory=list)
    risk_findings: list[RiskFinding] = Field(default_factory=list)
    day_contributors: list[ContributionItem] = Field(default_factory=list)
    unrealized_contributors: list[ContributionItem] = Field(default_factory=list)
    checklist: list[ChecklistItem] = Field(default_factory=list)


class BenchmarkSnapshot(BaseModel):
    """Benchmark index price snapshot."""

    symbol: str
    name: str
    market: str
    currency: str | None = None
    last_close: float | None = None
    change_pct: float | None = None
    return_20d: float | None = None
    return_60d: float | None = None
    source: str | None = None
    freshness: DataFreshness | None = None


class BenchmarkComparison(BaseModel):
    """Asset vs benchmark relative performance."""

    symbol: str
    name: str
    market: str
    source: str | None = None
    latest_date: str
    asset_return_20d: float | None = None
    benchmark_return_20d: float | None = None
    relative_return_20d: float | None = None
    asset_return_60d: float | None = None
    benchmark_return_60d: float | None = None
    relative_return_60d: float | None = None
    verdict: str


class AssetAnalysis(BaseModel):
    """Complete analysis for a single asset."""

    asset: StockAsset
    snapshot: PriceSnapshot
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    signal: SignalScore
    news: list[NewsItem] = Field(default_factory=list)
    position: PositionMetrics | None = None
    ai_summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
    benchmark: BenchmarkComparison | None = None
    freshness: DataFreshness | None = None


class DailyReport(BaseModel):
    """Complete daily market report."""

    title: str
    generated_at: datetime
    timezone: str
    language: str
    analyses: list[AssetAnalysis] = Field(default_factory=list)
    market_brief: str = ""
    portfolio: list[PortfolioSummary] = Field(default_factory=list)
    history: dict[str, list[HistoryPoint]] = Field(default_factory=dict)
    insights: InsightSummary = Field(default_factory=InsightSummary)
    benchmarks: list[BenchmarkSnapshot] = Field(default_factory=list)
    portfolio_ai_summary: str | None = None
    config_path: str | None = None


class NotificationTarget(BaseModel):
    """Notification channel configuration."""

    type: str
    name: str = ""
    enabled: bool = True
    settings: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# A-share factor models (from 0705aiinvest)
# ---------------------------------------------------------------------------


class FactorDef(BaseModel):
    """Factor metadata definition."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "RSI_14D", "category": "technical", "subcategory": "mean_reversion", "description": "14-day RSI", "direction": -1}
            ]
        }
    )

    name: str
    category: FactorCategory
    subcategory: str
    description: str
    ainvest_source: list[str] = Field(default_factory=list)
    calc_requires: list[str] = Field(default_factory=list)
    direction: int = 1


class RegimeState(BaseModel):
    """Current market regime state (trend + volatility)."""

    trend: TrendRegime = TrendRegime.NEUTRAL
    vol: VolRegime = VolRegime.NORMAL_VOL
    index_ma20: float = float("nan")
    index_ma60: float = float("nan")
    index_close: float = float("nan")
    realized_vol_20d: float = float("nan")


class CompositeConfig(BaseModel):
    """Composite factor configuration (IC/IR weighted)."""

    name: str
    factor_weights: dict[str, float] = Field(default_factory=dict)
    direction: int = 1


class StrategyConfigAshare(BaseModel):
    """A-share factor-based strategy configuration.

    Renamed from StrategyConfig to avoid collision with
    selene_space_types.strategy.StrategyConfig (crypto strategies).
    """

    name: str
    composite_config: CompositeConfig
    strategy_mode: str = "trend_follow"
    n_long: int = 5
    n_long_high_vol: int = 3
    holding_period: int = 1
    stop_loss: float = -0.02
    vol_target: float = 0.15
    max_position_pct: float = 0.30
    cash_return: float = 0.0
    min_composite_percentile: float = 0.6
    require_price_above_ma20: bool = True
    require_volume_above_avg: bool = False
