"""DecisionPipeline gate ordering: dedupe, kill switch, throttle, risk."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from selene_space_paper.decision_pipeline import (
    DecisionPipeline,
    PipelineAction,
    PipelineConfig,
)
from selene_space_paper.orders import PaperOrderManager
from selene_space_risk.engine import RiskEngine
from selene_space_risk.kill_switch import KillSwitch
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


@pytest.fixture(autouse=True)
def _fresh_kill_switch() -> None:
    KillSwitch._reset_singleton()


NOW_MS = int(time.time() * 1000)


def _signal(**overrides: Any) -> Signal:
    body = dict(
        strategy_id="strat-001",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        strength=0.85,
        timestamp=NOW_MS,
        reason="SMA cross",
    )
    body.update(overrides)
    return Signal(**body)


class _Harness:
    """Assembles a pipeline with the knobs each scenario needs."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        rules: list | None = None,
        with_kill_switch: bool = False,
        gap_seconds: float = 0.0,
    ) -> None:
        ks_file = tmp_path / "ks.json"
        engine = RiskEngine(rules or [])
        config = PipelineConfig(
            dedupe_ttl_seconds=60.0,
            min_order_interval_seconds=gap_seconds,
            default_quantity=0.01,
            default_account_id="test-acc",
            enable_validation=False,  # no live price provider in unit tests
        )
        self.pipeline = DecisionPipeline(
            risk_engine=engine,
            order_manager=PaperOrderManager(),
            kill_switch=KillSwitch(ks_file) if with_kill_switch else None,
            config=config,
        )

    def feed(self, **sig_kwargs):
        return self.pipeline.process_signal(_signal(**sig_kwargs))


class TestHappyPath:
    def test_clean_signal_becomes_an_order(self, tmp_path: Path) -> None:
        result = _Harness(tmp_path).feed()
        assert result.action == PipelineAction.EXECUTED
        assert result.order_id is not None


class TestDuplicateGate:
    def test_same_object_twice_skips_second(self, tmp_path: Path) -> None:
        harness = _Harness(tmp_path)
        sig = _signal()
        first = harness.pipeline.process_signal(sig)
        again = harness.pipeline.process_signal(sig)
        assert first.action == PipelineAction.EXECUTED
        assert again.action == PipelineAction.SKIPPED
        assert "Duplicate" in again.reason


class TestKillSwitchGate:
    def test_engaged_switch_blocks_before_execution(self, tmp_path: Path) -> None:
        harness = _Harness(tmp_path, with_kill_switch=True)
        KillSwitch(tmp_path / "ks.json").activate("test")
        result = harness.feed()
        assert result.action == PipelineAction.BLOCKED
        assert "Kill switch" in result.reason


class TestThrottleGate:
    def test_back_to_back_orders_within_gap_are_dropped(self, tmp_path: Path) -> None:
        harness = _Harness(tmp_path, gap_seconds=9999.0)
        first = harness.feed(timestamp=NOW_MS)
        second = harness.feed(timestamp=NOW_MS + 1)
        assert first.action == PipelineAction.EXECUTED
        assert second.action == PipelineAction.SKIPPED
        assert "Throttled" in second.reason

    def test_gap_is_per_symbol_not_global(self, tmp_path: Path) -> None:
        harness = _Harness(tmp_path, gap_seconds=9999.0)
        btc = harness.feed(symbol="BTC/USDT")
        eth = harness.feed(symbol="ETH/USDT")
        assert (btc.action, eth.action) == (
            PipelineAction.EXECUTED,
            PipelineAction.EXECUTED,
        )


class TestRiskGate:
    def test_blacklisted_symbol_is_denied(self, tmp_path: Path) -> None:
        from selene_space_risk.rules import SymbolBlacklistRule

        harness = _Harness(
            tmp_path, rules=[SymbolBlacklistRule(blacklist=["BTC/USDT"])]
        )
        result = harness.feed()
        assert result.action == PipelineAction.BLOCKED
        assert "Risk denied" in result.reason


class TestAuditLedger:
    """C1: hash-chain ledger appends even with audit_logger=None (prod shape)."""

    def _harness_with_ledger(self, tmp_path: Path, chain_path: Path):
        harness = _Harness(tmp_path)
        from selene_space_paper.audit_ledger import HashChainLedger

        harness.pipeline._audit_ledger = HashChainLedger(chain_path)
        return harness

    def test_appends_without_audit_logger(self, tmp_path: Path) -> None:
        chain_path = tmp_path / "decisions.chain.jsonl"
        harness = self._harness_with_ledger(tmp_path, chain_path)
        assert harness.pipeline._audit_logger is None
        result = harness.feed()
        assert result.action == PipelineAction.EXECUTED
        lines = chain_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        import json as _json

        payload = _json.loads(lines[0])["payload"]
        assert payload["action"] == "executed"
        assert payload["order_id"] == result.order_id

    def test_blocked_event_also_appends(self, tmp_path: Path) -> None:
        from selene_space_risk.rules import SymbolBlacklistRule

        chain_path = tmp_path / "decisions.chain.jsonl"
        harness = _Harness(
            tmp_path, rules=[SymbolBlacklistRule(blacklist=["BTC/USDT"])]
        )
        from selene_space_paper.audit_ledger import HashChainLedger

        harness.pipeline._audit_ledger = HashChainLedger(chain_path)
        harness.feed()
        lines = chain_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        import json as _json

        assert _json.loads(lines[0])["payload"]["action"] == "blocked"

    def test_broken_chain_does_not_break_pipeline(self, tmp_path: Path) -> None:
        chain_path = tmp_path / "decisions.chain.jsonl"
        harness = self._harness_with_ledger(tmp_path, chain_path)
        chain_path.write_text(
            '{"seq":1,"payload":{"action":"executed"},"prev_hash":"deadbeef",'
            '"record_hash":"00000000"}\n',
            encoding="utf-8",
        )
        result = harness.feed()
        assert result.action == PipelineAction.EXECUTED
        assert result.order_id is not None
