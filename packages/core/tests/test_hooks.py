"""Hook system locks for selene_space_core.hooks — registration, priority
ordering, handler isolation, async emit, listing, clearing, predefined set."""

import pytest

from selene_space_core.hooks.registry import HookRegistry
from selene_space_core.hooks.types import PREDEFINED_HOOKS, HookPriority


def test_registered_handler_receives_payload():
    reg = HookRegistry()
    inbox: list[dict] = []
    reg.register("on_signal", lambda e, p: inbox.append(p))
    reg.emit("on_signal", {"symbol": "ETH/USDT"})
    pair = (len(inbox), inbox[0]["symbol"])
    assert pair == (1, "ETH/USDT")


def test_emit_order_follows_priority_descending():
    reg = HookRegistry()
    sequence: list[str] = []
    tiers = [
        (HookPriority.LOW, "low"),
        (HookPriority.HIGH, "high"),
        (HookPriority.NORMAL, "normal"),
    ]
    for prio, label in tiers:
        reg.register("evt", lambda e, p, l=label: sequence.append(l), priority=prio)
    reg.emit("evt")
    assert sequence == ["high", "normal", "low"]


def test_raising_handler_is_contained():
    reg = HookRegistry()
    reg.register("evt", lambda e, p: 1 / 0)
    reg.emit("evt")  # Should not raise


@pytest.mark.asyncio
async def test_async_emit_delivers_to_async_handlers():
    reg = HookRegistry()
    inbox: list[dict] = []

    async def listener(event, payload):
        inbox.append(payload)

    reg.register("evt", listener)
    await reg.emit_async("evt", {"data": 1})
    assert len(inbox) == 1


def test_list_events_reports_every_bound_name():
    reg = HookRegistry()
    for name in ("alpha", "beta"):
        reg.register(name, lambda e, p: None)
    assert set(reg.list_events()) == {"alpha", "beta"}


def test_clear_unbinds_everything():
    reg = HookRegistry()
    reg.register("alpha", lambda e, p: None)
    reg.clear()
    assert reg.list_events() == []


EXPECTED_PREDEFINED = {
    "on_market_tick",
    "on_signal",
    "on_order_placed",
    "on_risk_triggered",
    "on_backtest_complete",
}


def test_predefined_hook_catalog_is_exact():
    checks = (
        len(PREDEFINED_HOOKS) == 5,
        EXPECTED_PREDEFINED <= set(PREDEFINED_HOOKS),
    )
    assert all(checks)
