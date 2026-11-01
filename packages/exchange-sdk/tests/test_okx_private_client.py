"""OKX private feed: order/account normalization and client bootstrap."""

from __future__ import annotations

from decimal import Decimal


from selene_space_exchange.exchanges.okx.private_client import (
    OKXPrivateNormalizer,
    OKXPrivateWSClient,
)
from selene_space_types.trading import OrderSide


def _norm() -> OKXPrivateNormalizer:
    return OKXPrivateNormalizer()


def _order_item(**overrides) -> dict:
    body = {
        "instId": "BTC-USDT",
        "ordId": "678901",
        "clOrdId": "myOrdId",
        "side": "buy",
        "ordType": "limit",
        "state": "filled",
        "sz": "0.01",
        "accFillSz": "0.01",
        "avgPx": "67000",
        "fillPx": "67000",
        "fillSz": "0.01",
        "fee": "-0.067",
        "feeCcy": "USDT",
        "uTime": "1711000000000",
    }
    return {**body, **overrides}


class TestOrderNormalization:
    def test_filled_buy_maps_every_field(self) -> None:
        item = _order_item()
        result = _norm().normalize_order(item)

        checks = {
            "exchange": result.exchange == "okx",
            "server-id": result.exchange_order_id == "678901",
            "client-id": result.client_order_id == "myOrdId",
            "symbol": result.symbol == "BTC/USDT",
            "side": result.side is OrderSide.BUY,
            "status": result.status == "FILLED",
            "filled-qty": result.filled_quantity == Decimal("0.01"),
            "avg-price": result.avg_fill_price == Decimal("67000.0"),
            # OKX reports fees as negative debits; the model stores magnitude.
            "commission": result.commission == Decimal("0.067"),
            "raw-preserved": result.raw == item,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_partially_filled_sell_keeps_partial_state(self) -> None:
        sell = _order_item(
            instId="ETH-USDT",
            ordId="111",
            clOrdId="",
            side="sell",
            ordType="market",
            state="partially_filled",
            sz="1.0",
            accFillSz="0.5",
            avgPx="3500",
            fillPx="3500",
            fillSz="0.5",
            fee="-0.175",
        )
        result = _norm().normalize_order(sell)
        checks = {
            "side": result.side is OrderSide.SELL,
            "status": result.status == "PARTIALLY_FILLED",
            "partial-qty": result.filled_quantity == Decimal("0.5"),
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestAccountNormalization:
    def test_balance_details_become_per_asset_balances(self) -> None:
        snapshot = {
            "details": [
                {"ccy": "BTC", "availBal": "0.5", "frozenBal": "0.1"},
                {"ccy": "USDT", "availBal": "10000", "frozenBal": "0"},
            ],
        }
        balances = _norm().normalize_account(snapshot, ts=1711000000000)

        checks = {
            "count": len(balances) == 2,
            "btc-free": (balances[0].asset, balances[0].free) == ("BTC", Decimal("0.5")),
            "btc-locked": balances[0].locked == Decimal("0.1"),
            "usdt-free": (balances[1].asset, balances[1].free) == ("USDT", Decimal("10000")),
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestPrivateClientBootstrap:
    def test_constructs_disconnected_with_okx_tag(self) -> None:
        client = OKXPrivateWSClient(api_key="test", api_secret="test", passphrase="test")
        checks = {
            "exchange": client.config.exchange == "okx",
            "disconnected": not client.is_connected,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"
