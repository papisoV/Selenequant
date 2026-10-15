"""AnalyticsProvider protocol — pluggable analytics library adapter.

Each provider wraps one Python analytics library (quantstats, pyportfolioopt,
etc.) and exposes a unified compute/describe/healthcheck contract.

Design aligned with selene_space_macrodata.source.MacroDataSource:
- Protocol (duck typing, @runtime_checkable)
- async lifecycle (start/stop/compute)
- pydantic Config BaseModel for identity
- AnalyticsReport as the standard return type
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class AnalyticsReportKind(str, Enum):
    """Category of analytics report."""

    PERFORMANCE = "performance"  # sharpe, sortino, drawdown
    PORTFOLIO = "portfolio"  # weights, optimization
    RISK = "risk"  # vol, var, cvar
    FORECAST = "forecast"  # time-series prediction
    STATISTICS = "statistics"  # regression, cointegration
    SMOOTHING = "smoothing"  # smoothing, filtering
    OTHER = "other"


class AnalyticsProviderConfig(BaseModel):
    """Identity of an analytics provider."""

    provider: str = Field(..., description="Provider id, e.g. 'quantstats'")
    library_version: str | None = Field(None, description="Optional library version pin")
    options: dict[str, Any] = Field(default_factory=dict, description="Provider options")
    cache_ttl_seconds: int = Field(3600, description="Cache TTL for reports")


class AnalyticsReport(BaseModel):
    """One analytics report produced by a provider."""

    provider: str
    kind: AnalyticsReportKind
    title: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    data: pd.DataFrame | None = None
    artifacts: dict[str, str] = Field(default_factory=dict, description="e.g. base64 html")
    computed_at: datetime
    notes: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


@runtime_checkable
class AnalyticsProvider(Protocol):
    """Contract every analytics provider must satisfy."""

    @property
    def config(self) -> AnalyticsProviderConfig: ...

    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def healthcheck(self) -> bool:
        """Return True if the underlying library can be imported + used."""
        ...

    async def compute(
        self,
        *,
        action: str,
        returns: pd.DataFrame | pd.Series | None = None,
        prices: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> AnalyticsReport:
        """Compute an analytics report. Action is provider-specific."""
        ...

    def list_actions(self) -> list[str]:
        """List supported actions for this provider."""
        ...

    def describe_action(self, action: str) -> str:
        """Return human-readable description of an action."""
        ...
