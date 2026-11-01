"""Tests for OKXContractConverter — USDT notional → contract count.

Covers:
- Async path (OKXRESTClient._okx_request mock)
- Sync path (legacy public_api.convert_contract_coin mock)
- Error handling (non-zero code, empty data, missing sz, non-numeric sz)
- Input validation (amount_usdt <= 0, price <= 0)
- Parity with the reference call-shape behavior
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from selene_space_exchange.exceptions import ExchangeAPIError
from selene_space_exchange.exchanges.okx.okx_contract_converter import (
    OKXContractConverter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _okx_response(sz: str = "0.001") -> dict[str, Any]:
    """Build a successful OKX convert-contract-coin response."""
    return {
        "code": "0",
        "msg": "",
        "data": [{"sz": sz, "px": "60000", "side": "buy"}],
    }


class _FakePublicAPI:
    """Legacy-style public_api stub for sync path tests."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.last_call: dict[str, Any] | None = None

    def convert_contract_coin(
        self,
        *,
        type: str = "",
        instId: str = "",
        sz: str = "",
        px: str = "",
        unit: str = "",
        opType: str = "",
    ) -> dict[str, Any]:
        self.last_call = {
            "type": type,
            "instId": instId,
            "sz": sz,
            "px": px,
            "unit": unit,
            "opType": opType,
        }
        return self._response


# ---------------------------------------------------------------------------
# Sync path tests (strategy-engine hot path)
# ---------------------------------------------------------------------------


class TestConvertSync:
    def test_basic_conversion_buy(self) -> None:
        """Buy side maps to opType='open' and returns the sz float."""
        public_api = _FakePublicAPI(_okx_response(sz="0.5"))
        result = OKXContractConverter.convert_sync(
            public_api,
            inst_id="BTC-USDT-SWAP",
            amount_usdt=100.0,
            price=60000.0,
            side="buy",
        )
        assert result == 0.5
        assert public_api.last_call is not None
        assert public_api.last_call["type"] == "1"
        assert public_api.last_call["instId"] == "BTC-USDT-SWAP"
        assert public_api.last_call["sz"] == "100.0"
        assert public_api.last_call["px"] == "60000.0"
        assert public_api.last_call["unit"] == "usdt"
        assert public_api.last_call["opType"] == "open"

    def test_basic_conversion_sell(self) -> None:
        """Sell side maps to opType='close'."""
        public_api = _FakePublicAPI(_okx_response(sz="0.25"))
        result = OKXContractConverter.convert_sync(
            public_api,
            inst_id="ETH-USDT-SWAP",
            amount_usdt=50.0,
            price=3000.0,
            side="sell",
        )
        assert result == 0.25
        assert public_api.last_call is not None
        assert public_api.last_call["opType"] == "close"

    def test_zero_sz_when_notional_too_small(self) -> None:
        """OKX returns sz='0' when notional is below one contract value."""
        public_api = _FakePublicAPI(_okx_response(sz="0"))
        result = OKXContractConverter.convert_sync(
            public_api,
            inst_id="BTC-USDT-SWAP",
            amount_usdt=0.01,
            price=60000.0,
            side="buy",
        )
        assert result == 0.0

    def test_parity_with_reference_call_shape(self) -> None:
        """Replicate the exact reference call signature."""
        public_api = _FakePublicAPI(_okx_response(sz="3"))
        # Reference call: public_api.convert_contract_coin(
        #     type='1', instId=instId, sz=str(amount_usdt),
        #     px=str(adjusted_price), unit='usdt', opType='open')
        OKXContractConverter.convert_sync(
            public_api,
            inst_id="BTC-USDT-SWAP",
            amount_usdt=20.0,
            price=60000.0,
            side="buy",
        )
        assert public_api.last_call == {
            "type": "1",
            "instId": "BTC-USDT-SWAP",
            "sz": "20.0",
            "px": "60000.0",
            "unit": "usdt",
            "opType": "open",
        }

    def test_invalid_amount_usdt_raises(self) -> None:
        public_api = _FakePublicAPI(_okx_response())
        with pytest.raises(ExchangeAPIError, match="amount_usdt"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=0.0,
                price=60000.0,
                side="buy",
            )
        with pytest.raises(ExchangeAPIError, match="amount_usdt"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=-5.0,
                price=60000.0,
                side="buy",
            )

    def test_invalid_price_raises(self) -> None:
        public_api = _FakePublicAPI(_okx_response())
        with pytest.raises(ExchangeAPIError, match="price"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=0.0,
                side="buy",
            )
        with pytest.raises(ExchangeAPIError, match="price"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=-1.0,
                side="buy",
            )

    def test_non_zero_code_raises(self) -> None:
        public_api = _FakePublicAPI(
            {"code": "50001", "msg": "instId not exist", "data": []}
        )
        with pytest.raises(ExchangeAPIError, match="instId not exist"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BAD-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )

    def test_empty_data_raises(self) -> None:
        public_api = _FakePublicAPI({"code": "0", "msg": "", "data": []})
        with pytest.raises(ExchangeAPIError, match="empty data"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )

    def test_missing_sz_raises(self) -> None:
        public_api = _FakePublicAPI({"code": "0", "msg": "", "data": [{"px": "60000"}]})
        with pytest.raises(ExchangeAPIError, match="missing 'sz'"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )

    def test_non_numeric_sz_raises(self) -> None:
        public_api = _FakePublicAPI({"code": "0", "msg": "", "data": [{"sz": "abc"}]})
        with pytest.raises(ExchangeAPIError, match="non-numeric sz"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )

    def test_public_api_exception_wrapped(self) -> None:
        """If convert_contract_coin raises, we wrap it in ExchangeAPIError."""
        public_api = MagicMock()
        public_api.convert_contract_coin.side_effect = RuntimeError("network")
        with pytest.raises(ExchangeAPIError, match="network"):
            OKXContractConverter.convert_sync(
                public_api,
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )


# ---------------------------------------------------------------------------
# Async path tests (live execution path)
# ---------------------------------------------------------------------------


class _FakeRestClient:
    """Stub mimicking OKXRESTClient._okx_request for async tests."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.last_call: dict[str, Any] | None = None

    async def _okx_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.last_call = {
            "method": method,
            "path": path,
            "params": params,
            "body": body,
        }
        return self._response


class TestConvertAsync:
    @pytest.mark.asyncio
    async def test_basic_async_conversion(self) -> None:
        rest_client = _FakeRestClient(_okx_response(sz="2.5"))
        converter = OKXContractConverter(rest_client)
        result = await converter.usdt_to_contracts(
            inst_id="BTC-USDT-SWAP",
            amount_usdt=150.0,
            price=60000.0,
            side="buy",
        )
        assert result == 2.5
        assert rest_client.last_call is not None
        assert rest_client.last_call["method"] == "GET"
        assert rest_client.last_call["path"] == "/api/v5/public/convert-contract-coin"
        params = rest_client.last_call["params"]
        assert params["type"] == "1"
        assert params["instId"] == "BTC-USDT-SWAP"
        assert params["sz"] == "150.0"
        assert params["px"] == "60000.0"
        assert params["unit"] == "usdt"
        assert params["opType"] == "open"

    @pytest.mark.asyncio
    async def test_async_sell_uses_close(self) -> None:
        rest_client = _FakeRestClient(_okx_response(sz="1.0"))
        converter = OKXContractConverter(rest_client)
        await converter.usdt_to_contracts(
            inst_id="ETH-USDT-SWAP",
            amount_usdt=60.0,
            price=3000.0,
            side="sell",
        )
        assert rest_client.last_call is not None
        assert rest_client.last_call["params"]["opType"] == "close"

    @pytest.mark.asyncio
    async def test_async_no_rest_client_raises(self) -> None:
        converter = OKXContractConverter(rest_client=None)
        with pytest.raises(ExchangeAPIError, match="rest_client"):
            await converter.usdt_to_contracts(
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )

    @pytest.mark.asyncio
    async def test_async_invalid_amount(self) -> None:
        rest_client = _FakeRestClient(_okx_response())
        converter = OKXContractConverter(rest_client)
        with pytest.raises(ExchangeAPIError, match="amount_usdt"):
            await converter.usdt_to_contracts(
                inst_id="BTC-USDT-SWAP",
                amount_usdt=0.0,
                price=60000.0,
                side="buy",
            )

    @pytest.mark.asyncio
    async def test_async_invalid_price(self) -> None:
        rest_client = _FakeRestClient(_okx_response())
        converter = OKXContractConverter(rest_client)
        with pytest.raises(ExchangeAPIError, match="price"):
            await converter.usdt_to_contracts(
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=0.0,
                side="buy",
            )

    @pytest.mark.asyncio
    async def test_async_non_zero_code(self) -> None:
        rest_client = _FakeRestClient(
            {"code": "50001", "msg": "instId not exist", "data": []}
        )
        converter = OKXContractConverter(rest_client)
        with pytest.raises(ExchangeAPIError, match="instId not exist"):
            await converter.usdt_to_contracts(
                inst_id="BAD-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )

    @pytest.mark.asyncio
    async def test_async_empty_data(self) -> None:
        rest_client = _FakeRestClient({"code": "0", "msg": "", "data": []})
        converter = OKXContractConverter(rest_client)
        with pytest.raises(ExchangeAPIError, match="empty data"):
            await converter.usdt_to_contracts(
                inst_id="BTC-USDT-SWAP",
                amount_usdt=20.0,
                price=60000.0,
                side="buy",
            )
