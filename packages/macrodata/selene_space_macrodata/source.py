"""MacroDataSource protocol — pluggable macro economic data provider.

Each provider wraps one data source (FRED, BIS, BLS, etc.) and exposes a
unified fetch/healthcheck/cache contract.

Design aligned with selene_space_market.ExchangeSource:
- Protocol (duck typing, @runtime_checkable)
- async lifecycle (start/stop/fetch)
- sync cache getters
- pydantic Config BaseModel for identity
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field


class SeriesKind(str, Enum):
    """Category of macro series."""

    RATES = "rates"  # interest rates, yield curve
    INFLATION = "inflation"  # CPI, PCE, PPI
    EMPLOYMENT = "employment"  # nonfarm, unemployment, JOLTS
    MONETARY = "monetary"  # M2, monetary base, reserves
    FINANCIAL_STRESS = "financial_stress"  # STLFSI, NFCI, VIX
    CONFIDENCE = "confidence"  # consumer sentiment, PMI
    FLOW = "flow"  # GDP, trade balance
    FX = "fx"  # DXY, effective exchange rates
    OTHER = "other"


class MacroDataSourceConfig(BaseModel):
    """Identity of a macro data source."""

    provider: str = Field(..., description="Provider id, e.g. 'fred'")
    api_key: str | None = Field(None, description="Optional API key")
    base_url: str | None = Field(None, description="Optional override base URL")
    timeout_seconds: float = Field(30.0, description="HTTP timeout")
    cache_ttl_seconds: int = Field(3600, description="Cache TTL for fetched series")


class MacroSeries(BaseModel):
    """One fetched macro series observation set."""

    provider: str
    series_id: str
    kind: SeriesKind
    description: str
    observations: pd.DataFrame  # columns: date, value
    fetched_at: datetime
    source_url: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


@runtime_checkable
class MacroDataSource(Protocol):
    """Contract every macro data provider must satisfy."""

    @property
    def config(self) -> MacroDataSourceConfig: ...

    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def healthcheck(self) -> bool:
        """Return True if the provider is reachable and credentials valid."""
        ...

    async def fetch_series(
        self,
        series_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> MacroSeries:
        """Fetch a single macro series by id."""
        ...

    def get_cached(self, series_id: str) -> MacroSeries | None:
        """Read cached observation set, or None if not cached / expired."""
        ...

    def list_series(self) -> list[str]:
        """List known series ids (provider-specific catalog)."""
        ...
