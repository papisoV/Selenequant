"""Tests for PolymarketNormalizer — user channel order/trade event mapping.

Verifies graceful defaults and field-by-field mapping using the same payload
shape as ``test_polymarket_ws_client.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from selene_space_exchange.exchanges.polymarket.normalizer import PolymarketNormalizer
from selene_space_types.trading import OrderSide, PolymarketOrderEventType


@pytest.fixture
def normalizer() -> PolymarketNormalizer:
    return PolymarketNormalizer()


class TestNormalizeOrderEvent:
    def test_normalize_order_placement(self, normalizer: PolymarketNormalizer) -> None:
        """Full PLACEMENT event with all fields populated."""
        data = {
            "event_type": "order",
            "id": "order-001",
            "asset_id": "abc123",
            "side": "BUY",
            "price": "0.55",
            "original_size": "10",
            "size_matched": "0",
            "type": "PLACEMENT",
            "market": "0xcondition",
            "owner": "0xowner",
            "timestamp": 1711000000000,
        }
        result = normalizer.normalize_order_event(data)

        assert result.exchange == "polymarket"
        assert result.order_id == "order-001"
        assert result.asset_id == "abc123"
        assert result.market == "0xcondition"
        assert result.owner == "0xowner"
        assert result.side == OrderSide.BUY
        assert result.price == Decimal("0.55")
        assert result.original_size == Decimal("10")
        assert result.size_matched == Decimal("0")
        assert result.event_type == PolymarketOrderEventType.PLACEMENT
        assert result.timestamp == 1711000000000
        assert result.raw == data

    def test_normalize_order_cancellation(self, normalizer: PolymarketNormalizer) -> None:
        """CANCELLATION event type maps correctly."""
        data = {
            "event_type": "order",
            "id": "order-002",
            "asset_id": "xyz789",
            "side": "SELL",
            "price": "0.30",
            "original_size": "5",
            "size_matched": "2",
            "type": "CANCELLATION",
            "market": "0xcond2",
            "owner": "0xowner2",
        }
        result = normalizer.normalize_order_event(data)

        assert result.side == OrderSide.SELL
        assert result.event_type == PolymarketOrderEventType.CANCELLATION
        assert result.size_matched == 2.0

    def test_normalize_order_update(self, normalizer: PolymarketNormalizer) -> None:
        """UPDATE event type maps correctly."""
        data = {
            "event_type": "order",
            "id": "order-003",
            "asset_id": "t1",
            "side": "BUY",
            "price": "0.40",
            "original_size": "100",
            "size_matched": "50",
            "type": "UPDATE",
            "market": "0xm",
        }
        result = normalizer.normalize_order_event(data)
        assert result.event_type == PolymarketOrderEventType.UPDATE

    def test_normalize_unknown_event_type_defaults_to_placement(
        self, normalizer: PolymarketNormalizer
    ) -> None:
        """Unknown type string → PLACEMENT (graceful, no exception)."""
        data = {
            "event_type": "order",
            "id": "order-x",
            "asset_id": "a",
            "side": "BUY",
            "price": "0.5",
            "original_size": "1",
            "size_matched": "0",
            "type": "UNKNOWN_EVENT",
            "market": "0xm",
        }
        result = normalizer.normalize_order_event(data)
        assert result.event_type == PolymarketOrderEventType.PLACEMENT

    def test_normalize_missing_price_defaults_to_zero(
        self, normalizer: PolymarketNormalizer
    ) -> None:
        """Missing price → 0.0 (no exception)."""
        data = {
            "event_type": "order",
            "id": "order-y",
            "asset_id": "a",
            "side": "BUY",
            "original_size": "10",
            "type": "PLACEMENT",
            "market": "0xm",
        }
        result = normalizer.normalize_order_event(data)
        assert float(result.price) == pytest.approx(0.0)
        assert result.size_matched == 0.0

    def test_normalize_missing_side_defaults_to_buy(
        self, normalizer: PolymarketNormalizer
    ) -> None:
        data = {
            "id": "z",
            "asset_id": "a",
            "price": "0.5",
            "original_size": "1",
            "type": "PLACEMENT",
            "market": "m",
        }
        result = normalizer.normalize_order_event(data)
        assert result.side == OrderSide.BUY

    def test_normalize_falls_back_to_ts_when_timestamp_missing(
        self, normalizer: PolymarketNormalizer
    ) -> None:
        """When timestamp absent, fall back to ts key (Polymarket sometimes uses ts)."""
        data = {
            "id": "z",
            "asset_id": "a",
            "side": "BUY",
            "price": "0.5",
            "original_size": "1",
            "type": "PLACEMENT",
            "market": "m",
            "ts": 1711000000500,
        }
        result = normalizer.normalize_order_event(data)
        assert result.timestamp == 1711000000500


class TestNormalizeTradeEvent:
    def test_normalize_trade_matched(self, normalizer: PolymarketNormalizer) -> None:
        """Full MATCHED trade event with all fields."""
        data = {
            "event_type": "trade",
            "id": "trade-001",
            "asset_id": "abc123",
            "side": "BUY",
            "price": "0.55",
            "size": "10",
            "status": "MATCHED",
            "market": "0xcondition",
            "owner": "0xowner",
            "timestamp": 1711000000000,
        }
        result = normalizer.normalize_trade_event(data)

        assert result.exchange == "polymarket"
        assert result.trade_id == "trade-001"
        assert result.asset_id == "abc123"
        assert result.market == "0xcondition"
        assert result.owner == "0xowner"
        assert result.side == OrderSide.BUY
        assert result.price == Decimal("0.55")
        assert result.size == Decimal("10")
        assert result.status == "MATCHED"
        assert result.timestamp == 1711000000000
        assert result.raw == data

    def test_normalize_trade_failed_status_preserved(
        self, normalizer: PolymarketNormalizer
    ) -> None:
        """FAILED status passes through verbatim (status is a free-form string)."""
        data = {
            "event_type": "trade",
            "id": "t2",
            "asset_id": "a",
            "side": "SELL",
            "price": "0.20",
            "size": "5",
            "status": "FAILED",
            "market": "0xm",
        }
        result = normalizer.normalize_trade_event(data)
        assert result.status == "FAILED"
        assert result.side == OrderSide.SELL

    def test_normalize_trade_missing_status_defaults_to_matched(
        self, normalizer: PolymarketNormalizer
    ) -> None:
        """Missing status → MATCHED (most common default)."""
        data = {
            "event_type": "trade",
            "id": "t3",
            "asset_id": "a",
            "side": "BUY",
            "price": "0.50",
            "size": "1",
            "market": "m",
        }
        result = normalizer.normalize_trade_event(data)
        assert result.status == "MATCHED"
