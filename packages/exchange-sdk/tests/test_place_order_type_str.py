"""Bug 5: BinanceOrderType enum str() conversion regression.

On Python 3.11+ ``str(BinanceOrderType.MARKET)`` returns
``"BinanceOrderType.MARKET"`` instead of ``"MARKET"`` (PEP 663 changed
IntEnum/str-Enum __str__ to return the qualified name). Sending that string
to Binance triggers -1100 "Illegal characters found" rejections.

place_order() must use ``.value`` when the caller passes an enum member,
and continue to apply ``.upper()`` to plain strings for backward compat.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import SecretStr

from selene_space_exchange.base.auth import ExchangeCredentials
from selene_space_exchange.exchanges.binance.rest_client import (
    BinanceOrderType,
    BinanceRESTClient,
)


def _make_creds() -> ExchangeCredentials:
    return ExchangeCredentials(
        api_key=SecretStr("test-key"),
        api_secret=SecretStr("test-secret"),
    )


def _make_client() -> BinanceRESTClient:
    return BinanceRESTClient(_make_creds(), testnet=True)


class TestPlaceOrderTypeStr:
    """Bug 5: order_type enum/string → params["type"] must be a bare type name."""

    def test_case_1_enum_member_resolves_to_value(self) -> None:
        """BinanceOrderType.MARKET → "MARKET"."""
        client = _make_client()
        captured: dict[str, Any] = {}

        async def _fake_get_step_size(symbol: str) -> float | None:
            return None  # disable Bug 8 rounding for this test

        async def _fake_signed(method, path, params=None):
            captured.update(params or {})
            return {"orderId": 1}

        client._get_step_size = _fake_get_step_size
        client._signed_request = _fake_signed

        asyncio.run(
            client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type=BinanceOrderType.MARKET,
                quantity="0.001",
            )
        )
        assert captured["type"] == "MARKET"

    def test_case_2_plain_string_limit_backward_compat(self) -> None:
        """Passing "LIMIT" string still works → "LIMIT"."""
        client = _make_client()
        captured: dict[str, Any] = {}

        async def _fake_get_step_size(symbol: str) -> float | None:
            return None

        async def _fake_signed(method, path, params=None):
            captured.update(params or {})
            return {"orderId": 1}

        client._get_step_size = _fake_get_step_size
        client._signed_request = _fake_signed

        asyncio.run(
            client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity="0.001",
                price="60000",
                time_in_force="GTC",
            )
        )
        assert captured["type"] == "LIMIT"

    def test_case_3_lowercase_string_uppered(self) -> None:
        """Passing "market" lowercase still uppers → "MARKET"."""
        client = _make_client()
        captured: dict[str, Any] = {}

        async def _fake_get_step_size(symbol: str) -> float | None:
            return None

        async def _fake_signed(method, path, params=None):
            captured.update(params or {})
            return {"orderId": 1}

        client._get_step_size = _fake_get_step_size
        client._signed_request = _fake_signed

        asyncio.run(
            client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type="market",
                quantity="0.001",
            )
        )
        assert captured["type"] == "MARKET"

    def test_case_4_payload_has_no_qualified_enum_substring(self) -> None:
        """Wire payload must never contain "BinanceOrderType." substring."""
        client = _make_client()
        captured: dict[str, Any] = {}

        async def _fake_get_step_size(symbol: str) -> float | None:
            return None

        async def _fake_signed(method, path, params=None):
            captured.update(params or {})
            return {"orderId": 1}

        client._get_step_size = _fake_get_step_size
        client._signed_request = _fake_signed

        asyncio.run(
            client.place_order(
                symbol="BTCUSDT",
                side="BUY",
                order_type=BinanceOrderType.MARKET,
                quantity="0.001",
            )
        )
        # Payload's "type" field must be a bare type name, not the qualified
        # enum name (Binance would reject -1100 Illegal characters).
        assert "BinanceOrderType." not in captured.get("type", "")
        assert captured["type"] == "MARKET"
