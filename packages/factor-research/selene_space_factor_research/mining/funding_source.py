"""Funding-rate source adapter layer (FD6).

Selene-specific seam between funding-rate data sources (e.g. market-data's
``FundingRateFetcher.get_history``) and the backtest cost model. Kept
dependency-free: this module imports neither market-data nor backtest-engine.
The factory resolves the backtest engine lazily via
:meth:`SeleneCryptoCostModel.from_rates`, which imports
``HistoricalFundingModel`` only when called, so the common
``import selene_space_factor_research`` path never pulls market-data.
"""
from __future__ import annotations

from typing import Protocol

from .cost_models import SeleneCryptoCostModel


class FundingRateSource(Protocol):
    """Sync contract: return an ascending ``(ts_ms, rate)`` pair list.

    Adapters are responsible for async→sync bridging (e.g. wrapping a
    ``FundingRateFetcher.get_history``). The pairs feed
    ``HistoricalFundingModel``, which sorts and filters zero rates internally.
    """

    def get_history(
        self,
        *,
        exchange: str,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[tuple[int, float]]: ...


def build_funding_cost_model(
    source: FundingRateSource,
    *,
    exchange: str,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> SeleneCryptoCostModel:
    """Build a funding-enabled cost model from a rate source's history.

    ``source.get_history(...)`` returns an ascending ``(ts_ms, rate)`` pair
    list that is handed to :meth:`SeleneCryptoCostModel.from_rates`, producing
    one independent per-side funding model (FD4).
    """
    pairs = source.get_history(
        exchange=exchange,
        symbol=symbol,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return SeleneCryptoCostModel.from_rates(pairs)
