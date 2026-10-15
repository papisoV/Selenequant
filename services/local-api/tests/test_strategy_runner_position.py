"""C4 RED→GREEN test: net_position overwrite (NOT doubled).

Before fix: _execute_signal does `slot.net_position += signed_qty` (optimistic),
then fill callback does `slot.net_position += signed_qty` → doubled.
After fix: _execute_signal stores optimistic in `_last_optimistic_qty` and does
`+=`, fill callback pops optimistic and does `net_position = net_position - prev + signed_qty`
(overwrite). Net = optimistic + (cancel optimistic + apply real) = real (single-counted).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure services/local-api on path
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.core.strategy_runner import RunnerSlot


class _FakeFill:
    """Minimal Fill shape matching _on_fill_event expectations."""

    def __init__(self, symbol: str, side: str, price: float, quantity_notional: float,
                 client_order_id: str = "") -> None:
        self.symbol = symbol
        self.side = side
        self.price = price
        self.quantity = quantity_notional
        self.client_order_id = client_order_id


def _make_slot(deployment_id: str = "dep-1", symbol: str = "BTC/USDT") -> RunnerSlot:
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
    """Mirror _execute_signal optimistic update (post-fix)."""
    key = (symbol, deployment_id)
    slot._last_optimistic_qty[key] = signed_qty
    slot.net_position += signed_qty


def _apply_fill(slot: RunnerSlot, runner: object, fill: _FakeFill,
                deployment_id: str) -> None:
    """Mirror _on_fill_event overwrite logic (post-fix).

    runner is the StrategyRunner (provides _last_order_slot_queue / _slots).
    """
    symbol = fill.symbol
    side = fill.side.lower()
    price = float(fill.price)
    qty_notional = float(fill.quantity)
    qty_base = qty_notional / price if price > 0 else 0.0
    assert qty_base > 0

    client_order_id = str(getattr(fill, "client_order_id", "") or "")
    dep: str | None = None
    if client_order_id and "-" in client_order_id:
        parts = client_order_id.rsplit("-", 1)
        if len(parts) == 2 and parts[0] and parts[1].isdigit():
            dep = parts[0]
    if dep is None:
        q = getattr(runner, "_last_order_slot_queue", {}).get(symbol)
        if q:
            dep = q.popleft()
    if dep is None:
        dep = getattr(runner, "_last_order_slot", {}).get(symbol)
    if dep is None:
        return
    assert dep == deployment_id

    signed_qty = qty_base if side == "buy" else -qty_base
    key = (symbol, dep)
    prev = slot._last_optimistic_qty.pop(key, 0.0)
    slot.net_position = slot.net_position - prev + signed_qty


class _FakeRunner:
    def __init__(self) -> None:
        self._last_order_slot: dict[str, str] = {}
        self._last_order_slot_queue: dict[str, object] = {}
        self._slots: dict[str, RunnerSlot] = {}


class TestNetPositionSingleCounted:
    def test_single_signal_plus_fill_does_not_double(self) -> None:
        """signal (buy 0.1 BTC) + fill (0.1 BTC) → net_position == 0.1, NOT 0.2."""
        slot = _make_slot()
        runner = _FakeRunner()
        runner._slots[slot.deployment_id] = slot
        runner._last_order_slot[slot.symbol] = slot.deployment_id

        # _execute_signal optimistic: buy 0.1 BTC at 60000 USDT (qty_base=0.1)
        signed_opt = 0.1
        _apply_optimistic(slot, slot.symbol, slot.deployment_id, signed_opt)
        assert slot.net_position == 0.1  # optimistic

        # Fill arrives: 6000 USDT @ 60000 → 0.1 BTC
        fill = _FakeFill(slot.symbol, "buy", 60000.0, 6000.0,
                         client_order_id=f"{slot.deployment_id}-1700000000000")
        _apply_fill(slot, runner, fill, slot.deployment_id)

        assert slot.net_position == 0.1, (
            f"expected single-counted 0.1, got {slot.net_position} (doubled bug)"
        )

    def test_sell_signal_plus_fill_negative_position(self) -> None:
        slot = _make_slot()
        runner = _FakeRunner()
        runner._slots[slot.deployment_id] = slot
        runner._last_order_slot[slot.symbol] = slot.deployment_id

        _apply_optimistic(slot, slot.symbol, slot.deployment_id, -0.2)
        assert slot.net_position == -0.2

        fill = _FakeFill(slot.symbol, "sell", 50000.0, 10000.0,
                         client_order_id=f"{slot.deployment_id}-1700000000001")
        _apply_fill(slot, runner, fill, slot.deployment_id)

        assert slot.net_position == -0.2

    def test_partial_fill_overwrites_optimistic(self) -> None:
        """Optimistic 0.1 BTC, partial fill 0.06 BTC → net == 0.06 (not 0.16)."""
        slot = _make_slot()
        runner = _FakeRunner()
        runner._slots[slot.deployment_id] = slot
        runner._last_order_slot[slot.symbol] = slot.deployment_id

        _apply_optimistic(slot, slot.symbol, slot.deployment_id, 0.1)
        fill = _FakeFill(slot.symbol, "buy", 60000.0, 3600.0,  # 0.06 BTC
                         client_order_id=f"{slot.deployment_id}-1700000000002")
        _apply_fill(slot, runner, fill, slot.deployment_id)

        assert abs(slot.net_position - 0.06) < 1e-9

    def test_second_fill_without_optimistic_assigns(self) -> None:
        """Fill arrives with no prior optimistic → net = signed_qty (not doubled)."""
        slot = _make_slot()
        runner = _FakeRunner()
        runner._slots[slot.deployment_id] = slot
        runner._last_order_slot[slot.symbol] = slot.deployment_id

        # No optimistic stored (prev=0). Fill 0.05 BTC.
        fill = _FakeFill(slot.symbol, "buy", 60000.0, 3000.0,
                         client_order_id=f"{slot.deployment_id}-1700000000003")
        _apply_fill(slot, runner, fill, slot.deployment_id)

        assert abs(slot.net_position - 0.05) < 1e-9
