"""Position reconciliation watchdog tests (independent source).

The watchdog polls an INDEPENDENT position source (exchange positionRisk
via engine._client.get_positions for live modes, paper engine get_fills()
for paper) — NOT the callback-written ledger, which shares the same race
as the callback. Divergence >1% for 2+ consecutive rounds with no in-flight
order → watchdog rewrites slot.net_position (US-004); also covers the
post-warmup position backfill (US-003).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.core.strategy_runner import RunnerSlot, StrategyRunner


class _FakeFill:
    def __init__(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        client_order_id: str = "",
    ) -> None:
        self.symbol = symbol
        self.side = side
        self.price = price
        self.quantity = quantity
        self.client_order_id = client_order_id


class _FakePaperEngine:
    """Paper engine with get_fills() — the independent source for paper mode."""

    def __init__(self, fills: list[_FakeFill]) -> None:
        self._fills = fills

    def get_fills(self, account_id: str) -> list[_FakeFill]:
        return list(self._fills)


def _make_runner(
    paper_engine: object | None = None, live_engine: object | None = None
) -> StrategyRunner:
    return StrategyRunner(
        paper_engine=paper_engine,
        market_service=None,
        live_engine=live_engine,
        shadow_engine=None,
        mode_provider=(lambda: "paper")
        if paper_engine is not None
        else (lambda: "full_auto"),
    )


def _make_slot(deployment_id: str = "dep-1", symbol: str = "BTC/USDT") -> RunnerSlot:
    return RunnerSlot(
        deployment_id=deployment_id,
        strategy_id="strat-1",
        account_id="acc-1",
        symbol=symbol,
        interval="1m",
        runtime=None,
    )


class TestIndependentSourceReconciliation:
    def test_paper_engine_get_fills_is_independent_source(self) -> None:
        """_sum_independent_fills calls engine.get_fills (NOT the callback ledger)."""
        # Independent source: 1 fill of 0.1 BTC buy
        fill = _FakeFill(
            "BTC/USDT", "buy", 60000.0, 6000.0, client_order_id="dep-1-1700000000000"
        )
        engine = _FakePaperEngine([fill])
        runner = _make_runner(paper_engine=engine)
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot

        result = runner._sum_independent_fills(slot)
        assert result is not None
        assert abs(result - 0.1) < 1e-9

    def test_divergence_detected_when_tracked_diverges(self) -> None:
        """slot.net_position = 0.2 but independent source = 0.1 → divergence >1%."""
        fill = _FakeFill(
            "BTC/USDT", "buy", 60000.0, 6000.0, client_order_id="dep-1-1700000000000"
        )  # 0.1 BTC
        engine = _FakePaperEngine([fill])
        runner = _make_runner(paper_engine=engine)
        slot = _make_slot()
        slot.net_position = 0.2  # doubled (the bug) — diverges from independent 0.1
        runner._slots[slot.deployment_id] = slot

        independent = runner._sum_independent_fills(slot)
        assert independent is not None
        tracked = slot.net_position
        diverge_pct = abs(tracked - independent) / abs(independent)
        assert diverge_pct > 0.01, f"expected divergence >1%, got {diverge_pct:.2%}"

    def test_no_divergence_when_tracked_matches_independent(self) -> None:
        fill = _FakeFill(
            "BTC/USDT", "buy", 60000.0, 6000.0, client_order_id="dep-1-1700000000000"
        )
        engine = _FakePaperEngine([fill])
        runner = _make_runner(paper_engine=engine)
        slot = _make_slot()
        slot.net_position = 0.1  # matches independent source
        runner._slots[slot.deployment_id] = slot

        independent = runner._sum_independent_fills(slot)
        assert independent is not None
        diverge_pct = abs(slot.net_position - independent) / abs(independent)
        assert diverge_pct <= 0.01

    def test_returns_none_when_no_independent_source(self) -> None:
        """Engine without get_fills → None (skip reconcile)."""
        runner = _make_runner(paper_engine=object())  # bare object, no methods
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot
        result = runner._sum_independent_fills(slot)
        assert result is None

    def test_filters_fills_by_deployment_id(self) -> None:
        """Independent source must only sum fills for THIS deployment_id."""
        fill_a = _FakeFill(
            "BTC/USDT", "buy", 60000.0, 6000.0, client_order_id="dep-A-1700000000000"
        )  # 0.1 BTC
        fill_b = _FakeFill(
            "BTC/USDT", "buy", 60000.0, 6000.0, client_order_id="dep-B-1700000000001"
        )  # 0.1 BTC (other dep)
        engine = _FakePaperEngine([fill_a, fill_b])
        runner = _make_runner(paper_engine=engine)
        slot = _make_slot(deployment_id="dep-A")
        runner._slots[slot.deployment_id] = slot

        result = runner._sum_independent_fills(slot)
        assert result is not None
        assert (
            abs(result - 0.1) < 1e-9
        ), f"expected 0.1 (filtered), got {result} (filter bug)"


class _PositionRiskClient:
    """Stands in for the adapter's futures client: get_positions returns
    raw positionRisk dicts (positionAmt signed)."""

    def __init__(self, positions: list[dict] | Exception) -> None:
        self._positions = positions

    async def get_positions(self, symbol: str | None = None) -> list[dict]:
        if isinstance(self._positions, Exception):
            raise self._positions
        return list(self._positions)


class _LiveEngineWithClient:
    """Live engine exposing ._client (the adapter) like LiveExecutionEngine."""

    def __init__(self, client: _PositionRiskClient) -> None:
        self._client = client


def _live_runner_with_positions(positions: list[dict] | Exception) -> StrategyRunner:
    """Runner in full_auto mode whose live engine reads the given positionRisk dicts."""
    client = _PositionRiskClient(positions)
    return StrategyRunner(
        paper_engine=object(),
        market_service=None,
        live_engine=_LiveEngineWithClient(client),
        shadow_engine=None,
        mode_provider=lambda: "full_auto",
    )


class TestQueryIndependentNetPosition:
    """US-002: live leg reads exchange positionRisk via engine._client.get_positions.

    Replaces the dead get_fills_history path (no such method on
    LiveExecutionEngine — the live branch was a permanent no-op)."""

    @pytest.mark.asyncio
    async def test_net_mode_single_positionamt(self) -> None:
        # Net (BOTH) mode: one entry, positionAmt signed.
        runner = _live_runner_with_positions(
            [{"symbol": "BTCUSDT", "positionAmt": "0.5"}]
        )
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot

        result = await runner._query_independent_net_position(slot)
        assert result == 0.5

    @pytest.mark.asyncio
    async def test_hedge_mode_two_entries_sum_to_net(self) -> None:
        # Hedge mode: LONG + SHORT entries; sum = net exposure.
        runner = _live_runner_with_positions(
            [
                {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "1.0"},
                {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.3"},
            ]
        )
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot

        result = await runner._query_independent_net_position(slot)
        assert result is not None
        assert abs(result - 0.7) < 1e-9

    @pytest.mark.asyncio
    async def test_client_without_get_positions_returns_none(self) -> None:
        # Engine without ._client.get_positions → None (skip reconcile, no crash).
        engine = type("Bare", (), {})()  # no _client attribute
        runner = StrategyRunner(
            paper_engine=object(),
            market_service=None,
            live_engine=engine,
            shadow_engine=None,
            mode_provider=lambda: "full_auto",
        )
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot

        result = await runner._query_independent_net_position(slot)
        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self) -> None:
        runner = _live_runner_with_positions(RuntimeError("api down"))
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot

        result = await runner._query_independent_net_position(slot)
        assert result is None

    @pytest.mark.asyncio
    async def test_paper_mode_falls_back_to_fill_sum(self) -> None:
        fill = _FakeFill(
            "BTC/USDT", "buy", 60000.0, 6000.0, client_order_id="dep-1-1700000000000"
        )
        runner = _make_runner(paper_engine=_FakePaperEngine([fill]))
        slot = _make_slot()
        runner._slots[slot.deployment_id] = slot

        result = await runner._query_independent_net_position(slot)
        assert result is not None
        assert abs(result - 0.1) < 1e-9


class _RuntimeWithPosition:
    """Minimal runtime stand-in exposing _position (strategy contract)."""

    def __init__(self) -> None:
        self._position = "flat"


class TestBackfillNetPosition:
    """US-003: after warmup, adopt the exchange's real leftover position.

    _reset_warmup_position_state forces flat; without backfill the runner
    trades against a phantom flat. Backfill sets net_position AND
    runtime._position (the reduce_only decision at _execute_signal reads it).
    """

    @staticmethod
    def _slot_with_runtime() -> tuple[RunnerSlot, _RuntimeWithPosition]:
        slot = _make_slot()
        runtime = _RuntimeWithPosition()
        slot.runtime = runtime
        return slot, runtime

    @pytest.mark.asyncio
    async def test_long_position_adopted(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "0.5"}])
        slot, runtime = self._slot_with_runtime()
        runner._slots[slot.deployment_id] = slot

        await runner._backfill_net_position(slot)

        assert slot.net_position == 0.5
        assert runtime._position == "long"

    @pytest.mark.asyncio
    async def test_short_position_adopted(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "-0.3"}])
        slot, runtime = self._slot_with_runtime()
        runner._slots[slot.deployment_id] = slot

        await runner._backfill_net_position(slot)

        assert slot.net_position == -0.3
        assert runtime._position == "short"

    @pytest.mark.asyncio
    async def test_zero_position_stays_flat(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "0"}])
        slot, runtime = self._slot_with_runtime()
        runner._slots[slot.deployment_id] = slot

        await runner._backfill_net_position(slot)

        assert slot.net_position == 0.0
        assert runtime._position == "flat"

    @pytest.mark.asyncio
    async def test_unavailable_source_stays_flat(self) -> None:
        # No get_positions on client → None → flat, no crash.
        runner = StrategyRunner(
            paper_engine=object(),
            market_service=None,
            live_engine=type("Bare", (), {})(),
            shadow_engine=None,
            mode_provider=lambda: "full_auto",
        )
        slot, runtime = self._slot_with_runtime()
        runner._slots[slot.deployment_id] = slot

        await runner._backfill_net_position(slot)

        assert slot.net_position == 0.0
        assert runtime._position == "flat"


class TestWatchdogCorrection:
    """US-004: watchdog upgrades from log-only to corrective.

    Rewrite only after >=2 consecutive divergent rounds AND no in-flight
    order; in-flight → report only (callback will overwrite net_position
    with the authoritative fill qty); converged → streak resets.
    """

    @staticmethod
    def _runner_with_positions(positions: list[dict]) -> StrategyRunner:
        client = _PositionRiskClient(positions)
        return StrategyRunner(
            paper_engine=object(),
            market_service=None,
            live_engine=_LiveEngineWithClient(client),
            shadow_engine=None,
            mode_provider=lambda: "full_auto",
        )

    @pytest.mark.asyncio
    async def test_two_consecutive_divergent_rounds_correct(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "0.5"}])
        slot = _make_slot()
        slot.net_position = 0.2  # diverges 60% from 0.5
        runner._slots[slot.deployment_id] = slot

        await runner._reconcile_one_slot(slot)
        assert slot.net_position == 0.2  # round 1: report only
        assert runner._diverge_streak[slot.deployment_id] == 1

        await runner._reconcile_one_slot(slot)
        assert slot.net_position == 0.5  # round 2: corrected
        assert runner._diverge_streak[slot.deployment_id] == 0

    @pytest.mark.asyncio
    async def test_single_round_does_not_correct(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "0.5"}])
        slot = _make_slot()
        slot.net_position = 0.2
        runner._slots[slot.deployment_id] = slot

        await runner._reconcile_one_slot(slot)
        assert slot.net_position == 0.2
        assert runner._diverge_streak[slot.deployment_id] == 1

    @pytest.mark.asyncio
    async def test_inflight_order_blocks_correction(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "0.5"}])
        slot = _make_slot()
        slot.net_position = 0.2
        slot._last_optimistic_qty[(slot.symbol, slot.deployment_id)] = 0.3
        runner._slots[slot.deployment_id] = slot

        await runner._reconcile_one_slot(slot)
        await runner._reconcile_one_slot(slot)
        await runner._reconcile_one_slot(slot)

        # Still not corrected after 3 rounds — in-flight qty protected.
        assert slot.net_position == 0.2
        assert runner._diverge_streak[slot.deployment_id] == 3

    @pytest.mark.asyncio
    async def test_converged_resets_streak(self) -> None:
        runner = _live_runner_with_positions([{"positionAmt": "0.2"}])
        slot = _make_slot()
        slot.net_position = 0.2  # matches → no divergence
        runner._slots[slot.deployment_id] = slot

        runner._diverge_streak[slot.deployment_id] = 1
        await runner._reconcile_one_slot(slot)
        assert runner._diverge_streak[slot.deployment_id] == 0
        assert slot.net_position == 0.2
