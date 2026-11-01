"""Bug 7: get_min_notional must query both MIN_NOTIONAL and NOTIONAL filters.

Root cause: original implementation only queried ``MIN_NOTIONAL`` filter.
SOLUSDT (and many other spot pairs) only exposes a ``NOTIONAL`` filter, so
``get_min_notional`` returned ``None`` and the startup check at
``main.py:876`` silently under-estimated the threshold. The 0.065 SOL ×
$76.62 = $4.98 order was below the $5 NOTIONAL floor and got rejected
server-side.

Fix (Plan v4 Option b): scan both filter types, return the max value when
both are present (conservative — never under-estimate). The
``applyMinToMarket`` accumulator is intentionally dropped (no caller
consumes it; see Round 4 ADR for rationale).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import SecretStr

from selene_space_exchange.base.auth import ExchangeCredentials
from selene_space_exchange.exchanges.binance.rest_client import (
    BinanceRESTClient,
)


def _make_creds() -> ExchangeCredentials:
    return ExchangeCredentials(
        api_key=SecretStr("test-key"),
        api_secret=SecretStr("test-secret"),
    )


def _make_client() -> BinanceRESTClient:
    return BinanceRESTClient(_make_creds(), testnet=True)


def _exchange_info_with(filters: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a single-symbol exchangeInfo payload with the given filters."""
    return {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "filters": filters,
            }
        ]
    }


def _make_async_exchange_info(filters: list[dict[str, Any]]):
    """Build an async fake for get_exchange_info returning the given filters."""

    async def _fake(symbol: str | None = None) -> dict[str, Any]:
        return _exchange_info_with(filters)

    return _fake


def _make_async_empty_exchange_info():
    async def _fake(symbol: str | None = None) -> dict[str, Any]:
        return {"symbols": []}

    return _fake


class TestGetMinNotional:
    def test_case_1_only_min_notional_filter(self) -> None:
        """filters=[MIN_NOTIONAL=5] → returns 5.0."""
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [{"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"}]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result == 5.0

    def test_case_2_only_notional_filter_returns_value(self) -> None:
        """filters=[NOTIONAL=5] → returns 5.0 (was None before fix)."""
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [{"filterType": "NOTIONAL", "minNotional": "5.00000000"}]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result == 5.0

    def test_case_3_both_filters_returns_max(self) -> None:
        """MIN_NOTIONAL=$5 + NOTIONAL=$10 → returns $10 (max)."""
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [
                {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
                {"filterType": "NOTIONAL", "minNotional": "10.00000000"},
            ]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result == 10.0

    def test_case_4_both_filters_returns_max_reversed(self) -> None:
        """MIN_NOTIONAL=$10 + NOTIONAL=$5 → returns $10 (max)."""
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [
                {"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result == 10.0

    def test_case_5_neither_filter_returns_none(self) -> None:
        """filters=[PRICE_FILTER, LOT_SIZE] (no MIN_NOTIONAL/NOTIONAL) → None."""
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            ]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result is None

    def test_case_6_symbol_not_found_returns_none(self) -> None:
        """symbols=[] (symbol absent) → None."""
        client = _make_client()
        client.get_exchange_info = _make_async_empty_exchange_info()  # type: ignore[assignment]
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result is None

    def test_case_7_regression_only_min_notional_unchanged(self) -> None:
        """Regression: pre-fix behavior on MIN_NOTIONAL-only symbols is
        preserved (returns the same value as before the fix)."""
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00010000"},
            ]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result == 10.0

    def test_case_8_empty_min_notional_string_skipped(self) -> None:
        """NOTIONAL with empty minNotional string → skip filter, return None.

        Defensive parsing: a malformed filter (empty / whitespace-only
        minNotional) must be skipped, not crash.
        """
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info(  # type: ignore[assignment]
            [
                {"filterType": "NOTIONAL", "minNotional": ""},
                {"filterType": "NOTIONAL", "minNotional": "   "},
            ]
        )
        result = asyncio.run(client.get_min_notional("SOLUSDT"))
        assert result is None
