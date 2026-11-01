"""Bug 8: place_order must floor quantity to LOT_SIZE stepSize multiple.

Root cause: a 0.07489 SOL holding passed straight through to Binance got
rejected with -1013 (LOT_SIZE stepSize=0.001, 0.07489 is not a 0.001
multiple). Rounding UP to 0.075 would exceed the holding and trigger
-2010 insufficient balance. The fix floors the quantity to the largest
stepSize multiple <= the requested quantity, returning a string
(Binance expects string quantity params).
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


class TestRoundToStep:
    """Pure-function tests for the static _round_to_step helper."""

    def test_case_1_step_001_qty_0_07489_floors_to_0_074(self) -> None:
        assert BinanceRESTClient._round_to_step(0.07489, 0.001) == "0.074"

    def test_case_2_step_001_qty_0_075_exact_multiple(self) -> None:
        assert BinanceRESTClient._round_to_step(0.075, 0.001) == "0.075"

    def test_case_3_step_0_01_qty_1_2345_floors_to_1_23(self) -> None:
        assert BinanceRESTClient._round_to_step(1.2345, 0.01) == "1.23"

    def test_case_4_step_0_001_qty_0_5_zero_pads_to_0_500(self) -> None:
        """Decimals count derived from step: step=0.001 → 3 decimals."""
        assert BinanceRESTClient._round_to_step(0.5, 0.001) == "0.500"

    def test_case_5_step_0_0001_qty_1_0_zero_pads_to_1_0000(self) -> None:
        """step=0.0001 → 4 decimals, exact multiple stays put."""
        assert BinanceRESTClient._round_to_step(1.0, 0.0001) == "1.0000"


def _exchange_info_with_step(step_size: str = "0.00100000") -> dict[str, Any]:
    return {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": step_size, "minQty": "0.0", "maxQty": "100000"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
                ],
            }
        ]
    }


def _exchange_info_without_lot_size() -> dict[str, Any]:
    return {
        "symbols": [
            {
                "symbol": "SOLUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
                ],
            }
        ]
    }


def _make_async_exchange_info_with_step(step_size: str = "0.00100000"):
    async def _fake(symbol: str | None = None) -> dict[str, Any]:
        return _exchange_info_with_step(step_size)

    return _fake


def _make_async_exchange_info_without_lot_size():
    async def _fake(symbol: str | None = None) -> dict[str, Any]:
        return _exchange_info_without_lot_size()

    return _fake


class TestGetStepSize:
    def test_case_6_lot_size_filter_present_returns_step(self) -> None:
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info_with_step("0.00100000")  # type: ignore[assignment]
        result = asyncio.run(client._get_step_size("SOLUSDT"))
        assert result == 0.001

    def test_case_7_lot_size_filter_absent_returns_none(self) -> None:
        client = _make_client()
        client.get_exchange_info = _make_async_exchange_info_without_lot_size()  # type: ignore[assignment]
        result = asyncio.run(client._get_step_size("SOLUSDT"))
        assert result is None


class TestPlaceOrderRoundsQuantity:
    def test_case_8_e2e_place_order_floors_quantity(self) -> None:
        """End-to-end: place_order(quantity=0.07489, symbol=SOLUSDT)
        with mocked exchange_info returns stepSize=0.001 → the params
        sent to _signed_request must have quantity == "0.074"."""
        client = _make_client()
        captured: dict[str, Any] = {}

        # Real get_exchange_info is replaced by a sync lambda returning the
        # canned payload — _get_step_size calls it via `await self.get_exchange_info`,
        # so wrap in an async function to satisfy the await contract.
        async def _fake_get_exchange_info(symbol: str | None = None) -> dict[str, Any]:
            return _exchange_info_with_step("0.00100000")

        async def _fake_signed(method, path, params=None):
            captured.update(params or {})
            return {"orderId": 1, "status": "FILLED", "executedQty": "0.074"}

        client.get_exchange_info = _fake_get_exchange_info  # type: ignore[assignment]
        client._signed_request = _fake_signed  # type: ignore[assignment]

        asyncio.run(
            client.place_order(
                symbol="SOLUSDT",
                side="SELL",
                order_type=BinanceOrderType.MARKET,
                quantity="0.07489",
            )
        )
        assert captured["quantity"] == "0.074"
