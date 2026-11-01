"""Base types for selene_space_optimize — Protocol + Config + Report."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict


class OptimizeKind(str, Enum):
    """Kind of optimization result."""

    RL_TRAIN = "rl_train"
    RL_PREDICT = "rl_predict"
    PORTFOLIO = "portfolio"
    BACKTEST = "backtest"
    OTHER = "other"


class OptimizeProviderConfig(BaseModel):
    """Config for instantiating an optimize provider.

    `provider` is the registry id (e.g. "ppo", "mean_variance").
    Other fields are provider-specific and passed through via `params`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    params: dict[str, Any] = {}


class OptimizeReport(BaseModel):
    """Result of an optimization run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    kind: OptimizeKind
    title: str
    metrics: dict[str, Any] = {}
    data: pd.DataFrame | pd.Series | None = None
    artifacts: dict[str, Any] = {}  # e.g. {"model_bytes_b64": "..."}
    computed_at: datetime
    notes: str | None = None


@runtime_checkable
class OptimizeProvider(Protocol):
    """Async optimize provider protocol — mirrors AnalyticsProvider."""

    @property
    def config(self) -> OptimizeProviderConfig: ...

    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def healthcheck(self) -> bool: ...

    async def compute(
        self,
        *,
        action: str,
        prices: pd.DataFrame | pd.Series | None = None,
        returns: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> OptimizeReport: ...

    def list_actions(self) -> list[str]: ...

    def describe_action(self, action: str) -> str: ...
