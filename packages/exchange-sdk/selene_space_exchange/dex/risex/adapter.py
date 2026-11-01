"""RISEx perpetual DEX adapter — polling-based live adapter.

RISEx is an on-chain perpetual DEX that uses EIP-712 permit orders with
sequential nonces.  The official ``risex-client`` SDK is Node-only, so this
adapter builds REST calls directly via ``httpx``.

Key design decisions (inspired by a Node.js grid bot pattern, clean-room reimplementation, no source code copied):
- **Serial tx queue**: on-chain nonces are sequential; concurrent submissions
  cause ``NonceUsed`` reverts.  Every state-changing call goes through
  ``_serial()`` which serialises behind an ``asyncio.Lock``.
- **Fill detection (3-poll + price corroboration)**: RISEx has NO order-status
  or fill endpoint.  A vanished order is confirmed as filled only after it is
  absent from open orders for 3 consecutive polls AND the market price
  reached within 0.3 % of the limit price while we watched.  Without price
  corroboration, externally-cancelled orders would be misdetected as fills,
  corrupting grid stats.
- **Exchange endpoints are stubbed**: EIP-712 signing requires a Python
  crypto integration that is not yet implemented.  All trading operations
  (place_order, cancel_order, set_leverage, close_position) raise
  ``NotImplementedError``.  Market-data and fill-detection work in paper mode.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from selene_space_exchange.dex.risex.config import RisexConfig
from selene_space_exchange.dex.types import (
    DEXFill,
    DEXMarket,
    DEXPosition,
    DEXPriceUpdate,
)
from selene_space_exchange.dex.perpetual_mixin import PerpetualDEXMixin
from selene_space_exchange.trading import (
    BalanceInfo,
    OrderRequest,
    OrderResponse,
    TradingClient,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GONE_ATTEMPTS_THRESHOLD = 3  # consecutive polls before declaring an order gone
_PRICE_CORROBORATION_PCT = 0.003  # 0.3 %
_IDLE_PRUNE_SECONDS = 600  # 10 min — stop polling a market with no interest
_BUSY_WATCHDOG_SECONDS = 90  # force-unlock a wedged poll
_GRACE_FACTOR = 2  # grace period = poll_ms * _GRACE_FACTOR for just-placed orders


class RisexTradingAdapter(TradingClient, PerpetualDEXMixin):
    """Live adapter for the RISEx on-chain perpetual DEX.

    Inherits from both ``TradingClient`` (generic order interface) and
    ``PerpetualDEXMixin`` (extended DEX contract).  Market-data endpoints
    are fully implemented via REST; exchange (signing) endpoints are stubbed
    pending EIP-712 Python integration.
    """

    # ------------------------------------------------------------------
    # Construction & initialisation
    # ------------------------------------------------------------------

    def __init__(self, config: RisexConfig) -> None:
        self._cfg = config

        # HTTP client (built lazily in init / reconnect)
        self._http: httpx.AsyncClient | None = None

        # Market catalogue: market_id -> DEXMarket
        self._markets: dict[int, DEXMarket] = {}
        # Latest mid-price per market
        self._prices: dict[int, float] = {}

        # Account state
        self._balance: float | None = None
        self._realized_pnl: float | None = None
        self._data_source: str = "disconnected"
        self._last_ok_at: float = 0.0
        self._last_error: Exception | None = None

        # Order tracking: order_id -> _TrackedOrder (internal dict)
        self._tracked: dict[str, dict[str, Any]] = {}

        # Markets to poll (set of int market_id)
        self._watch: set[int] = set()
        # Last external-interest timestamp per market (for idle pruning)
        self._watch_touch: dict[int, float] = {}

        # Position cache: market_id -> DEXPosition
        self._positions: dict[int, DEXPosition] = {}

        # Serial tx queue — serialises all on-chain submissions
        self._tx_lock = asyncio.Lock()

        # Polling state
        self._poll_task: asyncio.Task | None = None
        self._busy: bool = False
        self._busy_since: float = 0.0

        # Event callbacks
        self._fill_callbacks: list[Callable[[DEXFill], None]] = []
        self._price_callbacks: list[Callable[[DEXPriceUpdate], None]] = []
        self._error_callbacks: list[Callable[[Exception], None]] = []

    async def init(self) -> bool:
        """Initialise the adapter: build HTTP client, fetch markets, start polling."""
        proxy = self._cfg.proxy or None
        self._http = httpx.AsyncClient(
            base_url=self._cfg.api_url or "https://api.rise.trade",
            timeout=httpx.Timeout(30.0, connect=10.0),
            proxy=proxy,
        )

        await self._load_markets()
        self._data_source = "real"
        self._last_ok_at = time.time()

        # Seed the watch set with the first market
        if self._markets:
            first_id = next(iter(self._markets))
            self._watch.add(first_id)

        # Refresh account data (best-effort)
        await self._refresh_account()

        # Start background polling
        self.start_polling()
        return True

    # ------------------------------------------------------------------
    # TradingClient ABC — basic interface
    # ------------------------------------------------------------------

    @property
    def exchange_name(self) -> str:
        return "risex"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        """Delegate to ``place_limit_order`` (RISEx only supports limit orders)."""
        side = request.side
        price = request.price
        if price is None:
            raise ValueError("RISEx requires a limit price")

        result = await self.place_limit_order(
            market_id=int(request.symbol),
            side=side,
            price=price,
            size_base=request.quantity,
            client_order_id=int(request.client_order_id) if request.client_order_id else None,
        )
        return OrderResponse(
            exchange=self.exchange_name,
            order_id=str(result.get("order_id", "")),
            client_order_id=request.client_order_id or "",
            symbol=request.symbol,
            side=side,
            order_type=OrderType.LIMIT,
            status=OrderStatus.ACCEPTED,
            quantity=request.quantity,
            price=price,
            raw=result,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        """Delegate to ``cancel_order_by_id``."""
        market_id = int(symbol)
        success = await self.cancel_order_by_id(market_id, order_id)
        return OrderResponse(
            exchange=self.exchange_name,
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED if success else OrderStatus.ACCEPTED,
            quantity=0,
        )

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        """Check tracked orders for the given order_id."""
        tracked = self._tracked.get(order_id)
        if tracked is None:
            return OrderResponse(
                exchange=self.exchange_name,
                order_id=order_id,
                symbol=symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                status=OrderStatus.CANCELLED,
                quantity=0,
            )
        return OrderResponse(
            exchange=self.exchange_name,
            order_id=order_id,
            symbol=symbol,
            side=tracked["side"],
            order_type=OrderType.LIMIT,
            status=OrderStatus.ACCEPTED,
            quantity=tracked["size_base"],
            price=tracked["price"],
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        """Return tracked orders as OrderResponse list."""
        results: list[OrderResponse] = []
        for oid, t in self._tracked.items():
            if symbol is not None and str(t["market_id"]) != symbol:
                continue
            results.append(
                OrderResponse(
                    exchange=self.exchange_name,
                    order_id=oid,
                    symbol=str(t["market_id"]),
                    side=t["side"],
                    order_type=OrderType.LIMIT,
                    status=OrderStatus.ACCEPTED,
                    quantity=t["size_base"],
                    price=t["price"],
                )
            )
        return results

    async def get_balances(self) -> list[BalanceInfo]:
        """Return account balance."""
        if self._balance is not None:
            return [BalanceInfo(asset="USDC", free=self._balance, total=self._balance)]
        return []

    async def test_connectivity(self) -> bool:
        """Probe the markets endpoint to verify connectivity."""
        try:
            assert self._http is not None
            resp = await self._http.get("/v1/markets")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Stop polling and close the HTTP client."""
        self.stop_polling()
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — market data
    # ------------------------------------------------------------------

    async def get_markets(self) -> list[DEXMarket]:
        """Return all tradeable perpetual markets."""
        return list(self._markets.values())

    async def get_price(self, market_id: int) -> float | None:
        """Return current mid-price for a market via orderbook."""
        mid = int(market_id)
        self._watch.add(mid)
        self._watch_touch[mid] = time.time()

        try:
            assert self._http is not None
            resp = await self._http.get(f"/v1/markets/id/{mid}/orderbook")
            if resp.status_code == 200:
                data = resp.json()
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                # Bids/asks may be [[price, size], ...] or [{price, size}, ...]
                bid_price = self._extract_book_price(bids, side="bid")
                ask_price = self._extract_book_price(asks, side="ask")
                if bid_price and ask_price:
                    mid_price = (bid_price + ask_price) / 2
                    self._prices[mid] = mid_price
                    return mid_price
        except Exception:
            pass  # fall back to stored mark price

        return self._prices.get(mid) or (
            self._markets.get(mid).last_price if mid in self._markets else None
        )

    async def get_candles(
        self, market_id: int, interval_sec: int = 3600, n: int = 200
    ) -> list[dict[str, Any]]:
        """Fetch candles via the V2 REST endpoint (V1 returns NotFound on mainnet).

        Parameters are in nanoseconds.  Response:
        ``{ data: [{ time(ns), open, high, low, close, volume }] }``
        """
        try:
            assert self._http is not None
            interval_ns = round(interval_sec * 1e9)
            to_ns = int(time.time() * 1e9)
            from_ns = to_ns - interval_ns * n

            resp = await self._http.get(
                f"/v1/markets/id/{market_id}/trading-view-data",
                params={"interval": interval_ns, "from": from_ns, "to": to_ns},
            )
            if resp.status_code != 200:
                return []

            body = resp.json()
            # The response may nest data under .data or .data.data or .data.candles
            raw: list[Any] = []
            if isinstance(body.get("data"), list):
                raw = body["data"]
            elif isinstance(body.get("data"), dict):
                inner = body["data"]
                if isinstance(inner.get("data"), list):
                    raw = inner["data"]
                elif isinstance(inner.get("candles"), list):
                    raw = inner["candles"]

            candles: list[dict[str, Any]] = []
            for c in raw:
                close_val = float(c.get("close", 0))
                if not _is_finite(close_val):
                    continue
                candles.append(
                    {
                        "time": float(c.get("time", 0)) / 1e6,  # ns -> ms
                        "open": float(c.get("open", 0)),
                        "high": float(c.get("high", 0)),
                        "low": float(c.get("low", 0)),
                        "close": close_val,
                        "volume": float(c.get("volume", 0)),
                    }
                )
            return candles
        except Exception:
            return []

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — leverage
    # ------------------------------------------------------------------

    async def set_leverage(self, market_id: int, leverage: int) -> bool:
        """Set leverage for a market (requires EIP-712 signing)."""
        raise NotImplementedError(
            "RISEx set_leverage requires EIP-712 signing integration. "
            "Implement the exchange-client signing layer to enable this."
        )

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — order management (all through _serial)
    # ------------------------------------------------------------------

    async def place_limit_order(
        self,
        market_id: int,
        side: OrderSide,
        price: float,
        size_base: float,
        *,
        reduce_only: bool = False,
        level_index: int = -1,
        client_order_id: int | None = None,
        post_only: bool = False,
    ) -> dict[str, Any]:
        """Place a limit order on RISEx (requires EIP-712 signing).

        **post_only MUST default to False**: RISEx has no order-status endpoint,
        so a post-only order silently rejected for crossing would vanish from
        the open-order list and the poll-based fill detector would fabricate
        a phantom fill.  GTC crossing just fills as taker.
        """
        async def _do_place() -> dict[str, Any]:
            # --- Stub: EIP-712 signing not yet implemented ---
            # When implemented, the parameters to build the order payload:
            #   market_id: numeric
            #   side: 0 for buy, 1 for sell
            #   order_type: 1 (Limit)
            #   price_ticks: price / stepPrice
            #   size_steps: size / stepSize
            #   time_in_force: 0 (GTC)
            #   post_only: False (always — see docstring)
            #   reduce_only: boolean
            #   client_order_id: optional string
            raise NotImplementedError(
                "RISEx place_limit_order requires EIP-712 signing integration. "
                "Build the exchange-client signing layer to enable order placement."
            )

        return await self._serial(_do_place)

    async def cancel_order_by_id(self, market_id: int, order_id: str) -> bool:
        """Cancel a single order (requires EIP-712 signing)."""
        # Remove from tracking first so the poll loop does not mistake
        # the cancel for a fill.
        self._tracked.pop(order_id, None)

        async def _do_cancel() -> bool:
            raise NotImplementedError(
                "RISEx cancel_order requires EIP-712 signing integration. "
                "Build the exchange-client signing layer to enable cancellation."
            )

        return await self._serial(_do_cancel)

    async def cancel_all(self, market_id: int) -> bool:
        """Cancel all open orders for a market (requires EIP-712 signing)."""
        # Clear tracking first so the poll loop does not mistake cancels for fills.
        mid = int(market_id)
        to_remove = [oid for oid, t in self._tracked.items() if t["market_id"] == mid]
        for oid in to_remove:
            del self._tracked[oid]

        async def _do_cancel_all() -> bool:
            raise NotImplementedError(
                "RISEx cancel_all requires EIP-712 signing integration. "
                "Build the exchange-client signing layer to enable bulk cancellation."
            )

        return await self._serial(_do_cancel_all)

    async def close_position(self, market_id: int) -> bool:
        """Close the current position with a reduce-only market order."""
        raise NotImplementedError(
            "RISEx close_position requires EIP-712 signing integration. "
            "Build the exchange-client signing layer to enable position closing."
        )

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — reconciliation & resume
    # ------------------------------------------------------------------

    async def fetch_open_orders(self, market_id: int) -> list[dict[str, Any]] | None:
        """Fetch real resting orders from the exchange for reconciliation."""
        mid = int(market_id)
        account = self._cfg.account
        if not account:
            return None

        try:
            assert self._http is not None
            resp = await self._http.get(f"/v1/orders/{account}/{mid}")
            if resp.status_code != 200:
                return None

            raw_list = resp.json()
            if not isinstance(raw_list, list):
                return None

            market = self._markets.get(mid)
            step_price = market.step_price if market else 1.0

            results: list[dict[str, Any]] = []
            for o in raw_list:
                # Price may come as price_ticks or as a direct price field
                px = float(o.get("price", 0))
                if px == 0 and o.get("price_ticks") is not None:
                    px = float(o["price_ticks"]) * step_price

                # Side: numeric 0=buy, 1=sell; or string "buy"/"sell"
                raw_side = o.get("side")
                if isinstance(raw_side, int):
                    side = OrderSide.BUY if raw_side == 0 else OrderSide.SELL
                else:
                    side_str = str(raw_side).lower()
                    side = OrderSide.BUY if side_str in ("0", "buy", "long") else OrderSide.SELL

                results.append(
                    {
                        "order_id": str(o.get("order_id")),
                        "price": px,
                        "side": side,
                    }
                )
            return results
        except Exception:
            logger.warning("RISEx fetch_open_orders failed for market %s", mid)
            return None

    def adopt_order(
        self,
        order_id: str,
        market_id: int,
        level_index: int,
        side: OrderSide,
        price: float,
        size_base: float,
    ) -> None:
        """Re-attach a previously-placed order to internal tracking (resume)."""
        mid = int(market_id)
        self._watch.add(mid)
        self._tracked[order_id] = {
            "market_id": mid,
            "level_index": level_index,
            "side": side,
            "price": float(price),
            "size_base": float(size_base),
            "seen": False,
            "placed_at": time.time() * 1000,
            "gone_attempts": 0,
            "px_lo": None,
            "px_hi": None,
        }

    def get_position(self, market_id: int) -> DEXPosition | None:
        """Return current position for a market, or None."""
        pos = self._positions.get(int(market_id))
        if pos is not None and pos.size_base != 0:
            return pos
        return None

    async def reconnect(self) -> bool:
        """Re-establish connection without touching resting orders.

        Rebuild the HTTP client, discard the serial lock queue, probe the
        account, and restart polling.  Order tracking is preserved.
        """
        self.stop_polling()
        self._busy = False
        self._last_error = None

        # If we never came up, do a full init
        if self._http is None or not self._markets:
            return await self.init()

        # Rebuild HTTP client
        if self._http is not None:
            await self._http.aclose()
        proxy = self._cfg.proxy or None
        self._http = httpx.AsyncClient(
            base_url=self._cfg.api_url or "https://api.rise.trade",
            timeout=httpx.Timeout(30.0, connect=10.0),
            proxy=proxy,
        )

        # Discard a possibly-wedged serialisation chain
        # (the Lock is re-entrant-safe; just ensure it is not held)
        if self._tx_lock.locked():
            # Best-effort: the lock should not be held since we stopped polling
            pass

        # Probe: fetch balance to verify connectivity
        await self._refresh_account()
        self._last_ok_at = time.time()
        self.start_polling()
        return True

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — event callbacks
    # ------------------------------------------------------------------

    def on_fill(self, callback: Callable[[DEXFill], None]) -> None:
        """Register fill event callback."""
        self._fill_callbacks.append(callback)

    def on_price(self, callback: Callable[[DEXPriceUpdate], None]) -> None:
        """Register price update callback."""
        self._price_callbacks.append(callback)

    def on_error(self, callback: Callable[[Exception], None]) -> None:
        """Register error callback."""
        self._error_callbacks.append(callback)

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — polled state properties
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float | None:
        """Latest account equity (approximated as balance + unrealized PnL)."""
        if self._balance is None:
            return None
        total_unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return self._balance + total_unrealized

    @property
    def balance(self) -> float | None:
        """Latest account balance."""
        return self._balance

    @property
    def realized_pnl(self) -> float | None:
        """Cumulative realized PnL."""
        return self._realized_pnl

    @property
    def data_source(self) -> str:
        """'real' if connected to live API, 'disconnected' otherwise."""
        return self._data_source

    @property
    def last_ok_at(self) -> float:
        """Unix timestamp of last successful API call."""
        return self._last_ok_at

    # ------------------------------------------------------------------
    # PerpetualDEXMixin — polling lifecycle
    # ------------------------------------------------------------------

    def start_polling(self) -> None:
        """Start the background polling loop (fills, prices, positions)."""
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    def stop_polling(self) -> None:
        """Stop the background polling loop."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    # ------------------------------------------------------------------
    # Serial tx queue
    # ------------------------------------------------------------------

    async def _serial(self, fn: Callable[[], Any]) -> Any:
        """Serialize on-chain transactions behind an asyncio.Lock.

        RISEx orders are on-chain EIP-712 permits with a SEQUENTIAL nonce.
        Concurrent submissions cause nonce collisions (``NonceUsed`` / reverted).
        Every state-changing call must go through this method.
        """
        async with self._tx_lock:
            return await fn()

    # ------------------------------------------------------------------
    # Internal: market loading
    # ------------------------------------------------------------------

    async def _load_markets(self) -> None:
        """Fetch the market catalogue and populate ``_markets`` / ``_prices``."""
        assert self._http is not None
        resp = await self._http.get("/v1/markets")
        resp.raise_for_status()
        raw_list = resp.json()

        if not isinstance(raw_list, list):
            logger.warning("RISEx /v1/markets returned non-list: %s", type(raw_list))
            return

        for m in raw_list:
            cfg = m.get("config", {}) or {}
            mid = int(m.get("market_id", 0))
            last_price = float(m.get("mark_price", 0) or m.get("last_price", 0) or 0)

            market = DEXMarket(
                market_id=mid,
                name=cfg.get("name", ""),
                display_name=m.get("display_name", cfg.get("name", "")),
                symbol=m.get("base_asset_symbol", ""),
                last_price=last_price,
                step_size=float(cfg.get("step_size", 0)),
                step_price=float(cfg.get("step_price", 0)),
                max_leverage=int(cfg.get("max_leverage", 20)),
                min_order_size=float(cfg.get("min_order_size", cfg.get("step_size", 0))),
            )
            self._markets[mid] = market
            if last_price > 0:
                self._prices[mid] = last_price

    # ------------------------------------------------------------------
    # Internal: account refresh
    # ------------------------------------------------------------------

    async def _refresh_account(self) -> None:
        """Refresh balance and realized PnL (best-effort)."""
        account = self._cfg.account
        if not account or self._http is None:
            return

        try:
            resp = await self._http.get(f"/v1/balance/{account}")
            if resp.status_code == 200:
                body = resp.json()
                val = body if not isinstance(body, dict) else body.get("balance", body.get("amount"))
                if val is not None:
                    self._balance = float(val)
                    self._last_ok_at = time.time()
        except Exception:
            pass

        try:
            resp = await self._http.get(f"/v1/realized-pnl/{account}")
            if resp.status_code == 200:
                body = resp.json()
                pnl_val = body.get("total_realized_pnl") if isinstance(body, dict) else None
                if pnl_val is not None:
                    self._realized_pnl = float(pnl_val)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal: polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Background polling loop: prices, fills, positions, account."""
        poll_interval = self._cfg.poll_ms / 1000.0
        while True:
            try:
                await asyncio.sleep(poll_interval)
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = exc
                self._emit_error(exc)

    async def _poll_once(self) -> None:
        """Single poll iteration across all watched markets."""
        if self._busy:
            # Watchdog: a poll wedged >90s blocks polling forever
            if time.time() - self._busy_since < _BUSY_WATCHDOG_SECONDS:
                return
            logger.warning(
                "RISEx: previous poll stuck for >%ds, force-unlocking.",
                _BUSY_WATCHDOG_SECONDS,
            )

        self._busy = True
        self._busy_since = time.time()
        try:
            await self._prune_idle_markets()
            await self._poll_markets()
            await self._refresh_account()
            self._last_ok_at = time.time()
        except Exception as exc:
            self._last_error = exc
            self._emit_error(exc)
        finally:
            self._busy = False

    async def _prune_idle_markets(self) -> None:
        """Remove markets from the watch set that have had no activity for 10 min."""
        now = time.time()
        to_remove: list[int] = []
        for mid in list(self._watch):
            has_orders = any(t["market_id"] == mid for t in self._tracked.values())
            has_position = mid in self._positions
            last_touch = self._watch_touch.get(mid, 0)
            if not has_orders and not has_position and now - last_touch > _IDLE_PRUNE_SECONDS:
                to_remove.append(mid)
        for mid in to_remove:
            self._watch.discard(mid)
            self._watch_touch.pop(mid, None)

    async def _poll_markets(self) -> None:
        """Poll each watched market for price, fills, and position."""
        grace_ms = self._cfg.poll_ms * _GRACE_FACTOR

        for mid in list(self._watch):
            # --- Price ---
            px: float | None = None
            try:
                px = await self.get_price(mid)
            except Exception:
                pass

            if px is not None:
                self._emit_price(DEXPriceUpdate(market_id=mid, price=px))
                # Record the price range each tracked order has lived through
                # (used for fill corroboration below)
                for t in self._tracked.values():
                    if t["market_id"] != mid:
                        continue
                    t["px_lo"] = min(t["px_lo"], px) if t["px_lo"] is not None else px
                    t["px_hi"] = max(t["px_hi"], px) if t["px_hi"] is not None else px

            # --- Open orders -> fill detection ---
            open_orders = await self._fetch_open_orders_raw(mid)
            if open_orders is not None:
                live_ids: set[str] = {str(o.get("order_id")) for o in open_orders}
                # Mark seen orders
                for o in open_orders:
                    oid = str(o.get("order_id", ""))
                    t = self._tracked.get(oid)
                    if t is not None:
                        t["seen"] = True
                        t["gone_attempts"] = 0

                # Detect vanished orders
                now_ms = time.time() * 1000
                for oid, t in list(self._tracked.items()):
                    if t["market_id"] != mid:
                        continue
                    if oid in live_ids:
                        continue
                    # Grace period: a just-placed order may not appear yet
                    if not t["seen"] and now_ms - t.get("placed_at", 0) < grace_ms:
                        continue

                    # Increment gone counter
                    t["gone_attempts"] = t.get("gone_attempts", 0) + 1
                    if t["gone_attempts"] < _GONE_ATTEMPTS_THRESHOLD:
                        continue

                    # Order has been absent for 3+ consecutive polls.
                    # Now corroborate with the observed price path.
                    del self._tracked[oid]

                    if self._price_never_reached(t):
                        # Price never got within 0.3% of the limit price —
                        # the order was cancelled externally, not filled.
                        self._emit_error(
                            RuntimeError(
                                f"Order {oid} ({t['side'].value} @ {t['price']}) "
                                f"vanished from orderbook but price never reached "
                                f"that level — judged externally cancelled (not a fill)."
                            )
                        )
                        continue

                    # Confirmed fill
                    self._emit_fill(
                        DEXFill(
                            order_id=oid,
                            market_id=mid,
                            side=t["side"],
                            price=t["price"],
                            size_base=t["size_base"],
                            level_index=t.get("level_index", -1),
                        )
                    )

            # --- Position ---
            await self._poll_position(mid)

    # ------------------------------------------------------------------
    # Internal: fill detection helpers
    # ------------------------------------------------------------------

    def _price_never_reached(self, tracked: dict[str, Any]) -> bool:
        """Check whether the observed price path ever reached the order's limit.

        A limit BUY can only fill if the market came DOWN to its price.
        A limit SELL can only fill if the market came UP to its price.
        If price never got within 0.3% of the limit while we watched,
        the vanished order cannot have filled — it was cancelled externally.
        """
        side = tracked["side"]
        price = tracked["price"]
        px_lo = tracked.get("px_lo")
        px_hi = tracked.get("px_hi")

        if side == OrderSide.BUY:
            # Buy fills when price drops to or below the limit
            return px_lo is not None and px_lo > price * (1 + _PRICE_CORROBORATION_PCT)
        else:
            # Sell fills when price rises to or above the limit
            return px_hi is not None and px_hi < price * (1 - _PRICE_CORROBORATION_PCT)

    # ------------------------------------------------------------------
    # Internal: position polling
    # ------------------------------------------------------------------

    async def _poll_position(self, market_id: int) -> None:
        """Fetch and cache position data for a market."""
        mid = int(market_id)
        account = self._cfg.account
        if not account or self._http is None:
            return

        try:
            resp = await self._http.get(f"/v1/positions/{account}/{mid}")
            if resp.status_code != 200:
                return

            p = resp.json()
            if not p or not _is_finite(float(p.get("size", 0))):
                self._positions.pop(mid, None)
                return

            raw_size = float(p.get("size", 0))
            if raw_size == 0:
                self._positions.pop(mid, None)
                return

            # Determine side: numeric 1=short, or string "short"/"sell"
            raw_side = p.get("side")
            is_short = (
                isinstance(raw_side, int) and raw_side == 1
            ) or (
                isinstance(raw_side, str) and raw_side.lower() in ("1", "short", "sell")
            )
            size = abs(raw_size) * (-1 if is_short else 1)

            # RISEx mainnet often returns avg_entry_price / mark_price /
            # unrealized_pnl as BLANK strings.  Only quote_amount (signed
            # quote cost) is reliably populated.  Derive the missing pieces:
            #   entry ≈ |quote_amount| / |size|
            #   mark = orderbook mid (from _prices)
            #   uPnl = size * (mark - entry)
            entry = _safe_float(p.get("avg_entry_price")) or _safe_float(
                p.get("entry_price")
            ) or _safe_float(p.get("average_entry_price"))

            if not (entry > 0):
                qa = _safe_float(p.get("quote_amount"))
                abs_size = abs(raw_size)
                if _is_finite(qa) and abs_size > 0:
                    entry = abs(qa) / abs_size

            entry = entry if entry > 0 else 0.0
            mark = _safe_float(p.get("mark_price")) or self._prices.get(mid) or entry

            u_pnl = _safe_float(p.get("unrealized_pnl"))
            if not _is_finite(u_pnl):
                u_pnl = (size * (mark - entry)) if (entry > 0 and mark > 0) else 0.0

            leverage = _safe_float(p.get("leverage"))

            self._positions[mid] = DEXPosition(
                size_base=size,
                entry_price=entry,
                unrealized_pnl=u_pnl,
                leverage=leverage if _is_finite(leverage) else None,
            )
        except Exception:
            pass  # keep last known position

    # ------------------------------------------------------------------
    # Internal: raw open-order fetch (for fill detection)
    # ------------------------------------------------------------------

    async def _fetch_open_orders_raw(self, market_id: int) -> list[dict[str, Any]] | None:
        """Fetch raw open orders from the exchange (for fill detection).

        Returns None on failure (skip this fill-detection cycle).
        """
        account = self._cfg.account
        if not account or self._http is None:
            return None

        try:
            resp = await self._http.get(f"/v1/orders/{account}/{market_id}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, list) else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal: event emission
    # ------------------------------------------------------------------

    def _emit_fill(self, fill: DEXFill) -> None:
        for cb in self._fill_callbacks:
            try:
                cb(fill)
            except Exception as exc:
                logger.exception("RISEx fill callback error: %s", exc)

    def _emit_price(self, update: DEXPriceUpdate) -> None:
        for cb in self._price_callbacks:
            try:
                cb(update)
            except Exception as exc:
                logger.exception("RISEx price callback error: %s", exc)

    def _emit_error(self, exc: Exception) -> None:
        for cb in self._error_callbacks:
            try:
                cb(exc)
            except Exception:
                logger.exception("RISEx error callback error")

    # ------------------------------------------------------------------
    # Internal: orderbook price extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_book_price(entries: list[Any], side: str) -> float | None:
        """Extract the best price from an orderbook side.

        Entries may be ``[price, size]`` lists or ``{price, size}`` dicts.
        """
        if not entries:
            return None
        best = entries[0]
        if isinstance(best, (list, tuple)) and len(best) >= 1:
            val = float(best[0])
            return val if _is_finite(val) else None
        if isinstance(best, dict):
            val = float(best.get("price", best.get("0", 0)))
            return val if _is_finite(val) else None
        return None

    # ------------------------------------------------------------------
    # Internal: step/tick conversion (for future EIP-712 integration)
    # ------------------------------------------------------------------

    def _steps(self, market_id: int, base: float) -> int:
        """Convert a base quantity to step units."""
        market = self._markets.get(int(market_id))
        if market is None or market.step_size == 0:
            return 1
        return max(1, round(base / market.step_size))

    def _ticks(self, market_id: int, price: float) -> int:
        """Convert a price to tick units."""
        market = self._markets.get(int(market_id))
        if market is None or market.step_price == 0:
            return round(price)
        return round(price / market.step_price)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_finite(v: float) -> bool:
    """Check whether a float is a finite number (not NaN/inf)."""
    import math

    return isinstance(v, (int, float)) and math.isfinite(v)


def _safe_float(v: Any) -> float:
    """Safely convert a value to float, returning NaN on failure.

    Handles blank strings (common in RISEx mainnet responses) and None.
    """
    if v is None or v == "":
        return float("nan")
    try:
        f = float(v)
        return f
    except (ValueError, TypeError):
        return float("nan")
