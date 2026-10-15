"""Tests for FundingRateFetcher.get_history() — historical funding rate pagination.

Mock-based (no real HTTP). Verifies:
- Binance pagination: cursor advances past last seen fundingTime.
- OKX pagination: walks backwards via `after` param, reverses to ascending.
- Symbol normalization: BTC/USDT → BTCUSDT (Binance) / BTC-USDT-SWAP (OKX).
- Empty result handling.
- Unsupported exchange raises ValueError.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selene_space_market.funding_rate_fetcher import FundingRateFetcher


class _FakeResponse:
    """Minimal httpx.Response stub for testing."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class TestBinanceHistory:
    @pytest.mark.asyncio
    async def test_binance_single_page(self) -> None:
        """Single page response → items list with correct fields.

        First call returns 2 rows; second call (after cursor advance) returns
        [] to terminate pagination — the mock returns [] because cursor moved
        past all data.
        """
        fetcher = FundingRateFetcher()
        rows = [
            {"symbol": "BTCUSDT", "fundingTime": 1711000000000, "fundingRate": "0.00010000"},
            {"symbol": "BTCUSDT", "fundingTime": 1711028800000, "fundingRate": "0.00020000"},
        ]
        mock_client = MagicMock()
        # First call returns 2 rows; subsequent calls return empty to terminate.
        mock_client.get = AsyncMock(side_effect=[_FakeResponse(rows), _FakeResponse([])])
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            items = await fetcher.get_history(
                exchange="binance",
                symbol="BTC/USDT",
                start_ms=1711000000000,
                end_ms=1711030000000,
            )

        assert len(items) == 2
        assert items[0].exchange == "binance"
        assert items[0].symbol == "BTC/USDT"
        assert items[0].funding_rate == 0.0001
        assert items[0].timestamp == 1711000000000
        assert items[1].funding_rate == 0.0002
        # Verify request params
        assert mock_client.get.await_count == 2
        called_url = mock_client.get.await_args_list[0].args[0]
        assert "/fapi/v1/fundingRate" in called_url

    @pytest.mark.asyncio
    async def test_binance_paginates_multiple_pages(self) -> None:
        """When a full page (1000 rows) returns, fetch again with advanced cursor.

        Builds two pages: first returns 1000 rows ending at ts=200, second
        returns 2 more rows. Verify both pages are fetched.
        """
        fetcher = FundingRateFetcher()
        page1 = [
            {"symbol": "BTCUSDT", "fundingTime": 100 + i, "fundingRate": "0.0001"}
            for i in range(1000)
        ]
        # Make last row ts=200 to advance cursor
        page1[-1] = {"symbol": "BTCUSDT", "fundingTime": 200, "fundingRate": "0.0001"}
        page2 = [
            {"symbol": "BTCUSDT", "fundingTime": 300, "fundingRate": "0.0002"},
            {"symbol": "BTCUSDT", "fundingTime": 400, "fundingRate": "0.0003"},
        ]

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=[
            _FakeResponse(page1),
            _FakeResponse(page2),
            _FakeResponse([]),  # third call returns empty to terminate
        ])
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            items = await fetcher.get_history(
                exchange="binance",
                symbol="BTC/USDT",
                start_ms=0,
                end_ms=1000,
            )

        # Should have 1000 + 2 items, and 3 total HTTP calls
        assert len(items) == 1002
        assert mock_client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_binance_empty_response(self) -> None:
        """Empty list response → no items, single call."""
        fetcher = FundingRateFetcher()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResponse([]))
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            items = await fetcher.get_history(
                exchange="binance",
                symbol="BTC/USDT",
                start_ms=0,
                end_ms=1000,
            )

        assert items == []
        assert mock_client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_binance_symbol_normalization(self) -> None:
        """BTC/USDT → BTCUSDT in request params."""
        fetcher = FundingRateFetcher()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResponse([]))
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            await fetcher.get_history(
                exchange="binance",
                symbol="ETH/USDT",
                start_ms=0,
                end_ms=1000,
            )

        # The symbol passed in params should be "ETHUSDT" (no slash)
        called_kwargs = mock_client.get.await_args.kwargs
        assert called_kwargs["params"]["symbol"] == "ETHUSDT"


class TestOkxHistory:
    @pytest.mark.asyncio
    async def test_okx_single_page_ascending(self) -> None:
        """OKX returns desc, we reverse to ascending."""
        fetcher = FundingRateFetcher()
        # OKX returns desc — newest first
        okx_data = [
            {"fundingTime": "300", "fundingRate": "0.0003"},
            {"fundingTime": "200", "fundingRate": "0.0002"},
            {"fundingTime": "100", "fundingRate": "0.0001"},
        ]
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResponse({"data": okx_data}))
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            items = await fetcher.get_history(
                exchange="okx",
                symbol="BTC/USDT",
                start_ms=50,
                end_ms=400,
            )

        # Reversed: ts=100, 200, 300
        assert len(items) == 3
        assert items[0].timestamp == 100
        assert items[0].funding_rate == 0.0001
        assert items[2].timestamp == 300
        assert items[2].funding_rate == 0.0003

    @pytest.mark.asyncio
    async def test_okx_filters_below_start_ms(self) -> None:
        """Rows older than start_ms are not included."""
        fetcher = FundingRateFetcher()
        okx_data = [
            {"fundingTime": "200", "fundingRate": "0.0002"},
            {"fundingTime": "100", "fundingRate": "0.0001"},  # below start_ms=150
            {"fundingTime": "50", "fundingRate": "0.0005"},  # below start_ms=150
        ]
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResponse({"data": okx_data}))
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            items = await fetcher.get_history(
                exchange="okx",
                symbol="BTC/USDT",
                start_ms=150,
                end_ms=400,
            )

        # Only ts=200 should be in results (100 and 50 are below start_ms)
        assert len(items) == 1
        assert items[0].timestamp == 200

    @pytest.mark.asyncio
    async def test_okx_symbol_normalization(self) -> None:
        """BTC/USDT → BTC-USDT-SWAP in instId param."""
        fetcher = FundingRateFetcher()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_FakeResponse({"data": []}))
        mock_client.is_closed = False

        with patch.object(fetcher, "_get_client", AsyncMock(return_value=mock_client)):
            await fetcher.get_history(
                exchange="okx",
                symbol="ETH/USDT",
                start_ms=0,
                end_ms=1000,
            )

        called_kwargs = mock_client.get.await_args.kwargs
        assert called_kwargs["params"]["instId"] == "ETH-USDT-SWAP"


class TestUnsupportedExchange:
    @pytest.mark.asyncio
    async def test_unsupported_exchange_raises(self) -> None:
        """Unsupported exchange string → ValueError."""
        fetcher = FundingRateFetcher()
        with pytest.raises(ValueError, match="Unsupported exchange"):
            await fetcher.get_history(
                exchange="kraken",
                symbol="BTC/USDT",
                start_ms=0,
                end_ms=1000,
            )
