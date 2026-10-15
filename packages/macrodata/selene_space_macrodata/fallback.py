"""Fallback helpers — healthcheck + retry + cached fallback for providers.

Each provider should use these to satisfy AC-Q4 (失效接口有 fallback).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pandas as pd

from selene_space_macrodata.source import MacroSeries, SeriesKind

logger = logging.getLogger(__name__)


async def with_retry(
    op: Callable[[], Awaitable[MacroSeries]],
    *,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> MacroSeries:
    """Retry an async op up to `attempts` times with linear backoff."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await op()
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "macro provider attempt %d/%d failed: %s",
                i + 1,
                attempts,
                exc,
            )
            if i < attempts - 1:
                import asyncio

                await asyncio.sleep(backoff_seconds * (i + 1))
    assert last_exc is not None
    raise last_exc


def _now_utc() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


def is_cache_valid(series: MacroSeries, ttl_seconds: int) -> bool:
    """Return True if cached series is still within TTL.

    Handles both naive (UTC) and timezone-aware datetimes.
    """
    now = _now_utc()
    fetched = series.fetched_at
    if fetched.tzinfo is None:
        # Treat naive datetimes as UTC (providers use datetime.utcnow()).
        fetched = fetched.replace(tzinfo=UTC)
    age = now - fetched
    return age <= timedelta(seconds=ttl_seconds)


def stale_fallback(
    cached: MacroSeries | None,
    series_id: str,
) -> MacroSeries:
    """Return cached series (even if stale) or raise if no fallback exists."""
    if cached is None:
        raise RuntimeError(
            f"macro provider fetch failed for {series_id} and no cached fallback"
        )
    logger.warning(
        "macro provider using stale cache for %s (fetched_at=%s)",
        series_id,
        cached.fetched_at,
    )
    return cached


def empty_series(provider: str, series_id: str) -> MacroSeries:
    """Construct an empty MacroSeries (used when provider returns no rows)."""
    return MacroSeries(
        provider=provider,
        series_id=series_id,
        kind=SeriesKind.OTHER,
        description="empty",
        observations=pd.DataFrame(columns=["date", "value"]),
        fetched_at=_now_utc(),
    )
