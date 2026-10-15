"""P2-3: SmartOrderRouter injection into LiveExecutionEngine.

Verifies that LiveExecutionEngine can be configured with a router +
quote_provider + venues, and that ``place_order(metadata={"route": True})``
selects the best venue and submits through that venue's client.

Covers:
- Routing disabled by default (no router): uses default client.
- Routing requested but router not wired: falls back to default client.
- Routing with router + venues: best-quote venue's client is used.
- Routing still passes through kill_switch + risk_engine gates.
- Routing fallback when venue not registered in ``venues``.
- Routing fallback when quote_provider returns empty list.
- Journal records routed_exchange in raw_request.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_exchange.routing import ExchangeInfo, SmartOrderRouter
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


class _MockJournal:
    def __init__(self) -> None:
        self.intents: list[dict] = []
        self.failed: list[tuple[str, str]] = []

    def append_intent(self, **kwargs) -> None:
        self.intents.append(kwargs)

    def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        self.failed.append((client_order_id, reason))

    def mark_submitted(self, *a, **k) -> None:
        pass

    def mark_confirmed(self, *a, **k) -> None:
        pass


def _make_client(exchange_name: str, *, order_id: str = "ex-1") -> SimpleNamespace:
    """Build a mock TradingClient with a distinct exchange_name."""
    return SimpleNamespace(
        exchange_name=exchange_name,
        place_order=AsyncMock(return_value=SimpleNamespace(
            order_id=order_id,
            status=OrderStatus.ACCEPTED,
            filled_quantity=0.0,
            avg_fill_price=0.0,
        )),
        cancel_order=AsyncMock(),
    )


def _make_engine(
    *,
    risk_engine=None,
    default_client: SimpleNamespace | None = None,
    router: SmartOrderRouter | None = None,
    quote_provider=None,
    venues: dict | None = None,
) -> tuple[LiveExecutionEngine, _MockJournal, SimpleNamespace]:
    journal = _MockJournal()
    client = default_client or _make_client("default-exchange")
    engine = LiveExecutionEngine(
        trading_client=client,
        journal=journal,
        state_repo=None,
        risk_engine=risk_engine,
        router=router,
        quote_provider=quote_provider,
        venues=venues,
    )
    return engine, journal, client


def _quotes_binance_better() -> list[ExchangeInfo]:
    """Two venue quotes where Binance has lower total cost."""
    return [
        ExchangeInfo(
            exchange_id="binance",
            fee_rate=0.001,
            estimated_slippage_bps=1.0,
            available_liquidity=1_000_000.0,
            latency_ms=50,
        ),
        ExchangeInfo(
            exchange_id="okx",
            fee_rate=0.002,
            estimated_slippage_bps=3.0,
            available_liquidity=500_000.0,
            latency_ms=80,
        ),
    ]


def _quotes_okx_better() -> list[ExchangeInfo]:
    """Two venue quotes where OKX has lower total cost."""
    return [
        ExchangeInfo(
            exchange_id="binance",
            fee_rate=0.002,
            estimated_slippage_bps=5.0,
            available_liquidity=500_000.0,
            latency_ms=100,
        ),
        ExchangeInfo(
            exchange_id="okx",
            fee_rate=0.001,
            estimated_slippage_bps=1.0,
            available_liquidity=1_000_000.0,
            latency_ms=50,
        ),
    ]


@pytest.mark.asyncio
async def test_routing_disabled_by_default_uses_default_client() -> None:
    """No router wired → metadata={"route": True} still uses default client."""
    engine, _journal, default_client = _make_engine()

    order = await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},  # requested, but no router → fallback
    )

    default_client.place_order.assert_awaited_once()
    assert order.symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_routing_with_router_uses_best_venue() -> None:
    """With router + quote_provider + venues, best-quote venue's client is used.

    Quotes favor Binance (lower fee + slippage) → router selects "binance"
    → engine submits via venues["binance"] (not the default client).
    """
    binance_client = _make_client("binance", order_id="bin-1")
    okx_client = _make_client("okx", order_id="okx-1")
    default_client = _make_client("default-exchange")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return _quotes_binance_better()

    engine, _journal, _default = _make_engine(
        default_client=default_client,
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"binance": binance_client, "okx": okx_client},
    )

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )

    # Binance client was used; default + okx were not.
    binance_client.place_order.assert_awaited_once()
    okx_client.place_order.assert_not_awaited()
    default_client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_routing_selects_okx_when_okx_quote_better() -> None:
    """Router selects OKX when OKX has the better quote."""
    binance_client = _make_client("binance", order_id="bin-1")
    okx_client = _make_client("okx", order_id="okx-1")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return _quotes_okx_better()

    engine, _journal, _default = _make_engine(
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"binance": binance_client, "okx": okx_client},
    )

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )

    okx_client.place_order.assert_awaited_once()
    binance_client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_routing_falls_back_when_venue_not_registered() -> None:
    """If selected venue is not in venues dict, fall back to default client."""
    default_client = _make_client("default-exchange")
    binance_client = _make_client("binance")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return _quotes_binance_better()  # selects binance

    engine, _journal, _default = _make_engine(
        default_client=default_client,
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"okx": binance_client},  # binance NOT registered
    )

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )

    # Router selected binance but it's not registered → default client used.
    default_client.place_order.assert_awaited_once()
    binance_client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_routing_falls_back_when_no_quotes() -> None:
    """Empty quote list → fall back to default client."""
    default_client = _make_client("default-exchange")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return []

    engine, _journal, _default = _make_engine(
        default_client=default_client,
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"binance": _make_client("binance")},
    )

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )

    default_client.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_routing_records_routed_exchange_in_journal() -> None:
    """Journal intent raw_request must include routed_exchange for audit trail."""
    binance_client = _make_client("binance", order_id="bin-1")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return _quotes_binance_better()

    engine, journal, _default = _make_engine(
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"binance": binance_client, "okx": _make_client("okx")},
    )

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )

    # Journal intent was written with routed_exchange="binance"
    intent = journal.intents[-1]
    assert intent["raw_request"]["routed_exchange"] == "binance"


@pytest.mark.asyncio
async def test_routing_still_passes_kill_switch_gate() -> None:
    """Routed orders still go through kill_switch — must be blocked when active."""
    binance_client = _make_client("binance", order_id="bin-1")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return _quotes_binance_better()

    kill_switch = SimpleNamespace(
        is_active=True,
        reason="test kill",
        is_active_for=lambda **kw: True,
    )
    engine, journal, _default = _make_engine(
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"binance": binance_client},
    )
    engine.set_kill_switch(kill_switch)

    from selene_space_exchange.exceptions import PreTradeRiskBlockError

    with pytest.raises(PreTradeRiskBlockError, match="kill switch"):
        await engine.place_order(
            account_id="acct-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            price=50_000.0,
            metadata={"route": True},
        )

    # No REST call was made — blocked at gate.
    binance_client.place_order.assert_not_awaited()
    # Journal recorded the blocked attempt with routed_exchange.
    intent = journal.intents[-1]
    assert intent["raw_request"]["routed_exchange"] == "binance"
    assert intent["raw_request"]["rejected_by"] == "kill_switch"


@pytest.mark.asyncio
async def test_set_router_injection_after_construction() -> None:
    """set_router() injects router/quote_provider/venues post-construction."""
    binance_client = _make_client("binance", order_id="bin-1")
    default_client = _make_client("default-exchange")

    async def quote_provider(symbol: str, side: str, quantity: float) -> list[ExchangeInfo]:
        return _quotes_binance_better()

    engine, _journal, _default = _make_engine(default_client=default_client)
    # Initially no router → routing requested but falls back.
    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )
    default_client.place_order.assert_awaited_once()
    default_client.place_order.reset_mock()

    # Inject router via setter.
    engine.set_router(
        router=SmartOrderRouter(strategy="best_price"),
        quote_provider=quote_provider,
        venues={"binance": binance_client},
    )

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        metadata={"route": True},
    )
    binance_client.place_order.assert_awaited_once()
    default_client.place_order.assert_not_awaited()
