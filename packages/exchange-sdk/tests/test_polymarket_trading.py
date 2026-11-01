"""Polymarket CLOB trading client: credentials, auth signing, order guards."""

from __future__ import annotations

import pytest

from selene_space_exchange.exceptions import InvalidOrderError
from selene_space_exchange.exchanges.polymarket.trading import (
    PolymarketCredentials,
    PolymarketOrderType,
    PolymarketSide,
    PolymarketTradingClient,
)

CLOB_BASE = "https://clob.polymarket.com"


def _creds(**overrides) -> PolymarketCredentials:
    body = {
        "api_key": "test-key",
        "api_secret": "test-secret",
        "api_passphrase": "test-pass",
        "wallet_address": "0xTestAddress",
    }
    return PolymarketCredentials(**{**body, **overrides})


def _trading_client() -> PolymarketTradingClient:
    return PolymarketTradingClient(_creds(), base_url=CLOB_BASE)


class TestCredentialsShape:
    def test_fields_round_trip(self) -> None:
        creds = _creds()
        assert (creds.api_key, creds.wallet_address) == ("test-key", "0xTestAddress")


class TestAuthSigning:
    REQUIRED_POLY_HEADERS = (
        "POLY-API-KEY",
        "POLY-SIGNATURE",
        "POLY-TIMESTAMP",
        "POLY-ADDRESS",
        "POLY-PASSPHRASE",
    )

    def test_every_header_present_and_key_identified(self) -> None:
        headers = _trading_client()._build_auth_headers("POST", "/order", '{"tokenID":"abc"}')
        missing = [name for name in self.REQUIRED_POLY_HEADERS if name not in headers]
        assert not missing, f"missing: {missing}"
        assert headers["POLY-API-KEY"] == "test-key"

    def test_signature_depends_on_request_body(self) -> None:
        client = _trading_client()
        signed_a = client._build_auth_headers("POST", "/order", '{"a":1}')
        signed_b = client._build_auth_headers("POST", "/order", '{"b":2}')
        assert signed_a["POLY-SIGNATURE"] != signed_b["POLY-SIGNATURE"]


class TestPlaceOrderGuards:
    @staticmethod
    async def _attempt(client: PolymarketTradingClient, *, price: float, size: float):
        return await client.place_order(token_id="abc", side="BUY", price=price, size=size)

    @pytest.mark.asyncio
    async def test_price_above_one_rejected_with_band_hint(self) -> None:
        with pytest.raises(InvalidOrderError, match="between 0.01 and 0.99"):
            await self._attempt(_trading_client(), price=1.5, size=10)

    @pytest.mark.asyncio
    async def test_price_of_zero_is_out_of_band(self) -> None:
        with pytest.raises(InvalidOrderError):
            await self._attempt(_trading_client(), price=0, size=10)

    @pytest.mark.asyncio
    async def test_negative_size_rejected(self) -> None:
        with pytest.raises(InvalidOrderError, match="positive"):
            await self._attempt(_trading_client(), price=0.5, size=-1)


class TestTradingEnums:
    def test_time_in_force_values(self) -> None:
        tif = {t.name: t.value for t in PolymarketOrderType}
        assert tif == {"GTC": "GTC", "FOK": "FOK", "GTD": "GTD"}

    def test_side_values(self) -> None:
        sides = {s.name: s.value for s in PolymarketSide}
        assert sides == {"BUY": "BUY", "SELL": "SELL"}
