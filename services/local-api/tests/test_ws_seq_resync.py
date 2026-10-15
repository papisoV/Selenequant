"""WS seq / resync protocol tests (protocol §3.2/§3.3, §4 Step 4, §5).

Step 4 covers the single-writer seq stamping at the two broadcast chokepoints:

- Double-increment regression: envelope seq == same-event audit sequence_id.
- P1: paper aid=None path consumes no seq (no fake gap).
- P2: paper bridge path emits 3 consecutive seqs per fill (fill / account_snapshot
  / equity_point), never reusing the fill's seq; trading bridge path emits the
  fill on the trading channel plus 2 events per paper account (2N events) in an
  independent namespace.

Step 5 tests (subscribe snapshot / resync) live in test_ws_account_state.py.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
import app.api.v1.ws as ws_mod
from app.core import audit as audit_mod
from app.core.dependencies import set_paper_managers
from app.core.sequence import ChannelSequenceManager, set_channel_sequence
from app.main import _bridge_execution_events, _bridge_paper_engine_events
from selene_space_paper.accounts import AccountManager
from selene_space_paper.positions import PositionManager
from selene_space_types.trading import (
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


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeWS:
    headers: dict = {}
    client = None

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def close(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass


async def _subscribe(mgr: ws_mod.ConnectionManager, fake_ws: FakeWS, channel: str) -> None:
    """Register *fake_ws* with *mgr* and subscribe it to *channel*."""
    await mgr.connect(fake_ws)
    mgr.subscribe(fake_ws, channel)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, data: dict) -> None:
        self.events.append(data)


class FakeEngine:
    exchange = "paper"
    mode = ExecutionMode.PAPER

    def __init__(self, order_accounts: dict[str, str] | None = None) -> None:
        self._order_accounts = order_accounts or {}
        self._on_order_cbs = []
        self._on_fill_cbs = []
        self._on_position_cbs = []
        self._on_balance_cbs = []
        self._on_position_scoped_cbs = []
        self._on_balance_scoped_cbs = []

    def _get_order_account(self, order_id: str) -> str | None:
        return self._order_accounts.get(order_id)

    def on_order_update(self, cb):  # type: ignore[no-untyped-def]
        self._on_order_cbs.append(cb)

    def on_fill(self, cb):  # type: ignore[no-untyped-def]
        self._on_fill_cbs.append(cb)

    def on_position_update(self, cb):  # type: ignore[no-untyped-def]
        self._on_position_cbs.append(cb)

    def on_balance_update(self, cb):  # type: ignore[no-untyped-def]
        self._on_balance_cbs.append(cb)

    def on_position_update_scoped(self, cb):  # type: ignore[no-untyped-def]
        self._on_position_scoped_cbs.append(cb)

    def on_balance_update_scoped(self, cb):  # type: ignore[no-untyped-def]
        self._on_balance_scoped_cbs.append(cb)

    def fire_order(self, order: Order) -> None:
        for cb in self._on_order_cbs:
            cb(order)

    def fire_fill(self, fill: Fill) -> None:
        for cb in self._on_fill_cbs:
            cb(fill)

    def fire_position(self, pos: Position) -> None:
        for cb in self._on_position_cbs:
            cb(pos)

    def fire_balance(self, balances: list[BalanceUpdate]) -> None:
        for cb in self._on_balance_cbs:
            cb(balances)

    def fire_position_scoped(self, account_id: str, pos: Position) -> None:
        for cb in self._on_position_scoped_cbs:
            cb(account_id, pos)

    def fire_balance_scoped(self, account_id: str, balances: list[BalanceUpdate]) -> None:
        for cb in self._on_balance_scoped_cbs:
            cb(account_id, balances)


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
        quantity=Decimal("0.1"),
        fee=Decimal("0.1"),
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


async def _drain() -> None:
    for _ in range(8):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _reset_state():
    ws_mod._trading_manager = ws_mod.ConnectionManager()
    ws_mod._paper_manager = ws_mod.ConnectionManager()
    ws_mod._ip_connection_counts.clear()
    set_channel_sequence(None)
    audit_mod.set_audit_repo(None)
    set_paper_managers(None, None, None)
    yield


# ---------------------------------------------------------------------------
# Step 4 — single-writer seq stamping
# ---------------------------------------------------------------------------


class TestTradingBridgeSingleWriter:
    async def test_fill_envelope_seq_equals_audit_sequence_id(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        repo = FakeAuditRepo()
        audit_mod.set_audit_repo(repo)

        engine = FakeEngine()
        _bridge_execution_events(engine)

        fake_ws = FakeWS()
        await _subscribe(ws_mod._trading_manager, fake_ws, "trading:local:orders")

        engine.fire_fill(_fill())
        await _drain()

        # Envelope on the trading orders channel.
        envs = [m for m in fake_ws.sent if m.get("type") == "fill"]
        assert len(envs) == 1
        env = envs[0]
        assert env["seq"] == 1
        assert env["session_id"] == "srv-test"

        # Same event's audit row carries the same sequence_id (single writer).
        trade_fills = [e for e in repo.events if e.get("event_type") == "trade.fill"]
        assert len(trade_fills) == 1
        assert trade_fills[0]["details"]["sequence_id"] == env["seq"]

    async def test_order_position_balance_channels_stamp_independently(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        engine = FakeEngine()
        _bridge_execution_events(engine)

        ws_orders = FakeWS()
        ws_positions = FakeWS()
        ws_balances = FakeWS()
        await _subscribe(ws_mod._trading_manager, ws_orders, "trading:local:orders")
        await _subscribe(ws_mod._trading_manager, ws_positions, "trading:local:positions")
        await _subscribe(ws_mod._trading_manager, ws_balances, "trading:local:balances")

        engine.fire_order(_order())
        engine.fire_position(_order_to_position())
        engine.fire_balance([_balance()])
        await _drain()

        order_envs = [m for m in ws_orders.sent if m.get("type") == "order_update"]
        pos_envs = [m for m in ws_positions.sent if m.get("type") == "position_update"]
        bal_envs = [m for m in ws_balances.sent if m.get("type") == "balance_update"]
        assert order_envs[0]["seq"] == 1  # local:orders
        assert pos_envs[0]["seq"] == 1  # local:positions (independent)
        assert bal_envs[0]["seq"] == 1  # local:balances (independent)


class TestPaperBridgeP1:
    async def test_aid_none_consumes_no_seq(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        repo = FakeAuditRepo()
        audit_mod.set_audit_repo(repo)

        # No account mapping -> aid=None for every order.
        engine = FakeEngine(order_accounts={})
        _bridge_paper_engine_events(engine)

        fake_ws = FakeWS()
        await _subscribe(ws_mod._paper_manager, fake_ws, "paper:acct-1")

        engine.fire_order(_order())
        await _drain()

        # No WS event on the paper channel, and no seq burned.
        assert fake_ws.sent == []
        assert seq_mgr.watermark(orders="paper:acct-1") == AccountStateSeq()

        # Audit still fires (unconditional) with sequence_id=None.
        order_audits = [e for e in repo.events if e.get("event_type") == "paper.order_update"]
        assert len(order_audits) == 1
        assert order_audits[0]["details"].get("sequence_id") is None

    async def test_aid_none_then_real_event_stays_contiguous(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        engine = FakeEngine(order_accounts={"ord-1": "acct-1"})
        _bridge_paper_engine_events(engine)

        fake_ws = FakeWS()
        await _subscribe(ws_mod._paper_manager, fake_ws, "paper:acct-1")

        # First: an unresolvable order (aid=None) — must NOT burn a seq.
        engine.fire_order(_order(99))
        await _drain()
        assert seq_mgr.watermark(orders="paper:acct-1") == AccountStateSeq()

        # Next: a resolvable order — its seq is 1, not 2 (no fake gap).
        engine.fire_order(_order(1))
        await _drain()
        envs = [m for m in fake_ws.sent if m.get("type") == "order_update"]
        assert [e["seq"] for e in envs] == [1]


class TestPaperBridgeP2:
    async def test_paper_bridge_fill_emits_three_consecutive_seqs(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        accounts = AccountManager()
        positions = PositionManager()
        acct = accounts.create_account(name="A1", initial_balance=1000)
        aid = acct.id
        set_paper_managers(accounts, None, positions)

        engine = FakeEngine(order_accounts={_fill().order_id: aid})
        _bridge_paper_engine_events(engine)

        fake_ws = FakeWS()
        await _subscribe(ws_mod._paper_manager, fake_ws, f"paper:{aid}")

        engine.fire_fill(_fill())
        await _drain()

        # One fill -> 3 events on the same paper channel, 3 consecutive seqs.
        types = [m["type"] for m in fake_ws.sent]
        seqs = [m["seq"] for m in fake_ws.sent]
        assert types == ["fill", "account_snapshot", "equity_point"]
        assert seqs == [1, 2, 3]

    async def test_trading_bridge_fill_emits_2n_paper_events_independent_namespace(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        accounts = AccountManager()
        positions = PositionManager()
        acct1 = accounts.create_account(name="A1", initial_balance=1000)
        acct2 = accounts.create_account(name="A2", initial_balance=1000)
        aid1, aid2 = acct1.id, acct2.id
        set_paper_managers(accounts, None, positions)

        engine = FakeEngine()
        _bridge_execution_events(engine)

        ws_orders = FakeWS()
        ws_acct1 = FakeWS()
        ws_acct2 = FakeWS()
        await _subscribe(ws_mod._trading_manager, ws_orders, "trading:local:orders")
        await _subscribe(ws_mod._paper_manager, ws_acct1, f"paper:{aid1}")
        await _subscribe(ws_mod._paper_manager, ws_acct2, f"paper:{aid2}")

        engine.fire_fill(_fill())
        await _drain()

        # Fill event on the trading orders channel.
        fill_envs = [m for m in ws_orders.sent if m.get("type") == "fill"]
        assert [e["seq"] for e in fill_envs] == [1]  # local:orders namespace

        # Per paper account: 2 consecutive seqs (account_snapshot, equity_point),
        # namespace paper:{account_id} — independent of local:orders.
        acct1_types = [m["type"] for m in ws_acct1.sent]
        acct1_seqs = [m["seq"] for m in ws_acct1.sent]
        assert acct1_types == ["account_snapshot", "equity_point"]
        assert acct1_seqs == [1, 2]

        acct2_types = [m["type"] for m in ws_acct2.sent]
        acct2_seqs = [m["seq"] for m in ws_acct2.sent]
        assert acct2_types == ["account_snapshot", "equity_point"]
        assert acct2_seqs == [1, 2]

        # 2N events total (2 accounts x 2 events).
        assert len(ws_acct1.sent) + len(ws_acct2.sent) == 4


class TestPaperChannelNoSeqInfoEvent:
    """MAJOR-1: no-seq events on the paper channel are pure info events.

    ``strategy_signal``/``runner_status`` come from strategy_runner's
    ``_broadcast_signal``/``_broadcast_status``, which call
    ``broadcast_paper_event`` WITHOUT seq. They interleave on the same
    ``paper:{account_id}`` channel as the bridge's seq'd event types. A
    gap-checking client must skip them (no seq field -> no gap, no ``last``
    update, no baseline participation); the seq'd stream stays contiguous.
    """

    async def test_no_seq_strategy_signal_keeps_seq_stream_contiguous(self) -> None:
        seq_mgr = ChannelSequenceManager(session_id="srv-test")
        set_channel_sequence(seq_mgr)
        accounts = AccountManager()
        positions = PositionManager()
        acct = accounts.create_account(name="A1", initial_balance=1000)
        aid = acct.id
        set_paper_managers(accounts, None, positions)

        engine = FakeEngine(order_accounts={_order(1).id: aid, _fill(1).order_id: aid})
        _bridge_paper_engine_events(engine)

        fake_ws = FakeWS()
        await _subscribe(ws_mod._paper_manager, fake_ws, f"paper:{aid}")

        # 1. A seq'd order_update (client bookkeeping: last=1).
        engine.fire_order(_order(1))
        await _drain()
        assert [m["seq"] for m in fake_ws.sent if m.get("type") == "order_update"] == [1]

        # 2. strategy_runner-style no-seq info event interleaves (MAJOR-1).
        #    Directly drive broadcast_paper_event WITHOUT seq, exactly like
        #    strategy_runner._broadcast_signal / _broadcast_status.
        await ws_mod.broadcast_paper_event(aid, "strategy_signal", {"deployment_id": "d-1"})
        await _drain()

        info_envs = [m for m in fake_ws.sent if m.get("type") == "strategy_signal"]
        assert len(info_envs) == 1
        assert "seq" not in info_envs[0]  # envelope carries no seq field
        # No counter advanced by the info event.
        assert seq_mgr.watermark(orders=f"paper:{aid}") == AccountStateSeq(orders=1)

        # 3. Next seq'd event is seq=2 == client's last+1 — NO fake gap.
        engine.fire_fill(_fill(1))
        await _drain()
        fill_envs = [m for m in fake_ws.sent if m.get("type") == "fill"]
        assert [e["seq"] for e in fill_envs] == [2]


def _order_to_position() -> Position:
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
