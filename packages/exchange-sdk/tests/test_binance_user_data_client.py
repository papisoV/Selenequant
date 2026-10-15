"""Binance user-data stream: execution/account/balance events + futures ORDER_TRADE_UPDATE."""

from __future__ import annotations

import pytest

from selene_space_exchange.exchanges.binance.user_data_client import (
    BinanceUserDataClient,
    BinanceUserDataNormalizer,
)
from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_types.trading import OrderSide

EVENT_TS = 1711000000000


def _normalizer() -> BinanceUserDataNormalizer:
    return BinanceUserDataNormalizer(SymbolNormalizer())


def _execution_report(**overrides) -> dict:
    """Spot executionReport; every asserted field is overridable."""
    body = {
        "e": "executionReport",
        "E": EVENT_TS,
        "s": "BTCUSDT",
        "S": "BUY",
        "o": "LIMIT",
        "X": "FILLED",
        "q": "0.001",
        "z": "0.001",
        "Z": "67.0",
        "L": "67000.0",
        "l": "0.001",
        "n": "0.067",
        "N": "USDT",
        "i": 12345,
        "c": "myClientOrderId",
    }
    return {**body, **overrides}


def _futures_trade_update(**order_overrides) -> dict:
    """Futures ORDER_TRADE_UPDATE wrapper around its inner ``o`` order dict."""
    order = {
        "s": "BTCUSDT",
        "c": "myClientOrderId",
        "i": 67890,
        "S": "SELL",
        "o": "LIMIT",
        "q": "0.002",
        "X": "PARTIALLY_FILLED",
        "z": "0.001",
        "Z": "67.5",
        "ap": "67500.0",
        "L": "67500.0",
        "l": "0.001",
        "n": "0.0675",
        "N": "USDT",
        "T": EVENT_TS + 123,
    }
    return {"e": "ORDER_TRADE_UPDATE", "E": EVENT_TS, "o": {**order, **order_overrides}}


class TestSpotExecutionReports:
    def test_full_field_mapping(self) -> None:
        raw = _execution_report()
        result = _normalizer().normalize_execution_report(raw)

        checks = {
            "exchange": result.exchange == "binance",
            "exch-order-id": result.exchange_order_id == "12345",
            "client-order-id": result.client_order_id == "myClientOrderId",
            "status": result.status == "FILLED",
            "filled-qty": float(result.filled_quantity) == pytest.approx(0.001),
            "avg-px": float(result.avg_fill_price) == pytest.approx(67000.0),
            "last-px": float(result.last_fill_price) == pytest.approx(67000.0),
            "last-qty": float(result.last_fill_quantity) == pytest.approx(0.001),
            "commission": float(result.commission) == pytest.approx(0.067),
            "commission-asset": result.commission_asset == "USDT",
            "raw-preserved": result.raw == raw,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_raw_dict_round_trips_untouched(self) -> None:
        raw = _execution_report(i=777)
        assert _normalizer().normalize_execution_report(raw).raw["i"] == 777


class TestAccountAndBalanceEvents:
    def test_outbound_account_position_flattens_balances(self) -> None:
        data = {
            "e": "outboundAccountPosition",
            "E": EVENT_TS,
            "B": [
                {"a": "BTC", "f": "0.5", "l": "0.1"},
                {"a": "USDT", "f": "10000", "l": "500"},
            ],
        }
        balances = _normalizer().normalize_account_position(data)

        checks = {
            "count": len(balances) == 2,
            "btc-free": (balances[0].asset, float(balances[0].free)) == ("BTC", pytest.approx(0.5)),
            "btc-locked": float(balances[0].locked) == pytest.approx(0.1),
            "usdt-free": (balances[1].asset, float(balances[1].free)) == ("USDT", pytest.approx(10000.0)),
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_balance_update_becomes_single_delta_entry(self) -> None:
        data = {"e": "balanceUpdate", "E": EVENT_TS, "a": "BTC", "d": "0.001"}
        result = _normalizer().normalize_balance_update(data)

        checks = {
            "exchange": result.exchange == "binance",
            "asset": result.asset == "BTC",
            "free-delta": float(result.free) == pytest.approx(0.001),
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestFuturesOrderTradeUpdate:
    def test_partial_fill_maps_every_field(self) -> None:
        raw = _futures_trade_update()
        result = _normalizer().normalize_order_trade_update(raw)

        checks = {
            "exchange": result.exchange == "binance",
            "exch-order-id": result.exchange_order_id == "67890",
            "client-order-id": result.client_order_id == "myClientOrderId",
            "side": result.side is OrderSide.SELL,
            "order-type": result.order_type == "LIMIT",
            "status": result.status == "PARTIALLY_FILLED",
            "qty": float(result.quantity) == pytest.approx(0.002),
            "filled": float(result.filled_quantity) == pytest.approx(0.001),
            "avg-px": float(result.avg_fill_price) == pytest.approx(67500.0),
            "last-px": float(result.last_fill_price) == pytest.approx(67500.0),
            "last-qty": float(result.last_fill_quantity) == pytest.approx(0.001),
            "commission": float(result.commission) == pytest.approx(0.0675),
            "commission-asset": result.commission_asset == "USDT",
            "trade-ts": result.timestamp == EVENT_TS + 123,
            "raw-preserved": result.raw == raw,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_avg_price_derived_from_quote_division_when_ap_missing(self) -> None:
        """``ap`` absent → avg = Z / z."""
        raw = _futures_trade_update(
            s="ETHUSDT",
            c="client-1",
            i=111,
            S="BUY",
            o="MARKET",
            q="0.5",
            X="FILLED",
            z="0.5",
            Z="1500.0",
            L="3000.0",
            l="0.5",
            n="1.5",
            ap=None,
        )
        del raw["o"]["ap"]

        result = _normalizer().normalize_order_trade_update(raw)
        assert float(result.avg_fill_price) == pytest.approx(3000.0)


class TestUserDataClientConstruction:
    def test_fresh_client_is_disconnected_binance(self) -> None:
        client = BinanceUserDataClient(api_key="test_key", api_secret="test_secret")
        assert client.config.exchange == "binance"
        assert not client.is_connected
