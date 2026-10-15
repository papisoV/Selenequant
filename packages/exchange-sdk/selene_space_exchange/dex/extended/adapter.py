"""Extended (Starknet) perpetual DEX adapter.

LIVE adapter for Extended (https://extended.exchange), a perpetuals DEX on
Starknet. Uses REST via httpx (X-Api-Key header for reads) and order signing
via stark_crypto (SNIP-12 + Stark ECDSA, verified against the official python
SDK test vectors; self_test() runs on init).

Markets on Extended are addressed by NAME ("BTC-USD"). The bot uses numeric
marketIds, so this adapter assigns stable per-process ids (sorted by daily
volume) and keeps the name in ``market.name``.

Fills are detected by polling open orders: an order we placed that is no
longer resting is confirmed via GET /api/v1/user/orders/history?id={id}
(a FILLED order leaves the open-orders endpoint, so history is the only place
that reports its filledQty); only a positively-confirmed fill is reported.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from selene_space_exchange.dex.extended.config import ExtendedConfig
from selene_space_exchange.dex.extended.stark_crypto import (
    align_to_step,
    order_msg_hash,
    parse_dec,
    public_key_from_private,
    self_test,
    settlement_amounts,
    stark_sign,
    to_hex,
)
from selene_space_exchange.dex.perpetual_mixin import PerpetualDEXMixin
from selene_space_exchange.dex.types import (
    DEXFill,
    DEXMarket,
    DEXPosition,
    DEXPriceUpdate,
)
from selene_space_exchange.trading import (
    BalanceInfo,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    TradingClient,
)
from selene_space_types.trading import OrderSide

logger = logging.getLogger(__name__)

# ---- constants ----

DOMAINS: dict[str, dict[str, Any]] = {
    "mainnet": {
        "name": "Perpetuals",
        "version": "v0",
        "chainId": "SN_MAIN",
        "revision": 1,
    },
    "testnet": {
        "name": "Perpetuals",
        "version": "v0",
        "chainId": "SN_SEPOLIA",
        "revision": 1,
    },
}

INTERVALS: dict[int, str] = {
    60: "PT1M",
    300: "PT5M",
    900: "PT15M",
    1800: "PT30M",
    3600: "PT1H",
    7200: "PT2H",
    14400: "PT4H",
    86400: "P1D",
}

ORDER_EXPIRY_DAYS: int = 90
SETTLEMENT_BUFFER_DAYS: int = 14
USER_AGENT: str = "ExtendedGridBot/1.0"
_GONE_ATTEMPTS_LIMIT: int = 12
_IDLE_PRUNE_MS: int = 600_000  # 10 minutes
_BUSY_WATCHDOG_MS: int = 90_000  # 90 seconds


class _TrackedOrder:
    """Internal tracking state for a placed order."""

    __slots__ = (
        "market_id",
        "level_index",
        "side",
        "price",
        "size_base",
        "seen",
        "external_id",
        "placed_at",
        "gone_attempts",
        "resolving",
    )

    def __init__(
        self,
        *,
        market_id: int,
        level_index: int,
        side: OrderSide,
        price: float,
        size_base: float,
        external_id: str = "",
    ) -> None:
        self.market_id = market_id
        self.level_index = level_index
        self.side = side
        self.price = price
        self.size_base = size_base
        self.seen: bool = False
        self.external_id = external_id
        self.placed_at: float = time.time() * 1000
        self.gone_attempts: int = 0
        self.resolving: bool = False


class ExtendedTradingAdapter(TradingClient, PerpetualDEXMixin):
    """Extended (Starknet) perpetual DEX adapter.

    Inherits from both TradingClient (unified order interface) and
    PerpetualDEXMixin (grid-specific perpetual operations).
    """

    def __init__(self, config: ExtendedConfig) -> None:
        self._config = config
        self._api_key: str = config.api_key
        self._vault: int = config.vault
        self._private_key: int = int(config.stark_private_key, 0) if config.stark_private_key else 0
        self._public_key: int | None = (
            int(config.public_key, 0) if config.public_key else None
        )
        self._api_url: str = config.api_url.rstrip("/")
        self._network: str = config.network or "mainnet"
        self._fee_rate: str = config.fee_rate or "0.0005"
        self._poll_ms: int = config.poll_ms
        self._grace_ms: float = self._poll_ms * 2.0
        self._domain: dict[str, Any] = DOMAINS.get(self._network, DOMAINS["mainnet"])

        # State
        self._markets: dict[int, DEXMarket] = {}
        self._market_l2: dict[int, dict[str, Any]] = {}  # market_id -> l2 config
        self._balance: float | None = None
        self._equity: float | None = None
        self._realized_pnl: float | None = None
        self._data_source: str = "synthetic"
        self._last_ok_at: float = 0.0
        self._last_error: str | None = None

        # Tracking
        self._tracked: dict[str, _TrackedOrder] = {}
        self._watch: set[int] = set()
        self._watch_touch: dict[int, float] = {}
        self._positions: dict[int, DEXPosition] = {}
        self._prices: dict[int, float] = {}

        # Polling
        self._poll_task: asyncio.Task | None = None
        self._busy: bool = False
        self._busy_since: float = 0.0

        # HTTP client (created in init)
        self._http: httpx.AsyncClient | None = None

        # Event callbacks
        self._fill_callbacks: list[Callable[[DEXFill], None]] = []
        self._price_callbacks: list[Callable[[DEXPriceUpdate], None]] = []
        self._error_callbacks: list[Callable[[Exception], None]] = []

    # ---- TradingClient properties ----

    @property
    def exchange_name(self) -> str:
        return "extended"

    # ---- PerpetualDEXMixin properties ----

    @property
    def equity(self) -> float | None:
        return self._equity

    @property
    def balance(self) -> float | None:
        return self._balance

    @property
    def realized_pnl(self) -> float | None:
        return self._realized_pnl

    @property
    def data_source(self) -> str:
        return self._data_source

    @property
    def last_ok_at(self) -> float:
        return self._last_ok_at

    # ---- Lifecycle ----

    async def init(self) -> bool:
        """Initialize the adapter: validate keys, fetch markets, start polling."""
        if not self._api_key or not self._vault or not self._private_key:
            raise ValueError(
                "LIVE mode requires EXTENDED_API_KEY / EXTENDED_VAULT / "
                "EXTENDED_STARK_PRIVATE_KEY (from app.extended.exchange API Management)."
            )
        # Refuse to trade if the signing implementation doesn't reproduce the
        # official SDK test vector (protects against env/runtime quirks).
        self_test()

        if self._public_key is None:
            self._public_key = public_key_from_private(self._private_key)

        self._http = httpx.AsyncClient(
            base_url=self._api_url,
            headers=self._headers(),
            timeout=httpx.Timeout(15.0, connect=10.0),
        )

        # The markets endpoint has been observed transiently returning an EMPTY
        # list. Retry a few times before giving up.
        data: Any = None
        for attempt in range(1, 4):
            try:
                data = await self._get("/api/v1/info/markets")
            except Exception as exc:
                if attempt == 3:
                    raise
                logger.warning(
                    "[Extended] Markets fetch failed (attempt %d/3): %s",
                    attempt,
                    exc,
                )
                await asyncio.sleep(2.5 * attempt)
                continue
            if isinstance(data, list) and len(data) > 0:
                break
            if attempt < 3:
                logger.warning(
                    "[Extended] Market list empty/abnormal, retry %d/2...",
                    attempt,
                )
                await asyncio.sleep(2.5 * attempt)

        market_list = (data or [])
        market_list = [
            m
            for m in market_list
            if m.get("active", True)
            and (m.get("type", "PERPETUAL") == "PERPETUAL")
            and m.get("l2Config")
        ]
        market_list.sort(
            key=lambda m: float(m.get("marketStats", {}).get("dailyVolume", 0) or 0),
            reverse=True,
        )

        market_id = 1
        for m in market_list:
            t = m.get("tradingConfig", {})
            stats = m.get("marketStats", {})
            px = float(stats.get("lastPrice", 0) or stats.get("markPrice", 0) or 0)
            l2 = m.get("l2Config", {})

            self._markets[market_id] = DEXMarket(
                market_id=market_id,
                name=m["name"],
                display_name=m.get("assetName", m["name"]),
                symbol=m.get("assetName", ""),
                last_price=px,
                step_size=float(t.get("minOrderSizeChange", t.get("minOrderSize", 0)) or 0),
                step_price=float(t.get("minPriceChange", 0) or 0),
                max_leverage=int(t.get("maxLeverage", 50) or 50),
                min_order_size=float(t.get("minOrderSize", 0) or 0),
                qty_step=str(t.get("minOrderSizeChange", t.get("minOrderSize", ""))),
                price_step=str(t.get("minPriceChange", "")),
            )
            self._market_l2[market_id] = {
                "synthetic_id": str(l2.get("syntheticId", "")),
                "collateral_id": str(l2.get("collateralId", "")),
                "syn_res": int(l2.get("syntheticResolution", 0) or 0),
                "col_res": int(l2.get("collateralResolution", 0) or 0),
            }
            self._prices[market_id] = px
            market_id += 1

        if not self._markets:
            # Diagnostic: try to distinguish API maintenance from proxy IP block.
            try:
                resp = await self._http.get(
                    "/api/v1/info/markets",
                    headers=self._headers(),
                    timeout=10.0,
                )
                body = resp.text[:200].replace("\n", " ").replace("\r", "")
                logger.error(
                    "[Extended] Diagnostic: HTTP %d; response start: %s",
                    resp.status_code,
                    body,
                )
                if resp.status_code == 403 or any(
                    kw in body.lower()
                    for kw in ("cloudflare", "challenge", "blocked", "denied", "captcha")
                ):
                    logger.error(
                        "[Extended] Suspected proxy exit IP blocked by exchange WAF. "
                        "Switch proxy node and reconnect."
                    )
            except Exception as exc:
                logger.error("[Extended] Diagnostic request failed: %s (check proxy)", exc)
            raise ValueError("Extended returned no tradeable markets.")

        self._data_source = "real"
        self._last_ok_at = time.time() * 1000
        await self._refresh_account()
        self.start_polling()
        return True

    async def reconnect(self) -> bool:
        """Re-establish connection without touching resting orders."""
        self.stop_polling()
        self._busy = False
        self._last_error = None

        if not self._markets:
            return await self.init()

        try:
            b = await self._get("/api/v1/user/balance")
        except Exception as exc:
            if "404" in str(exc):
                b = None
            else:
                raise

        if b:
            self._balance = float(b.get("balance", 0))
            self._equity = float(b.get("equity", 0))

        self._last_ok_at = time.time() * 1000
        self.start_polling()
        return True

    async def close(self) -> None:
        """Stop polling and close HTTP client."""
        self.stop_polling()
        if self._http:
            await self._http.aclose()
            self._http = None

    # ---- HTTP ----

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self._api_key,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _req(self, method: str, path: str, body: Any = None) -> Any:
        """Make an HTTP request to the Extended API."""
        if self._http is None:
            raise RuntimeError("Adapter not initialized (call init() first)")

        resp = await self._http.request(
            method,
            path,
            json=body,
        )

        j: Any = None
        try:
            j = resp.json()
        except Exception:
            pass  # some endpoints return empty bodies

        if resp.status_code == 401:
            raise ValueError("API key invalid or expired (401).")
        if resp.status_code == 429:
            raise ValueError("Rate limited (429), please retry later.")

        if isinstance(j, dict) and j.get("status") == "ERROR":
            err = j.get("error", {})
            raise ValueError(
                f"Extended API error {err.get('code', resp.status_code)}: "
                f"{err.get('message', str(j))}"
            )

        if resp.status_code >= 400:
            raise ValueError(f"Extended API error HTTP {resp.status_code}: {path}")

        return j.get("data") if isinstance(j, dict) else None

    async def _get(self, path: str) -> Any:
        return await self._req("GET", path)

    # ---- Market helpers ----

    def _market(self, market_id: int) -> DEXMarket:
        """Get market by numeric ID, raising if unknown."""
        m = self._markets.get(market_id)
        if m is None:
            raise ValueError(f"Unknown market marketId={market_id}")
        return m

    # ---- PerpetualDEXMixin: Market data ----

    async def get_markets(self) -> list[DEXMarket]:
        """Return all tradeable perpetual markets."""
        return list(self._markets.values())

    async def get_price(self, market_id: int) -> float | None:
        """Return current mid-price for a market."""
        m_id = int(market_id)
        self._watch.add(m_id)
        self._watch_touch[m_id] = time.time() * 1000

        m = self._market(m_id)
        try:
            book = await self._get(
                f"/api/v1/info/markets/{m.name}/orderbook"
            )
            if isinstance(book, dict):
                bid = float(book.get("bid", [{}])[0].get("price", 0)) if book.get("bid") else 0
                ask = float(book.get("ask", [{}])[0].get("price", 0)) if book.get("ask") else 0
                if bid and ask:
                    mid = (bid + ask) / 2
                    self._prices[m_id] = mid
                    return mid
                if bid or ask:
                    px = bid or ask
                    self._prices[m_id] = px
                    return px
        except Exception:
            pass  # fall back
        return self._prices.get(m_id, m.last_price)

    async def get_candles(
        self, market_id: int, interval_sec: int = 3600, n: int = 200
    ) -> list[dict[str, Any]]:
        """Return recent candles."""
        m = self._market(market_id)
        interval = INTERVALS.get(interval_sec, "PT1H")
        data = await self._get(
            f"/api/v1/info/candles/{m.name}/trades"
            f"?interval={interval}&limit={min(n, 1000)}"
        )
        if not isinstance(data, list):
            return []
        candles = []
        for c in data:
            candle = {
                "time": int(c.get("T", 0)),
                "open": float(c.get("o", 0)),
                "high": float(c.get("h", 0)),
                "low": float(c.get("l", 0)),
                "close": float(c.get("c", 0)),
                "volume": float(c.get("v", 0) or 0),
            }
            if candle["close"] and candle["close"] == candle["close"]:  # isfinite check
                candles.append(candle)
        candles.sort(key=lambda x: x["time"])
        return candles

    # ---- PerpetualDEXMixin: Leverage ----

    async def set_leverage(self, market_id: int, leverage: int) -> bool:
        """Set leverage for a market. Return True on success."""
        m = self._market(market_id)
        try:
            await self._req(
                "PATCH",
                "/api/v1/user/leverage",
                {"market": m.name, "leverage": str(leverage)},
            )
            return True
        except Exception as exc:
            self._emit_error(exc)
            return False

    # ---- PerpetualDEXMixin: Order management ----

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
        """Place a limit order. Return {order_id, ...}."""
        m = self._market(market_id)
        qty_str = align_to_step(size_base, m.qty_step, "down")
        price_str = align_to_step(price, m.price_step, "nearest")

        if parse_dec(qty_str).i <= 0:
            raise ValueError("Quantity too small, below market minimum order unit.")

        # postOnly defaults to FALSE: a grid replacement placed 1-3s after a
        # fill can cross the book in a fast market; a post-only order is then
        # REJECTED and the bot (correctly) doesn't re-quote it.
        result = await self._submit_order(
            m,
            market_id=market_id,
            side=side,
            qty_str=qty_str,
            price_str=price_str,
            order_type="LIMIT",
            time_in_force="GTT",
            post_only=post_only,
            reduce_only=reduce_only,
        )
        order_id = result["order_id"]
        external_id = result.get("external_id", "")

        self._watch.add(market_id)
        self._tracked[order_id] = _TrackedOrder(
            market_id=market_id,
            level_index=level_index,
            side=side,
            price=float(price_str),
            size_base=float(qty_str),
            external_id=external_id,
        )
        return {"order_id": order_id}

    async def cancel_order_by_id(self, market_id: int, order_id: str) -> bool:
        """Cancel a single order. Return True on success."""
        self._tracked.pop(order_id, None)
        try:
            await self._req("DELETE", f"/api/v1/user/order/{order_id}")
            return True
        except Exception as exc:
            self._emit_error(exc)
            return False

    async def cancel_all(self, market_id: int) -> bool:
        """Cancel all open orders for a market. Return True on success."""
        m = self._market(market_id)
        # Remove tracked orders for this market
        to_remove = [
            oid for oid, t in self._tracked.items() if t.market_id == market_id
        ]
        for oid in to_remove:
            del self._tracked[oid]
        try:
            await self._req(
                "POST", "/api/v1/user/order/massCancel", {"markets": [m.name]}
            )
            return True
        except Exception as exc:
            self._emit_error(exc)
            return False

    async def close_position(self, market_id: int) -> bool:
        """Close the current position with a reduce-only IOC market order."""
        m = self._market(market_id)
        pos = self._positions.get(market_id)
        if pos is None or pos.size_base == 0:
            return True

        is_buy = pos.size_base < 0  # closing a short buys back
        last = self._prices.get(market_id, pos.entry_price)
        worst = last * (1.05 if is_buy else 0.95)
        qty_str = align_to_step(abs(pos.size_base), m.qty_step, "down")
        price_str = align_to_step(worst, m.price_step, "nearest")

        try:
            await self._submit_order(
                m,
                market_id=market_id,
                side=OrderSide.BUY if is_buy else OrderSide.SELL,
                qty_str=qty_str,
                price_str=price_str,
                order_type="MARKET",
                time_in_force="IOC",
                post_only=False,
                reduce_only=True,
            )
            return True
        except Exception as exc:
            self._emit_error(exc)
            return False

    # ---- PerpetualDEXMixin: Reconciliation & resume ----

    async def fetch_open_orders(self, market_id: int) -> list[dict[str, Any]] | None:
        """Fetch real resting orders from the exchange for reconciliation.

        Return None if the snapshot is unreliable (skip this reconcile cycle).
        """
        m = self._market(market_id)
        data = await self._get(
            f"/api/v1/user/orders?market={m.name}"
        )
        # A missing/malformed payload is NOT "zero orders" -- return None so
        # the reconciler skips this cycle instead of pruning live orders.
        if not isinstance(data, list):
            return None
        return [
            {
                "order_id": str(o.get("id", "")),
                "price": float(o.get("price", 0)),
                "side": (
                    OrderSide.BUY
                    if str(o.get("side", "")).lower() == "buy"
                    else OrderSide.SELL
                ),
            }
            for o in data
        ]

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
        m_id = int(market_id)
        self._watch.add(m_id)
        self._tracked[order_id] = _TrackedOrder(
            market_id=m_id,
            level_index=level_index,
            side=side,
            price=float(price),
            size_base=float(size_base),
        )

    def get_position(self, market_id: int) -> DEXPosition | None:
        """Return current position for a market, or None."""
        pos = self._positions.get(int(market_id))
        if pos is not None and pos.size_base != 0:
            return pos
        return None

    # ---- PerpetualDEXMixin: Event callbacks ----

    def on_fill(self, callback: Callable[[DEXFill], None]) -> None:
        """Register fill event callback."""
        self._fill_callbacks.append(callback)

    def on_price(self, callback: Callable[[DEXPriceUpdate], None]) -> None:
        """Register price update callback."""
        self._price_callbacks.append(callback)

    def on_error(self, callback: Callable[[Exception], None]) -> None:
        """Register error callback."""
        self._error_callbacks.append(callback)

    # ---- PerpetualDEXMixin: Polling lifecycle ----

    def start_polling(self) -> None:
        """Start the background polling loop (fills, prices, positions)."""
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.ensure_future(self._poll_loop())

    def stop_polling(self) -> None:
        """Stop the background polling loop."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

    # ---- TradingClient ABC methods ----

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place a new order on the exchange (TradingClient interface)."""
        # Extract market_id from symbol (Extended uses numeric IDs internally)
        try:
            market_id = int(request.symbol)
        except ValueError:
            raise ValueError(f"Extended adapter requires numeric market_id as symbol, got: {request.symbol}")

        side = request.side
        if request.order_type == OrderType.MARKET:
            m = self._market(market_id)
            last = self._prices.get(market_id, m.last_price)
            is_buy = side == OrderSide.BUY
            worst = last * (1.05 if is_buy else 0.95)
            qty_str = align_to_step(request.quantity, m.qty_step, "down")
            price_str = align_to_step(worst, m.price_step, "nearest")
            result = await self._submit_order(
                m,
                market_id=market_id,
                side=side,
                qty_str=qty_str,
                price_str=price_str,
                order_type="MARKET",
                time_in_force="IOC",
                post_only=False,
                reduce_only=False,
            )
        else:
            result = await self.place_limit_order(
                market_id,
                side,
                request.price or 0.0,
                request.quantity,
            )
            result = {"order_id": result.get("order_id", "")}

        return OrderResponse(
            exchange="extended",
            order_id=result.get("order_id", ""),
            symbol=request.symbol,
            side=side,
            order_type=request.order_type,
            status=OrderStatus.ACCEPTED,
            quantity=request.quantity,
            price=request.price,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        """Cancel an existing order (TradingClient interface)."""
        try:
            market_id = int(symbol)
        except ValueError:
            market_id = 0
        success = await self.cancel_order_by_id(market_id, order_id)
        return OrderResponse(
            exchange="extended",
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED if success else OrderStatus.ACCEPTED,
            quantity=0,
        )

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        """Get current status of an order (TradingClient interface)."""
        tracked = self._tracked.get(order_id)
        if tracked:
            return OrderResponse(
                exchange="extended",
                order_id=order_id,
                symbol=symbol,
                side=tracked.side,
                order_type=OrderType.LIMIT,
                status=OrderStatus.ACCEPTED,
                quantity=tracked.size_base,
                price=tracked.price,
            )
        return OrderResponse(
            exchange="extended",
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.FILLED,
            quantity=0,
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        """Get all currently open orders (TradingClient interface)."""
        results: list[OrderResponse] = []
        for oid, t in self._tracked.items():
            if symbol is not None:
                try:
                    if t.market_id != int(symbol):
                        continue
                except ValueError:
                    continue
            results.append(
                OrderResponse(
                    exchange="extended",
                    order_id=oid,
                    symbol=str(t.market_id),
                    side=t.side,
                    order_type=OrderType.LIMIT,
                    status=OrderStatus.ACCEPTED,
                    quantity=t.size_base,
                    price=t.price,
                )
            )
        return results

    async def get_balances(self) -> list[BalanceInfo]:
        """Get account balances (TradingClient interface)."""
        if self._balance is not None:
            return [
                BalanceInfo(
                    asset="USDC",
                    free=self._balance,
                    locked=0.0,
                    total=self._balance,
                )
            ]
        return []

    async def test_connectivity(self) -> bool:
        """Check if the exchange is reachable (TradingClient interface)."""
        try:
            await self._get("/api/v1/info/markets")
            return True
        except Exception:
            return False

    # ---- Internal: order submission ----

    async def _submit_order(
        self,
        market: DEXMarket,
        *,
        market_id: int,
        side: OrderSide,
        qty_str: str,
        price_str: str,
        order_type: str,
        time_in_force: str,
        post_only: bool,
        reduce_only: bool,
    ) -> dict[str, str]:
        """Build, sign and submit an order. Returns {order_id, external_id}."""
        is_buy = side == OrderSide.BUY
        l2 = self._market_l2.get(market_id, {})
        amounts = settlement_amounts(
            qty=qty_str,
            price=price_str,
            fee_rate=self._fee_rate,
            syn_res=l2.get("syn_res", 0),
            col_res=l2.get("col_res", 0),
            is_buy=is_buy,
        )

        nonce = random.randint(0, 0xFFFFFFFF)
        expiry_epoch_ms = int(time.time() * 1000) + ORDER_EXPIRY_DAYS * 86400_000
        expiration_sec = (
            (expiry_epoch_ms + 999) // 1000 + SETTLEMENT_BUFFER_DAYS * 86400
        )

        syn_id = int(l2.get("synthetic_id", "0"))
        col_id = int(l2.get("collateral_id", "0"))

        hash_val = order_msg_hash(
            position_id=self._vault,
            base_asset_id=syn_id,
            base_amount=amounts["synthetic_amount"],
            quote_asset_id=col_id,
            quote_amount=amounts["collateral_amount"],
            fee_asset_id=col_id,
            fee_amount=amounts["fee_amount"],
            expiration_sec=expiration_sec,
            salt=nonce,
            public_key=self._public_key or 0,
            domain=self._domain,
        )

        sig = stark_sign(hash_val, self._private_key)

        payload: dict[str, Any] = {
            "id": str(hash_val),
            "market": market.name,
            "type": order_type,
            "side": "BUY" if is_buy else "SELL",
            "qty": qty_str,
            "price": price_str,
            "reduceOnly": reduce_only,
            "postOnly": post_only,
            "timeInForce": time_in_force,
            "expiryEpochMillis": expiry_epoch_ms,
            "fee": self._fee_rate,
            "nonce": str(nonce),
            "selfTradeProtectionLevel": "ACCOUNT",
            "settlement": {
                "signature": {"r": to_hex(sig["r"]), "s": to_hex(sig["s"])},
                "starkKey": to_hex(self._public_key or 0),
                "collateralPosition": str(self._vault),
            },
        }

        data = await self._req("POST", "/api/v1/user/order", payload)
        return {
            "order_id": str(data.get("id", payload["id"]) if isinstance(data, dict) else payload["id"]),
            "external_id": str(payload["id"]),
        }

    # ---- Internal: polling ----

    async def _poll_loop(self) -> None:
        """Background polling loop for fills, prices, and positions."""
        while True:
            try:
                await self._poll()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._last_error = str(exc)
                self._emit_error(exc)
            await asyncio.sleep(self._poll_ms / 1000.0)

    async def _poll(self) -> None:
        """Single poll cycle: prices, open orders, positions, account."""
        if self._busy:
            # Watchdog: a poll wedged >90s would block polling forever.
            if time.time() * 1000 - self._busy_since < _BUSY_WATCHDOG_MS:
                return
            logger.warning(
                "[Extended] Previous poll stuck >90s, force-unlocking."
            )

        self._busy = True
        self._busy_since = time.time() * 1000
        try:
            # Prune idle watch entries: no resting orders, no position, and no
            # external price interest for 10 minutes -> stop polling that market.
            now_t = time.time() * 1000
            for m_id in list(self._watch):
                has_orders = any(t.market_id == m_id for t in self._tracked.values())
                if (
                    not has_orders
                    and m_id not in self._positions
                    and now_t - self._watch_touch.get(m_id, 0) > _IDLE_PRUNE_MS
                ):
                    self._watch.discard(m_id)
                    self._watch_touch.pop(m_id, None)

            for m_id in list(self._watch):
                m = self._markets.get(m_id)
                if m is None:
                    continue

                # Price (also emitted for the dashboard)
                try:
                    px = await self.get_price(m_id)
                    if px is not None:
                        update = DEXPriceUpdate(market_id=m_id, price=px)
                        for cb in self._price_callbacks:
                            try:
                                cb(update)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Open orders -> fill detection
                open_orders: Any = None
                try:
                    open_orders = await self._get(
                        f"/api/v1/user/orders?market={m.name}"
                    )
                except Exception:
                    pass

                if isinstance(open_orders, list):
                    live_ids = {str(o.get("id", "")) for o in open_orders}
                    for o in open_orders:
                        t = self._tracked.get(str(o.get("id", "")))
                        if t:
                            t.seen = True
                            t.gone_attempts = 0

                    now = time.time() * 1000
                    for oid, t in list(self._tracked.items()):
                        if t.market_id != m_id or oid in live_ids or t.resolving:
                            continue
                        # Resolve once seen resting OR aged past grace.
                        if not t.seen and now - t.placed_at < self._grace_ms:
                            continue
                        t.resolving = True
                        asyncio.ensure_future(
                            self._resolve_gone(oid, t)
                        ).add_done_callback(lambda _: setattr(t, "resolving", False))

                # Position
                try:
                    ps = await self._get(
                        f"/api/v1/user/positions?market={m.name}"
                    )
                    if isinstance(ps, list) and ps:
                        p = ps[0]
                        if p and float(p.get("size", 0) or 0) != 0:
                            is_short = str(p.get("side", "")).upper() == "SHORT"
                            size = abs(float(p.get("size", 0))) * (-1 if is_short else 1)
                            self._positions[m_id] = DEXPosition(
                                size_base=size,
                                entry_price=float(p.get("openPrice", 0) or 0),
                                unrealized_pnl=float(p.get("unrealisedPnl", 0) or 0),
                                leverage=float(p.get("leverage", 0)) if p.get("leverage") is not None else None,
                            )
                        else:
                            self._positions.pop(m_id, None)
                    elif isinstance(ps, list) and not ps:
                        self._positions.pop(m_id, None)
                except Exception:
                    pass  # keep last known

            await self._refresh_account()
            self._last_ok_at = time.time() * 1000
        except Exception as exc:
            self._last_error = str(exc)
            self._emit_error(exc)
        finally:
            self._busy = False

    async def _resolve_gone(self, order_id: str, t: _TrackedOrder) -> None:
        """Resolve a tracked order that disappeared from the book.

        A tracked order disappeared from the book: filled or cancelled?
        Inconclusive lookups are retried a few polls before defaulting to
        cancelled, so a transient API error can't fabricate a phantom fill.
        When the exchange reports the real filled qty / average price we
        report THOSE (accurate volume & PnL) instead of the order's intended
        size/price.
        """
        verdict = "unknown"
        fill_price = t.price
        fill_size = t.size_base

        try:
            # A FILLED order is NOT returned by the open-orders endpoints --
            # once it fills it moves to ORDER HISTORY. Confirm via history.
            # Match by externalId (the order hash -- a full-precision STRING
            # the API echoes back). The numeric id is a 19-digit integer that
            # exceeds JS's safe-integer range, so JSON parsing rounds it and
            # string comparisons never match reliably.
            mkt_name = self._markets.get(t.market_id)
            mkt_param = f"&market={mkt_name.name}" if mkt_name else ""
            data = await self._get(
                f"/api/v1/user/orders/history?limit=200{mkt_param}"
            )
            rows = data if isinstance(data, list) else []

            matched = None
            for x in rows:
                if (
                    (t.external_id and str(x.get("externalId", "")) == str(t.external_id))
                    or str(x.get("id", "")) == str(order_id)
                ):
                    matched = x
                    break

            if matched is not None:
                fq = float(matched.get("filledQty", 0) or 0)
                st = str(matched.get("status", ""))

                if fq > 0 or "FILLED" in st.upper():
                    # Positive confirmation only
                    verdict = "filled"
                    if fq > 0:
                        fill_size = fq
                    avg = float(matched.get("averagePrice", 0) or 0)
                    if avg > 0:
                        fill_price = avg
                elif any(
                    kw in st.upper() for kw in ("CANCELLED", "REJECTED", "EXPIRED")
                ):
                    verdict = "cancelled"
                elif any(
                    kw in st.upper()
                    for kw in ("NEW", "OPEN", "ACCEPTED", "PENDING", "UNTRIGGERED", "PARTIAL")
                ):
                    # History says the order is STILL LIVE: the open-orders
                    # snapshot that reported it "gone" was a glitch. Revive
                    # tracking and bail out.
                    t.gone_attempts = 0
                    t.seen = True
                    return
        except Exception:
            pass  # keep 'unknown'

        if verdict == "unknown":
            t.gone_attempts += 1
            if t.gone_attempts < _GONE_ATTEMPTS_LIMIT:
                return  # re-check; tolerate order-history settlement lag
            verdict = "cancelled"  # never confirmed filled -> assume NOT filled

        self._tracked.pop(order_id, None)

        if verdict == "filled":
            fill = DEXFill(
                order_id=order_id,
                market_id=t.market_id,
                side=t.side,
                price=fill_price,
                size_base=fill_size,
                level_index=t.level_index,
            )
            for cb in self._fill_callbacks:
                try:
                    cb(fill)
                except Exception:
                    pass
        else:
            self._emit_error(
                ValueError(
                    f"Order {order_id} ({t.side.value} @ {t.price}) "
                    "unconfirmed fill, stopped tracking (no re-quote)."
                )
            )

    # ---- Internal: account refresh ----

    async def _refresh_account(self) -> None:
        """Refresh account balance and equity."""
        try:
            b = await self._get("/api/v1/user/balance")
            if isinstance(b, dict):
                self._balance = float(b.get("balance", 0))
                self._equity = float(b.get("equity", 0))
                self._last_ok_at = time.time() * 1000
        except Exception as exc:
            if "401" in str(exc):
                raise  # bad API key: surface it
            if "404" in str(exc):
                self._balance = 0.0  # balance endpoint 404s when balance is 0
            # otherwise keep last known balance

    # ---- Internal: event emission ----

    def _emit_error(self, exc: Exception) -> None:
        """Emit an error to all registered callbacks."""
        for cb in self._error_callbacks:
            try:
                cb(exc)
            except Exception:
                pass
