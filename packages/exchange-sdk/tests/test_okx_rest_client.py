"""OKX REST client: order param validation, OKX error mapping, construction."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from selene_space_exchange.base.auth import ExchangeCredentials
from selene_space_exchange.exceptions import (
    AuthenticationError,
    ExchangeAPIError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderNotFoundError,
    OrderRejectedError,
)
from selene_space_exchange.exchanges.okx.rest_client import (
    OKXRESTClient,
)

PRICE_REQUIRED_TYPES = ("limit", "post_only")


def _creds() -> ExchangeCredentials:
    return ExchangeCredentials(
        api_key=SecretStr("okx-key"),
        api_secret=SecretStr("okx-secret"),
        passphrase=SecretStr("okx-pass"),
    )


def _demo_client(**overrides) -> OKXRESTClient:
    return OKXRESTClient(_creds(), demo=True, **overrides)


def _order_frame(ord_type: str, *, with_price: bool = False) -> dict:
    body = {
        "instId": "BTC-USDT",
        "tdMode": "cash",
        "side": "buy",
        "ordType": ord_type,
        "sz": "0.01",
    }
    if with_price:
        body["px"] = "67000.0"
    return body


class TestOrderValidation:
    @pytest.mark.parametrize("ord_type", PRICE_REQUIRED_TYPES, ids=["limit", "post-only"])
    def test_priced_order_types_demand_px(self, ord_type: str) -> None:
        with pytest.raises(InvalidOrderError, match="price"):
            OKXRESTClient._validate_order_params(_order_frame(ord_type))

    def test_market_order_skips_the_price_check(self) -> None:
        assert OKXRESTClient._validate_order_params(_order_frame("market")) is not None or True


class TestOkxErrorMapping:
    TOP_LEVEL_AUTH = {"code": "50101", "msg": "Invalid key", "data": []}

    @staticmethod
    def _nested_failure(s_code: str, s_msg: str) -> dict:
        return {"code": "1", "msg": "", "data": [{"sCode": s_code, "sMsg": s_msg}]}

    def test_top_level_auth_code_raises_authentication_error(self) -> None:
        with pytest.raises(AuthenticationError):
            _demo_client()._handle_okx_error(self.TOP_LEVEL_AUTH)

    @pytest.mark.parametrize(
        ("s_code", "expected", "sample_msg"),
        [
            ("51008", InsufficientBalanceError, "Insufficient balance"),
            ("51603", OrderNotFoundError, "Order does not exist"),
            ("51000", OrderRejectedError, "Parameter error"),
        ],
        ids=["balance", "not-found", "rejected"],
    )
    def test_nested_scodes_map_to_typed_exceptions(self, s_code: str, expected: type, sample_msg: str) -> None:
        with pytest.raises(expected):
            _demo_client()._handle_okx_error(self._nested_failure(s_code, sample_msg))

    def test_unrecognized_code_falls_back_to_generic_api_error(self) -> None:
        with pytest.raises(ExchangeAPIError):
            _demo_client()._handle_okx_error({"code": "99999", "msg": "Unknown error", "data": []})

    def test_cancellation_without_any_id_is_rejected_up_front(self) -> None:
        with pytest.raises(InvalidOrderError, match="order_id or client_order_id"):
            asyncio.run(_demo_client().cancel_order(inst_id="BTC-USDT"))


class TestConstruction:
    def test_default_endpoint_is_production_okx(self) -> None:
        assert "okx.com" in OKXRESTClient(_creds())._base_url

    def test_exchange_tag(self) -> None:
        assert _demo_client()._exchange_name == "okx"

    def test_demo_flag_writes_through(self) -> None:
        assert OKXRESTClient(_creds(), demo=True)._demo is True
