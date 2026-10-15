"""StreamManager coverage — typed subscription routing, ref-counted
subscribe/unsubscribe, and the resubscribe-all recovery path."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from selene_space_market.stream_manager import StreamManager, StreamType

SYMBOL = "BTC/USDT"


def _ws_client() -> MagicMock:
    client = MagicMock()
    client.subscribe_ticker = AsyncMock()
    client.subscribe_kline = AsyncMock()
    client.subscribe_depth = AsyncMock()
    client.unsubscribe = AsyncMock()
    return client


def _manager(ws: MagicMock) -> StreamManager:
    return StreamManager(ws_client=ws, l2_manager=MagicMock(), kline_interval="1h")


class TestSubscriptionRouting:
    @pytest.mark.asyncio
    async def test_ticker_stream_hits_ticker_channel(self) -> None:
        ws = _ws_client()
        await _manager(ws).start_stream(SYMBOL, StreamType.TICKER)
        ws.subscribe_ticker.assert_awaited_once_with(["btcusdt"])

    @pytest.mark.asyncio
    async def test_kline_stream_carries_interval(self) -> None:
        ws = _ws_client()
        await _manager(ws).start_stream(SYMBOL, StreamType.KLINE)
        ws.subscribe_kline.assert_awaited_once_with(["btcusdt"], "1h")

    @pytest.mark.asyncio
    async def test_depth_stream_hits_depth_channel(self) -> None:
        ws = _ws_client()
        await _manager(ws).start_stream(SYMBOL, StreamType.DEPTH)
        ws.subscribe_depth.assert_awaited_once_with(["btcusdt"])


class TestRefCounting:
    @pytest.mark.asyncio
    async def test_duplicate_start_subscribes_once(self) -> None:
        ws = _ws_client()
        manager = _manager(ws)
        await manager.start_stream(SYMBOL, StreamType.TICKER)
        await manager.start_stream(SYMBOL, StreamType.TICKER)
        assert ws.subscribe_ticker.await_count == 1
        assert manager.ref_count(SYMBOL, StreamType.TICKER) == 2

    @pytest.mark.asyncio
    async def test_partial_release_keeps_subscription(self) -> None:
        ws = _ws_client()
        manager = _manager(ws)
        for _ in range(2):
            await manager.start_stream(SYMBOL, StreamType.TICKER)
        await manager.stop_stream(SYMBOL, StreamType.TICKER)

        ws.unsubscribe.assert_not_awaited()  # one consumer remains
        assert manager.ref_count(SYMBOL, StreamType.TICKER) == 1

    @pytest.mark.asyncio
    async def test_final_release_unsubscribes(self) -> None:
        ws = _ws_client()
        manager = _manager(ws)
        await manager.start_stream(SYMBOL, StreamType.TICKER)
        await manager.stop_stream(SYMBOL, StreamType.TICKER)
        ws.unsubscribe.assert_awaited_once()
        assert manager.ref_count(SYMBOL, StreamType.TICKER) == 0

    @pytest.mark.asyncio
    async def test_stopping_unknown_stream_is_noop(self) -> None:
        await _manager(_ws_client()).stop_stream("NONE", StreamType.TICKER)


class TestInventoryQueries:
    @pytest.mark.asyncio
    async def test_active_symbols_lists_both(self) -> None:
        manager = _manager(_ws_client())
        await manager.start_stream("BTC/USDT", StreamType.TICKER)
        await manager.start_stream("ETH/USDT", StreamType.KLINE)
        symbols = manager.active_symbols()
        assert "BTC/USDT" in symbols
        assert "ETH/USDT" in symbols

    @pytest.mark.asyncio
    async def test_active_streams_lists_pairs(self) -> None:
        manager = _manager(_ws_client())
        await manager.start_stream(SYMBOL, StreamType.TICKER)
        await manager.start_stream(SYMBOL, StreamType.DEPTH)
        streams = manager.active_streams()
        assert (SYMBOL, StreamType.TICKER) in streams
        assert (SYMBOL, StreamType.DEPTH) in streams


class TestResubscribeAll:
    @pytest.mark.asyncio
    async def test_replays_every_active_channel(self) -> None:
        ws = _ws_client()
        manager = _manager(ws)
        await manager.start_stream("BTC/USDT", StreamType.TICKER)
        await manager.start_stream("ETH/USDT", StreamType.KLINE)

        ws.subscribe_ticker.reset_mock()
        ws.subscribe_kline.reset_mock()

        await manager.resubscribe_all()
        ws.subscribe_ticker.assert_awaited_once()
        ws.subscribe_kline.assert_awaited_once()
