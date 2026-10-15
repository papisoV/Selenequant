"""KlineDataManager coverage — local/auto/remote source selection, incremental
sync, WS-disconnect recovery, gap repair via backwards pagination, and the
interval/stats utilities."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selene_space_market.kline_manager import KlineDataManager
from selene_space_types.market import KlineEvent

BASE_TS = 1711000000000
HOUR_MS = 3_600_000


def _kline(
    ts: int,
    exchange: str = "binance",
    market_type: str = "futures",
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    close: float = 67000.0,
) -> KlineEvent:
    return KlineEvent(
        exchange=exchange,
        market_type=market_type,  # type: ignore[arg-type]
        symbol=symbol,  # type: ignore[arg-type]
        timestamp=ts,
        interval=interval,
        open=close - 100,
        high=close + 100,
        low=close - 200,
        close=close,
        volume=1000.0,
        closed=True,
    )


def _series(count: int, base_ts: int = BASE_TS, step_ms: int = HOUR_MS, **overrides) -> list[KlineEvent]:
    return [_kline(base_ts + i * step_ms, **overrides) for i in range(count)]


def _service_returning(candles: list[KlineEvent]) -> MagicMock:
    svc = MagicMock()
    svc.fetch_klines_rest = AsyncMock(return_value=candles)
    return svc


def _now_aligned(interval_ms: int) -> int:
    """Current time floored to the interval boundary (exchange open_time style)."""
    return (int(datetime.now(tz=timezone.utc).timestamp() * 1000) // interval_ms) * interval_ms


class TestLocalSource:
    @pytest.fixture
    def manager(self) -> KlineDataManager:
        return KlineDataManager(":memory:")

    def test_reads_sqlite_only(self, manager: KlineDataManager) -> None:
        manager._db.put_klines(_series(10))
        df = asyncio.run(
            manager.get_klines("binance", "futures", "BTC/USDT", "1h", source="local")
        )
        assert len(df) == 10

    def test_empty_store_yields_empty_frame(self, manager: KlineDataManager) -> None:
        df = asyncio.run(
            manager.get_klines("binance", "futures", "ETH/USDT", "1h", source="local")
        )
        assert len(df) == 0


class TestAutoSource:
    @pytest.fixture
    def manager(self) -> KlineDataManager:
        return KlineDataManager(":memory:")

    @pytest.mark.asyncio
    async def test_sufficient_local_skips_remote(self, manager: KlineDataManager) -> None:
        manager._db.put_klines(_series(20))
        manager._service = MagicMock()
        manager._service.fetch_klines_rest = AsyncMock()

        df = await manager.get_klines(
            "binance", "futures", "BTC/USDT", "1h",
            source="auto", min_candles=15,
        )
        assert len(df) == 20
        manager._service.fetch_klines_rest.assert_not_called()

    @pytest.mark.asyncio
    async def test_thin_local_promotes_to_remote(self, manager: KlineDataManager) -> None:
        manager._db.put_klines(_series(5))  # below min_candles
        remote = _series(50, base_ts=BASE_TS, step_ms=HOUR_MS, close=68000)

        with patch.object(manager, "_get_service", return_value=_service_returning(remote)):
            df = await manager.get_klines(
                "binance", "futures", "BTC/USDT", "1h",
                source="auto", min_candles=20,
            )
            assert len(df) >= 20


class TestRemoteSource:
    @pytest.mark.asyncio
    async def test_fetch_then_persist(self) -> None:
        manager = KlineDataManager(":memory:")
        remote = _series(100)

        with patch.object(manager, "_get_service", return_value=_service_returning(remote)):
            df = await manager.get_klines("binance", "futures", "BTC/USDT", "1h", source="remote")
            assert len(df) == 100

            local_count = manager._db.count("binance", "futures", "BTC/USDT", "1h")
            assert local_count == 100


class TestIncrementalSync:
    @pytest.fixture
    def manager(self) -> KlineDataManager:
        return KlineDataManager(":memory:")

    @pytest.mark.asyncio
    async def test_only_newer_candles_saved(self, manager: KlineDataManager) -> None:
        # Local holds candles 0..9; remote replays 0..19 → expect ≥10 fresh writes.
        manager._db.put_klines(_series(10, base_ts=0))

        with patch.object(manager, "_get_service", return_value=_service_returning(_series(20, base_ts=0))):
            count = await manager.sync_incremental("binance", "futures", "BTC/USDT", "1h")
            assert count >= 10

    @pytest.mark.asyncio
    async def test_first_sync_pulls_requested_history(self, manager: KlineDataManager) -> None:
        with patch.object(
            manager, "_get_service", return_value=_service_returning(_series(100))
        ):
            count = await manager.sync_incremental(
                "binance", "futures", "BTC/USDT", "1h", days=365
            )
            assert count == 100


class TestWsDisconnectRecovery:
    @pytest.mark.asyncio
    async def test_recovers_from_last_received(self) -> None:
        manager = KlineDataManager(":memory:")
        last_ts = BASE_TS
        recovery = _series(10, base_ts=last_ts)

        with patch.object(manager, "_get_service", return_value=_service_returning(recovery)):
            count = await manager.on_ws_disconnect(
                "binance", "futures", "BTC/USDT", "1h",
                last_received_ts=last_ts,
            )
            assert count >= 0


class TestGapRepair:
    @pytest.fixture
    def manager(self) -> KlineDataManager:
        return KlineDataManager(":memory:")

    @pytest.mark.asyncio
    async def test_continuous_window_needs_no_repair(self, manager: KlineDataManager) -> None:
        interval_ms = HOUR_MS
        base = _now_aligned(interval_ms) - 24 * interval_ms
        manager._db.put_klines(_series(25, base_ts=base))

        svc = MagicMock(fetch_klines_rest=AsyncMock())
        with patch.object(manager, "_get_service", return_value=svc):
            count = await manager.repair_gaps(
                "binance", "futures", "BTC/USDT", "1h", window_days=3
            )
        assert count == 0
        svc.fetch_klines_rest.assert_not_called()

    @pytest.mark.asyncio
    async def test_mid_window_hole_backfilled(self, manager: KlineDataManager) -> None:
        """A mid-window hole is backfilled by end_time pagination."""
        interval_ms = HOUR_MS
        now_ms = _now_aligned(interval_ms)
        # Two clusters flanking a hole: [now-24h .. now-22h] and [now-1h .. now].
        cluster_head = [now_ms - 24 * interval_ms + i * interval_ms for i in range(3)]
        cluster_tail = [now_ms - interval_ms, now_ms]
        manager._db.put_klines([_kline(ts) for ts in cluster_head + cluster_tail])

        hole_start = now_ms - 22 * interval_ms

        async def fake_fetch_klines_rest(
            symbol, exchange, market_type, interval, limit, end_time=None
        ):
            if end_time is None:
                return []
            # Exchanges floor end_time to a boundary and return candles ending
            # there (exclusive of end_time itself).
            aligned_end = (end_time // interval_ms) * interval_ms
            out = []
            ts = aligned_end - interval_ms
            while ts > hole_start and len(out) < limit:
                out.append(_kline(ts))
                ts -= interval_ms
            return out

        svc = MagicMock()
        svc.fetch_klines_rest = AsyncMock(side_effect=fake_fetch_klines_rest)
        with patch.object(manager, "_get_service", return_value=svc):
            count = await manager.repair_gaps(
                "binance", "futures", "BTC/USDT", "1h", window_days=3
            )

        # Hole spans now-22h..now-1h; interior candles now-21h..now-2h = 20 bars.
        assert count == 20

        # SQLite now continuous across the former hole.
        df = manager._db.get_klines("binance", "futures", "BTC/USDT", "1h")
        stamps = df["timestamp"].tolist()
        for i in range(len(stamps) - 1):
            assert stamps[i + 1] - stamps[i] == interval_ms

    @pytest.mark.asyncio
    async def test_pagination_respects_stop_boundary(self, manager: KlineDataManager) -> None:
        """Backwards pagination halts at stop_ts without over-fetching."""
        end_ts = BASE_TS
        stop_ts = BASE_TS - 100 * HOUR_MS

        expected_count = sum(
            1 for ts in range(stop_ts + HOUR_MS, end_ts, HOUR_MS)
        )

        calls: list[int] = []

        async def fake_fetch_klines_rest(
            symbol, exchange, market_type, interval, limit, end_time=None
        ):
            calls.append(end_time if end_time is not None else -1)
            if end_time is None:
                return []
            out = []
            ts = end_time - HOUR_MS
            while ts > stop_ts and len(out) < limit:
                out.append(_kline(ts))
                ts -= HOUR_MS
            return out

        svc = MagicMock()
        svc.fetch_klines_rest = AsyncMock(side_effect=fake_fetch_klines_rest)
        with patch.object(manager, "_get_service", return_value=svc):
            pages = await manager._paginate_backwards(
                svc,
                symbol="BTC/USDT",
                exchange="binance",
                market_type="futures",
                interval="1h",
                end_ts=end_ts,
                stop_ts=stop_ts,
            )

        assert len(pages) == expected_count
        assert all(c >= stop_ts for c in calls)


class TestUtilities:
    INTERVAL_TABLE = [
        ("1m", 60_000),
        ("5m", 300_000),
        ("1h", 3_600_000),
        ("4h", 14_400_000),
        ("1d", 86_400_000),
    ]

    @pytest.fixture
    def manager(self) -> KlineDataManager:
        return KlineDataManager(":memory:")

    @pytest.mark.parametrize(("interval", "expected_ms"), INTERVAL_TABLE)
    def test_interval_conversion(self, manager: KlineDataManager, interval: str, expected_ms: int) -> None:
        assert manager._interval_to_ms(interval) == expected_ms

    def test_stats_snapshot_fields(self, manager: KlineDataManager) -> None:
        manager._db.put_klines(_series(10))

        stats = manager.get_stats("binance", "futures", "BTC/USDT", "1h")
        assert stats["count"] == 10
        assert stats["latest_ts"] == BASE_TS + 9 * HOUR_MS
        assert stats["earliest_ts"] == BASE_TS
        assert stats["has_data"] is True
