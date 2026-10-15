"""Re-export Pydantic models from selene_space_types.stock.

Historically pulse used frozen dataclasses here, but the DailyReport type
authoritatively lives in selene_space_types.stock as a Pydantic BaseModel.
Passing dataclass AssetAnalysis/PortfolioSummary/InsightSummary instances to
a Pydantic DailyReport fails Pydantic v2 strict type-identity validation.
Pulse is now fully Pydantic-v2; use model_copy(update={...}) instead of
dataclasses.replace(), and model_dump() instead of __dict__.
"""

from selene_space_types.stock import (
    AssetAnalysis,
    AttentionItem,
    BenchmarkComparison,
    BenchmarkSnapshot,
    ChecklistItem,
    ContributionItem,
    DailyReport,
    DataFreshness,
    HistoryPoint,
    InsightSummary,
    NewsItem,
    PortfolioSummary,
    PositionMetrics,
    PriceSnapshot,
    RiskFinding,
    SignalScore,
    StockAsset as Asset,
)

__all__ = [
    "Asset",
    "AssetAnalysis",
    "AttentionItem",
    "BenchmarkComparison",
    "BenchmarkSnapshot",
    "ChecklistItem",
    "ContributionItem",
    "DailyReport",
    "DataFreshness",
    "HistoryPoint",
    "InsightSummary",
    "NewsItem",
    "PortfolioSummary",
    "PositionMetrics",
    "PriceSnapshot",
    "RiskFinding",
    "SignalScore",
]
