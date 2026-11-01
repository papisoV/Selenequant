"""Shared websocket subscription bookkeeping.

Several UI panels can want the same ticker/candle/depth feed at once. This
layer hands each of them a logical handle while opening exactly one physical
exchange subscription per (symbol, stream) pair, and tears the physical one
down when the last handle is released. After a socket drop, every still-held
feed can be replayed in one call.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum

from selene_space_exchange import BinanceL2Manager, BinanceWSClient

logger = logging.getLogger(__name__)


class StreamType(str, Enum):
    """Kinds of market data feed a consumer can hold."""

    TICKER = "ticker"
    KLINE = "kline"
    DEPTH = "depth"


def _to_wire(symbol: str) -> str:
    """``"BTC/USDT"`` → binance's ``"btcusdt"`` wire format."""
    return symbol.replace("/", "").lower()


class StreamManager:
    """Ref-counted fan-in between consumers and one exchange WS client.

    Args:
        ws_client: Exchange websocket client used for subscribe/unsubscribe.
        l2_manager: Depth-book maintainer fed by the depth stream.
        kline_interval: Primary candle interval to keep streaming.
        kline_intervals: Extra intervals; accepted as list or single string.
    """

    def __init__(
        self,
        ws_client: BinanceWSClient,
        l2_manager: BinanceL2Manager,
        kline_interval: str | None = None,
        kline_intervals: list[str] | str = "1h",
    ) -> None:
        self._ws_client = ws_client
        self._l2_manager = l2_manager
        chosen: list[str] | str = kline_interval if kline_interval is not None else kline_intervals
        self._intervals: list[str] = [chosen] if isinstance(chosen, str) else list(chosen)
        # symbol -> stream kind -> live consumer count
        self._counts: dict[str, dict[StreamType, int]] = {}
        self._guard = threading.Lock()

    async def start_stream(self, symbol: str, stream_type: StreamType) -> None:
        """Acquire a handle on *stream_type* for *symbol*.

        The first holder triggers the real exchange subscription.
        """
        open_needed = False
        with self._guard:
            per_symbol = self._counts.setdefault(symbol, {})
            current = per_symbol.get(stream_type, 0)
            if current == 0:
                open_needed = True
            per_symbol[stream_type] = current + 1

        if open_needed:
            await self._open(symbol, stream_type)
            logger.info("Opened %s feed for %s", stream_type.value, symbol)

    async def stop_stream(self, symbol: str, stream_type: StreamType) -> None:
        """Release one handle; the wire subscription closes on the last one."""
        close_needed = False
        with self._guard:
            per_symbol = self._counts.get(symbol)
            if not per_symbol or per_symbol.get(stream_type, 0) <= 0:
                return
            per_symbol[stream_type] -= 1
            if per_symbol[stream_type] == 0:
                del per_symbol[stream_type]
                if not per_symbol:
                    del self._counts[symbol]
                close_needed = True

        if close_needed:
            await self._close(symbol, stream_type)
            logger.info("Closed %s feed for %s", stream_type.value, symbol)

    def active_symbols(self) -> list[str]:
        """Symbols with at least one held feed, alphabetically."""
        with self._guard:
            return sorted(self._counts.keys())

    def active_streams(self) -> list[tuple[str, StreamType]]:
        """Every currently held (symbol, stream kind) combination."""
        with self._guard:
            return [
                (symbol, kind)
                for symbol, per_symbol in self._counts.items()
                for kind in per_symbol
                if per_symbol[kind] > 0
            ]

    def ref_count(self, symbol: str, stream_type: StreamType) -> int:
        """How many consumers hold this feed right now."""
        with self._guard:
            return self._counts.get(symbol, {}).get(stream_type, 0)

    async def resubscribe_all(self) -> None:
        """Replay every held feed after a reconnect."""
        with self._guard:
            held = [
                (symbol, kind)
                for symbol, per_symbol in self._counts.items()
                for kind in per_symbol
                if per_symbol[kind] > 0
            ]

        for symbol, kind in held:
            try:
                await self._open(symbol, kind)
            except Exception:
                logger.exception("Replay failed for %s/%s", kind.value, symbol)

    # -- wire calls --------------------------------------------------------

    async def _open(self, symbol: str, stream_type: StreamType) -> None:
        wire = [_to_wire(symbol)]
        if stream_type is StreamType.TICKER:
            await self._ws_client.subscribe_ticker(wire)
        elif stream_type is StreamType.KLINE:
            for interval in self._intervals:
                await self._ws_client.subscribe_kline(wire, interval)
        elif stream_type is StreamType.DEPTH:
            await self._ws_client.subscribe_depth(wire)

    async def _close(self, symbol: str, stream_type: StreamType) -> None:
        wire = _to_wire(symbol)
        if stream_type is StreamType.TICKER:
            await self._ws_client.unsubscribe([f"{wire}@ticker"])
        elif stream_type is StreamType.KLINE:
            await self._ws_client.unsubscribe(
                [f"{wire}@kline_{interval}" for interval in self._intervals]
            )
        elif stream_type is StreamType.DEPTH:
            await self._ws_client.unsubscribe([f"{wire}@depth@100ms"])
