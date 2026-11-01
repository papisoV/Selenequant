"""BinanceL2Manager: snapshot bootstrap, delta sequencing, gap recovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from selene_space_exchange.exchanges.binance.l2_manager import BinanceL2Manager
from selene_space_exchange.exchanges.binance.normalizer import BinanceDepthDelta
from selene_space_types.market import OrderBookL2Delta, OrderBookL2Snapshot, PriceLevel

SNAP_ID = 100
SYMBOL = "BTCUSDT"


def _levels(side: list[tuple[float, float]] | None) -> list[PriceLevel]:
    return [PriceLevel(price=p, quantity=q) for p, q in (side or [])]


def _delta(
    first_id: int,
    last_id: int,
    *,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
    previous_update_id: int | None = None,
) -> BinanceDepthDelta:
    frame = OrderBookL2Delta(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=1_711_000_043_210,
        sequence_id=last_id,
        bids=_levels(bids),
        asks=_levels(asks),
    )
    return BinanceDepthDelta(
        delta=frame,
        first_update_id=first_id,
        last_update_id=last_id,
        previous_update_id=previous_update_id,
    )


def _snapshot_response(last_id: int, bids: list[str], asks: list[str]) -> MagicMock:
    body = {
        "lastUpdateId": last_id,
        "bids": [[p, str(q)] for p, q in bids],
        "asks": [[p, str(q)] for p, q in asks],
    }
    reply = MagicMock()
    reply.status_code = 200
    reply.json.return_value = body
    reply.raise_for_status = MagicMock()
    return reply


def _http_with_snapshots(*specs) -> AsyncMock:
    """AsyncClient double whose .get pops queued snapshot replies in order."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[_snapshot_response(*spec) for spec in specs])
    client.aclose = AsyncMock()
    return client


def _bootstrapped(http: AsyncMock = None) -> BinanceL2Manager:
    mgr = BinanceL2Manager(http_client=http or _http_with_snapshots((100, [(66999.0, 2.0), (66998.0, 1.5)], [(67001.0, 1.0), (67002.0, 3.0)])))
    return mgr


BASE_BOOK = dict(bids=[(66999.0, 2.0), (66998.0, 1.5)], asks=[(67001.0, 1.0), (67002.0, 3.0)])


@pytest.mark.asyncio
async def test_initialize_builds_book_from_rest_snapshot() -> None:
    http = _http_with_snapshots((SNAP_ID, BASE_BOOK["bids"], BASE_BOOK["asks"]))
    snap = await BinanceL2Manager(http_client=http).initialize(SYMBOL)

    assert isinstance(snap, OrderBookL2Snapshot)
    checks = {
        "exchange": snap.exchange == "binance",
        "symbol": snap.symbol == "BTC/USDT",
        "sequence": snap.sequence_id == SNAP_ID,
        "bid-count": len(snap.bids) == 2,
        "ask-count": len(snap.asks) == 2,
        "best-bid": snap.bids[0].price == 66999.0,
        "best-ask": snap.asks[0].price == 67001.0,
        "one-fetch": http.get.call_count == 1,
    }
    assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestSpotSequencing:
    @pytest.mark.asyncio
    async def test_contiguous_deltas_apply_in_order(self) -> None:
        mgr = _bootstrapped()
        await mgr.initialize(SYMBOL)

        bridging = _delta(100, 101, bids=[(66999.0, 3.0)], asks=[(67001.0, 0.0)])
        assert await mgr.apply_delta(SYMBOL, bridging) is True
        after_first = mgr.get_snapshot(SYMBOL)
        assert after_first.bids[0].quantity == 3.0  # qty replaced
        assert after_first.asks[0].price == 67002.0  # zero-qty ask removed

        next_frame = _delta(102, 103, bids=[(67000.0, 1.0)])
        assert await mgr.apply_delta(SYMBOL, next_frame) is True
        assert mgr.get_snapshot(SYMBOL).bids[0].price == 67000.0

    @pytest.mark.asyncio
    async def test_frame_behind_the_cursor_is_discarded(self) -> None:
        mgr = _bootstrapped()
        await mgr.initialize(SYMBOL)

        replayed = _delta(40, 50, bids=[(66999.0, 999.0)])
        assert await mgr.apply_delta(SYMBOL, replayed) is False
        assert mgr.get_snapshot(SYMBOL).bids[0].quantity == 2.0

    @pytest.mark.asyncio
    async def test_bridging_first_delta_accepted(self) -> None:
        mgr = _bootstrapped()
        await mgr.initialize(SYMBOL)

        first = _delta(100, 101, bids=[(66999.0, 5.0)])
        assert await mgr.apply_delta(SYMBOL, first) is True
        assert mgr.get_snapshot(SYMBOL).bids[0].quantity == 5.0

    @pytest.mark.asyncio
    async def test_non_bridging_first_delta_tolerated_without_refetch(self) -> None:
        http = _http_with_snapshots((SNAP_ID, BASE_BOOK["bids"], BASE_BOOK["asks"]))
        mgr = _bootstrapped(http)
        await mgr.initialize(SYMBOL)

        late_joiner = _delta(110, 115, bids=[(66999.0, 5.0)])
        assert await mgr.apply_delta(SYMBOL, late_joiner) is True
        assert mgr.get_snapshot(SYMBOL).bids[0].quantity == 5.0
        assert http.get.call_count == 1  # no forced resync


class TestRecovery:
    @pytest.mark.asyncio
    async def test_big_gap_resyncs_from_rest(self) -> None:
        http = _http_with_snapshots(
            (SNAP_ID, BASE_BOOK["bids"], BASE_BOOK["asks"]),
            (300, [(67015.0, 2.0)], [(67025.0, 2.0)]),
        )
        mgr = _bootstrapped(http)
        await mgr.initialize(SYMBOL)
        await mgr.apply_delta(SYMBOL, _delta(100, 101, bids=[(66999.0, 3.0)]))

        torn = _delta(1110, 1115, bids=[(66999.0, 5.0)])
        assert await mgr.apply_delta(SYMBOL, torn) is False

        assert http.get.call_count == 2
        assert mgr.get_snapshot(SYMBOL).sequence_id == 300

    @pytest.mark.asyncio
    async def test_crossed_book_resyncs(self) -> None:
        http = _http_with_snapshots(
            (100, [(66999.0, 1.0)], [(67001.0, 1.0)]),
            (200, [(66990.0, 1.0)], [(67010.0, 1.0)]),
        )
        mgr = _bootstrapped(http)
        await mgr.initialize(SYMBOL)

        # A bid above the standing ask would invert the book.
        poison = _delta(100, 101, bids=[(68000.0, 1.0)])
        assert await mgr.apply_delta(SYMBOL, poison) is False
        assert http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_frames_queued_during_recovery_are_shed(self) -> None:
        mgr = _bootstrapped()
        await mgr.initialize(SYMBOL)

        mgr._books[SYMBOL].recovering = True
        live_frame = _delta(100, 101, bids=[(66999.0, 5.0)])
        assert await mgr.apply_delta(SYMBOL, live_frame) is False


class TestEdgeCases:
    def test_unknown_symbol_has_no_book(self) -> None:
        assert BinanceL2Manager().get_snapshot("UNKNOWN/PAIR") is None

    @pytest.mark.asyncio
    async def test_delta_before_initialize_is_rejected(self) -> None:
        mgr = BinanceL2Manager()
        orphan = _delta(1, 2, bids=[(100.0, 1.0)])
        assert await mgr.apply_delta("BTCUSDT", orphan) is False

    @pytest.mark.asyncio
    async def test_zero_quantity_deletes_the_level(self) -> None:
        mgr = _bootstrapped()
        await mgr.initialize(SYMBOL)

        delete_best_bid = _delta(100, 101, bids=[(66999.0, 0.0)])
        assert await mgr.apply_delta(SYMBOL, delete_best_bid) is True

        book = mgr.get_snapshot(SYMBOL)
        assert (len(book.bids), book.bids[0].price) == (1, 66998.0)

    @pytest.mark.asyncio
    async def test_book_side_ordering_enforced(self) -> None:
        jumbled_bids = [("50000.00", 1.0), ("50005.00", 2.0), ("49995.00", 3.0)]
        jumbled_asks = [("50010.00", 1.0), ("50006.00", 2.0), ("50015.00", 3.0)]
        http = _http_with_snapshots((100, jumbled_bids, jumbled_asks))
        snap = await BinanceL2Manager(http_client=http).initialize("BTCUSDT")

        bid_prices = [lvl.price for lvl in snap.bids]
        ask_prices = [lvl.price for lvl in snap.asks]
        assert bid_prices == sorted(bid_prices, reverse=True)
        assert ask_prices == sorted(ask_prices)


class TestEndToEndGapCycle:
    @pytest.mark.asyncio
    async def test_init_apply_gap_resync_resume(self) -> None:
        http = _http_with_snapshots(
            (
                100,
                [("50000.00", 1.0), ("49999.00", 2.0)],
                [("50001.00", 1.0), ("50002.00", 2.0)],
            ),
            (
                200,
                [("50010.00", 1.5), ("50009.00", 2.5)],
                [("50011.00", 1.5), ("50012.00", 2.5)],
            ),
        )
        mgr = BinanceL2Manager(http_client=http)

        await mgr.initialize("BTCUSDT")
        assert mgr.get_snapshot("BTCUSDT").sequence_id == 100

        d1 = _delta(100, 101, bids=[(50000.0, 1.5)])
        assert await mgr.apply_delta("BTCUSDT", d1) is True
        assert mgr.get_snapshot("BTCUSDT").bids[0].quantity == 1.5

        d2 = _delta(102, 103, asks=[(50001.0, 0.5)])
        assert await mgr.apply_delta("BTCUSDT", d2) is True

        d_gap = _delta(1104, 1110, bids=[(50000.0, 9.0)])
        assert await mgr.apply_delta("BTCUSDT", d_gap) is False

        healed = mgr.get_snapshot("BTCUSDT")
        assert (healed.sequence_id, healed.bids[0].price) == (200, 50010.0)

        d3 = _delta(200, 201, bids=[(50010.0, 2.0)])
        assert await mgr.apply_delta("BTCUSDT", d3) is True
        resumed = mgr.get_snapshot("BTCUSDT")
        assert resumed.bids[0] != resumed.asks[0]  # book uncrossed post-resume
        assert resumed.bids[0].quantity == 2.0


class TestFuturesPuChain:
    @pytest.mark.asyncio
    async def test_valid_pu_chain_never_resyncs(self) -> None:
        http = _http_with_snapshots((SNAP_ID, BASE_BOOK["bids"], BASE_BOOK["asks"]))
        mgr = _bootstrapped(http)
        await mgr.initialize(SYMBOL)

        chain = [
            _delta(100, 105, bids=[(66999.0, 3.0)], previous_update_id=99),
            _delta(500, 510, bids=[(66999.0, 4.0)], previous_update_id=105),
            _delta(2000, 2010, asks=[(67001.0, 2.0)], previous_update_id=510),
        ]
        outcomes = [await mgr.apply_delta(SYMBOL, frame) for frame in chain]

        assert outcomes == [True, True, True]
        assert mgr.get_snapshot(SYMBOL).bids[0].quantity == 4.0
        assert http.get.call_count == 1  # pu continuity means no REST resync

    @pytest.mark.asyncio
    async def test_broken_pu_chain_resyncs(self) -> None:
        http = _http_with_snapshots(
            (SNAP_ID, BASE_BOOK["bids"], BASE_BOOK["asks"]),
            (300, [(67015.0, 2.0)], [(67025.0, 2.0)]),
        )
        mgr = _bootstrapped(http)
        await mgr.initialize(SYMBOL)
        await mgr.apply_delta(SYMBOL, _delta(100, 105, bids=[(66999.0, 3.0)], previous_update_id=99))

        dangling_pu = _delta(500, 510, bids=[(66999.0, 5.0)], previous_update_id=999)
        assert await mgr.apply_delta(SYMBOL, dangling_pu) is False
        assert http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_huge_u_jump_fine_when_pu_links_up(self) -> None:
        """Futures update ids jump freely; only the pu handshake matters."""
        http = _http_with_snapshots((SNAP_ID, BASE_BOOK["bids"], BASE_BOOK["asks"]))
        mgr = _bootstrapped(http)
        await mgr.initialize(SYMBOL)
        await mgr.apply_delta(SYMBOL, _delta(100, 101, bids=[(66999.0, 3.0)], previous_update_id=99))

        far_forward = _delta(100101, 100200, bids=[(66999.0, 4.0)], previous_update_id=101)
        assert await mgr.apply_delta(SYMBOL, far_forward) is True
        assert http.get.call_count == 1
