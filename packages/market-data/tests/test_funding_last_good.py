"""FundingRateFetcher last-good degradation + freshness exposure.

When every venue fails, get_all serves the previous successful snapshot
(stale-marked) instead of an empty "fresh" one. Pattern from
internal-reference last-good.cjs — see PROVENANCE.md. Offline tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_market.funding_rate_fetcher import (
    FundingRateFetcher,
    FundingRateItem,
)


def _item(exchange: str = "binance", symbol: str = "BTC/USDT") -> FundingRateItem:
    return FundingRateItem(
        exchange=exchange,
        symbol=symbol,
        funding_rate=0.0001,
        mark_price=50000.0,
        index_price=50000.0,
        next_funding_time=1_711_000_000_000,
        timestamp=1_711_000_000_000,
    )


async def _patch_venues(
    fetcher: FundingRateFetcher,
    binance: Any,
    okx: Any,
) -> None:
    """Swap the three live fetchers for canned results (gather targets)."""

    async def _binance():
        if isinstance(binance, BaseException):
            raise binance
        return binance

    async def _okx():
        if isinstance(okx, BaseException):
            raise okx
        return okx

    async def _coinglass():
        return {}

    fetcher._live_binance = _binance  # type: ignore[method-assign]
    fetcher._live_okx = _okx  # type: ignore[method-assign]
    fetcher._coinglass = _coinglass  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_successful_refresh_populates_last_good_and_freshness() -> None:
    fetcher = FundingRateFetcher()
    await _patch_venues(fetcher, [_item()], [_item("okx", "ETH/USDT")])
    items = await fetcher.get_all()
    assert len(items) == 2
    fresh = fetcher.freshness()
    assert fresh["stale"] is False
    assert fresh["age_seconds"] is not None and fresh["age_seconds"] >= 0
    assert fresh["as_of_ms"] > 0


@pytest.mark.asyncio
async def test_total_failure_serves_last_good_with_stale_marker() -> None:
    fetcher = FundingRateFetcher()
    await _patch_venues(fetcher, [_item()], [])
    await fetcher.get_all()  # primes last-good

    await _patch_venues(
        fetcher,
        ConnectionError("binance down"),
        ConnectionError("okx down"),
    )
    items = await fetcher.get_all(force=True)
    assert [i.symbol for i in items] == ["BTC/USDT"]  # previous snapshot, not empty

    fresh = fetcher.freshness()
    assert fresh["stale"] is True


@pytest.mark.asyncio
async def test_total_failure_with_empty_history_returns_empty() -> None:
    fetcher = FundingRateFetcher()
    await _patch_venues(fetcher, ConnectionError("down"), ConnectionError("down"))
    items = await fetcher.get_all(force=True)
    assert items == []
    fresh = fetcher.freshness()
    assert fresh["stale"] is True
    assert fresh["age_seconds"] is None  # never succeeded
    assert fresh["as_of_ms"] == 0


@pytest.mark.asyncio
async def test_recovery_clears_stale_marker() -> None:
    fetcher = FundingRateFetcher()
    await _patch_venues(fetcher, [_item()], [])
    await fetcher.get_all()
    await _patch_venues(fetcher, ConnectionError("down"), ConnectionError("down"))
    await fetcher.get_all(force=True)
    assert fetcher.freshness()["stale"] is True

    await _patch_venues(fetcher, [_item()], [_item("okx", "ETH/USDT")])
    await fetcher.get_all(force=True)
    assert fetcher.freshness()["stale"] is False


@pytest.mark.asyncio
async def test_partial_failure_keeps_fresh_status() -> None:
    """One venue alive → merged result is new data, not degraded."""
    fetcher = FundingRateFetcher()
    await _patch_venues(fetcher, [_item()], [])
    await fetcher.get_all()
    await _patch_venues(fetcher, ConnectionError("down"), [_item("okx", "ETH/USDT")])
    items = await fetcher.get_all(force=True)
    assert [i.exchange for i in items] == ["okx"]
    assert fetcher.freshness()["stale"] is False
