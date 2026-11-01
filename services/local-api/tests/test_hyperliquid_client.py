"""Tests for HyperliquidClient (public info API, read-only).

The client fills a real gap: ``app/api/v1/hyperliquid.py`` imported
``app.clients.hyperliquid`` but the module never existed, so every endpoint
returned 503 on every deployment. These tests pin the three implemented
methods (and the deliberate refusal of the missing fourth), all offline via a
fake gateway.

Reverse-verify: point get_portfolio_history at the raw upstream list shape
(skip the dict conversion) and test_portfolio_history_is_keyed_by_bucket goes
red.
"""

from __future__ import annotations

import asyncio

import pytest

from app.clients import hyperliquid as hl

_ADDR = "0x" + "a" * 40


class _Resp:
    def __init__(self, status_code: int, json: object | None = None):
        self.status_code = status_code
        self._json = json

    def json(self) -> object | None:
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeGateway:
    """Records POSTs and replays canned bodies per request type."""

    def __init__(self, bodies: dict[str, object] | None = None) -> None:
        self.bodies = bodies or {}
        self.calls: list[dict] = []
        self.is_closed = False

    async def post(self, url: str, *, json: dict | None = None, headers=None) -> _Resp:
        self.calls.append(dict(json or {}))
        kind = (json or {}).get("type", "")
        if kind not in self.bodies:
            # Mirrors upstream: an unknown type 422s.
            return _Resp(422, {"error": "Failed to deserialize the JSON body"})
        return _Resp(200, self.bodies[kind])

    async def aclose(self) -> None:
        self.is_closed = True


def _run(coro):
    return asyncio.run(coro)


def _client(bodies: dict[str, object]) -> tuple[hl.HyperliquidClient, _FakeGateway]:
    fake = _FakeGateway(bodies)
    return hl.HyperliquidClient(client=fake), fake  # type: ignore[arg-type]


# -- get_user_state ---------------------------------------------------------


def test_user_state_merges_open_orders_from_their_own_endpoint():
    """clearinghouseState carries no orders; the client must fetch them."""
    state = {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}
    orders = [{"oid": 1, "coin": "BTC"}]
    client, fake = _client({"clearinghouseState": state, "frontendOpenOrders": orders})

    got = _run(client.get_user_state(_ADDR))

    assert got["marginSummary"]["accountValue"] == "1000"
    assert got["openOrders"] == orders
    assert [c["type"] for c in fake.calls] == [
        "clearinghouseState",
        "frontendOpenOrders",
    ]


def test_user_state_normalizes_address_case():
    client, fake = _client({"clearinghouseState": {}, "frontendOpenOrders": []})
    _run(client.get_user_state("0x" + "A" * 40))
    assert fake.calls[0]["user"] == "0x" + "a" * 40


def test_user_state_missing_orders_key_defaults_to_empty_list():
    """A gateway that answers only the state call must not crash the merge."""
    client, _ = _client({"clearinghouseState": {"assetPositions": []}})
    got = _run(client.get_user_state(_ADDR))
    assert got["openOrders"] == []


# -- get_user_fills ---------------------------------------------------------


def test_user_fills_caps_to_limit():
    fills = [{"tid": i, "closedPnl": "1.0", "fee": "0.1"} for i in range(2000)]
    client, _ = _client({"userFills": fills})

    got = _run(client.get_user_fills(_ADDR, limit=500))

    assert len(got) == 500
    assert got[0]["tid"] == 0  # newest-first order preserved


def test_user_fills_empty_limit_means_uncapped():
    fills = [{"tid": i} for i in range(5)]
    client, _ = _client({"userFills": fills})
    assert len(_run(client.get_user_fills(_ADDR, limit=0))) == 5


def test_user_fills_rejects_non_list_body():
    client, _ = _client({"userFills": {"unexpected": "object"}})
    with pytest.raises(hl.HyperliquidError, match="non-list"):
        _run(client.get_user_fills(_ADDR))


# -- get_portfolio_history --------------------------------------------------


def test_portfolio_history_is_keyed_by_bucket():
    """Upstream sends [[name, payload], ...]; the API layer indexes by name."""
    raw = [["day", {"accountValueHistory": [[1, "10"]]}], ["week", {"x": 1}]]
    client, _ = _client({"portfolio": raw})

    got = _run(client.get_portfolio_history(_ADDR))

    assert isinstance(got, dict)
    assert got["day"]["accountValueHistory"] == [[1, "10"]]
    assert got["week"] == {"x": 1}


def test_portfolio_history_passes_through_dict_body():
    already = {"day": {"accountValueHistory": []}}
    client, _ = _client({"portfolio": already})
    assert _run(client.get_portfolio_history(_ADDR)) == already


# -- get_leaderboard (deliberately unavailable) -----------------------------


def test_leaderboard_refuses_with_the_reason():
    """No public ranking endpoint exists; refusing beats returning []."""
    client, fake = _client({})

    with pytest.raises(hl.HyperliquidError, match="no trader leaderboard"):
        _run(client.get_leaderboard(window="7d"))

    assert fake.calls == []  # never even hits the network


# -- input validation & transport errors ------------------------------------


@pytest.mark.parametrize("bad", ["", "0x123", "not-an-address", "b" * 42])
def test_malformed_address_is_rejected_before_the_call(bad: str):
    client, fake = _client({"clearinghouseState": {}})
    with pytest.raises(hl.HyperliquidError, match="invalid Hyperliquid address"):
        _run(client.get_user_state(bad))
    assert fake.calls == []


def test_http_error_becomes_hyperliquid_error():
    client, _ = _client({})  # unknown type -> 422 from the fake
    with pytest.raises(hl.HyperliquidError, match="HTTP 422"):
        _run(client.get_user_fills(_ADDR))
