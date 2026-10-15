"""Funding-rate data: live snapshot across venues + per-symbol history.

Two access patterns live here:

* ``get_all`` — one cached snapshot of current funding for every USDT
  perpetual on Binance and OKX, optionally enriched with Coinglass
  positioning stats when an API key is configured.
* ``get_history`` — paginated time series for a single symbol, shaped for
  backtests that need realistic funding costs.

All HTTP goes through the shared ``OutboundGateway``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com"
OKX_BASE = "https://www.okx.com"
COINGLASS_BASE = "https://open-api.coinglass.com/public/v2"

COINGLASS_API_KEY: str = os.environ.get("COINGLASS_API_KEY", "")

CACHE_TTL_MS = 30_000
OKX_CONCURRENCY = 10

_PAGE_LIMIT = 1000
_OKX_PAGE = "100"

_COINGLASS_SYMBOLS = ("BTC", "ETH", "BNB", "SOL", "XRP")


@dataclass
class FundingRateItem:
    """One funding observation for a single perpetual symbol."""

    exchange: str
    symbol: str
    funding_rate: float
    mark_price: float
    index_price: float
    next_funding_time: int
    timestamp: int
    open_interest: float | None = None
    liquidation_volume_24h: float | None = None
    long_short_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "funding_rate": self.funding_rate,
            "mark_price": self.mark_price,
            "index_price": self.index_price,
            "next_funding_time": self.next_funding_time,
            "timestamp": self.timestamp,
        }
        for optional in ("open_interest", "liquidation_volume_24h", "long_short_ratio"):
            value = getattr(self, optional)
            if value is not None:
                payload[optional] = value
        return payload

    def to_funding_pair(self) -> tuple[int, float]:
        """The ``(ts_ms, rate)`` tuple the funding-cost backtest consumes."""
        return self.timestamp, self.funding_rate


def to_funding_pairs(items: list[FundingRateItem]) -> list[tuple[int, float]]:
    """Ascending ``(ts, rate)`` pairs; first rate wins on duplicate stamps."""
    best: dict[int, float] = {}
    for item in items:
        ts, rate = item.to_funding_pair()
        if ts not in best:
            best[ts] = rate
    return sorted(best.items())


class FundingRateFetcher:
    """Caching aggregator over Binance / OKX / Coinglass funding endpoints."""

    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._gateway: OutboundGateway | None = None
        self._snapshot: list[FundingRateItem] = []
        self._snapshot_at = 0
        self._refresh_lock = asyncio.Lock()
        # Last-good degradation (pattern from internal-reference last-good.cjs):
        # when every venue fails, serve the previous successful snapshot with a
        # stale marker instead of an empty "fresh" response.
        self._last_good: list[FundingRateItem] = []
        self._last_good_at = 0
        self._degraded = False

    async def _get_client(self) -> OutboundGateway:
        if self._gateway is None or self._gateway.is_closed:
            self._gateway = OutboundGateway(
                proxy=self._proxy_url,
                default_timeout=httpx.Timeout(15.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                default_circuit=True,
            )
        return self._gateway

    # -- live snapshot -----------------------------------------------------

    async def get_all(self, *, force: bool = False) -> list[FundingRateItem]:
        """Current rates for every tracked venue (cached for CACHE_TTL_MS)."""
        if self._snapshot_fresh() and not force:
            return self._snapshot

        async with self._refresh_lock:
            if self._snapshot_fresh() and not force:
                return self._snapshot

            binance_items, okx_items, coinglass_payload = await asyncio.gather(
                self._live_binance(),
                self._live_okx(),
                self._coinglass() if COINGLASS_API_KEY else asyncio.sleep(0),
                return_exceptions=True,
            )

            merged: list[FundingRateItem] = []
            if isinstance(binance_items, list):
                merged.extend(binance_items)
            else:
                logger.warning("binance funding fetch failed: %s", binance_items)
            if isinstance(okx_items, list):
                merged.extend(okx_items)
            else:
                logger.warning("okx funding fetch failed: %s", okx_items)

            if isinstance(coinglass_payload, dict):
                self._apply_coinglass(merged, coinglass_payload)
            elif COINGLASS_API_KEY:
                logger.warning("coinglass fetch failed: %s", coinglass_payload)

            if not merged:
                # All venues failed: keep the previous snapshot visible instead
                # of publishing an empty "fresh" one that erases it.
                self._degraded = True
                if self._last_good:
                    logger.warning(
                        "all funding venues failed — serving last-good snapshot "
                        "from %s (%d items)",
                        self._last_good_at,
                        len(self._last_good),
                    )
                    return list(self._last_good)
            else:
                self._last_good = list(merged)
                self._last_good_at = int(time.time() * 1000)
                self._degraded = False

            if merged:
                # An empty merge is a failure, not a success — never stamp it
                # as a fresh snapshot (it would erase both the data and the
                # timestamp that freshness() reports).
                self._snapshot = merged
                self._snapshot_at = int(time.time() * 1000)
            return merged

    def freshness(self) -> dict[str, Any]:
        """Snapshot freshness for API consumers: as_of_ms / stale / age_seconds."""
        as_of = self._last_good_at or self._snapshot_at
        if not as_of:
            return {"as_of_ms": 0, "stale": True, "age_seconds": None}
        age = max(0, int(time.time() * 1000) - as_of) // 1000
        return {"as_of_ms": as_of, "stale": self._degraded, "age_seconds": age}

    def _snapshot_fresh(self) -> bool:
        return (
            bool(self._snapshot)
            and (int(time.time() * 1000) - self._snapshot_at) < CACHE_TTL_MS
        )

    # -- history -------------------------------------------------------------

    async def get_history(
        self,
        *,
        exchange: str,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[FundingRateItem]:
        """Paginate one symbol's funding series across [start_ms, end_ms]."""
        if exchange == "binance":
            return await self._history_binance(symbol, start_ms, end_ms)
        if exchange == "okx":
            return await self._history_okx(symbol, start_ms, end_ms)
        raise ValueError(f"Unsupported exchange for funding history: {exchange!r}")

    async def _history_binance(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[FundingRateItem]:
        client = await self._get_client()
        wire_symbol = symbol.replace("/", "")
        collected: list[FundingRateItem] = []
        cursor = start_ms

        while cursor < end_ms:
            resp = await client.get(
                f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                params={
                    "symbol": wire_symbol,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": _PAGE_LIMIT,
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                break
            collected.extend(self._binance_history_row(symbol, row) for row in rows)

            last_ts = int(rows[-1].get("fundingTime", 0))
            if last_ts <= cursor:
                break
            cursor = last_ts + 1

        logger.info(
            "binance history %s: %d rows [%d,%d]",
            symbol,
            len(collected),
            start_ms,
            end_ms,
        )
        return collected

    @staticmethod
    def _binance_history_row(symbol: str, row: dict[str, Any]) -> FundingRateItem:
        stamp = int(row.get("fundingTime", 0))
        return FundingRateItem(
            exchange="binance",
            symbol=symbol,
            funding_rate=float(row.get("fundingRate", "0")),
            mark_price=0.0,
            index_price=0.0,
            next_funding_time=stamp,
            timestamp=stamp,
        )

    async def _history_okx(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> list[FundingRateItem]:
        client = await self._get_client()
        inst_id = symbol.replace("/", "-") + "-SWAP"
        walked_desc: list[FundingRateItem] = []
        after = end_ms

        while True:
            resp = await client.get(
                f"{OKX_BASE}/api/v5/public/funding-rate-history",
                params={"instId": inst_id, "after": str(after), "limit": _OKX_PAGE},
            )
            if resp.status_code != 200:
                logger.warning("okx history HTTP %d for %s", resp.status_code, inst_id)
                break
            rows = resp.json().get("data", [])
            if not rows:
                break

            stop_early = False
            for row in rows:
                stamp = int(row.get("fundingTime", "0"))
                if stamp < start_ms:
                    stop_early = True
                    break
                walked_desc.append(
                    FundingRateItem(
                        exchange="okx",
                        symbol=symbol,
                        funding_rate=float(row.get("fundingRate", "0")),
                        mark_price=0.0,
                        index_price=0.0,
                        next_funding_time=stamp,
                        timestamp=stamp,
                    )
                )
            if stop_early:
                break

            oldest = int(rows[-1].get("fundingTime", "0"))
            if len(rows) < int(_OKX_PAGE) or oldest <= start_ms:
                break
            after = oldest - 1

        walked_desc.reverse()
        logger.info(
            "okx history %s: %d rows [%d,%d]",
            symbol,
            len(walked_desc),
            start_ms,
            end_ms,
        )
        return walked_desc

    # -- live per-venue fetchers -------------------------------------------------

    async def _live_binance(self) -> list[FundingRateItem]:
        client = await self._get_client()
        resp = await client.get(f"{BINANCE_FAPI}/fapi/v1/premiumIndex")
        resp.raise_for_status()

        now_ms = int(time.time() * 1000)
        items: list[FundingRateItem] = []
        for row in resp.json():
            wire = row.get("symbol", "")
            if not wire.endswith("USDT"):
                continue
            unified = f"{wire[: -len('USDT')]}/USDT"
            try:
                items.append(
                    FundingRateItem(
                        exchange="binance",
                        symbol=unified,
                        funding_rate=float(row.get("lastFundingRate", "0")),
                        mark_price=float(row.get("markPrice", "0")),
                        index_price=float(row.get("indexPrice", "0")),
                        next_funding_time=int(row.get("nextFundingTime", 0)),
                        timestamp=now_ms,
                    )
                )
            except (TypeError, ValueError):
                continue
        logger.info("binance live: %d symbols", len(items))
        return items

    async def _live_okx(self) -> list[FundingRateItem]:
        client = await self._get_client()

        inst_resp = await client.get(
            f"{OKX_BASE}/api/v5/public/instruments", params={"instType": "SWAP"}
        )
        inst_resp.raise_for_status()
        usdt_insts = [
            i["instId"]
            for i in inst_resp.json().get("data", [])
            if i.get("instId", "").endswith("-USDT-SWAP")
        ]
        if not usdt_insts:
            return []

        mark_by_inst: dict[str, float] = {}
        try:
            mp_resp = await client.get(
                f"{OKX_BASE}/api/v5/public/mark-price", params={"instType": "SWAP"}
            )
            if mp_resp.status_code == 200:
                mark_by_inst = {
                    m.get("instId", ""): float(m.get("markPx", "0"))
                    for m in mp_resp.json().get("data", [])
                }
        except Exception:
            pass

        gate = asyncio.Semaphore(OKX_CONCURRENCY)
        now_ms = int(time.time() * 1000)

        async def one(inst_id: str) -> FundingRateItem | None:
            async with gate:
                try:
                    r = await client.get(
                        f"{OKX_BASE}/api/v5/public/funding-rate",
                        params={"instId": inst_id},
                    )
                    if r.status_code != 200:
                        return None
                    rows = r.json().get("data", [])
                    if not rows:
                        return None
                    head = inst_id.split("-")
                    unified = f"{head[0]}/{head[1]}" if len(head) >= 2 else inst_id
                    return FundingRateItem(
                        exchange="okx",
                        symbol=unified,
                        funding_rate=float(rows[0].get("fundingRate", "0")),
                        mark_price=0.0,
                        index_price=0.0,
                        next_funding_time=int(rows[0].get("nextFundingTime", "0")),
                        timestamp=now_ms,
                    )
                except Exception:
                    return None

        results = await asyncio.gather(
            *(one(i) for i in usdt_insts), return_exceptions=True
        )
        items: list[FundingRateItem] = []
        for inst_id, res in zip(usdt_insts, results, strict=False):
            if isinstance(res, FundingRateItem):
                res.mark_price = mark_by_inst.get(inst_id, 0.0)
                items.append(res)
        logger.info("okx live: %d symbols", len(items))
        return items

    # -- coinglass enrichment ----------------------------------------------

    async def _coinglass(self) -> dict[str, Any]:
        client = await self._get_client()
        headers = {"coinglassSecret": COINGLASS_API_KEY}
        payload: dict[str, Any] = {
            "open_interest": {},
            "liquidation_volume_24h": {},
            "long_short_ratio": {},
        }

        for base in _COINGLASS_SYMBOLS:
            try:
                r = await client.get(
                    f"{COINGLASS_BASE}/openInterest",
                    params={"symbol": base},
                    headers=headers,
                )
                if r.status_code == 200:
                    body = r.json()
                    for entry in body.get("data") or []:
                        payload["open_interest"][base.upper()] = entry.get(
                            "openInterest"
                        )
                        break
            except Exception as exc:
                logger.warning("coinglass OI %s failed: %s", base, exc)

        for base in _COINGLASS_SYMBOLS:
            try:
                r = await client.get(
                    f"{COINGLASS_BASE}/longShort",
                    params={"symbol": base, "exchangeName": "Binance", "period": "h4"},
                    headers=headers,
                )
                if r.status_code == 200:
                    rows = r.json().get("data") or []
                    if rows:
                        payload["long_short_ratio"][base.upper()] = rows[-1].get(
                            "longShortRatio"
                        )
            except Exception as exc:
                logger.warning("coinglass L/S %s failed: %s", base, exc)

        return payload

    def _apply_coinglass(
        self, items: list[FundingRateItem], payload: dict[str, Any]
    ) -> None:
        oi = payload.get("open_interest", {})
        liq = payload.get("liquidation_volume_24h", {})
        ls = payload.get("long_short_ratio", {})
        for item in items:
            base = item.symbol.split("/")[0].upper()
            if base in oi:
                item.open_interest = oi[base]
            if base in liq:
                item.liquidation_volume_24h = liq[base]
            if base in ls:
                item.long_short_ratio = ls[base]

    async def close(self) -> None:
        if self._gateway and not self._gateway.is_closed:
            await self._gateway.aclose()
            self._gateway = None
