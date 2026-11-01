"""Tests for LiveDecisionPipeline — hard limit enforcement (Phase 2)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from selene_space_paper.live_decision_pipeline import (
    LiveDecisionPipeline,
    LimitedAutoConfig,
    LivePipelineConfig,
)
from selene_space_paper.decision_pipeline import (
    PipelineAction,
    PipelineConfig,
)
from selene_space_paper.orders import PaperOrderManager
from selene_space_risk.engine import RiskEngine
from selene_space_risk.kill_switch import KillSwitch
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


@pytest.fixture(autouse=True)
def _reset_ks() -> None:
    KillSwitch._reset_singleton()


def _make_signal(**overrides: Any) -> Signal:
    defaults = {
        "strategy_id": "strat-001",
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "strength": 0.85,
        "timestamp": int(time.time() * 1000),
        "reason": "SMA cross",
    }
    defaults.update(overrides)
    return Signal(**defaults)


def _make_pipeline(
    tmp_path: Path,
    *,
    limits: LimitedAutoConfig | None = None,
    enforce_limits: bool = True,
    kill_switch: bool = False,
    default_quantity: float = 100.0,
) -> LiveDecisionPipeline:
    ks_path = tmp_path / "ks.json"
    ks = KillSwitch(ks_path) if kill_switch else None
    engine = RiskEngine([])
    order_mgr = PaperOrderManager()
    base = PipelineConfig(
        dedupe_ttl_seconds=60.0,
        min_order_interval_seconds=0.0,
        default_quantity=default_quantity,
        default_account_id="test-acc",
        enable_validation=False,
    )
    live_cfg = LivePipelineConfig(
        base=base,
        limits=limits or LimitedAutoConfig(),
        enforce_limits=enforce_limits,
    )
    return LiveDecisionPipeline(
        risk_engine=engine,
        order_manager=order_mgr,
        kill_switch=ks,
        config=base,
        live_config=live_cfg,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestLiveDecisionPipelineHappyPath:
    def test_signal_executed_when_under_all_limits(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            max_position_notional_per_symbol=500.0,
            max_daily_notional=2000.0,
            max_daily_orders=20,
            max_single_order_notional=200.0,
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=100.0)
        result = pipeline.process_signal(_make_signal())
        assert result.action == PipelineAction.EXECUTED
        assert result.order_id is not None

    def test_no_limits_when_enforce_limits_false(self, tmp_path: Path) -> None:
        # With enforce_limits=False, even huge orders pass
        limits = LimitedAutoConfig(max_single_order_notional=10.0)
        pipeline = _make_pipeline(
            tmp_path, limits=limits, enforce_limits=False, default_quantity=1000.0
        )
        result = pipeline.process_signal(_make_signal())
        assert result.action == PipelineAction.EXECUTED


# ---------------------------------------------------------------------------
# Hard limits
# ---------------------------------------------------------------------------


class TestLiveDecisionPipelineLimits:
    def test_blocks_when_single_order_exceeds_cap(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            max_single_order_notional=100.0,
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=200.0)
        result = pipeline.process_signal(_make_signal())
        assert result.action == PipelineAction.BLOCKED
        assert "Single order notional" in result.reason

    def test_blocks_when_daily_notional_exceeded(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            max_daily_notional=150.0,
            max_single_order_notional=1000.0,
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=100.0)
        # First order OK (100 < 150)
        r1 = pipeline.process_signal(_make_signal())
        assert r1.action == PipelineAction.EXECUTED
        # Second order would push daily to 200 > 150
        r2 = pipeline.process_signal(
            _make_signal(symbol="ETH/USDT", timestamp=int(time.time() * 1000) + 1)
        )
        assert r2.action == PipelineAction.BLOCKED
        assert "Daily notional" in r2.reason

    def test_blocks_when_daily_order_count_exceeded(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            max_daily_orders=2,
            max_daily_notional=10000.0,
            max_single_order_notional=1000.0,
            require_stop_loss=False,
            cooldown_between_same_symbol_s=0,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=10.0)
        # Place 2 orders (OK)
        for i in range(2):
            r = pipeline.process_signal(
                _make_signal(
                    symbol=f"SYM{i}/USDT", timestamp=int(time.time() * 1000) + i
                )
            )
            assert r.action == PipelineAction.EXECUTED
        # 3rd should be blocked
        r3 = pipeline.process_signal(
            _make_signal(symbol="SYM3/USDT", timestamp=int(time.time() * 1000) + 10)
        )
        assert r3.action == PipelineAction.BLOCKED
        assert "Daily order limit" in r3.reason

    def test_blocks_when_per_symbol_position_exceeds_cap(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            max_position_notional_per_symbol=150.0,
            max_daily_notional=10000.0,
            max_single_order_notional=1000.0,
            require_stop_loss=False,
            cooldown_between_same_symbol_s=0,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=100.0)
        # First order on BTC: position=100 ≤ 150
        r1 = pipeline.process_signal(_make_signal())
        assert r1.action == PipelineAction.EXECUTED
        # Second order on same symbol would push position to 200 > 150
        # Use different timestamp to bypass dedup
        r2 = pipeline.process_signal(
            _make_signal(timestamp=int(time.time() * 1000) + 5000)
        )
        assert r2.action == PipelineAction.BLOCKED
        assert "Position limit" in r2.reason

    def test_blocks_when_symbol_not_in_whitelist(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            allowed_symbols=["BTC/USDT", "ETH/USDT"],
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=10.0)
        r = pipeline.process_signal(_make_signal(symbol="DOGE/USDT"))
        assert r.action == PipelineAction.BLOCKED
        assert "not in allowed list" in r.reason

    def test_blocks_when_leverage_exceeds_cap(self, tmp_path: Path) -> None:
        """Leverage in risk_params is checked against max_leverage."""
        limits = LimitedAutoConfig(
            max_leverage=3,
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=10.0)
        # Override intent construction by patching _signal_to_intent
        from selene_space_types.agent import TradeIntent

        orig = pipeline._signal_to_intent

        def _with_high_leverage(signal: Signal) -> TradeIntent:
            intent = orig(signal)
            return intent.model_copy(update={"risk_params": {"leverage": 10}})

        pipeline._signal_to_intent = _with_high_leverage  # type: ignore[assignment]
        r = pipeline.process_signal(_make_signal())
        assert r.action == PipelineAction.BLOCKED
        assert "Leverage" in r.reason

    def test_blocks_when_stop_loss_required_but_missing(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            require_stop_loss=True,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=10.0)
        r = pipeline.process_signal(_make_signal())
        assert r.action == PipelineAction.BLOCKED
        assert "Stop loss is required" in r.reason


# ---------------------------------------------------------------------------
# Kill switch integration
# ---------------------------------------------------------------------------


class TestLiveDecisionPipelineKillSwitch:
    def test_blocks_when_kill_switch_active(self, tmp_path: Path) -> None:
        pipeline = _make_pipeline(tmp_path, kill_switch=True)
        # Activate kill switch
        from selene_space_risk.kill_switch import KillSwitch

        ks = KillSwitch()
        ks.activate(reason="test")

        result = pipeline.process_signal(_make_signal())
        assert result.action == PipelineAction.BLOCKED
        assert "Kill switch" in result.reason


# ---------------------------------------------------------------------------
# Daily reset
# ---------------------------------------------------------------------------


class TestLiveDecisionPipelineDailyReset:
    def test_reset_daily_counters_clears_state(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            max_daily_orders=1,
            max_daily_notional=10000.0,
            max_single_order_notional=1000.0,
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=10.0)
        r1 = pipeline.process_signal(_make_signal())
        assert r1.action == PipelineAction.EXECUTED

        # 2nd blocked
        r2 = pipeline.process_signal(
            _make_signal(symbol="ETH/USDT", timestamp=int(time.time() * 1000) + 1)
        )
        assert r2.action == PipelineAction.BLOCKED

        # Reset clears counters
        pipeline.reset_daily_counters()
        status = pipeline.daily_status()
        assert status["daily_orders"] == 0
        assert status["daily_notional"] == 0.0

        # Now orders flow again
        r3 = pipeline.process_signal(
            _make_signal(symbol="ETH/USDT", timestamp=int(time.time() * 1000) + 2)
        )
        assert r3.action == PipelineAction.EXECUTED


# ---------------------------------------------------------------------------
# Cooldown between same-symbol orders
# ---------------------------------------------------------------------------


class TestLiveDecisionPipelineCooldown:
    def test_cooldown_blocks_rapid_same_symbol_orders(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(
            cooldown_between_same_symbol_s=60,
            max_daily_notional=10000.0,
            max_daily_orders=100,
            max_single_order_notional=1000.0,
            max_position_notional_per_symbol=10000.0,
            require_stop_loss=False,
        )
        pipeline = _make_pipeline(tmp_path, limits=limits, default_quantity=10.0)
        r1 = pipeline.process_signal(_make_signal())
        assert r1.action == PipelineAction.EXECUTED
        # Same symbol, different timestamp — should be throttled by cooldown
        r2 = pipeline.process_signal(
            _make_signal(timestamp=int(time.time() * 1000) + 100)
        )
        assert r2.action == PipelineAction.SKIPPED
        assert "Throttled" in r2.reason


# ---------------------------------------------------------------------------
# Trailing stop integration (M2)
# ---------------------------------------------------------------------------


class TestLivePipelineTrailingStop:
    """M2: LiveDecisionPipeline + TrailingStopTracker integration."""

    def _make_pipeline_with_trailing(
        self, tmp_path: Path
    ) -> tuple[LiveDecisionPipeline, Any]:
        from selene_space_paper.trailing_stop import (
            TrailingStopConfig,
            TrailingStopTracker,
        )

        engine = RiskEngine([])
        order_mgr = PaperOrderManager()
        base = PipelineConfig(
            dedupe_ttl_seconds=0.0,
            min_order_interval_seconds=0.0,
            default_quantity=0.1,
            default_account_id="test-acc",
            enable_validation=False,
        )
        limits = LimitedAutoConfig(
            require_stop_loss=False,
            max_single_order_notional=10_000.0,
            max_position_notional_per_symbol=10_000.0,
            max_daily_notional=100_000.0,
            max_daily_orders=100,
        )
        tracker = TrailingStopTracker(
            config=TrailingStopConfig(
                activation_gain=0.05,
                initial_stop_pct=0.03,
                trail_drop=0.03,
                min_emit_interval_seconds=0.0,
            )
        )
        pipeline = LiveDecisionPipeline(
            risk_engine=engine,
            order_manager=order_mgr,
            config=base,
            live_config=LivePipelineConfig(base=base, limits=limits),
            trailing_stop=tracker,
        )
        return pipeline, tracker

    def test_notify_buy_fill_seeds_tracker(self, tmp_path: Path) -> None:
        pipeline, tracker = self._make_pipeline_with_trailing(tmp_path)
        pipeline.notify_fill("BTC/USDT", 10000.0, "buy")
        assert tracker.status("BTC/USDT") is not None
        assert tracker.status("BTC/USDT")["entry_price"] == 10000.0

    def test_notify_sell_clears_tracker(self, tmp_path: Path) -> None:
        pipeline, tracker = self._make_pipeline_with_trailing(tmp_path)
        pipeline.notify_fill("BTC/USDT", 10000.0, "buy")
        pipeline.notify_fill("BTC/USDT", 11000.0, "sell")
        assert tracker.status("BTC/USDT") is None

    def test_check_trailing_stop_emits_sell_through_pipeline(
        self, tmp_path: Path
    ) -> None:
        pipeline, _ = self._make_pipeline_with_trailing(tmp_path)
        pipeline.notify_fill("BTC/USDT", 10000.0, "buy")
        # Activate trailing (+6%).
        pipeline.check_trailing_stop("BTC/USDT", 10600.0, 0.1)
        # Ratchet peak to 11000.
        pipeline.check_trailing_stop("BTC/USDT", 11000.0, 0.1)
        # Drop 3.6% from peak → SELL emitted through pipeline.
        result = pipeline.check_trailing_stop("BTC/USDT", 10600.0, 0.1)
        assert result is not None
        # The SELL goes through the same pipeline — should EXECUTE.
        assert result.action == PipelineAction.EXECUTED

    def test_check_trailing_stop_no_position_returns_none(self, tmp_path: Path) -> None:
        pipeline, _ = self._make_pipeline_with_trailing(tmp_path)
        # No prior BUY fill → tracker has no state.
        result = pipeline.check_trailing_stop("BTC/USDT", 67000.0, 0.1)
        assert result is None

    def test_no_tracker_configured_is_noop(self, tmp_path: Path) -> None:
        """Without trailing_stop, both methods are safe no-ops."""
        engine = RiskEngine([])
        order_mgr = PaperOrderManager()
        base = PipelineConfig(
            min_order_interval_seconds=0.0,
            default_quantity=0.1,
            default_account_id="test-acc",
            enable_validation=False,
        )
        pipeline = LiveDecisionPipeline(
            risk_engine=engine,
            order_manager=order_mgr,
            config=base,
            live_config=LivePipelineConfig(
                base=base, limits=LimitedAutoConfig(require_stop_loss=False)
            ),
        )
        # Should not raise.
        pipeline.notify_fill("BTC/USDT", 10000.0, "buy")
        assert pipeline.check_trailing_stop("BTC/USDT", 67000.0, 0.1) is None


# ---------------------------------------------------------------------------
# C1: audit ledger wiring
# ---------------------------------------------------------------------------


class _FakeLedger:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def append(self, entry: dict) -> None:
        self.entries.append(entry)


class TestAuditLedgerWiring:
    def test_ledger_receives_decision_entries(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(require_stop_loss=False)
        ledger = _FakeLedger()
        pipeline = _make_pipeline(tmp_path, limits=limits)
        pipeline._audit_ledger = (
            ledger  # direct injection keeps _make_pipeline signature stable
        )
        result = pipeline.process_signal(_make_signal())
        assert result.action == PipelineAction.EXECUTED
        assert len(ledger.entries) >= 1
        entry = ledger.entries[-1]
        assert entry["strategy_id"] == "strat-001"
        assert entry["action"] == PipelineAction.EXECUTED.value
        assert entry["order_id"] == result.order_id
        assert entry["symbol"] == "BTC/USDT"

    def test_ledger_none_is_noop(self, tmp_path: Path) -> None:
        limits = LimitedAutoConfig(require_stop_loss=False)
        pipeline = _make_pipeline(tmp_path, limits=limits)
        assert pipeline._audit_ledger is None
        result = pipeline.process_signal(_make_signal())
        assert result.action == PipelineAction.EXECUTED

    def test_constructor_passthrough(self, tmp_path: Path) -> None:
        engine = RiskEngine([])
        order_mgr = PaperOrderManager()
        base = PipelineConfig(
            min_order_interval_seconds=0.0,
            default_quantity=0.1,
            default_account_id="test-acc",
            enable_validation=False,
        )
        ledger = _FakeLedger()
        pipeline = LiveDecisionPipeline(
            risk_engine=engine,
            order_manager=order_mgr,
            audit_ledger=ledger,
            config=base,
            live_config=LivePipelineConfig(
                base=base, limits=LimitedAutoConfig(require_stop_loss=False)
            ),
        )
        pipeline.process_signal(_make_signal())
        assert len(ledger.entries) >= 1
