"""H19 integration test: exchange adapter Decimal conversion end-to-end.

Verifies that Binance/OKX/Polymarket adapters correctly convert float JSON
input to internal Decimal, and that OrderResponse/BalanceInfo fields are
Decimal instances with clean precision (no float pollution).
"""

from decimal import Decimal


from selene_space_exchange.trading import (
    BalanceInfo,
    OrderResponse,
    _parse_binance_order_response,
    _BINANCE_FUTURES_STATUS_MAP,
    _BINANCE_FUTURES_TYPE_MAP,
)


class TestExchangeAdapterDecimalConversion:
    """H19: adapter float JSON input → internal Decimal, precision preserved."""

    def test_binance_order_response_decimal_fields(self):
        """Binance adapter: float JSON → Decimal OrderResponse fields."""
        raw = {
            "orderId": "12345",
            "clientOrderId": "test-id",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "status": "NEW",
            "origQty": "0.1",
            "executedQty": "0.2",
            "cummulativeQuoteQty": "0.3",
            "price": "67000.00",
            "transactTime": 1711000000000,
        }
        resp = _parse_binance_order_response(
            raw,
            exchange="binance",
            status_map=_BINANCE_FUTURES_STATUS_MAP,
            type_map=_BINANCE_FUTURES_TYPE_MAP,
            quote_field="cummulativeQuoteQty",
            timestamp_fields=("transactTime", "time"),
        )
        assert isinstance(resp.quantity, Decimal), f"quantity: {type(resp.quantity).__name__}"
        assert isinstance(resp.filled_quantity, Decimal), f"filled_quantity: {type(resp.filled_quantity).__name__}"
        assert isinstance(resp.price, Decimal), f"price: {type(resp.price).__name__}"
        # Precision preserved (Decimal(str(...)) not Decimal(float))
        assert resp.quantity == Decimal("0.1")
        assert resp.filled_quantity == Decimal("0.2")
        assert resp.price == Decimal("67000.00")

    def test_binance_order_response_float_input_no_pollution(self):
        """Float input (not str) still coerced via Decimal(str()) — no pollution."""
        raw = {
            "orderId": "1",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "status": "FILLED",
            "origQty": 0.1,  # float, not str
            "executedQty": 0.2,
            "cumQuote": 0.3,
            "price": "67000",
            "time": 1,
        }
        resp = _parse_binance_order_response(
            raw,
            exchange="binance",
            status_map=_BINANCE_FUTURES_STATUS_MAP,
            type_map=_BINANCE_FUTURES_TYPE_MAP,
            quote_field="cumQuote",
            timestamp_fields=("time",),
        )
        # Decimal(str(0.1)) == Decimal('0.1') — clean, not Decimal(0.1) polluted
        assert resp.quantity == Decimal("0.1")
        assert resp.quantity != Decimal(0.1)  # polluted form would not match
        assert (resp.quantity + resp.filled_quantity) == Decimal("0.3")

    def test_order_response_json_serializes_to_float(self):
        """model_dump(mode='json') emits float, not str, for Decimal fields."""
        resp = OrderResponse(
            exchange="binance",
            order_id="1",
            symbol="BTCUSDT",
            side="buy",
            order_type="limit",
            status="accepted",
            quantity=Decimal("0.1"),
            filled_quantity=Decimal("0.2"),
            price=Decimal("67000"),
        )
        d = resp.model_dump(mode="json")
        assert isinstance(d["quantity"], float), f"quantity: {type(d['quantity']).__name__}"
        assert isinstance(d["filled_quantity"], float)
        assert isinstance(d["price"], float)
        assert d["quantity"] == 0.1

    def test_balance_info_decimal_fields(self):
        """BalanceInfo: float input → Decimal fields, precision preserved."""
        b = BalanceInfo(asset="BTC", free=0.1, locked=0.2, total=0.3)
        assert isinstance(b.free, Decimal)
        assert isinstance(b.locked, Decimal)
        assert isinstance(b.total, Decimal)
        assert (b.free + b.locked) == Decimal("0.3")
        assert b.free == Decimal("0.1")
        assert b.free != Decimal(0.1)  # polluted form would not match

    def test_balance_info_json_serializes_to_float(self):
        b = BalanceInfo(asset="BTC", free=0.1, locked=0.2, total=0.3)
        d = b.model_dump(mode="json")
        assert isinstance(d["free"], float)
        assert isinstance(d["locked"], float)
        assert isinstance(d["total"], float)
        assert d["free"] == 0.1
