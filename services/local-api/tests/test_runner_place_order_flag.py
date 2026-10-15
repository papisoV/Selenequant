"""Unit tests: runner respects engine.supports_mark_price flag.

Step 4 of v6 plan — verifies that strategy_runner.py:992-1010 correctly
branches on engine.supports_mark_price flag, passing mark_price/
signal_timestamp_ms only when flag=True (Shadow/PaperEngine path) and
omitting them when flag=False (LiveEngine path).

Engines are injected via the public ``runner.set_engines(live_engine=mock,
mode_provider=lambda: "confirm")`` API (MINOR 2: mode_provider mock returns
"confirm" so ``_resolve_mode`` routes to the injected LiveEngine, not the
default PaperEngine).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.strategy_runner import RunnerSlot, StrategyRunner
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


# ---------------------------------------------------------------------------
# Mock engines
# ---------------------------------------------------------------------------


class _MockLiveEngineNoFlag:
    """Mock engine: flag=False (like LiveEngine).

    place_order signature does NOT declare mark_price/signal_timestamp_ms,
    so receiving them would be a contract violation.
    """

    supports_mark_price = False

    def __init__(self) -> None:
        self.place_order_calls: list[dict[str, Any]] = []

    async def place_order(
        self,
        *,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        leverage: int,
        margin_mode: str,
        pos_side: str,
        reduce_only: bool,
        stop_price: float,
        client_order_id: str,
        **kwargs: Any,
    ) -> SimpleNamespace:
        # Contract: flag=False ⇒ runner must NOT pass mark_price / signal_timestamp_ms.
        assert "mark_price" not in kwargs, (
            f"flag=False but got mark_price={kwargs['mark_price']}"
        )
        assert "signal_timestamp_ms" not in kwargs, (
            f"flag=False but got signal_timestamp_ms={kwargs['signal_timestamp_ms']}"
        )
        self.place_order_calls.append({"client_order_id": client_order_id})
        return SimpleNamespace(client_order_id=client_order_id, id="no-flag-order-1")


class _MockLiveEngineWithFlag:
    """Mock engine: flag=True (like Shadow/PaperEngine).

    place_order signature declares mark_price/signal_timestamp_ms, so
    receiving them is required.
    """

    supports_mark_price = True

    def __init__(self) -> None:
        self.place_order_calls: list[dict[str, Any]] = []

    async def place_order(
        self,
        *,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        leverage: int,
        margin_mode: str,
        pos_side: str,
        reduce_only: bool,
        stop_price: float,
        client_order_id: str,
        mark_price: float | None = None,
        signal_timestamp_ms: int | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        # Contract: flag=True ⇒ runner must pass mark_price / signal_timestamp_ms.
        assert mark_price is not None, "flag=True but mark_price not passed"
        assert signal_timestamp_ms is not None, (
            "flag=True but signal_timestamp_ms not passed"
        )
        self.place_order_calls.append({
            "client_order_id": client_order_id,
            "mark_price": mark_price,
            "signal_timestamp_ms": signal_timestamp_ms,
        })
        return SimpleNamespace(client_order_id=client_order_id, id="with-flag-order-1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner() -> StrategyRunner:
    """Build a StrategyRunner with a stub paper_engine (not used in these tests)."""
    return StrategyRunner(
        paper_engine=SimpleNamespace(),  # not used; mode_provider routes to live
        market_service=None,
    )


def _make_slot() -> RunnerSlot:
    """Build a minimal RunnerSlot for _execute_signal."""
    return RunnerSlot(
        deployment_id="dep-test-1",
        strategy_id="strat-test",
        account_id="acct-1",
        symbol="BTC/USDT",
        interval="1h",
        runtime=SimpleNamespace(position="flat", bar_count=10),
        config_snapshot={},
    )


def _make_signal() -> Signal:
    """Build a BUY Signal that triggers a flat→long entry."""
    return Signal(
        strategy_id="strat-test",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        strength=0.8,
        timestamp=1711000000000,
        reason="test signal",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_order_flag_false_no_mark_price() -> None:
    """flag=False (LiveEngine path) → runner omits mark_price/signal_timestamp_ms."""
    runner = _make_runner()
    mock_engine = _MockLiveEngineNoFlag()
    runner.set_engines(
        live_engine=mock_engine,
        mode_provider=lambda: "confirm",
    )

    slot = _make_slot()
    signal = _make_signal()
    await runner._execute_signal(slot, signal, current_price=100.0, signal_ts_ms=12345)

    assert len(mock_engine.place_order_calls) == 1, (
        f"expected 1 place_order call, got {len(mock_engine.place_order_calls)}"
    )
    # No mark_price/signal_timestamp_ms in recorded kwargs — asserted inside mock.


@pytest.mark.asyncio
async def test_place_order_flag_true_with_mark_price() -> None:
    """flag=True (Shadow/PaperEngine path) → runner passes mark_price/signal_timestamp_ms."""
    runner = _make_runner()
    mock_engine = _MockLiveEngineWithFlag()
    runner.set_engines(
        live_engine=mock_engine,
        mode_provider=lambda: "confirm",
    )

    slot = _make_slot()
    signal = _make_signal()
    await runner._execute_signal(slot, signal, current_price=100.0, signal_ts_ms=12345)

    assert len(mock_engine.place_order_calls) == 1, (
        f"expected 1 place_order call, got {len(mock_engine.place_order_calls)}"
    )
    call = mock_engine.place_order_calls[0]
    assert call["mark_price"] == 100.0
    assert call["signal_timestamp_ms"] == 12345
