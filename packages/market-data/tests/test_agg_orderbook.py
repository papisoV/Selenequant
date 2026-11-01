"""AggregatedOrderbookEngine — malformed L2 levels must not crash the merge.

A half-populated snapshot carries NaN prices; ``int(round(nan))`` inside
``_quantize_price`` raises ValueError and, before the guard, took the whole
``/api/v1/agg-orderbook/current`` endpoint down with a 500 (users saw an
intermittent error on a page that normally returns fine).
"""

from __future__ import annotations

import math
import time

from selene_space_market.aggregators.agg_orderbook import AggregatedOrderbookEngine
from selene_space_types.market import OrderBookL2Snapshot, PriceLevel


class _Svc:
    """Minimal MarketService stand-in exposing only get_orderbook()."""

    def __init__(self, books: dict[tuple[str, str], OrderBookL2Snapshot]) -> None:
        self._books = books

    def get_orderbook(
        self, symbol: str, exchange: str = "binance", market_type: str = "spot"
    ) -> OrderBookL2Snapshot | None:
        return self._books.get((exchange, market_type))


def _book(
    exchange: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]]
) -> OrderBookL2Snapshot:
    return OrderBookL2Snapshot(
        exchange=exchange,
        symbol="BTC/USDT",
        market_type="futures",
        timestamp=int(time.time() * 1000),
        sequence_id=1,
        bids=[PriceLevel(price=p, quantity=q) for p, q in bids],
        asks=[PriceLevel(price=p, quantity=q) for p, q in asks],
    )


def _engine(
    books: dict[tuple[str, str], OrderBookL2Snapshot],
) -> AggregatedOrderbookEngine:
    eng = AggregatedOrderbookEngine()
    eng.set_service(_Svc(books))
    return eng


def test_nan_price_does_not_raise_and_drops_only_the_bad_level() -> None:
    # One venue ships a NaN top-of-book; its bids are unusable but the other
    # venue's book must still merge — and nothing may raise.
    books = {
        ("binance", "futures"): _book(
            "binance",
            bids=[(float("nan"), 1.0), (83000.0, 2.0)],
            asks=[(83100.0, 3.0)],
        ),
        ("okx", "futures"): _book(
            "okx",
            bids=[(83001.0, 4.0)],
            asks=[(83101.0, 5.0)],
        ),
    }
    snap = _engine(books).compute("BTC/USDT", depth=25)

    assert snap is not None
    assert all(math.isfinite(lv.price) for lv in snap.bids)
    assert all(math.isfinite(lv.price) for lv in snap.asks)
    # okx's healthy bid survived despite binance's NaN top-of-book.
    assert any(abs(lv.price - 83001.0) < 1e-6 for lv in snap.bids)


def test_all_nan_book_yields_no_snapshot_not_a_crash() -> None:
    # Every level malformed → no usable levels → compute returns None, which
    # the endpoint renders as the existing "no data" payload (not a 500).
    books = {
        ("binance", "futures"): _book(
            "binance",
            bids=[(float("nan"), 1.0)],
            asks=[(float("nan"), 1.0)],
        ),
    }
    assert _engine(books).compute("BTC/USDT", depth=25) is None


def test_healthy_book_is_unchanged() -> None:
    # The guard must not perturb the all-good path.
    books = {
        ("binance", "futures"): _book(
            "binance", bids=[(83000.0, 1.0), (82990.0, 2.0)], asks=[(83100.0, 1.5)]
        ),
    }
    snap = _engine(books).compute("BTC/USDT", depth=25)
    assert snap is not None
    assert [lv.price for lv in snap.bids] == [83000.0, 82990.0]
    assert snap.bids[0].total_quantity == 1.0
    assert snap.source_count == 1
