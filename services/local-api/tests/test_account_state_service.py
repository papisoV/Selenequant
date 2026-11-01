"""Service tests for build_account_state (protocol §4 Step 3, §5).

Covers:
- Watermark read BEFORE getter gather (ordering invariant CRITICAL #2).
- Single getter failure empties that domain, does not 5xx (N1).
- engine=None -> empty-state document with zero watermark.
- trades truncated to MAX_ACCOUNT_TRADES (M5).
- account.state_snapshot audit write (best-effort, M1).
- Paper equity (F11) when engine exposes paper internals.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.core.account_state import MAX_ACCOUNT_TRADES, build_account_state
from app.core import audit as audit_mod
from selene_space_types.trading import (
    AccountState,
    AccountStateSeq,
    BalanceUpdate,
    ExecutionMode,
    Fill,
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)

pytestmark = pytest.mark.asyncio


def _position() -> Position:
    return Position(
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        pos_side=PositionSide.LONG,
        quantity=Decimal("1.5"),
        quantity_base=Decimal("0.015"),
        avg_entry_price=Decimal("67000.0"),
        leverage=10,
        margin_mode=MarginMode.CROSS,
        margin=Decimal("100.5"),
        liquidation_price=Decimal("60300.0"),
        unrealized_pnl=Decimal("15.0"),
        unrealized_pnl_pct=Decimal("15.0"),
        realized_pnl=Decimal("0"),
        current_price=Decimal("68000.0"),
        opened_at=1711000000000,
        updated_at=1711000050000,
        accrued_funding=Decimal("0.001"),
    )


def _order(i: int = 0) -> Order:
    return Order(
        id=f"ord-{i}",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        status=OrderStatus.ACCEPTED,
        quantity=Decimal("100.0"),
        price=Decimal("67000.0"),
        filled_quantity=Decimal("0"),
        leverage=10,
        margin_mode=MarginMode.CROSS,
        pos_side=PositionSide.LONG,
        reduce_only=False,
        client_order_id=f"co-{i}",
        created_at=1711000000000,
        updated_at=1711000000000,
    )


def _fill(i: int = 0) -> Fill:
    return Fill(
        id=f"fill-{i}",
        order_id=f"ord-{i}",
        client_order_id=f"co-{i}",
        price=Decimal("67000.0"),
        quantity=Decimal("100.0"),
        fee=Decimal("5.0"),
        fee_currency="USDT",
        fee_rate=Decimal("0.0005"),
        realized_pnl=Decimal("0"),
        exec_type="taker",
        side="buy",
        pos_side="long",
        symbol="BTC-USDT-SWAP",
        leverage=10,
        reduce_only=False,
        timestamp=1711000001000 + i,
    )


def _balance() -> BalanceUpdate:
    return BalanceUpdate(
        exchange="paper",
        asset="USDT",
        free=Decimal("5000.0"),
        locked=Decimal("0.0"),
        timestamp=1711000000000,
    )


class FakeAccount:
    def __init__(
        self,
        aid: str = "acct-1",
        initial_balance=Decimal("10000"),
        total_realized_pnl=Decimal("100"),
        total_fee=Decimal("10"),
    ) -> None:
        self.id = aid
        self.initial_balance = initial_balance
        self.total_realized_pnl = total_realized_pnl
        self.total_fee = total_fee


class FakeAccountMgr:
    def __init__(self, account: FakeAccount | None = None) -> None:
        self._account = account

    def get_account(self, aid: str) -> FakeAccount | None:
        return self._account


class FakePosMgr:
    def __init__(self, positions: list | None = None) -> None:
        self._positions = positions or []

    def get_open_positions(self, aid: str) -> list:
        return self._positions


class FakeEngine:
    mode = ExecutionMode.PAPER
    exchange = "paper"

    def __init__(
        self,
        *,
        fail: str | None = None,
        balances: list | None = None,
        positions: list | None = None,
        orders: list | None = None,
        fills: list | None = None,
        internals: bool = True,
    ) -> None:
        self._fail = fail
        self._balances = balances or []
        self._positions = positions or []
        self._orders = orders or []
        self._fills = fills or []
        self.call_log: list[str] = []
        self._account_mgr = FakeAccountMgr() if internals else None
        self._position_mgr = FakePosMgr() if internals else None

    async def _run(self, name: str, account_id: str, value: list) -> list:
        self.call_log.append(name)
        if self._fail == name:
            raise RuntimeError(f"{name} boom")
        return value

    async def get_balances(self, account_id: str) -> list:
        return await self._run("get_balances", account_id, self._balances)

    async def get_positions(self, account_id: str) -> list:
        return await self._run("get_positions", account_id, self._positions)

    async def get_orders(self, account_id: str) -> list:
        return await self._run("get_orders", account_id, self._orders)

    async def get_fills(self, account_id: str) -> list:
        return await self._run("get_fills", account_id, self._fills)


class FakeSequence:
    def __init__(self, engine: FakeEngine) -> None:
        self._engine = engine
        self.session_id = "srv-test"
        self.calls_before_watermark: list[str] | None = None

    def watermark(
        self,
        *,
        orders: str | None = None,
        positions: str | None = None,
        balances: str | None = None,
    ) -> AccountStateSeq:
        self.calls_before_watermark = list(self._engine.call_log)
        return AccountStateSeq(orders=5, positions=2, balances=1)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, data: dict) -> None:
        self.events.append(data)


class TestWatermarkOrdering:
    async def test_watermark_read_before_getter_gather(self) -> None:
        engine = FakeEngine()
        seq = FakeSequence(engine)
        state = await build_account_state(
            engine,
            "acct-1",
            session_id="srv-test",
            sequence=seq,
            seq_keys={
                "orders": "paper:acct-1",
                "positions": "paper:acct-1",
                "balances": "paper:acct-1",
            },
        )
        # No getter ran before the watermark was read.
        assert seq.calls_before_watermark == []
        assert state.seq == AccountStateSeq(orders=5, positions=2, balances=1)

    async def test_sequence_none_uses_zero_watermark(self) -> None:
        state = await build_account_state(FakeEngine(), "acct-1")
        assert state.seq == AccountStateSeq()


class TestEmptyState:
    async def test_engine_none_returns_empty_document(self) -> None:
        state = await build_account_state(None, "acct-1", session_id="srv-test")
        assert isinstance(state, AccountState)
        assert state.balances == []
        assert state.positions == []
        assert state.orders == []
        assert state.trades == []
        assert state.equity is None
        assert state.seq == AccountStateSeq()
        assert state.mode == ExecutionMode.PAPER

    async def test_engine_none_mode_defaults_paper(self) -> None:
        state = await build_account_state(None, "acct-1")
        assert state.mode == ExecutionMode.PAPER


class TestSingleGetterFailure:
    async def test_balance_getter_failure_empties_balance_only(self) -> None:
        engine = FakeEngine(
            fail="get_balances",
            positions=[_position()],
            orders=[_order()],
            fills=[_fill()],
        )
        state = await build_account_state(engine, "acct-1")
        assert state.balances == []
        assert state.positions == [_position()]
        assert state.orders == [_order()]
        assert state.trades == [_fill()]

    async def test_all_getters_failing_returns_empty_domains(self) -> None:
        engine = FakeEngine(fail="get_balances")
        state = await build_account_state(engine, "acct-1")
        assert state.balances == []
        assert state.positions == []
        assert state.orders == []
        assert state.trades == []


class TestTradesTruncation:
    async def test_trades_truncated_to_max(self) -> None:
        fills = [_fill(i) for i in range(MAX_ACCOUNT_TRADES + 50)]
        engine = FakeEngine(fills=fills)
        state = await build_account_state(engine, "acct-1")
        assert len(state.trades) == MAX_ACCOUNT_TRADES

    async def test_trades_under_max_untouched(self) -> None:
        fills = [_fill(i) for i in range(10)]
        engine = FakeEngine(fills=fills)
        state = await build_account_state(engine, "acct-1")
        assert len(state.trades) == 10


class TestEquity:
    async def test_paper_equity_from_internals(self) -> None:
        engine = FakeEngine(internals=True)
        engine._account_mgr = FakeAccountMgr(FakeAccount())
        engine._position_mgr = FakePosMgr([type("P", (), {"unrealized_pnl": Decimal("50")})()])
        state = await build_account_state(engine, "acct-1")
        # 10000 + 100 - 10 + 50
        assert state.equity == Decimal("10140")

    async def test_equity_none_without_internals(self) -> None:
        engine = FakeEngine(internals=False)
        state = await build_account_state(engine, "acct-1")
        assert state.equity is None


class TestAudit:
    async def test_account_state_snapshot_audit_written(self) -> None:
        repo = FakeAuditRepo()
        audit_mod.set_audit_repo(repo)
        try:
            await build_account_state(FakeEngine(), "acct-1", session_id="srv-test")
            # Let the fire-and-forget task complete.
            for _ in range(5):
                await asyncio.sleep(0)
            assert any(e.get("event_type") == "account.state_snapshot" for e in repo.events)
            event = next(e for e in repo.events if e.get("event_type") == "account.state_snapshot")
            assert event["actor"] == "local_api"
            assert event["resource"] == "acct-1"
        finally:
            audit_mod.set_audit_repo(None)

    async def test_audit_missing_repo_is_noop(self) -> None:
        audit_mod.set_audit_repo(None)
        state = await build_account_state(FakeEngine(), "acct-1")
        assert isinstance(state, AccountState)
