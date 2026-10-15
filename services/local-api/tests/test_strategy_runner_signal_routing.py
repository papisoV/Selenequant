"""Tests for StrategyRunner signal routing (Phase 1).

Verifies that ``_select_engine`` routes to Paper / Shadow / Live based on the
current execution mode, and that ``set_engines`` injects engines correctly.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.core.strategy_runner import RunnerSlot, StrategyRunner


def _make_runner(
    paper=None, live=None, shadow=None, mode_provider=None,
) -> StrategyRunner:
    return StrategyRunner(
        paper_engine=paper or _FakeEngine("paper"),
        market_service=None,
        live_engine=live,
        shadow_engine=shadow,
        mode_provider=mode_provider,
    )


class _FakeEngine:
    def __init__(self, name: str) -> None:
        self.name = name
        self.last_call: dict | None = None

    async def place_order(self, **kwargs) -> SimpleNamespace:
        self.last_call = kwargs
        return SimpleNamespace(id=f"{self.name}-order-1")


def test_select_engine_paper_returns_paper_engine() -> None:
    paper = _FakeEngine("paper")
    runner = _make_runner(paper=paper, mode_provider=lambda: "paper")
    assert runner._select_engine("paper") is paper


def test_select_engine_shadow_returns_shadow_engine() -> None:
    shadow = _FakeEngine("shadow")
    runner = _make_runner(shadow=shadow, mode_provider=lambda: "shadow")
    assert runner._select_engine("shadow") is shadow


def test_select_engine_confirm_returns_live_engine() -> None:
    live = _FakeEngine("live")
    runner = _make_runner(live=live, mode_provider=lambda: "confirm")
    assert runner._select_engine("confirm") is live


def test_select_engine_limited_auto_returns_live_engine() -> None:
    live = _FakeEngine("live")
    runner = _make_runner(live=live, mode_provider=lambda: "limited_auto")
    assert runner._select_engine("limited_auto") is live


def test_select_engine_full_auto_returns_live_engine() -> None:
    live = _FakeEngine("live")
    runner = _make_runner(live=live, mode_provider=lambda: "full_auto")
    assert runner._select_engine("full_auto") is live


def test_select_engine_returns_none_for_live_when_not_configured() -> None:
    # No live engine injected — live modes should yield None (caller handles)
    runner = _make_runner(mode_provider=lambda: "confirm")
    assert runner._select_engine("confirm") is None


def test_resolve_mode_defaults_to_paper_when_no_provider() -> None:
    runner = StrategyRunner(
        paper_engine=_FakeEngine("paper"),
        market_service=None,
    )
    assert runner._resolve_mode() == "paper"


def test_resolve_mode_defaults_to_paper_when_provider_returns_empty() -> None:
    runner = _make_runner(mode_provider=lambda: "")
    assert runner._resolve_mode() == "paper"


def test_resolve_mode_returns_provider_value() -> None:
    runner = _make_runner(mode_provider=lambda: "shadow")
    assert runner._resolve_mode() == "shadow"


def test_resolve_mode_falls_back_on_provider_exception() -> None:
    def _bad():
        raise RuntimeError("boom")
    runner = _make_runner(mode_provider=_bad)
    assert runner._resolve_mode() == "paper"


def test_set_engines_injects_live_and_shadow() -> None:
    runner = StrategyRunner(
        paper_engine=_FakeEngine("paper"),
        market_service=None,
    )
    assert runner._live is None
    assert runner._shadow is None

    live = _FakeEngine("live")
    shadow = _FakeEngine("shadow")
    runner.set_engines(live_engine=live, shadow_engine=shadow, mode_provider=lambda: "live")

    assert runner._live is live
    assert runner._shadow is shadow
    assert runner._resolve_mode() == "live"


def test_set_engines_does_not_overwrite_with_none() -> None:
    """Passing None for an engine should not clear an existing one."""
    live = _FakeEngine("live")
    runner = _make_runner(live=live, mode_provider=lambda: "confirm")
    runner.set_engines(live_engine=None)  # should be ignored
    assert runner._live is live


# ---------------------------------------------------------------------------
# Funding-arb dual-leg executor routing (#1)
# ---------------------------------------------------------------------------


class _FakeDualLegExecutor:
    """Records open/close calls; returns a canned PairResult-like object."""

    def __init__(self) -> None:
        self.open_calls: list[dict] = []
        self.close_calls: list[dict] = []
        self.open_result: object | None = None
        self.close_result: object | None = None

    async def open_delta_neutral(self, **kwargs: object) -> object:
        self.open_calls.append(kwargs)
        return self.open_result

    async def close_delta_neutral(self, **kwargs: object) -> object:
        self.close_calls.append(kwargs)
        return self.close_result


def _pair_result(delta: float, side: str) -> SimpleNamespace:
    """Canned PairResult-shaped object for the fake executor."""
    from decimal import Decimal

    from selene_space_types.trading import OrderSide

    return SimpleNamespace(
        spot_position_delta=Decimal(str(delta)),
        flattened=False,
        leg=lambda name: SimpleNamespace(side=OrderSide(side)),
    )


def _arb_signal(side: str) -> SimpleNamespace:
    """funding-arb-shaped signal (strategy_id=funding_arb)."""
    from selene_space_types.trading import OrderSide

    def _build() -> SimpleNamespace:
        return SimpleNamespace(
            side=OrderSide(side),
            strategy_id="funding_arb",
            reason="funding-positive",
            strength=1.0,
            model_copy=lambda **kw: _build(),
        )

    return _build()


async def _noop_broadcast(self, slot, signal, order) -> None:  # type: ignore[no-untyped-def]
    return None


@pytest.mark.asyncio
async def test_funding_arb_live_routes_buy_through_dual_leg_executor() -> None:
    dle = _FakeDualLegExecutor()
    dle.open_result = _pair_result(0.1666, "buy")
    live = _FakeEngine("live")
    runner = StrategyRunner(
        paper_engine=_FakeEngine("paper"), market_service=None,
        live_engine=live, mode_provider=lambda: "confirm",
        dual_leg_executor=dle,
    )
    runner._broadcast_signal = _noop_broadcast  # type: ignore[method-assign]
    slot = _register_real_slot(runner, "dep-arb", symbol="BTC/USDT")

    await runner._execute_signal(slot, _arb_signal("buy"), 60000.0, 1700000000000)

    assert len(dle.open_calls) == 1
    call = dle.open_calls[0]
    assert call["spot_symbol"] == "BTC/USDT"
    assert call["account_id"] == "acct-1"
    assert call["strategy_id"] == "funding_arb"
    assert float(call["notional_usdt"]) == pytest.approx(runner._order_size)
    assert float(call["spot_price"]) == 60000.0
    # Single-order engine was NOT used for the routed pair.
    assert live.last_call is None
    assert slot.orders_placed == 1
    assert slot.net_position == pytest.approx(0.1666)


@pytest.mark.asyncio
async def test_funding_arb_live_routes_sell_through_dual_leg_executor() -> None:
    dle = _FakeDualLegExecutor()
    dle.close_result = _pair_result(-0.1666, "sell")
    runner = StrategyRunner(
        paper_engine=_FakeEngine("paper"), market_service=None,
        live_engine=_FakeEngine("live"), mode_provider=lambda: "confirm",
        dual_leg_executor=dle,
    )
    runner._broadcast_signal = _noop_broadcast  # type: ignore[method-assign]
    slot = _register_real_slot(runner, "dep-arb-close", symbol="BTC/USDT")
    slot.net_position = 0.1666  # runner is tracking the spot long

    await runner._execute_signal(slot, _arb_signal("sell"), 61000.0, 1700000000000)

    assert len(dle.close_calls) == 1
    call = dle.close_calls[0]
    assert call["spot_qty"] == pytest.approx(0.1666)
    assert call["perp_qty"] == pytest.approx(0.1666)
    # Close reduces the tracked spot position to flat.
    assert slot.net_position == pytest.approx(0.0)
    assert slot.orders_placed == 1


@pytest.mark.asyncio
async def test_funding_arb_paper_keeps_single_order_path() -> None:
    """Paper mode must NOT route through the executor — single spot order only."""
    dle = _FakeDualLegExecutor()
    paper = _FakeEngine("paper")
    runner = StrategyRunner(
        paper_engine=paper, market_service=None,
        mode_provider=lambda: "paper", dual_leg_executor=dle,
    )
    runner._broadcast_signal = _noop_broadcast  # type: ignore[method-assign]
    slot = _register_real_slot(runner, "dep-arb-paper", symbol="BTC/USDT")

    await runner._execute_signal(slot, _arb_signal("buy"), 60000.0, 1700000000000)

    assert not dle.open_calls
    assert paper.last_call is not None  # single-order paper path ran


@pytest.mark.asyncio
async def test_non_funding_strategy_ignores_dual_leg_executor() -> None:
    """Non-funding-arb strategies keep the single-order path even when a
    dual-leg executor is wired."""
    dle = _FakeDualLegExecutor()
    live = _FakeEngine("live")
    runner = StrategyRunner(
        paper_engine=_FakeEngine("paper"), market_service=None,
        live_engine=live, mode_provider=lambda: "confirm",
        dual_leg_executor=dle,
    )
    runner._broadcast_signal = _noop_broadcast  # type: ignore[method-assign]
    slot = _register_real_slot(runner, "dep-bb", symbol="BTC/USDT")
    from selene_space_types.trading import OrderSide

    bb_signal = SimpleNamespace(
        side=OrderSide.BUY, strategy_id="bb_v3", reason="bb", strength=1.0,
        model_copy=lambda **kw: bb_signal,
    )

    await runner._execute_signal(slot, bb_signal, 60000.0, 1700000000000)

    assert not dle.open_calls
    assert live.last_call is not None  # single-order live path ran



# ---------------------------------------------------------------------------
# Fill routing via client_order_id (precise) + symbol+recency fallback
# ---------------------------------------------------------------------------


def _make_fill(
    symbol: str = "BTC/USDT",
    side: str = "buy",
    price: float = 100.0,
    quantity: float = 50.0,
    client_order_id: str = "",
) -> SimpleNamespace:
    """Build a minimal fill-like object for _on_fill_event tests."""
    return SimpleNamespace(
        symbol=symbol,
        side=side,
        price=price,
        quantity=quantity,
        client_order_id=client_order_id,
    )


def _register_slot(runner: StrategyRunner, deployment_id: str, symbol: str = "BTC/USDT") -> None:
    """Register a fake slot so _on_fill_event can route to it."""
    from app.core.strategy_runner import RunnerSlot

    slot = RunnerSlot(
        deployment_id=deployment_id,
        strategy_id="strat-test",
        account_id="acct-1",
        symbol=symbol,
        interval="1h",
        runtime=None,
    )
    runner._slots[deployment_id] = slot


def test_on_fill_event_routes_via_client_order_id() -> None:
    """Fill with client_order_id='{deployment_id}-{ts}' routes to that slot."""
    runner = _make_runner()
    _register_slot(runner, "dep-live-abc")
    _register_slot(runner, "dep-live-xyz")

    # Fill carries client_order_id pointing to dep-live-abc.
    fill = _make_fill(client_order_id="dep-live-abc-1700000000000")
    runner._on_fill_event(fill)

    # Only dep-live-abc's net_position should be updated.
    assert runner._slots["dep-live-abc"].net_position == 0.5  # 50 USDT / 100 = 0.5
    assert runner._slots["dep-live-xyz"].net_position == 0.0


def test_on_fill_event_routes_via_symbol_recency_when_no_client_order_id() -> None:
    """Paper-engine fills (no client_order_id) fall back to symbol+recency."""
    runner = _make_runner()
    _register_slot(runner, "dep-a", symbol="BTC/USDT")
    _register_slot(runner, "dep-b", symbol="BTC/USDT")

    # Last order went to dep-b.
    runner._last_order_slot["BTC/USDT"] = "dep-b"
    fill = _make_fill(client_order_id="")
    runner._on_fill_event(fill)

    assert runner._slots["dep-b"].net_position == 0.5
    assert runner._slots["dep-a"].net_position == 0.0


def test_on_fill_event_ignores_non_numeric_suffix_in_client_order_id() -> None:
    """Suffix must be purely numeric — protects against deployment_ids ending in hyphen
    or malformed suffixes like 'dep-None' (when signal_ts_ms was None)."""
    runner = _make_runner()
    _register_slot(runner, "dep-live-abc")
    # Malformed: 'None' suffix — should NOT match, should fall back to last_order_slot.
    runner._last_order_slot["BTC/USDT"] = "dep-live-abc"
    fill = _make_fill(client_order_id="dep-live-abc-None")
    runner._on_fill_event(fill)
    # Falls back to last_order_slot → dep-live-abc still gets the fill.
    # (This is intentional: when client_order_id is malformed we still want
    # to route via the recency fallback rather than dropping the fill.)
    assert runner._slots["dep-live-abc"].net_position == 0.5


def test_on_fill_event_sell_side_subtracts_position() -> None:
    """Sell fills decrement net_position (signed qty)."""
    runner = _make_runner()
    _register_slot(runner, "dep-sell")
    runner._slots["dep-sell"].net_position = 1.0  # pre-existing long
    fill = _make_fill(side="sell", client_order_id="dep-sell-1700000000000")
    runner._on_fill_event(fill)
    # 1.0 - 0.5 = 0.5
    assert runner._slots["dep-sell"].net_position == 0.5


def test_on_fill_event_no_matching_slot_is_safe_noop() -> None:
    """When no slot matches (neither client_order_id nor last_order_slot),
    the callback should be a safe no-op (no exception, no state change)."""
    runner = _make_runner()
    _register_slot(runner, "dep-a")
    # Fill with unknown client_order_id and no last_order_slot entry.
    fill = _make_fill(client_order_id="dep-unknown-1700000000000")
    runner._on_fill_event(fill)  # should not raise
    assert runner._slots["dep-a"].net_position == 0.0


# ---------------------------------------------------------------------------
# H11/H12: slot task tracking + stop_deployment_async cancellation
# ---------------------------------------------------------------------------


class _HangEngine:
    """Engine whose place_order blocks on an event we never set."""

    def __init__(self) -> None:
        self.hang_event = asyncio.Event()
        self.last_call: dict | None = None

    async def place_order(self, **kwargs) -> SimpleNamespace:
        self.last_call = kwargs
        await self.hang_event.wait()  # never set
        return SimpleNamespace(id="hang-order-1")


def _register_real_slot(
    runner: StrategyRunner, deployment_id: str, symbol: str = "BTC/USDT"
) -> RunnerSlot:
    """Register a slot with a minimal runtime for _on_kline-driven tests."""
    slot = RunnerSlot(
        deployment_id=deployment_id,
        strategy_id="strat-test",
        account_id="acct-1",
        symbol=symbol,
        interval="1h",
        runtime=SimpleNamespace(position="flat", bar_count=0, on_kline=lambda e: None),
    )
    runner._slots[deployment_id] = slot
    return slot


@pytest.mark.asyncio
async def test_execute_signal_exception_logged_not_silently_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H11: _execute_signal exceptions are logged as warnings, not silently swallowed."""
    runner = _make_runner(paper=_FakeEngine("paper"), mode_provider=lambda: "paper")
    slot = _register_real_slot(runner, "dep-exc")

    # Build a coroutine that raises immediately.
    async def _boom() -> None:
        raise RuntimeError("execute_signal boom")

    with caplog.at_level(logging.WARNING, logger="app.core.strategy_runner"):
        runner._schedule_slot_task("dep-exc", _boom())
        # Yield until the task completes (it raises synchronously on first await).
        for _ in range(20):
            await asyncio.sleep(0)
            # The done callback fires after the task completes.
            if not runner._slot_tasks.get("dep-exc"):
                break

    # Task auto-discarded from the tracker.
    assert "dep-exc" not in runner._slot_tasks or not runner._slot_tasks["dep-exc"]
    # Exception was logged (not silently swallowed).
    assert any(
        "execute_signal boom" in (rec.getMessage() + str(rec.exc_info))
        and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_stop_deployment_cancels_inflight_execute_signal() -> None:
    """H12: stop_deployment_async cancels in-flight _execute_signal tasks."""
    hang_engine = _HangEngine()
    runner = _make_runner(paper=hang_engine, mode_provider=lambda: "paper")
    slot = _register_real_slot(runner, "dep-hang")

    # Schedule an _execute_signal that will hang inside place_order.
    # Use a fake signal object; _execute_signal reads .side, .strength, .reason,
    # .strategy_id, .model_copy, .model_dump.
    fake_signal = SimpleNamespace(
        side=SimpleNamespace(value="buy", name="BUY"),
        strength=1.0,
        reason="test",
        strategy_id="strat-test",
        model_copy=lambda **kw: fake_signal,
        model_dump=lambda: {},
    )
    runner._schedule_slot_task(
        "dep-hang",
        runner._execute_signal(slot, fake_signal, 100.0, 1700000000000),
    )
    # Yield so the task starts and reaches the hang.
    for _ in range(10):
        await asyncio.sleep(0)
    assert "dep-hang" in runner._slot_tasks
    inflight = list(runner._slot_tasks["dep-hang"])
    assert len(inflight) == 1
    assert not inflight[0].done()

    # stop_deployment_async must cancel + await the in-flight task.
    result = await asyncio.wait_for(
        runner.stop_deployment_async("dep-hang", timeout=5.0),
        timeout=15.0,
    )
    assert result is True
    # Slot removed.
    assert "dep-hang" not in runner._slots
    # Task is done / cancelled.
    assert inflight[0].done()


@pytest.mark.asyncio
async def test_stop_deployment_cancels_inflight_broadcast_status() -> None:
    """H12: stop_deployment_async cancels in-flight _broadcast_status tasks."""
    runner = _make_runner(mode_provider=lambda: "paper")
    slot = _register_real_slot(runner, "dep-bcast")

    # _broadcast_status imports broadcast_paper_event from app.api.v1.ws.
    # Patch it to hang on an event we never set.
    hang_event = asyncio.Event()
    original_module = None
    try:
        import app.api.v1.ws as ws_mod
        original_module = ws_mod
    except Exception:
        ws_mod = None

    # Build a hanging _broadcast_status by patching the broadcast function.
    async def _hanging_broadcast(*args, **kwargs) -> None:
        await hang_event.wait()

    # Monkeypatch the import target inside _broadcast_status via sys.modules.
    import sys
    fake_ws = SimpleNamespace(broadcast_paper_event=_hanging_broadcast)
    sys.modules["app.api.v1.ws"] = fake_ws
    try:
        runner._schedule_slot_task(
            "dep-bcast",
            runner._broadcast_status(slot),
        )
        for _ in range(10):
            await asyncio.sleep(0)
        inflight = list(runner._slot_tasks["dep-bcast"])
        assert len(inflight) == 1
        assert not inflight[0].done()

        result = await asyncio.wait_for(
            runner.stop_deployment_async("dep-bcast", timeout=5.0),
            timeout=15.0,
        )
        assert result is True
        assert "dep-bcast" not in runner._slots
        assert inflight[0].done()
    finally:
        if original_module is not None:
            sys.modules["app.api.v1.ws"] = original_module
        else:
            sys.modules.pop("app.api.v1.ws", None)


@pytest.mark.asyncio
async def test_stop_deployment_no_dangling_tasks() -> None:
    """H12: after stop_deployment_async, _slot_tasks[deployment_id] is empty/gone."""
    runner = _make_runner(paper=_FakeEngine("paper"), mode_provider=lambda: "paper")
    _register_real_slot(runner, "dep-clean")

    # Schedule a quick-completing task.
    async def _quick() -> None:
        await asyncio.sleep(0)

    runner._schedule_slot_task("dep-clean", _quick())
    # Let it complete.
    for _ in range(20):
        await asyncio.sleep(0)
        if not runner._slot_tasks.get("dep-clean"):
            break

    result = await runner.stop_deployment_async("dep-clean", timeout=5.0)
    assert result is True
    # No dangling entry.
    assert "dep-clean" not in runner._slot_tasks or not runner._slot_tasks["dep-clean"]
    assert "dep-clean" not in runner._slots

