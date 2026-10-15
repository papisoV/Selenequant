"""Deribit options data source — integrates with the ExchangeSource pattern.

Wraps ``DeribitWSClient``, ``DeribitRESTClient``, and ``ReconnectManager``
from ``selene_space_exchange`` and exposes a unified surface for options
market data (instruments, tickers with Greeks, OI summaries).
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable
from typing import Any

from selene_space_exchange import ReconnectManager
from selene_space_exchange.exchanges.deribit.rest_client import DeribitRESTClient
from selene_space_exchange.exchanges.deribit.ws_client import (
    DEFAULT_DERIBIT_WS_URL,
    DeribitWSClient,
)
from selene_space_market.event_bus import EventBus
from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.derivatives import (
    OptionsInstrument,
    OptionsSummary,
    OptionsTicker,
)

logger = logging.getLogger(__name__)


class DeribitSource:
    """Deribit options data source for a single base currency (BTC or ETH).

    Unlike the spot/futures sources (BinanceSource), this source focuses on
    options-specific data: instrument metadata, real-time tickers with Greeks, and
    per-expiry OI summaries. It does not implement the full ExchangeSource
    protocol (no L2 orderbook, no klines) because Deribit options data
    has a fundamentally different shape.
    """

    def __init__(
        self,
        *,
        currency: str = "BTC",
        ws_url: str | None = None,
        testnet: bool = False,
        proxy_url: str | None = None,
        ticker_frequency: str = "100ms",
    ) -> None:
        self._currency = currency.upper()
        # MarketType is Literal["spot", "futures"] — options are derivatives,
        # so "futures" is the closest match. The exchange="deribit" identifier
        # distinguishes this source from spot/futures adapters.
        self._config = ExchangeSourceConfig(exchange="deribit", market_type="futures")
        self._ws_url = ws_url or DEFAULT_DERIBIT_WS_URL
        self._testnet = testnet
        self._proxy_url = proxy_url
        self._ticker_frequency = ticker_frequency
        self._running = False

        self._event_bus = EventBus()

        # Caches
        self._instruments: dict[str, OptionsInstrument] = {}
        self._tickers: dict[str, OptionsTicker] = {}
        self._summaries: dict[int, OptionsSummary] = {}

        # Clients (initialized in start())
        self._ws_client: DeribitWSClient | None = None
        self._rest_client: DeribitRESTClient | None = None
        self._reconnect_manager: ReconnectManager | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None

        self._subscribed_instruments: set[str] = set()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> ExchangeSourceConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def currency(self) -> str:
        return self._currency

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Deribit data source: REST client, WS client, and refresh loop."""
        if self._running:
            return

        # Initialize REST client and fetch instrument catalog.
        self._rest_client = DeribitRESTClient(
            testnet=self._testnet,
        )
        await self._refresh_instruments()

        # Initialize WS client.
        self._ws_client = DeribitWSClient(
            url=self._ws_url,
            proxy_url=self._proxy_url,
            on_ticker=self._on_ticker,
            on_connect=self._on_ws_connect,
        )

        self._reconnect_manager = ReconnectManager(self._ws_client)
        self._reconnect_task = asyncio.create_task(
            self._reconnect_manager.run(), name="deribit-options-reconnect"
        )

        # Periodic refresh of instruments and summaries.
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="deribit-options-refresh"
        )

        self._running = True
        logger.info("DeribitSource started: %s options (ws=%s)", self._currency, self._ws_url)

    async def stop(self) -> None:
        """Stop the Deribit data source and clean up all resources."""
        if not self._running:
            return
        self._running = False

        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        self._refresh_task = None

        if self._reconnect_manager:
            await self._reconnect_manager.stop()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        if self._rest_client:
            await self._rest_client.close()
            self._rest_client = None

        self._instruments.clear()
        self._tickers.clear()
        self._summaries.clear()
        self._subscribed_instruments.clear()
        logger.info("DeribitSource stopped: %s options", self._currency)

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    async def subscribe_instruments(self, instrument_names: list[str]) -> None:
        """Subscribe to real-time ticker updates for specific options instruments.

        Args:
            instrument_names: Deribit instrument names, e.g.
                ``["BTC-28MAR25-80000-C", "BTC-28MAR25-80000-P"]``.
        """
        if not self._running or not self._ws_client:
            return
        await self._ws_client.subscribe_ticker(instrument_names, frequency=self._ticker_frequency)
        self._subscribed_instruments.update(instrument_names)
        logger.info(
            "Subscribed to %d Deribit option tickers (total=%d)",
            len(instrument_names),
            len(self._subscribed_instruments),
        )

    async def subscribe_all_active(self) -> None:
        """Subscribe to ticker updates for all active instruments."""
        active_names = [
            name for name, inst in self._instruments.items() if inst.is_active
        ]
        if active_names:
            await self.subscribe_instruments(active_names)

    async def unsubscribe(self, instrument_names: list[str]) -> None:
        """Unsubscribe from ticker updates for specific instruments."""
        if not self._ws_client:
            return
        channels = [f"ticker.{name}.{self._ticker_frequency}" for name in instrument_names]
        await self._ws_client.unsubscribe(channels)
        self._subscribed_instruments -= set(instrument_names)

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_instruments(self) -> list[OptionsInstrument]:
        """Return all cached option instruments."""
        return list(self._instruments.values())

    def get_instrument(self, instrument_name: str) -> OptionsInstrument | None:
        """Return a single cached instrument by name."""
        return self._instruments.get(instrument_name)

    def get_ticker(self, instrument_name: str) -> OptionsTicker | None:
        """Return the latest ticker for an instrument."""
        return self._tickers.get(instrument_name)

    def get_tickers(self) -> list[OptionsTicker]:
        """Return all cached tickers."""
        return list(self._tickers.values())

    def get_summary(self, expiration_timestamp: int) -> OptionsSummary | None:
        """Return the latest per-expiry summary."""
        return self._summaries.get(expiration_timestamp)

    def get_summaries(self) -> list[OptionsSummary]:
        """Return all cached per-expiry summaries."""
        return list(self._summaries.values())

    def get_subscribed_instruments(self) -> list[str]:
        """Return sorted list of subscribed instrument names."""
        return sorted(self._subscribed_instruments)

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------

    def on_ticker(self, callback: Callable[[OptionsTicker], Any]) -> None:
        """Register a callback for ticker updates."""
        self._event_bus.subscribe(OptionsTicker, callback)

    def on_summary(self, callback: Callable[[OptionsSummary], Any]) -> None:
        """Register a callback for per-expiry summary updates."""
        self._event_bus.subscribe(OptionsSummary, callback)

    # ------------------------------------------------------------------
    # Internal handlers
    # ------------------------------------------------------------------

    def _on_ticker(self, event: OptionsTicker) -> None:
        """Handle a ticker update from the WS client."""
        self._tickers[event.instrument_name] = event
        self._event_bus.publish(event)

    async def _on_ws_connect(self) -> None:
        """Handle WebSocket (re)connection by re-subscribing to instruments."""
        if not self._subscribed_instruments:
            return
        names = list(self._subscribed_instruments)
        logger.info("Deribit WS reconnect: re-subscribing to %d instruments", len(names))
        try:
            await self._ws_client.subscribe_ticker(names, frequency=self._ticker_frequency)
        except Exception:
            logger.warning("Failed to re-subscribe on Deribit reconnect", exc_info=True)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        """Periodically refresh instrument catalog and compute summaries."""
        try:
            while self._running:
                await asyncio.sleep(300)  # every 5 minutes
                if not self._running:
                    break
                try:
                    await self._refresh_instruments()
                    await self._refresh_book_summary()
                    self._compute_summaries()
                except Exception:
                    logger.warning("Deribit periodic refresh failed", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _refresh_instruments(self) -> None:
        """Fetch the latest instrument catalog from Deribit REST API."""
        if not self._rest_client:
            return
        try:
            instruments = await self._rest_client.get_instruments(currency=self._currency)
            new_map: dict[str, OptionsInstrument] = {}
            for inst in instruments:
                new_map[inst.instrument_name] = inst
            self._instruments = new_map
            logger.info("Refreshed %d %s option instruments", len(new_map), self._currency)
        except Exception:
            logger.warning("Failed to refresh Deribit instruments", exc_info=True)

    async def _refresh_book_summary(self) -> None:
        """Fetch book summary (OI, volume) and update ticker cache."""
        if not self._rest_client:
            return
        try:
            summaries = await self._rest_client.get_book_summary(currency=self._currency)
            now_ms = int(_time.time() * 1000)
            for item in summaries:
                name = item.get("instrument_name", "")
                if not name:
                    continue
                # Update OI and volume in existing ticker, or create a partial one.
                existing = self._tickers.get(name)
                oi = float(item.get("open_interest", 0.0))
                vol = float(item.get("volume_24h", 0.0))
                if existing is not None:
                    # Create a new ticker with updated OI/volume (immutable pattern).
                    updated = existing.model_copy(update={
                        "open_interest": oi,
                        "volume_24h": vol,
                    })
                    self._tickers[name] = updated
                # If no ticker exists yet, we don't create a partial one —
                # the WS ticker will populate the full data.
        except Exception:
            logger.warning("Failed to refresh Deribit book summary", exc_info=True)

    def _compute_summaries(self) -> None:
        """Compute per-expiry OptionsSummary from cached instruments and tickers."""
        now_ms = int(_time.time() * 1000)

        # Group instruments by expiration.
        by_expiry: dict[int, list[OptionsInstrument]] = {}
        for inst in self._instruments.values():
            if not inst.is_active:
                continue
            by_expiry.setdefault(inst.expiration_timestamp, []).append(inst)

        new_summaries: dict[int, OptionsSummary] = {}
        for exp_ts, instruments in by_expiry.items():
            days_to_expiry = max(0.0, (exp_ts - now_ms) / 86_400_000.0)

            total_call_oi = 0.0
            total_put_oi = 0.0
            total_call_volume = 0.0
            total_put_volume = 0.0
            call_ivs: list[float] = []
            put_ivs: list[float] = []

            for inst in instruments:
                ticker = self._tickers.get(inst.instrument_name)
                if ticker is None:
                    continue
                if inst.option_type == "call":
                    total_call_oi += ticker.open_interest
                    total_call_volume += ticker.volume_24h
                    if ticker.implied_volatility > 0:
                        call_ivs.append(ticker.implied_volatility)
                else:
                    total_put_oi += ticker.open_interest
                    total_put_volume += ticker.volume_24h
                    if ticker.implied_volatility > 0:
                        put_ivs.append(ticker.implied_volatility)

            put_call_ratio = total_call_oi / total_put_oi if total_put_oi > 0 else 1.0
            call_iv_atm = _compute_atm_iv(call_ivs) if call_ivs else 0.0
            put_iv_atm = _compute_atm_iv(put_ivs) if put_ivs else 0.0

            summary = OptionsSummary(
                base_currency=self._currency,
                expiration_timestamp=exp_ts,
                days_to_expiry=days_to_expiry,
                total_call_oi=total_call_oi,
                total_put_oi=total_put_oi,
                put_call_ratio=put_call_ratio,
                max_pain=0.0,  # Requires full strike/oi matrix — computed separately
                total_call_volume=total_call_volume,
                total_put_volume=total_put_volume,
                call_iv_atm=call_iv_atm,
                put_iv_atm=put_iv_atm,
                timestamp=now_ms,
            )
            new_summaries[exp_ts] = summary
            self._event_bus.publish(summary)

        self._summaries = new_summaries


def _compute_atm_iv(ivs: list[float]) -> float:
    """Compute a representative ATM IV from a list of IVs.

    Uses the median of the IVs closest to the center of the range,
    which approximates the ATM IV when strikes are distributed around
    the current underlying price.
    """
    if not ivs:
        return 0.0
    sorted_ivs = sorted(ivs)
    n = len(sorted_ivs)
    if n % 2 == 1:
        return sorted_ivs[n // 2]
    return (sorted_ivs[n // 2 - 1] + sorted_ivs[n // 2]) / 2.0
