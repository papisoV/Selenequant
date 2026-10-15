"""Decibel (Aptos) perpetual DEX adapter.

LIVE adapter for Decibel (https://decibel.trade), a fully on-chain perpetuals
DEX on Aptos.

Unlike a normal CEX-style REST API, every order/cancel on Decibel is an Aptos
transaction signed with an Ed25519 key. This adapter uses the Decibel/Geomi
REST API for read operations (market data, account info, open orders) and will
require Aptos BCS transaction signing for live trading (currently stubbed — the
grid engine runs in paper mode initially).

Markets are addressed by NAME ("BTC-USD") and an on-chain object address.
The bot uses numeric marketIds, so this adapter assigns stable per-process
ids and keeps name + address on the market object.

Fills are detected by polling open orders: a tracked order that is no longer
resting is looked up in order history; if it wasn't cancelled/rejected it is
reported as a fill (positive-confirmation detection).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from selene_space_exchange.dex.decibel.config import DecibelConfig
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

# ---- network configs ----

NETWORKS: dict[str, dict[str, Any]] = {
    "mainnet": {
        "fullnode_url": "https://api.mainnet.aptoslabs.com/v1",
        "trading_http_url": "https://api.mainnet.aptoslabs.com/decibel",
        "package": "0x50ead22afd6ffd9769e3b3d6e0e64a2a350d68e8b102c4e72e33d0b8cfdfdb06",
    },
    "testnet": {
        "fullnode_url": "https://api.testnet.aptoslabs.com/v1",
        "trading_http_url": "https://api.testnet.aptoslabs.com/decibel",
        "package": "0xe7da2794b1d8af76532ed95f38bfdf1136abfd8ea3a240189971988a83101b7f",
    },
}

# bot intervalSec -> Decibel candlestick interval string
INTERVALS: dict[int, str] = {
    60: "1m",
    300: "5m",
    900: "15m",
    1800: "30m",
    3600: "1h",
    7200: "2h",
    14400: "4h",
    86400: "1d",
}

_GONE_ATTEMPTS_LIMIT: int = 6
_IDLE_PRUNE_MS: int = 600_000  # 10 minutes
_BUSY_WATCHDOG_MS: int = 90_000  # 90 seconds
_STALE_PRICE_THRESHOLD_ENTER: float = 0.0025  # 0.25%
_STALE_PRICE_THRESHOLD_EXIT: float = 0.0010  # 0.10%


# ---- pure helpers ----


def to_chain_price(price: float, px_decimals: int, tick_size: int) -> int:
    """Convert a decimal price to integer chain units snapped to tick_size."""
    raw = price * 10**px_decimals
    ticks = max(1, round(raw / tick_size))
    return ticks * tick_size


def from_chain_price(chain: int, px_decimals: int) -> float:
    """Convert integer chain units back to decimal price."""
    return chain / 10**px_decimals


def to_chain_size(size: float, sz_decimals: int, lot_size: int) -> int:
    """Convert a decimal size to integer chain units snapped DOWN to lot_size."""
    raw = size * 10**sz_decimals
    lots = int(raw / lot_size + 1e-9)  # epsilon absorbs float error
    return lots * lot_size


def from_chain_size(chain: int, sz_decimals: int) -> float:
    """Convert integer chain units back to decimal size."""
    return chain / 10**sz_decimals


def pick_num(obj: dict[str, Any], *keys: str) -> float | None:
    """Pick the first present, finite numeric field from an object."""
    for k in keys:
        raw = obj.get(k)
        if raw is None or raw == "":
            continue
        try:
            v = float(raw)
        except (ValueError, TypeError):
            continue
        if v != v:  # NaN check
            continue
        return v
    return None


class _TrackedOrder:
    """Internal tracking state for a placed order."""

    __slots__ = (
        "market_id",
        "level_index",
        "side",
        "price",
        "size_base",
        "seen",
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
    ) -> None:
        self.market_id = market_id
        self.level_index = level_index
        self.side = side
        self.price = price
        self.size_base = size_base
        self.seen: bool = False
        self.placed_at: float = time.time() * 1000
        self.gone_attempts: int = 0
        self.resolving: bool = False


class DecibelTradingAdapter(TradingClient, PerpetualDEXMixin):
    """Decibel (Aptos) perpetual DEX adapter.

    Inherits from both TradingClient (unified order interface) and
    PerpetualDEXMixin (grid-specific perpetual operations).
    """

    def __init__(self, config: DecibelConfig) -> None:
        self._config = config
        self._api_key: str = config.api_key
        self._private_key: str = config.private_key
        self._subaccount: str = config.subaccount
        self._api_url: str = config.api_url
        self._network: str = config.network or "mainnet"
        self._poll_ms: int = config.poll_ms
        self._grace_ms: float = self._poll_ms * 2.0
        self._net = NETWORKS.get(self._network, NETWORKS["mainnet"])

        # State
        self._markets: dict[int, DEXMarket] = {}
        self._market_meta: dict[int, dict[str, Any]] = {}  # addr, px_decimals, etc.
        self._by_addr: dict[str, DEXMarket] = {}
        self._balance: float | None = None
        self._equity: float | None = None
        self._realized_pnl: float | None = None
        self._data_source: str = "synthetic"
        self._last_ok_at: float = 0.0
        self._last_error: str | None = None
        self._acct_upnl: float | None = None

        # Tracking
        self._tracked: dict[str, _TrackedOrder] = {}
        self._watch: set[int] = set()
        self._watch_touch: dict[int, float] = {}
        self._positions: dict[int, DEXPosition] = {}
        self._prices: dict[int, float] = {}
        self._px_stale: set[int] = set()

        # Polling
        self._poll_task: asyncio.Task | None = None
        self._busy: bool = False
        self._busy_since: float = 0.0

        # HTTP client
        self._http: httpx.AsyncClient | None = None

        # Event callbacks
        self._fill_callbacks: list[Callable[[DEXFill], None]] = []
        self._price_callbacks: list[Callable[[DEXPriceUpdate], None]] = []
        self._error_callbacks: list[Callable[[Exception], None]] = []

    # ---- TradingClient properties ----

    @property
    def exchange_name(self) -> str:
        return "decibel"

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
        """Initialize the adapter: fetch markets, validate keys, start polling."""
        if not self._api_key or not self._private_key:
            raise ValueError(
                "LIVE mode requires DECIBEL_API_KEY (from geomi.dev) and "
                "DECIBEL_PRIVATE_KEY (API wallet private key from app.decibel.trade/api)."
            )
        if not self._subaccount:
            raise ValueError(
                "DECIBEL_SUBACCOUNT (Trading Account address from app.decibel.trade) is required."
            )

        self._http = httpx.AsyncClient(
            base_url=self._api_url or self._net["trading_http_url"],
            headers=self._headers(),
            timeout=httpx.Timeout(15.0),
        )

        # Load markets with retry for transient failures
        await self._retry(self._load_markets, "markets list")
        if not self._markets:
            raise ValueError("Decibel returned no tradeable markets.")

        self._data_source = "real"
        self._last_ok_at = time.time() * 1000
        await self._retry(self._refresh_account, "account info")
        self.start_polling()
        return True

    async def reconnect(self) -> bool:
        """Re-establish connection without touching resting orders."""
        self.stop_polling()
        self._busy = False
        self._last_error = None

        if not self._markets:
            return await self.init()

        # Rebuild HTTP client (discard any wedged state)
        if self._http:
            await self._http.aclose()
        self._http = httpx.AsyncClient(
            base_url=self._api_url or self._net["trading_http_url"],
            headers=self._headers(),
            timeout=httpx.Timeout(15.0),
        )

        await self._retry(self._refresh_account, "account info", 2)
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
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _req(self, method: str, path: str, body: Any = None) -> Any:
        """Make an HTTP request to the Decibel API."""
        if self._http is None:
            raise RuntimeError("Adapter not initialized (call init() first)")

        resp = await self._http.request(method, path, json=body)

        if resp.status_code == 401:
            raise ValueError("API key invalid or expired (401).")
        if resp.status_code == 429:
            raise ValueError("Rate limited (429), please retry later.")

        j: Any = None
        try:
            j = resp.json()
        except Exception:
            pass

        if resp.status_code >= 400:
            raise ValueError(
                f"Decibel API error HTTP {resp.status_code}: {path}"
            )

        if isinstance(j, dict) and j.get("success") is False:
            raise ValueError(
                f"Decibel rejected: {j.get('reason', j.get('error', str(j)))}"
            )

        return j

    async def _get(self, path: str) -> Any:
        return await self._req("GET", path)

    async def _retry(
        self, fn: Any, label: str, tries: int = 4
    ) -> Any:
        """Retry transient network failures."""
        last_err: Exception | None = None
        for i in range(1, tries + 1):
            try:
                return await fn()
            except Exception as exc:
                last_err = exc
                msg = str(exc)
                transient = any(
                    kw in msg
                    for kw in (
                        "ECONNRESET",
                        "ETIMEDOUT",
                        "fetch failed",
                        "socket",
                        "EPIPE",
                        "ECONNREFUSED",
                        "aborted",
                        "timed out",
                        "Connection",
                    )
                )
                if not transient or i == tries:
                    raise
                logger.warning(
                    "[Decibel] Read %s failed (%s), %d/%d retry...",
                    label,
                    exc,
                    i,
                    tries - 1,
                )
                await asyncio.sleep(1.5 * i)
        raise last_err  # type: ignore[misc]

    # ---- Market helpers ----

    def _market(self, market_id: int) -> DEXMarket:
        """Get market by numeric ID, raising if unknown."""
        m = self._markets.get(market_id)
        if m is None:
            raise ValueError(f"Unknown market marketId={market_id}")
        return m

    async def _load_markets(self) -> None:
        """Fetch and index all open Decibel markets."""
        list_data = await self._get("/markets")
        markets_list = list_data if isinstance(list_data, list) else []

        # Try to fetch prices snapshot
        prices_list: list[dict[str, Any]] = []
        try:
            px_data = await self._retry(
                lambda: self._get("/market-prices"), "price snapshot", 3
            )
            if isinstance(px_data, list):
                prices_list = px_data
        except Exception:
            pass

        px_by_addr: dict[str, dict[str, Any]] = {}
        for p in prices_list:
            addr = str(p.get("market", p.get("market_addr", "")))
            if addr:
                px_by_addr[addr] = p

        open_markets = [
            m
            for m in markets_list
            if str(m.get("mode", "Open")) == "Open"
        ]

        market_id = 1
        for m in open_markets:
            addr = str(m.get("market_addr", ""))
            px_info = px_by_addr.get(addr, {})
            last_price = pick_num(px_info, "mid_px", "mark_px", "oracle_px", "last_px") or 0

            px_decimals = int(m.get("px_decimals", 0))
            sz_decimals = int(m.get("sz_decimals", 0))
            tick_size = int(m.get("tick_size", 1))
            lot_size = int(m.get("lot_size", 1))
            min_size = int(m.get("min_size", 1))

            name = str(m.get("market_name", ""))
            mkt = DEXMarket(
                market_id=market_id,
                name=name,
                display_name=name,
                symbol=name.split("-")[0] if "-" in name else name.split("/")[0],
                last_price=last_price,
                step_size=from_chain_size(lot_size, sz_decimals),
                step_price=from_chain_size(tick_size, px_decimals),
                max_leverage=int(m.get("max_leverage", 20)),
                min_order_size=from_chain_size(min_size, sz_decimals),
                qty_step=str(from_chain_size(lot_size, sz_decimals)),
                price_step=str(from_chain_size(tick_size, px_decimals)),
            )
            self._markets[market_id] = mkt
            self._market_meta[market_id] = {
                "addr": addr,
                "px_decimals": px_decimals,
                "sz_decimals": sz_decimals,
                "tick_size": tick_size,
                "lot_size": lot_size,
                "min_size": min_size,
            }
            self._by_addr[addr] = mkt
            if last_price:
                self._prices[market_id] = last_price
            market_id += 1

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
        for _ in range(2):  # one transparent retry for flaky proxies
            try:
                p = await self._get(f"/market-prices/{m.name}")
                if isinstance(p, dict):
                    px = pick_num(p, "mid_px", "mark_px", "oracle_px", "last_px")
                    ts = pick_num(p, "transaction_unix_ms")
                    if px and px > 0:
                        # If indexer price is stale (>15s old), try fresh candle price
                        if ts and time.time() * 1000 - ts > 15_000:
                            fresh = await self._fresh_price_from_candles(m)
                            if fresh and fresh > 0:
                                self._prices[m_id] = fresh
                                return fresh
                        self._prices[m_id] = px
                        return px
            except Exception:
                pass

        last = self._prices.get(m_id, m.last_price)
        return last if last and last > 0 else None

    async def _fresh_price_from_candles(self, m: DEXMarket) -> float | None:
        """Close of the most recent 1m candle — used when the prices row is stale."""
        end = time.time() * 1000
        data = await self._get(
            f"/candlesticks/{m.name}?interval=1m"
            f"&startTime={end - 5 * 60 * 1000}&endTime={end}"
        )
        if not isinstance(data, list):
            return None
        for c in reversed(data):
            close = float(c.get("c", 0))
            if close and close == close:  # isfinite
                return close
        return None

    async def get_candles(
        self, market_id: int, interval_sec: int = 3600, n: int = 200
    ) -> list[dict[str, Any]]:
        """Return recent candles."""
        m = self._market(market_id)
        interval = INTERVALS.get(interval_sec, "1h")
        end = time.time() * 1000
        start = end - min(n, 1000) * interval_sec * 1000

        data = await self._get(
            f"/candlesticks/{m.name}?interval={interval}"
            f"&startTime={start}&endTime={end}"
        )
        if not isinstance(data, list):
            return []

        candles = []
        for c in data:
            candle = {
                "time": int(c.get("t", c.get("T", 0))),
                "open": float(c.get("o", 0)),
                "high": float(c.get("h", 0)),
                "low": float(c.get("l", 0)),
                "close": float(c.get("c", 0)),
                "volume": float(c.get("v", 0) or 0),
            }
            if candle["close"] and candle["close"] == candle["close"]:
                candles.append(candle)
        candles.sort(key=lambda x: x["time"])
        return candles

    # ---- PerpetualDEXMixin: Leverage ----

    async def set_leverage(self, market_id: int, leverage: int) -> bool:
        """Set leverage for a market. Return True on success."""
        meta = self._market_meta.get(market_id, {})
        addr = meta.get("addr", "")
        try:
            await self._req(
                "POST",
                "/user/settings/market",
                {
                    "market_addr": addr,
                    "subaccount_addr": self._subaccount,
                    "is_cross": True,
                    "user_leverage": leverage,
                },
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
        """Place a limit order. Return {order_id, ...}.

        NOTE: Full Aptos BCS transaction signing is not yet implemented.
        This method currently raises NotImplementedError for live trading.
        Use paper mode for grid engine development.
        """
        raise NotImplementedError(
            "Decibel order placement requires Aptos BCS transaction signing. "
            "Install aptos-sdk or implement BCS encoding to enable live trading. "
            "Paper mode is available via the paper engine."
        )

    async def cancel_order_by_id(self, market_id: int, order_id: str) -> bool:
        """Cancel a single order. Return True on success."""
        self._tracked.pop(order_id, None)
        m = self._market(market_id)
        try:
            await self._req(
                "DELETE",
                f"/orders/{order_id}",
                {"market_name": m.name, "subaccount_addr": self._subaccount},
            )
            return True
        except Exception as exc:
            self._emit_error(exc)
            return False

    async def cancel_all(self, market_id: int) -> bool:
        """Cancel all open orders for a market. Return True on success."""
        m = self._market(market_id)
        to_remove = [
            oid for oid, t in self._tracked.items() if t.market_id == market_id
        ]
        for oid in to_remove:
            del self._tracked[oid]

        try:
            open_orders = await self._fetch_open_orders_raw()
            for o in open_orders:
                o_market = str(o.get("market", ""))
                if o_market != m.name and o_market != self._market_meta.get(market_id, {}).get("addr", ""):
                    continue
                if o.get("is_tpsl"):
                    continue
                try:
                    await self._req(
                        "DELETE",
                        f"/orders/{o['order_id']}",
                        {"market_name": m.name, "subaccount_addr": self._subaccount},
                    )
                except Exception as exc:
                    self._emit_error(exc)
            return True
        except Exception as exc:
            self._emit_error(exc)
            return False

    async def close_position(self, market_id: int) -> bool:
        """Close the current position with a reduce-only IOC order at worst-case price."""
        pos = self._positions.get(market_id)
        if pos is None or pos.size_base == 0:
            return True

        # Same as place_limit_order — needs BCS signing for live mode
        raise NotImplementedError(
            "Decibel close_position requires Aptos BCS transaction signing. "
            "Use paper mode for grid engine development."
        )

    # ---- PerpetualDEXMixin: Reconciliation & resume ----

    async def fetch_open_orders(self, market_id: int) -> list[dict[str, Any]] | None:
        """Fetch real resting orders from the exchange for reconciliation."""
        m = self._market(market_id)
        meta = self._market_meta.get(market_id, {})
        addr = meta.get("addr", "")

        rows = await self._fetch_open_orders_raw()
        out: list[dict[str, Any]] = []
        for o in rows:
            o_market = str(o.get("market", ""))
            if o_market != addr and o_market != m.name:
                continue
            if o.get("is_tpsl"):
                continue

            px = pick_num(o, "price", "limit_px", "px", "order_price")
            if px is not None and meta.get("px_decimals"):
                ref = self._prices.get(market_id, m.last_price) or 0
                if ref > 0:
                    conv = from_chain_price(int(px), meta["px_decimals"])
                    if abs(conv - ref) < abs(px - ref):
                        px = conv

            side = (
                OrderSide.BUY
                if o.get("is_buy") is True
                or "buy" in str(o.get("side", "")).lower()
                or "long" in str(o.get("side", "")).lower()
                else OrderSide.SELL
            )
            out.append({"order_id": str(o.get("order_id", "")), "price": px, "side": side})
        return out

    async def _fetch_open_orders_raw(self) -> list[dict[str, Any]]:
        """Fetch all open orders for the subaccount."""
        r = await self._get(
            f"/user/open-orders?subAddr={self._subaccount}&limit=500&offset=0"
        )
        if isinstance(r, list):
            return r
        if isinstance(r, dict):
            return r.get("items", [])
        return []

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
        try:
            market_id = int(request.symbol)
        except ValueError:
            raise ValueError(
                f"Decibel adapter requires numeric market_id as symbol, got: {request.symbol}"
            )

        if request.order_type == OrderType.MARKET:
            m = self._market(market_id)
            last = self._prices.get(market_id, m.last_price) or 0
            is_buy = request.side == OrderSide.BUY
            worst = last * (1.05 if is_buy else 0.95)
            result = await self.place_limit_order(
                market_id,
                request.side,
                worst,
                request.quantity,
                post_only=False,
            )
        else:
            result = await self.place_limit_order(
                market_id,
                request.side,
                request.price or 0.0,
                request.quantity,
            )
            result = {"order_id": result.get("order_id", "")}

        return OrderResponse(
            exchange="decibel",
            order_id=result.get("order_id", ""),
            symbol=request.symbol,
            side=request.side,
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
            exchange="decibel",
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
                exchange="decibel",
                order_id=order_id,
                symbol=symbol,
                side=tracked.side,
                order_type=OrderType.LIMIT,
                status=OrderStatus.ACCEPTED,
                quantity=tracked.size_base,
                price=tracked.price,
            )
        return OrderResponse(
            exchange="decibel",
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
                    exchange="decibel",
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
            await self._get("/markets")
            return True
        except Exception:
            return False

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
        """Single poll cycle: open orders, prices, positions, account."""
        if self._busy:
            if time.time() * 1000 - self._busy_since < _BUSY_WATCHDOG_MS:
                return
            logger.warning("[Decibel] Previous poll stuck >90s, force-unlocking.")

        self._busy = True
        self._busy_since = time.time() * 1000
        try:
            # Prune idle watch entries
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
                    self._px_stale.discard(m_id)

            # Open orders -> fill detection (account-wide, one call)
            open_orders: list[dict[str, Any]] | None = None
            try:
                open_orders = await self._fetch_open_orders_raw()
            except Exception:
                pass

            if open_orders is not None:
                live_ids = {str(o.get("order_id", "")) for o in open_orders}
                for oid in live_ids:
                    t = self._tracked.get(oid)
                    if t:
                        t.seen = True
                        t.gone_attempts = 0

                now = time.time() * 1000
                for oid, t in list(self._tracked.items()):
                    if oid in live_ids or t.resolving:
                        continue
                    if not t.seen and now - t.placed_at < self._grace_ms:
                        continue
                    t.resolving = True
                    asyncio.ensure_future(
                        self._resolve_gone(oid, t)
                    ).add_done_callback(lambda _: setattr(t, "resolving", False))

            # Fetch feed prices for watched markets
            feed_px: dict[int, float] = {}
            for m_id in list(self._watch):
                try:
                    px = await self.get_price(m_id)
                    if px and px > 0:
                        feed_px[m_id] = px
                except Exception:
                    pass

            # Positions (account-wide)
            try:
                ps_data = await self._get(
                    f"/user/positions?subAddr={self._subaccount}"
                )
                ps = ps_data if isinstance(ps_data, list) else (ps_data.get("positions", []) if isinstance(ps_data, dict) else [])
                seen: set[int] = set()
                for p in ps:
                    mkt = self._by_addr.get(str(p.get("market", ""))) or next(
                        (m for m in self._markets.values() if m.name == p.get("market_name")),
                        None,
                    )
                    if mkt is None:
                        continue

                    size = float(p.get("size", p.get("open_size", 0)) or 0)
                    if p.get("is_long") is False and size > 0:
                        size = -size
                    if isinstance(p.get("side"), str) and "short" in p["side"].lower() and size > 0:
                        size = -size

                    if not size or p.get("is_deleted"):
                        self._positions.pop(mkt.market_id, None)
                        continue

                    entry = float(p.get("entry_price", 0) or 0)
                    mark = self._prices.get(mkt.market_id, entry)
                    ex_upnl = pick_num(p, "unrealized_pnl")

                    self._positions[mkt.market_id] = DEXPosition(
                        size_base=size,
                        entry_price=entry,
                        unrealized_pnl=float(p.get("unrealized_pnl", size * (mark - entry)) or 0),
                        leverage=float(p["user_leverage"]) if p.get("user_leverage") is not None else None,
                    )
                    seen.add(mkt.market_id)

                for m_id in self._watch:
                    if m_id not in seen:
                        self._positions.pop(m_id, None)

                # Single-position: prefer exchange-authoritative unrealized PnL
                if len(seen) == 1 and self._acct_upnl is not None and self._acct_upnl == self._acct_upnl:
                    only_id = next(iter(seen))
                    only_pos = self._positions.get(only_id)
                    if only_pos:
                        self._positions[only_id] = DEXPosition(
                            size_base=only_pos.size_base,
                            entry_price=only_pos.entry_price,
                            unrealized_pnl=self._acct_upnl,
                            leverage=only_pos.leverage,
                        )
            except Exception:
                pass

            # Emit prices with stale-price fallback via position-implied mark
            for m_id in self._watch:
                px = feed_px.get(m_id, self._prices.get(m_id))
                pos = self._positions.get(m_id)

                if (
                    pos
                    and pos.size_base != 0
                    and pos.entry_price > 0
                    and self._acct_upnl is not None
                    and self._acct_upnl == self._acct_upnl
                ):
                    implied_mark = pos.entry_price + self._acct_upnl / pos.size_base
                    if implied_mark > 0 and implied_mark == implied_mark:
                        dev = abs(implied_mark - px) / implied_mark if px and px > 0 else 1
                        was_stale = m_id in self._px_stale
                        now_stale = was_stale and dev > _STALE_PRICE_THRESHOLD_EXIT or not was_stale and dev > _STALE_PRICE_THRESHOLD_ENTER

                        if now_stale:
                            if not was_stale:
                                self._px_stale.add(m_id)
                                logger.warning(
                                    "[Decibel] Feed price (%s) diverges from position-implied (%.2f), "
                                    "suspecting indexer lag — switching to position-implied price for risk checks.",
                                    px or "N/A",
                                    implied_mark,
                                )
                            px = implied_mark
                            self._prices[m_id] = implied_mark
                        elif was_stale:
                            self._px_stale.discard(m_id)
                            logger.info("[Decibel] Feed price recovered (%s), switching back.", px)

                if px and px > 0:
                    update = DEXPriceUpdate(market_id=m_id, price=px)
                    for cb in self._price_callbacks:
                        try:
                            cb(update)
                        except Exception:
                            pass

            await self._refresh_account()
            self._last_ok_at = time.time() * 1000
        except Exception as exc:
            self._last_error = str(exc)
            self._emit_error(exc)
        finally:
            self._busy = False

    async def _resolve_gone(self, order_id: str, t: _TrackedOrder) -> None:
        """Resolve a tracked order that disappeared from the book.

        Positive-confirmation fill detection: a vanished order is NOT assumed
        filled. We only emit a fill when the order history shows it actually
        executed (filledQty > 0 / status FILLED). Anything inconclusive defaults
        to "not filled" — stop tracking, do NOT re-quote.
        """
        verdict = "unknown"
        try:
            h_data = await self._get(
                f"/user/order-history?subAddr={self._subaccount}&limit=100&offset=0"
            )
            rows = h_data if isinstance(h_data, list) else (h_data.get("items", []) if isinstance(h_data, dict) else [])
            for r in rows:
                if str(r.get("order_id", "")) != order_id:
                    continue
                st = str(r.get("status", "")).lower()
                fill_qty = float(r.get("orig_size", 0) or 0) - float(r.get("remaining_size", 0) or 0)
                if fill_qty > 0 or any(kw in st for kw in ("fill", "filled", "matched", "closed")):
                    verdict = "filled"
                elif any(kw in st for kw in ("cancel", "reject", "expire")):
                    verdict = "cancelled"
                elif any(kw in st for kw in ("open", "new", "resting", "pending", "acknowledged")):
                    # History says the order is STILL LIVE — indexer glitch
                    t.gone_attempts = 0
                    t.seen = True
                    return
                break
        except Exception:
            pass

        if verdict == "unknown":
            t.gone_attempts += 1
            if t.gone_attempts < _GONE_ATTEMPTS_LIMIT:
                return
            verdict = "cancelled"

        self._tracked.pop(order_id, None)

        if verdict == "filled":
            fill = DEXFill(
                order_id=order_id,
                market_id=t.market_id,
                side=t.side,
                price=t.price,
                size_base=t.size_base,
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
        o = await self._get(f"/account/overview?subAddr={self._subaccount}")
        if isinstance(o, dict):
            equity = pick_num(o, "perp_equity_balance", "equity", "account_value")
            upnl = pick_num(o, "unrealized_pnl")
            if equity is not None:
                self._equity = equity
                self._balance = equity - (upnl or 0)
            self._acct_upnl = upnl

    # ---- Internal: event emission ----

    def _emit_error(self, exc: Exception) -> None:
        """Emit an error to all registered callbacks."""
        for cb in self._error_callbacks:
            try:
                cb(exc)
            except Exception:
                pass
