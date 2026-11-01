"""Polymarket CLOB REST client: market/book/price parsing over mocked HTTP."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from selene_space_exchange.exchanges.polymarket.client import PolymarketClient
from selene_space_exchange.exchanges.polymarket.models import (
    PolymarketMarket,
    PolymarketOrderBook,
)

MID = "0.65"


def _reply(data, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    resp.raise_for_status = MagicMock()
    return resp


def _client_serving(payload) -> PolymarketClient:
    client = PolymarketClient()
    client._http = MagicMock()
    client._http.get = AsyncMock(return_value=_reply(payload))
    return client


def _gamma_market() -> dict:
    """Gamma API market listing, assembled field by field."""
    body = {
        "condition_id": "0xabc",
        "question_id": "0xdef",
        "question": "Will BTC reach $100k?",
        "description": "Test market",
        "market_slug": "btc-100k",
        "active": True,
        "closed": False,
    }
    outcomes = [("Yes", "0x111", 0.65), ("No", "0x222", 1 - 0.65)]
    body["tokens"] = [
        {"token_id": tid, "outcome": label, "price": price} for label, tid, price in outcomes
    ]
    stats = {"volume": 500000, "volume_num_24hr": 12000, "liquidity": 80000}
    body.update(stats)
    return body


def _level(price: str, size: str) -> dict:
    return {"price": price, "size": size}


def _clob_book() -> dict:
    """CLOB order book for token 0x111: bids below mid, asks above."""
    bid_side = [_level("0.64", "100"), _level("0.63", "200")]
    ask_side = [_level("0.66", "150"), _level("0.67", "250")]
    return {
        "market": "0xabc",
        "asset_id": "0x111",
        "timestamp": "1700000000",
        "bids": bid_side,
        "asks": ask_side,
        "last_trade_price": MID,
        "tick_size": "0.01",
        "min_order_size": "5",
    }


class TestListMarkets:
    @pytest.mark.asyncio
    async def test_gamma_payload_maps_to_model(self) -> None:
        markets = await _client_serving([_gamma_market()]).list_markets(limit=1)

        assert len(markets) == 1
        m = markets[0]
        checks = {
            "model": isinstance(m, PolymarketMarket),
            "question": m.question == "Will BTC reach $100k?",
            "two-tokens": len(m.tokens) == 2,
            "first-outcome": m.tokens[0].outcome == "Yes",
            "first-price": m.tokens[0].price == 0.65,
            "volume": m.volume == 500000,
            "volume-24h": m.volume_24h == 12000,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestOrderBook:
    @pytest.mark.asyncio
    async def test_clob_book_levels_stay_raw_strings(self) -> None:
        book = await _client_serving(_clob_book()).get_orderbook("0x111")

        checks = {
            "model": isinstance(book, PolymarketOrderBook),
            "asset": book.asset_id == "0x111",
            "bid-count": len(book.bids) == 2,
            "ask-count": len(book.asks) == 2,
            "best-bid-string": book.bids[0]["price"] == "0.64",
            "last-trade": book.last_trade_price == MID,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestPriceEndpoints:
    @pytest.mark.asyncio
    async def test_midpoint_string_becomes_float(self) -> None:
        assert await _client_serving({"mid": MID}).get_midpoint("0x111") == float(MID)

    @pytest.mark.asyncio
    async def test_side_quote_parsed(self) -> None:
        quote = await _client_serving({"price": "0.64"}).get_price("0x111", side="BUY")
        assert quote == 0.64

    @pytest.mark.asyncio
    async def test_last_trade_carries_side(self) -> None:
        result = await _client_serving({"price": MID, "side": "BUY"}).get_last_trade_price("0x111")
        assert (result.price, result.side) == (0.65, "BUY")


class TestServerTime:
    @pytest.mark.asyncio
    async def test_plain_text_epoch_is_int_parsed(self) -> None:
        client = PolymarketClient()
        resp = MagicMock()
        resp.text = "1700000000"
        resp.raise_for_status = MagicMock()
        client._http = MagicMock()
        client._http.get = AsyncMock(return_value=resp)

        assert await client.get_server_time() == 1700000000
