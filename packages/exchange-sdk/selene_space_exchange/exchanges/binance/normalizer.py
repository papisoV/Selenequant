"""Translate raw Binance WS payloads into selene_space_types events."""

from __future__ import annotations

from dataclasses import dataclass
from logging import getLogger
from typing import Any, Callable

from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_types.derivatives import FundingRateEvent, LiquidationEvent
from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Delta,
    PriceLevel,
    TickerEvent,
    TradeEvent,
)

_log = getLogger(__name__)

EXCHANGE = "binance"


@dataclass(slots=True)
class BinanceDepthDelta:
    """Depth diff plus the U/pu update-id pair used for gap detection."""

    delta: OrderBookL2Delta
    first_update_id: int
    last_update_id: int
    previous_update_id: int | None = None


def _levels(rows: Any) -> list[PriceLevel]:
    out: list[PriceLevel] = []
    if isinstance(rows, list):
        for row in rows:
            try:
                out.append(PriceLevel(price=float(row[0]), quantity=float(row[1])))
            except (TypeError, ValueError, IndexError):
                continue
    return out


class BinanceNormalizer:
    """Route by the payload's ``e`` discriminator; unknown kinds yield None."""

    def __init__(self, symbol_normalizer: SymbolNormalizer) -> None:
        self._sym = symbol_normalizer
        self._routes: dict[str, Callable[[dict[str, Any]], Any]] = {
            "24hrTicker": self._ticker_from,
            "trade": self._trade_from,
            "kline": self._kline_from,
            "aggTrade": self._agg_trade_from,
            "forceOrder": self._liquidation_from,
            "markPriceUpdate": self._funding_from,
            "depthUpdate": self._depth_from,
        }

    def normalize(
        self, data: dict[str, Any]
    ) -> TickerEvent | TradeEvent | KlineEvent | BinanceDepthDelta | LiquidationEvent | FundingRateEvent | None:
        kind = data.get("e") if isinstance(data, dict) else None
        handler = self._routes.get(kind)
        if handler is None:
            _log.debug("dropping unrecognised binance event %r", kind)
            return None
        return handler(data)

    def _ticker_from(self, d: dict[str, Any]) -> TickerEvent:
        return TickerEvent(
            exchange=EXCHANGE,
            symbol=self._sym.to_unified(EXCHANGE, d["s"]),
            timestamp=int(d["E"]),
            last_price=float(d["c"]),
            bid=float(d["b"]),
            ask=float(d["a"]),
            volume_24h=float(d["v"]),
            change_24h_pct=float(d["P"]),
        )

    def _side_from_flag(self, buyer_is_maker: bool) -> str:
        # m=True means the buyer was the maker, so the aggressor sold.
        return "sell" if buyer_is_maker else "buy"

    def _trade_from(self, d: dict[str, Any]) -> TradeEvent:
        return TradeEvent(
            exchange=EXCHANGE,
            symbol=self._sym.to_unified(EXCHANGE, d["s"]),
            timestamp=int(d["E"]),
            trade_id=str(d["t"]),
            price=float(d["p"]),
            quantity=float(d["q"]),
            side=self._side_from_flag(bool(d["m"])),
        )

    def _agg_trade_from(self, d: dict[str, Any]) -> TradeEvent:
        return TradeEvent(
            exchange=EXCHANGE,
            symbol=self._sym.to_unified(EXCHANGE, d["s"]),
            timestamp=int(d["T"]),
            trade_id=str(d["a"]),
            price=float(d["p"]),
            quantity=float(d["q"]),
            side=self._side_from_flag(bool(d["m"])),
        )

    def _kline_from(self, d: dict[str, Any]) -> KlineEvent:
        k = d["k"]
        # Candle open time (k.t), never event time (E): buffer-merge logic
        # relies on timestamp equality to update bars in place.
        return KlineEvent(
            exchange=EXCHANGE,
            market_type=d.get("market_type", "spot"),
            symbol=self._sym.to_unified(EXCHANGE, k.get("s") or d["s"]),
            timestamp=int(k["t"]),
            interval=k["i"],
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            closed=bool(k["x"]),
        )

    def _liquidation_from(self, d: dict[str, Any]) -> LiquidationEvent:
        o = d["o"]
        qty = float(o.get("l") or o.get("z") or 0.0)
        avg = float(o.get("ap") or 0.0)
        # A SELL force order closes out longs; BUY closes out shorts.
        side = "long" if str(o["S"]).upper() == "SELL" else "short"
        return LiquidationEvent(
            exchange=EXCHANGE,
            symbol=self._sym.to_unified(EXCHANGE, o["s"]),
            side=side,
            quantity=qty,
            price=float(o["p"]),
            avg_price=avg,
            notional_usd=qty * avg,
            status=str(o.get("X", "FILLED")),
            timestamp=int(o.get("T") or d["E"]),
        )

    def _funding_from(self, d: dict[str, Any]) -> FundingRateEvent:
        return FundingRateEvent(
            exchange=EXCHANGE,
            symbol=self._sym.to_unified(EXCHANGE, d["s"]),
            funding_rate=float(d["r"]),
            mark_price=float(d["p"]),
            index_price=float(d.get("i") or 0.0),
            next_funding_time=int(d.get("T") or 0),
            timestamp=int(d["E"]),
        )

    def _depth_from(self, d: dict[str, Any]) -> BinanceDepthDelta:
        last = int(d["u"])
        delta = OrderBookL2Delta(
            exchange=EXCHANGE,
            symbol=self._sym.to_unified(EXCHANGE, d["s"]),
            timestamp=int(d["E"]),
            sequence_id=last,
            bids=_levels(d.get("b")),
            asks=_levels(d.get("a")),
        )
        pu = d.get("pu")
        return BinanceDepthDelta(
            delta=delta,
            first_update_id=int(d["U"]),
            last_update_id=last,
            previous_update_id=None if pu is None else int(pu),
        )
