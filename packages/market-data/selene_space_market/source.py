"""Adapter contract for feeding one exchange/segment into MarketDataService.

A source owns its own websocket lifecycle, L2 book state, and reconnect
logic; the service only sees this uniform interface plus the three event
callbacks it registers at wiring time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, Field

from selene_space_types.market import (
    KlineEvent,
    OrderBookL2Snapshot,
    TickerEvent,
)


class ExchangeSourceConfig(BaseModel):
    """Which exchange and segment a source adapter serves.

    ``market_type`` is a plain str — KlineEvent.market_type is str too — so
    non-crypto segments ("us_stock", "ashare") can register without touching
    shared-type literals; routing keys on the (exchange, market_type) tuple.
    """

    exchange: str = Field(description="Exchange id such as 'binance'")
    market_type: str = Field(
        default="spot", description="'spot', 'futures', 'us_stock', ..."
    )


class ExchangeSource(Protocol):
    """Structural type every market-data source must duck-type as."""

    @property
    def config(self) -> ExchangeSourceConfig: ...
    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def subscribe(
        self,
        symbol: str,
        *,
        ticker: bool = True,
        kline: bool = True,
        depth: bool = True,
        trade: bool = True,
        kline_interval: str | None = None,
    ) -> None: ...

    async def unsubscribe(
        self, symbol: str, kline_interval: str | None = None
    ) -> None: ...

    # Poll-style reads used by REST fallback paths.
    def get_ticker(self, symbol: str) -> TickerEvent | None: ...
    def get_kline(self, symbol: str) -> KlineEvent | None: ...
    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]: ...
    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None: ...
    def get_symbols(self) -> list[str]: ...

    # Push wiring: the service attaches its handlers through these.
    def on_ticker(self, callback: Callable[[TickerEvent], Any]) -> None: ...
    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None: ...
    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], Any]) -> None: ...
