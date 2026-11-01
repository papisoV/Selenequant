"""Unified trading adapters: request/response mapping, tdMode, Decimal fidelity."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.trading import (
    BalanceInfo,
    BinanceFuturesTradingAdapter,
    BinanceTradingAdapter,
    OKXTradingAdapter,
    OrderRequest,
    PolymarketTradingAdapter,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


def _limit_buy(symbol: str = "BTC/USDT", qty: float = 0.001, price: float = 60000.0) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=qty,
        price=price,
    )


def _market_buy(symbol: str = "BTC/USDT", qty: float = 0.01) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=qty,
    )


class TestOrderRequestModel:
    def test_limit_defaults_to_good_till_cancel(self) -> None:
        req = _limit_buy()
        assert (req.symbol, req.time_in_force) == ("BTC/USDT", "GTC")

    def test_market_order_carries_no_price(self) -> None:
        assert _market_buy("ETH/USDT", 0.5).price is None

    def test_serialized_enums_become_wire_strings(self) -> None:
        dumped = _limit_buy("ETH/USDT", 1.0, 3500.0).model_dump()
        assert (dumped["symbol"], dumped["order_type"]) == ("ETH/USDT", "limit")


class TestBinanceSpotAdapter:
    @staticmethod
    def _wired() -> tuple[BinanceTradingAdapter, AsyncMock]:
        client = AsyncMock()
        return BinanceTradingAdapter(client), client

    BINANCE_ACK = {
        "orderId": 12345,
        "clientOrderId": "test-id",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "status": "NEW",
        "origQty": "0.001",
        "executedQty": "0.0",
        "cummulativeQuoteQty": "0.0",
        "price": "60000.00",
        "transactTime": 1711000000000,
    }

    @pytest.mark.asyncio
    async def test_place_order_maps_ack_fields(self) -> None:
        adapter, client = self._wired()
        client.place_order.return_value = dict(self.BINANCE_ACK)

        result = await adapter.place_order(_limit_buy())

        checks = {
            "exchange": result.exchange == "binance",
            "id": result.order_id == "12345",
            "status": result.status is OrderStatus.ACCEPTED,
            "qty": result.quantity == Decimal("0.001"),
            "one-call": client.place_order.call_count == 1,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    CANCELLED_ACK = {
        "orderId": 12345,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "status": "CANCELED",
        "origQty": "0.001",
        "executedQty": "0.0",
        "cummulativeQuoteQty": "0.0",
        "price": "60000.00",
    }

    @pytest.mark.asyncio
    async def test_cancel_order_lands_in_cancelled_state(self) -> None:
        adapter, client = self._wired()
        client.cancel_order.return_value = dict(self.CANCELLED_ACK)

        outcome = await adapter.cancel_order(symbol="BTC/USDT", order_id="12345")
        assert outcome.status is OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_balances_sum_free_and_locked_into_total(self) -> None:
        adapter, client = self._wired()
        client.get_balances.return_value = [
            {"asset": "BTC", "free": "0.5", "locked": "0.1"},
            {"asset": "USDT", "free": "10000", "locked": "0"},
        ]

        balances = await adapter.get_balances()

        checks = {
            "count": len(balances) == 2,
            "first-asset": balances[0].asset == "BTC",
            "free-decimal": balances[0].free == Decimal("0.5"),
            "total-adds-up": balances[0].total == Decimal("0.6"),
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_exchange_tag(self) -> None:
        assert self._wired()[0].exchange_name == "binance"

    def test_fully_executed_order_derives_average_price(self) -> None:
        adapter = self._wired()[0]
        parsed = adapter._parse_order_response(
            {
                "orderId": 99,
                "symbol": "ETHUSDT",
                "side": "SELL",
                "type": "MARKET",
                "status": "FILLED",
                "origQty": "1.0",
                "executedQty": "1.0",
                "cummulativeQuoteQty": "3500.0",
                "price": "0.00",
                "transactTime": 1711000000000,
            }
        )
        checks = {
            "filled": parsed.status is OrderStatus.FILLED,
            "qty": parsed.filled_quantity == 1.0,
            "avg": parsed.avg_fill_price == 3500.0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestOKXAdapter:
    @staticmethod
    def _wired(market_type: str = "spot") -> tuple[OKXTradingAdapter, AsyncMock]:
        client = AsyncMock()
        return OKXTradingAdapter(client, market_type=market_type), client

    OKX_ACK = {"code": "0", "msg": "", "data": [{"ordId": "98765", "clOrdId": "my-order"}]}

    @pytest.mark.asyncio
    async def test_place_order_reads_first_data_row(self) -> None:
        adapter, client = self._wired()
        client.place_order.return_value = dict(self.OKX_ACK)

        result = await adapter.place_order(_limit_buy(qty=0.01))

        checks = {
            "exchange": result.exchange == "okx",
            "id": result.order_id == "98765",
            "accepted": result.status is OrderStatus.ACCEPTED,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_balance_details_flatten_out_per_currency(self) -> None:
        adapter, client = self._wired()
        client.get_balance.return_value = {
            "code": "0",
            "data": [
                {
                    "details": [
                        {"ccy": "USDT", "availBal": "5000", "frozenBal": "200", "cashBal": "5200"},
                        {"ccy": "BTC", "availBal": "0.1", "frozenBal": "0", "cashBal": "0.1"},
                    ]
                }
            ],
        }

        balances = await adapter.get_balances()

        checks = {
            "count": len(balances) == 2,
            "order-preserved": balances[0].asset == "USDT",
            "free": balances[0].free == 5000.0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_exchange_tag(self) -> None:
        assert self._wired()[0].exchange_name == "okx"

    def test_single_order_parse_uses_avg_price_field(self) -> None:
        adapter = self._wired()[0]
        parsed = adapter._parse_single_order(
            {
                "ordId": "111",
                "clOrdId": "c1",
                "instId": "ETH-USDT",
                "side": "sell",
                "ordType": "limit",
                "state": "filled",
                "sz": "2.0",
                "fillSz": "2.0",
                "px": "3500",
                "avgPx": "3498",
            }
        )
        checks = {
            "filled": parsed.status is OrderStatus.FILLED,
            "symbol": parsed.symbol == "ETH/USDT",
            "avg": parsed.avg_fill_price == 3498.0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestPolymarketAdapter:
    @staticmethod
    def _wired() -> tuple[PolymarketTradingAdapter, AsyncMock]:
        client = AsyncMock()
        return PolymarketTradingAdapter(client), client

    @pytest.mark.asyncio
    async def test_place_order_returns_clob_order_id(self) -> None:
        adapter, client = self._wired()
        client.place_order.return_value = {"orderID": "poly-order-1", "status": "live"}

        result = await adapter.place_order(_limit_buy("token-abc", 10.0, 0.52))

        checks = {
            "exchange": result.exchange == "polymarket",
            "id": result.order_id == "poly-order-1",
            "one-call": client.place_order.call_count == 1,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_single_usdc_balance_from_wallet(self) -> None:
        adapter, client = self._wired()
        client.get_balance.return_value = {"balance": "1000.50"}

        balances = await adapter.get_balances()

        checks = {
            "single": len(balances) == 1,
            "usdc": balances[0].asset == "USDC",
            "amount": balances[0].free == 1000.50,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_exchange_tag(self) -> None:
        assert self._wired()[0].exchange_name == "polymarket"


class TestBalanceInfoModel:
    def test_explicit_totals_round_trip(self) -> None:
        assert BalanceInfo(asset="BTC", free=1.0, locked=0.5, total=1.5).total == 1.5

    def test_zeroed_defaults(self) -> None:
        empty = BalanceInfo(asset="USDT")
        zeroed = {empty.free == 0.0, empty.locked == 0.0, empty.total == 0.0}
        assert zeroed == {True}


class TestOKXTdModeSelection:
    @staticmethod
    def _ack(order_id: str) -> dict:
        return {"code": "0", "data": [{"ordId": order_id}]}

    def test_market_type_defaults_to_spot(self) -> None:
        assert OKXTradingAdapter(AsyncMock()).market_type == "spot"

    def test_futures_constructor_writes_through(self) -> None:
        adapter = OKXTradingAdapter(AsyncMock(), market_type="futures")
        assert adapter.market_type == "futures"

    @pytest.mark.asyncio
    async def test_futures_orders_travel_as_cross_margin(self) -> None:
        client = AsyncMock()
        adapter = OKXTradingAdapter(client, market_type="futures")
        client.place_order.return_value = self._ack("fut-1")

        await adapter.place_order(_market_buy())

        assert client.place_order.call_args.kwargs["trade_mode"].value == "cross"

    @pytest.mark.asyncio
    async def test_spot_orders_travel_as_cash(self) -> None:
        client = AsyncMock()
        adapter = OKXTradingAdapter(client, market_type="spot")
        client.place_order.return_value = self._ack("spot-1")

        await adapter.place_order(_market_buy())

        assert client.place_order.call_args.kwargs["trade_mode"].value == "cash"


class TestBinanceFuturesAdapterConstruction:
    def test_wraps_client_and_reports_own_exchange_name(self) -> None:
        assert BinanceFuturesTradingAdapter(AsyncMock()).exchange_name == "binance_futures"


class TestDecimalBoundaryFidelity:
    """H19 invariant: raw JSON → internal Decimal → float JSON without drift."""

    PRECISION_RAW_BINANCE = {
        "orderId": "dec-1",
        "clientOrderId": "c-dec",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "status": "FILLED",
        "origQty": "0.3",
        "executedQty": "0.3",
        "cummulativeQuoteQty": "18000.3",
        "price": "60001.00",
        "transactTime": 1711000000000,
    }

    PRECISION_RAW_OKX = {
        "ordId": "okx-dec-1",
        "clOrdId": "c-okx-dec",
        "instId": "BTC-USDT",
        "side": "buy",
        "ordType": "limit",
        "state": "filled",
        "sz": "0.3",
        "fillSz": "0.3",
        "px": "60001.00",
        "avgPx": "60001.0",
    }

    def test_binance_roundtrip_is_lossless(self) -> None:
        resp = BinanceTradingAdapter(AsyncMock())._parse_order_response(dict(self.PRECISION_RAW_BINANCE))

        internal_decimal = {
            "quantity": isinstance(resp.quantity, Decimal) and resp.quantity == Decimal("0.3"),
            "filled": resp.filled_quantity == Decimal("0.3"),
            "avg": resp.avg_fill_price == Decimal("60001.0"),
        }
        assert all(internal_decimal.values()), f"internal: {internal_decimal}"

        as_json = resp.model_dump(mode="json")
        boundary_float = {
            "quantity-float": isinstance(as_json["quantity"], float),
            "avg-float": isinstance(as_json["avg_fill_price"], float),
            "quantity-no-drift": Decimal(str(as_json["quantity"])) == resp.quantity,
            "avg-no-drift": Decimal(str(as_json["avg_fill_price"])) == resp.avg_fill_price,
        }
        assert all(boundary_float.values()), f"boundary: {boundary_float}"

    def test_okx_roundtrip_is_lossless(self) -> None:
        resp = OKXTradingAdapter(AsyncMock())._parse_single_order(dict(self.PRECISION_RAW_OKX))

        as_json = resp.model_dump(mode="json")
        checks = {
            "internal-qty": isinstance(resp.quantity, Decimal) and resp.quantity == Decimal("0.3"),
            "internal-filled": resp.filled_quantity == Decimal("0.3"),
            "internal-avg": resp.avg_fill_price == Decimal("60001.0"),
            "json-float": isinstance(as_json["quantity"], float) and isinstance(as_json["avg_fill_price"], float),
            "no-drift": Decimal(str(as_json["quantity"])) == resp.quantity
            and Decimal(str(as_json["avg_fill_price"])) == resp.avg_fill_price,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_futures_balances_keep_exact_decimals(self) -> None:
        client = AsyncMock()
        client.get_balances.return_value = [
            {"asset": "USDT", "availableBalance": "100.3", "initialMargin": "0.2", "balance": "100.5"},
        ]

        balances = asyncio.run(BinanceFuturesTradingAdapter(client).get_balances())
        b = balances[0]

        checks = {
            "single": len(balances) == 1,
            "free-type": isinstance(b.free, Decimal) and b.free == Decimal("100.3"),
            "locked": b.locked == Decimal("0.2"),
            "total": b.total == Decimal("100.5"),
            "json-float": isinstance(b.model_dump(mode="json")["free"], float),
            "json-roundtrip": Decimal(str(b.model_dump(mode="json")["free"])) == b.free,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_source_has_no_decimal_times_float_mixing(self) -> None:
        """Static regression guard against Decimal*float TypeError reintroduction."""
        import selene_space_exchange.trading as trading_mod

        source = open(trading_mod.__file__, encoding="utf-8").read()
        mixing = re.compile(
            r"Decimal\([^)]*\)\s*\*\s*(?!Decimal\()(?!float\()([a-zA-Z_]\w*|[0-9]+\.[0-9]+)"
        )
        offenders = [
            m.group(0)
            for m in mixing.finditer(source)
            if re.match(r"^[0-9]+\.[0-9]+$", m.group(1))
        ]
        assert offenders == [], f"Decimal*float mixing found: {offenders}"
