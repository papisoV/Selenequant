"""Deribit REST client for public options market data.

Provides async methods for fetching instrument lists, book summaries,
and individual ticker data. No API key required for public endpoints.

Docs: https://docs.deribit.com/#public-api
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_types.derivatives import OptionsInstrument, OptionsTicker

logger = logging.getLogger(__name__)

DERIBIT_API_URL = "https://www.deribit.com/api/v2"
DERIBIT_TEST_API_URL = "https://test.deribit.com/api/v2"

# Deribit public rate limit: 20 requests per second.
_DERIBIT_PUBLIC_RATE_LIMIT = 20
_DERIBIT_RATE_WINDOW_MS = 1_000


class DeribitRESTClient:
    """Async REST client for Deribit public API endpoints.

    Usage::

        client = DeribitRESTClient()
        instruments = await client.get_instruments(currency="BTC")
        ticker = await client.get_ticker(instrument_name="BTC-28MAR25-80000-C")
        await client.close()
    """

    def __init__(
        self,
        *,
        testnet: bool = False,
        base_url: str | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url or (DERIBIT_TEST_API_URL if testnet else DERIBIT_API_URL)
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(
            calls_per_window=_DERIBIT_PUBLIC_RATE_LIMIT,
            window_ms=_DERIBIT_RATE_WINDOW_MS,
        )
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_instruments(self, currency: str = "BTC") -> list[OptionsInstrument]:
        """Fetch all option instruments for a given currency.

        Args:
            currency: Base currency, ``"BTC"`` or ``"ETH"``.

        Returns:
            List of :class:`OptionsInstrument` models.
        """
        data = await self._public_request("public/get_instruments", {
            "currency": currency.upper(),
            "kind": "option",
        })

        result = data.get("result", [])
        if not isinstance(result, list):
            logger.warning("Unexpected get_instruments response format")
            return []

        instruments: list[OptionsInstrument] = []
        for item in result:
            parsed = self._parse_instrument(item)
            if parsed is not None:
                instruments.append(parsed)

        logger.info("Fetched %d %s option instruments from Deribit", len(instruments), currency.upper())
        return instruments

    async def get_book_summary(self, currency: str = "BTC") -> list[dict[str, Any]]:
        """Fetch book summary (OI, volume) for all option instruments.

        Args:
            currency: Base currency, ``"BTC"`` or ``"ETH"``.

        Returns:
            List of raw book summary dicts from Deribit, each containing
            ``instrument_name``, ``open_interest``, ``volume_24h``, etc.
        """
        data = await self._public_request("public/get_book_summary_by_currency", {
            "currency": currency.upper(),
            "kind": "option",
        })

        result = data.get("result", [])
        if not isinstance(result, list):
            logger.warning("Unexpected get_book_summary response format")
            return []

        logger.info("Fetched book summary for %d %s option instruments", len(result), currency.upper())
        return result

    async def get_ticker(self, instrument_name: str) -> OptionsTicker | None:
        """Fetch full ticker with Greeks for a single options instrument.

        Args:
            instrument_name: Deribit instrument name, e.g.
                ``"BTC-28MAR25-80000-C"``.

        Returns:
            :class:`OptionsTicker` model, or ``None`` on error.
        """
        data = await self._public_request("public/ticker", {
            "instrument_name": instrument_name,
        })

        result = data.get("result")
        if result is None or not isinstance(result, dict):
            logger.warning("No ticker data for %s", instrument_name)
            return None

        return self._parse_ticker(result)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _public_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a public (unauthenticated) JSON-RPC 2.0 request to Deribit.

        Args:
            method: API method name, e.g. ``"public/get_instruments"``.
            params: Method parameters.

        Returns:
            Parsed JSON response as dict.
        """
        await self._rate_limiter.acquire()

        url = self._base_url
        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }

        logger.debug("Deribit REST %s params=%s", method, params)
        resp = await self._http.post(url, json=body)

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                self._rate_limiter.set_retry_after(retry_after)
            logger.warning("Deribit rate limit hit on %s", method)
            return {}

        if resp.status_code >= 400:
            logger.error("Deribit API error %d on %s", resp.status_code, method)
            return {}

        try:
            parsed: dict[str, Any] = resp.json()
            return parsed
        except Exception:
            logger.error("Failed to parse Deribit response for %s", method)
            return {}

    @staticmethod
    def _parse_instrument(item: dict[str, Any]) -> OptionsInstrument | None:
        """Parse a raw Deribit instrument dict into an OptionsInstrument model."""
        try:
            instrument_name = item.get("instrument_name", "")
            option_type_str = item.get("option_type", "")

            # Deribit uses "call" / "put" directly.
            if option_type_str not in ("call", "put"):
                logger.debug("Skipping non-standard option_type: %s", option_type_str)
                return None

            return OptionsInstrument(
                instrument_name=instrument_name,
                base_currency=item.get("base_currency", ""),
                expiration_timestamp=int(item.get("expiration_timestamp", 0)),
                strike=float(item.get("strike", 0.0)),
                option_type=option_type_str,
                is_active=item.get("is_active", True),
                tick_size=float(item.get("tick_size", 0.0005)),
                contract_size=float(item.get("contract_size", 1.0)),
            )
        except Exception as exc:
            logger.debug("Failed to parse Deribit instrument: %s", exc)
            return None

    @staticmethod
    def _parse_ticker(data: dict[str, Any]) -> OptionsTicker | None:
        """Parse a raw Deribit ticker response into an OptionsTicker model."""
        try:
            mark_price = float(data.get("mark_price", 0.0))
            underlying_price = float(data.get("underlying_price", 0.0))
            mark_price_usd = mark_price * underlying_price if underlying_price > 0 else 0.0

            greeks = data.get("greeks", {}) or {}
            stats = data.get("stats", {}) or {}

            return OptionsTicker(
                instrument_name=data.get("instrument_name", ""),
                mark_price=mark_price,
                mark_price_usd=mark_price_usd,
                underlying_price=underlying_price,
                implied_volatility=float(data.get("mark_iv", 0.0)) / 100.0
                if data.get("mark_iv") is not None
                else 0.0,
                delta=float(greeks.get("delta", 0.0)),
                gamma=float(greeks.get("gamma", 0.0)),
                theta=float(greeks.get("theta", 0.0)),
                vega=float(greeks.get("vega", 0.0)),
                rho=float(greeks.get("rho", 0.0)),
                open_interest=float(data.get("open_interest", 0.0)),
                volume_24h=float(stats.get("volume", 0.0)),
                bid_price=float(data.get("best_bid_price", 0.0)),
                ask_price=float(data.get("best_ask_price", 0.0)),
                last_price=float(data.get("last_price", 0.0)),
                timestamp=int(data.get("timestamp", 0)),
            )
        except Exception as exc:
            logger.warning("Failed to parse Deribit ticker: %s", exc)
            return None
