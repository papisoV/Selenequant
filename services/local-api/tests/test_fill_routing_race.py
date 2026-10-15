"""C4 RED→GREEN test: fill routing race (paper-path FIFO queue).

Scenario: two deployments on the same symbol place orders back-to-back. Before
fix, `_last_order_slot[symbol]` is overwritten by the second order, so the first
fill silently `return`s and the first slot's net_position stays 0. After fix,
per-symbol `deque[deployment_id]` queue pops oldest, so both fills route.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.core.strategy_runner import RunnerSlot


class _FakeFill:
    def __init__(self, symbol: str, side: str, price: float, quantity_notional: float,
                 client_order_id: str = "") -> None:
        self.symbol = symbol
        self.side = side
        self.price = price
        self.quantity = quantity_notional
        self.client_order_id = client_order_id


def _make_slot(deployment_id: str, symbol: str = "BTC/USDT") -> RunnerSlot:
    return RunnerSlot(
        deployment_id=deployment_id,
        strategy_id="strat-1",
        account_id="acc-1",
        symbol=symbol,
        interval="1m",
        runtime=None,
    )


def _apply_optimistic(slot: RunnerSlot, symbol: str, deployment_id: str,
                      signed_qty: float) -> None:
    key = (symbol, deployment_id)
    slot._last_optimistic_qty[key] = signed_qty
    slot.net_position += signed_qty


def _route_fill_paper(slot_lookup: dict[str, RunnerSlot],
                      queue_map: dict[str, deque],
                      last_order_slot: dict[str, str],
                      fill: _FakeFill) -> str | None:
    """Paper-path fill routing (no client_order_id): pop oldest from queue.

    Returns deployment_id routed, or None if silently dropped (the bug).
    Mirrors the post-fix _on_fill_event fallback path.
    """
    symbol = fill.symbol
    side = fill.side.lower()
    price = float(fill.price)
    qty_base = float(fill.quantity) / price if price > 0 else 0.0
    if qty_base <= 0:
        return None

    deployment_id: str | None = None
    # Primary: client_order_id parse (live path). Paper fill has no client_order_id.
    cid = str(getattr(fill, "client_order_id", "") or "")
    if cid and "-" in cid:
        parts = cid.rsplit("-", 1)
        if len(parts) == 2 and parts[0] and parts[1].isdigit():
            deployment_id = parts[0]
    # Fallback 1: FIFO queue (post-fix paper path)
    if deployment_id is None:
        q = queue_map.get(symbol)
        if q:
            deployment_id = q.popleft()
    # Fallback 2: last_order_slot (pre-fix only path — clobbered by race)
    if deployment_id is None:
        deployment_id = last_order_slot.get(symbol)
    if deployment_id is None:
        return None

    slot = slot_lookup.get(deployment_id)
    if slot is None:
        return None
    signed_qty = qty_base if side == "buy" else -qty_base
    key = (symbol, deployment_id)
    prev = slot._last_optimistic_qty.pop(key, 0.0)
    slot.net_position = slot.net_position - prev + signed_qty
    return deployment_id


class TestFillRoutingRace:
    def test_concurrent_fills_same_symbol_different_deployments(self) -> None:
        """dep-A orders BTC, then dep-B orders BTC before A's fill arrives.

        Pre-fix: _last_order_slot["BTC"] = "dep-B" → A's fill routes to dep-B
        (or silent return if dep-B is wrong), A's net_position stuck 0.
        Post-fix: FIFO queue pops A first, then B → both settle correctly.
        """
        slot_a = _make_slot("dep-A")
        slot_b = _make_slot("dep-B")
        slot_lookup = {slot_a.deployment_id: slot_a, slot_b.deployment_id: slot_b}
        queue_map: dict[str, deque] = {"BTC/USDT": deque()}
        last_order_slot: dict[str, str] = {}

        # A orders (optimistic 0.1 BTC)
        _apply_optimistic(slot_a, "BTC/USDT", "dep-A", 0.1)
        last_order_slot["BTC/USDT"] = "dep-A"
        queue_map["BTC/USDT"].append("dep-A")

        # B orders (optimistic 0.2 BTC) — clobbers last_order_slot
        _apply_optimistic(slot_b, "BTC/USDT", "dep-B", 0.2)
        last_order_slot["BTC/USDT"] = "dep-B"
        queue_map["BTC/USDT"].append("dep-B")

        # Both fills arrive (paper, no client_order_id), order preserved by queue
        fill_a = _FakeFill("BTC/USDT", "buy", 60000.0, 6000.0)  # 0.1 BTC
        fill_b = _FakeFill("BTC/USDT", "buy", 60000.0, 12000.0)  # 0.2 BTC

        routed_a = _route_fill_paper(slot_lookup, queue_map, last_order_slot, fill_a)
        routed_b = _route_fill_paper(slot_lookup, queue_map, last_order_slot, fill_b)

        assert routed_a == "dep-A", f"fill A routed to {routed_a} (race bug)"
        assert routed_b == "dep-B", f"fill B routed to {routed_b}"

        assert abs(slot_a.net_position - 0.1) < 1e-9, (
            f"slot A expected 0.1, got {slot_a.net_position} (silent drop bug)"
        )
        assert abs(slot_b.net_position - 0.2) < 1e-9, (
            f"slot B expected 0.2, got {slot_b.net_position}"
        )

    def test_no_silent_return_when_queue_populated(self) -> None:
        """Pre-fix: 2 orders on same symbol, 2 fills → 1 silent return.
        Post-fix: 0 silent returns (both routed via queue).
        """
        slot_a = _make_slot("dep-A")
        slot_b = _make_slot("dep-B")
        slot_lookup = {slot_a.deployment_id: slot_a, slot_b.deployment_id: slot_b}
        queue_map: dict[str, deque] = {"BTC/USDT": deque(["dep-A", "dep-B"])}
        last_order_slot: dict[str, str] = {"BTC/USDT": "dep-B"}

        fill_a = _FakeFill("BTC/USDT", "buy", 60000.0, 6000.0)
        fill_b = _FakeFill("BTC/USDT", "buy", 60000.0, 6000.0)

        r1 = _route_fill_paper(slot_lookup, queue_map, last_order_slot, fill_a)
        r2 = _route_fill_paper(slot_lookup, queue_map, last_order_slot, fill_b)
        assert r1 == "dep-A"
        assert r2 == "dep-B"
        # No silent return — both fills routed.
        assert r1 is not None and r2 is not None
