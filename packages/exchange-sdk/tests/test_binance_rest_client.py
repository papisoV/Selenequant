"""Binance REST client: order validation matrix, error mapping, amendments 4/5."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from selene_space_exchange.base.auth import ExchangeCredentials
from selene_space_exchange.exceptions import (
    AuthenticationError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitExceededError,
)
from selene_space_exchange.exchanges.binance.rest_client import (
    BinanceRESTClient,
)


def _creds() -> ExchangeCredentials:
    return ExchangeCredentials(api_key=SecretStr("test-key"), api_secret=SecretStr("test-secret"))


def _client(**overrides) -> BinanceRESTClient:
    return BinanceRESTClient(_creds(), testnet=True, **overrides)


def _order(order_type: str, **fields) -> dict:
    body = {"type": order_type, **fields}
    return body


LIMIT_COMPLETE = {"quantity": "1", "price": "100", "timeInForce": "GTC"}


class TestOrderValidation:
    @pytest.mark.parametrize(
        ("missing_field", "partial"),
        [
            ("price", {"quantity": "1", "timeInForce": "GTC"}),
            ("timeInForce", {"quantity": "1", "price": "100"}),
            ("quantity", {"price": "100", "timeInForce": "GTC"}),
        ],
        ids=["no-price", "no-tif", "no-qty"],
    )
    def test_limit_order_demands_each_required_field(self, missing_field: str, partial: dict) -> None:
        with pytest.raises(InvalidOrderError, match=missing_field):
            BinanceRESTClient._validate_order_params(_order("LIMIT", **partial))

    def test_market_without_any_size_rejected(self) -> None:
        with pytest.raises(InvalidOrderError, match="quantity"):
            BinanceRESTClient._validate_order_params(_order("MARKET"))

    def test_market_accepts_quote_denominated_size(self) -> None:
        BinanceRESTClient._validate_order_params(_order("MARKET", quoteOrderQty="100"))

    def test_stop_loss_needs_its_trigger(self) -> None:
        with pytest.raises(InvalidOrderError, match="stopPrice"):
            BinanceRESTClient._validate_order_params(_order("STOP_LOSS", quantity="1"))

    def test_stop_loss_limit_missing_trigger_still_rejected(self) -> None:
        with pytest.raises(InvalidOrderError):
            BinanceRESTClient._validate_order_params(_order("STOP_LOSS_LIMIT", quantity="1", price="100"))

    def test_complete_limit_passes_silently(self) -> None:
        assert (
            BinanceRESTClient._validate_order_params(_order("LIMIT", **LIMIT_COMPLETE)) is not None
        ) or True  # no raise is the contract


class TestErrorMapping:
    HTTP_RATE_LIMIT = 429
    CODE_AUTH = -2015
    CODE_BALANCE = -2010
    CODE_NO_ORDER = -2013
    CODE_FILTER = -1013

    TYPED_FAILURES = [
        (-2015, AuthenticationError, "Invalid API key"),
        (-2010, InsufficientBalanceError, "Account has insufficient balance"),
        (-2013, OrderNotFoundError, "Order does not exist"),
        (-1013, OrderRejectedError, "Filter failure"),
    ]

    @staticmethod
    def _response(status: int, payload: dict):
        from unittest.mock import MagicMock as _MM

        resp = _MM()
        resp.status_code = status
        resp.json.return_value = payload
        resp.text = str(payload)
        return resp

    def test_http_429_maps_to_rate_limit(self) -> None:
        with pytest.raises(RateLimitExceededError):
            _client()._handle_error_response(self._response(self.HTTP_RATE_LIMIT, {"code": -1, "msg": "rate limit"}))

    @pytest.mark.parametrize(
        ("code", "expected", "msg"),
        TYPED_FAILURES,
        ids=["auth", "balance", "not-found", "rejected"],
    )
    def test_error_codes_map_to_typed_exceptions(self, code: int, expected: type, msg: str) -> None:
        with pytest.raises(expected):
            _client()._handle_error_response(self._response(400, {"code": code, "msg": msg}))

    def test_cancellation_needs_an_identifier(self) -> None:
        with pytest.raises(InvalidOrderError, match="order_id or client_order_id"):
            asyncio.run(_client().cancel_order(symbol="BTCUSDT"))


class TestEndpointSelection:
    def test_production_default(self) -> None:
        assert "api.binance.com" in BinanceRESTClient(_creds())._base_url

    def test_testnet_variant(self) -> None:
        assert "testnet" in BinanceRESTClient(_creds(), testnet=True)._base_url

    def test_explicit_base_url_wins(self) -> None:
        assert BinanceRESTClient(_creds(), base_url="https://custom.api")._base_url == "https://custom.api"

    def test_exchange_tag(self) -> None:
        assert _client()._exchange_name == "binance"


class TestAmendment4ApiRestrictions:
    """get_api_restrictions surfaces the key's permission map for engine startup."""

    @staticmethod
    async def _all_disabled(method: str, path: str, params=None) -> dict:
        return {
            "enableWithdrawals": False,
            "enableFutures": False,
            "enableMargin": False,
            "enableVanillaOptions": False,
        }

    def test_permission_map_returned_verbatim(self) -> None:
        client = _client()
        client._signed_request = self._all_disabled
        perms = asyncio.run(client.get_api_restrictions())

        locked_down = {k: perms[k] is False for k in (
            "enableWithdrawals",
            "enableFutures",
            "enableMargin",
            "enableVanillaOptions",
        )}
        assert all(locked_down.values()), f"failing: {[k for k, ok in locked_down.items() if not ok]}"


class TestAmendment5MinNotional:
    """get_min_notional digs the MIN_NOTIONAL filter out of exchangeInfo."""

    @staticmethod
    def _with_exchange_info(client: BinanceRESTClient, info: dict) -> None:
        async def fake_info(symbol=None):
            return info

        client.get_exchange_info = fake_info

    BTCUSDT_WITH_FILTER = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5.00000000"},
                ],
            }
        ]
    }

    def test_extracted_as_float_when_present(self) -> None:
        client = _client()
        self._with_exchange_info(client, self.BTCUSDT_WITH_FILTER)
        assert asyncio.run(client.get_min_notional("BTCUSDT")) == 5.0

    def test_none_when_filter_missing(self) -> None:
        client = _client()
        self._with_exchange_info(client, {"symbols": [{"symbol": "BTCUSDT", "filters": []}]})
        assert asyncio.run(client.get_min_notional("BTCUSDT")) is None

    def test_none_when_symbol_absent(self) -> None:
        client = _client()
        self._with_exchange_info(client, {"symbols": []})
        assert asyncio.run(client.get_min_notional("BTCUSDT")) is None
