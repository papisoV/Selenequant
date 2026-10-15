"""OKX v5 payload translation into unified market events.

OKX frames arrive as ``{"arg": {"channel": ..., "instId": ...}, "data": [...]}``.
These helpers work on individual data items; the WS client peels the frame.
Candles are positional arrays ``[ts, o, h, l, c, vol, volCcy, volCcyQuote,
confirm]`` whose channel name (``candle1H`` etc.) carries the interval.
"""

from __future__ import annotations

import re
from time import time
from typing import Any

from selene_space_types.derivatives import FundingRateEvent, LiquidationEvent
from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Snapshot,
    PriceLevel,
    TickerEvent,
    TradeEvent,
)

EXCHANGE = "okx"

_PRODUCT_SUFFIXES = ("-SWAP", "-FUTURES")

_CANDLE_CHANNEL = re.compile(r"^candle(\S+)$")


def _okx_symbol_to_unified(inst_id: str) -> str:
    """``BTC-USDT`` and ``BTC-USDT-SWAP`` both map to ``BTC/USDT``."""
    token = inst_id.upper()
    for suffix in _PRODUCT_SUFFIXES:
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    head, sep, tail = token.partition("-")
    return f"{head}/{tail.split('-', 1)[0]}" if sep and tail else token


def _interval_from_channel(channel: str) -> str:
    """Derive a unified interval tag from an OKX candle channel name.

    OKX spells bars like ``candle1m`` / ``candle1H`` / ``candle1D`` / ``candle1M``
    where only minutes are lowercase; unified form lowercases everything but
    monthly ``M``.
    """
    hit = _CANDLE_CHANNEL.match(channel)
    bar = hit.group(1) if hit else ""
    if not bar:
        return "1h"
    if bar.endswith("M"):
        stem = bar[:-1].lower()
        if stem.isdigit():  # 1M/3M... months, not minutes
            return f"{stem}M"
    return bar.lower()


def _f(value: Any, fallback: float = 0.0) -> float:
    """Tolerant float(): None / '' / garbage all collapse to *fallback*."""
    if value in (None, ""):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _ms(value: Any) -> int:
    return int(_f(value))


class OKXNormalizer:
    """One method per OKX channel; each returns zero or more typed events."""

    def _frame(self, inst_id: str, **extra: Any) -> dict[str, Any]:
        """Common envelope every event model shares."""
        body: dict[str, Any] = {
            "exchange": EXCHANGE,
            "symbol": _okx_symbol_to_unified(inst_id),
        }
        body.update(extra)
        return body

    # -- ticker ------------------------------------------------------------

    def normalize_ticker(self, data: dict[str, Any], inst_id: str) -> TickerEvent:
        last = _f(data.get("last"))
        open24 = _f(data.get("open24h"))
        drifted = ((last - open24) / open24 * 100.0) if open24 > 0 else 0.0
        return TickerEvent(
            **self._frame(inst_id),
            timestamp=_ms(data["ts"]),
            last_price=last,
            bid=_f(data.get("bidPx")),
            ask=_f(data.get("askPx")),
            volume_24h=_f(data.get("vol24h")),
            change_24h_pct=round(drifted, 4),
        )

    # -- candles -----------------------------------------------------------

    def normalize_candle(self, candle: list[str], inst_id: str, channel: str) -> KlineEvent:
        ts, o, high, low, close, vol = candle[:6]
        confirmed = len(candle) > 8 and candle[8] == "1"
        return KlineEvent(
            **self._frame(inst_id),
            timestamp=int(ts),
            interval=_interval_from_channel(channel),
            open=float(o),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(vol),
            closed=confirmed,
        )

    # -- trades ------------------------------------------------------------

    def normalize_trade(self, data: dict[str, Any], inst_id: str) -> TradeEvent:
        return TradeEvent(
            **self._frame(inst_id, timestamp=_ms(data["ts"])),
            trade_id=str(data["tradeId"]),
            price=float(data["px"]),
            quantity=float(data["sz"]),
            side=data["side"],
        )

    # -- liquidations --------------------------------------------------------

    def normalize_liquidation(self, data: dict[str, Any], inst_id: str) -> list[LiquidationEvent]:
        """Fan one push item out per entry of its ``details`` array."""
        envelope = self._frame(inst_id)
        out: list[LiquidationEvent] = []
        for row in data.get("details", []):
            qty = _f(row.get("sz"))
            bk_px = _f(row.get("bkPx"))
            # A SELL-side forced order closes longs, hence the inversion.
            closed_side = "long" if row.get("side", "sell") == "sell" else "short"
            out.append(
                LiquidationEvent(
                    **envelope,
                    side=closed_side,  # type: ignore[arg-type]
                    quantity=qty,
                    price=bk_px,
                    avg_price=bk_px,
                    notional_usd=qty * bk_px,
                    status="FILLED",
                    timestamp=_ms(row.get("ts", 0)),
                )
            )
        return out

    # -- funding -------------------------------------------------------------

    def normalize_funding_rate(self, data: dict[str, Any], inst_id: str) -> FundingRateEvent:
        stamp = data.get("fundingTime") or data.get("ts")
        return FundingRateEvent(
            **self._frame(inst_id),
            funding_rate=_f(data.get("fundingRate")),
            mark_price=_f(data.get("markPx")),
            index_price=0.0,
            next_funding_time=_ms(data.get("nextFundingTime")),
            timestamp=_ms(stamp),
        )

    # -- order book ----------------------------------------------------------

    @staticmethod
    def _rungs(rows: Any) -> list[PriceLevel]:
        """books5 rows look like ``[px, sz, liqOrders, numOrders]``."""
        return [
            PriceLevel(price=float(row[0]), quantity=float(row[1]))
            for row in rows or []
            if len(row) >= 2
        ]

    def normalize_orderbook(self, data: dict[str, Any], inst_id: str) -> OrderBookL2Snapshot:
        raw_ts = data.get("ts")
        stamp = int(raw_ts) if raw_ts else int(time() * 1000)
        return OrderBookL2Snapshot(
            **self._frame(inst_id),
            timestamp=stamp,
            sequence_id=stamp,
            bids=self._rungs(data.get("bids")),
            asks=self._rungs(data.get("asks")),
        )
