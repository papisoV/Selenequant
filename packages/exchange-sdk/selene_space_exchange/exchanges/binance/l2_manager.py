"""Local L2 order-book maintenance fed by Binance depth diffs.

Implements the venue-documented sync procedure: seed from a REST depth
snapshot, then fold ordered ``depthUpdate`` diffs on top. Diffs that arrive
out of order invalidate the book and force a fresh snapshot fetch. Futures
feeds carry an explicit ``pu`` back-reference which replaces the numeric-gap
heuristic used for spot feeds.
"""

from __future__ import annotations

import time
from logging import getLogger
from typing import Any, Callable

import httpx

from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.exceptions import SnapshotRecoveryError
from selene_space_exchange.exchanges.binance.normalizer import BinanceDepthDelta
from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_types.market import OrderBookL2Snapshot, PriceLevel

_log = getLogger(__name__)

BINANCE_REST_DEPTH_URL = "https://api.binance.com/api/v3/depth"
DEFAULT_DEPTH_LIMIT = 1000

Callback = Callable[..., Any]

#: Max missing update-ids tolerated on a spot diff before resyncing.
MAX_GAP_UPDATE_IDS = 500

_RECOVERY_COOLDOWN_S = 15.0
_RECOVERY_TIMEOUT_S = 6.0


class _LocalBook:
    __slots__ = (
        "symbol",
        "unified_symbol",
        "bids",
        "asks",
        "last_update_id",
        "initialized",
        "recovering",
        "seen_diff",
        "last_recovery_at",
    )

    def __init__(self, raw_symbol: str, unified_symbol: str) -> None:
        self.symbol = raw_symbol
        self.unified_symbol = unified_symbol
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id = 0
        self.initialized = False
        self.recovering = False
        # True once the first post-snapshot diff has been folded in; spot
        # gap detection only makes sense from the second diff onward.
        self.seen_diff = False
        self.last_recovery_at = 0.0


def _now_ms() -> int:
    return int(time.time() * 1000)


class BinanceL2Manager:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        symbol_normalizer: SymbolNormalizer | None = None,
        on_snapshot: Callback | None = None,
        rest_url: str = BINANCE_REST_DEPTH_URL,
        depth_limit: int = DEFAULT_DEPTH_LIMIT,
    ) -> None:
        self._own_client = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=_RECOVERY_TIMEOUT_S)
        self._limiter = rate_limiter
        self._sym = symbol_normalizer if symbol_normalizer is not None else SymbolNormalizer()
        self._on_snapshot = on_snapshot
        self._rest_url = rest_url
        self._limit = depth_limit
        self._books: dict[str, _LocalBook] = {}

    async def initialize(self, symbol: str) -> OrderBookL2Snapshot:
        unified = self._sym.to_unified("binance", symbol)
        book = _LocalBook(symbol, unified)
        self._books[symbol] = book
        await self._fetch_and_apply_snapshot(book)
        return self._build_snapshot(book)

    async def reinitialize_all(self) -> None:
        for book in list(self._books.values()):
            await self._fetch_and_apply_snapshot(book)

    async def apply_delta(self, symbol: str, diff: BinanceDepthDelta) -> bool:
        book = self._books.get(symbol)
        if book is None or not book.initialized:
            _log.warning("depth diff for %s before initialize(); ignored", symbol)
            return False
        if book.recovering:
            return False

        verdict = self._classify(book, diff)
        if verdict == "stale":
            return False
        if verdict == "resync":
            await self._recover(book)
            return False

        self._fold(book, diff)
        if not self._book_is_consistent(book):
            _log.warning("crossed book on %s after diff %d", symbol, diff.last_update_id)
            await self._recover(book)
            return False
        book.seen_diff = True
        return True

    def get_snapshot(self, symbol: str) -> OrderBookL2Snapshot | None:
        book = self._books.get(symbol)
        if book is None or not book.initialized:
            return None
        return self._build_snapshot(book)

    async def close(self) -> None:
        if self._own_client:
            await self._http.aclose()

    # -- classification ----------------------------------------------------

    def _classify(self, book: _LocalBook, diff: BinanceDepthDelta) -> str:
        """Pick among apply / stale / resync for one incoming diff."""
        if diff.previous_update_id is not None:
            # Futures protocol: the diff itself names its predecessor.
            if not book.seen_diff:
                return "apply"
            if diff.previous_update_id == book.last_update_id:
                return "apply"
            return "resync"

        if diff.last_update_id <= book.last_update_id:
            return "stale"

        if not book.seen_diff:
            # First diff after the snapshot tolerates an imperfect bridge;
            # venue docs allow events buffered during the fetch to be dropped.
            if diff.first_update_id > book.last_update_id + 1:
                _log.info(
                    "%s: first diff starts at %d, snapshot ended at %d",
                    book.symbol,
                    diff.first_update_id,
                    book.last_update_id,
                )
            return "apply"

        missing = diff.first_update_id - book.last_update_id - 1
        if missing > MAX_GAP_UPDATE_IDS:
            _log.warning("%s: %d missing update ids -> resync", book.symbol, missing)
            return "resync"
        if missing > 0:
            _log.debug("%s: small gap of %d ids, folding anyway", book.symbol, missing)
        return "apply"

    # -- mutation ----------------------------------------------------------

    @staticmethod
    def _fold_levels(side_map: dict[float, float], rows: list[PriceLevel]) -> None:
        for row in rows:
            if row.quantity == 0.0:
                side_map.pop(row.price, None)
            else:
                side_map[row.price] = row.quantity

    def _fold(self, book: _LocalBook, diff: BinanceDepthDelta) -> None:
        self._fold_levels(book.bids, diff.delta.bids)
        self._fold_levels(book.asks, diff.delta.asks)
        book.last_update_id = diff.last_update_id

    @staticmethod
    def _book_is_consistent(book: _LocalBook) -> bool:
        if not book.bids or not book.asks:
            return True
        return max(book.bids) < min(book.asks)

    # -- snapshot plumbing ---------------------------------------------------

    async def _recover(self, book: _LocalBook) -> None:
        now = time.monotonic()
        if now - book.last_recovery_at < _RECOVERY_COOLDOWN_S:
            _log.debug("%s: recovery throttled by cooldown", book.symbol)
            return
        book.last_recovery_at = now
        book.recovering = True
        try:
            await self._fetch_and_apply_snapshot(book)
        finally:
            book.recovering = False

    async def _fetch_and_apply_snapshot(self, book: _LocalBook) -> None:
        params = {"symbol": book.symbol, "limit": self._limit}
        try:
            if self._limiter is not None:
                await self._limiter.acquire()
            resp = await self._http.get(self._rest_url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            book.initialized = False
            raise SnapshotRecoveryError(
                f"depth snapshot fetch failed for {book.symbol}: {exc}"
            ) from exc

        book.bids = {float(p): float(q) for p, q in payload.get("bids", [])}
        book.asks = {float(p): float(q) for p, q in payload.get("asks", [])}
        book.last_update_id = int(payload["lastUpdateId"])
        book.seen_diff = False
        book.initialized = True

        snapshot = self._build_snapshot(book)
        if self._on_snapshot is not None:
            outcome = self._on_snapshot(snapshot)
            if hasattr(outcome, "__await__"):
                await outcome

    def _build_snapshot(self, book: _LocalBook) -> OrderBookL2Snapshot:
        bid_rows = sorted(
            (PriceLevel(price=p, quantity=q) for p, q in book.bids.items()),
            key=lambda lv: lv.price,
            reverse=True,
        )
        ask_rows = sorted(
            (PriceLevel(price=p, quantity=q) for p, q in book.asks.items()),
            key=lambda lv: lv.price,
        )
        return OrderBookL2Snapshot(
            exchange="binance",
            symbol=book.unified_symbol,
            timestamp=_now_ms(),
            sequence_id=book.last_update_id,
            bids=bid_rows,
            asks=ask_rows,
        )
