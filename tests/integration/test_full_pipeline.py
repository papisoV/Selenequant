"""End-to-end sweeps across every subsystem: strategy → backtest → paper → risk.

Also covers the agent tool loop, market state analysis and storage round-trips.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from selene_space_agent.context.manager import ContextManager
from selene_space_agent.prompt_builder import AgentContext
from selene_space_agent.runtime import AgentRuntime
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_market.state_engine import MarketStateEngine
from selene_space_paper.decision_pipeline import DecisionPipeline, PipelineAction, PipelineConfig
from selene_space_paper.orders import PaperOrderManager
from selene_space_risk.engine import RiskEngine
from selene_space_risk.rules import DailyLossLimitRule
from selene_space_storage.repositories.backtests import BacktestRepository
from selene_space_storage.repositories.strategies import StrategyRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_strategy.compiler import compile as compile_strategy
from selene_space_strategy.models import load_strategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.agent import AgentStreamEventType, TradeIntent
from selene_space_types.market import KlineEvent
from selene_space_types.risk import RiskLevel as RiskLevelEnum
from selene_space_types.strategy import (
    BacktestMetrics,
    BacktestResult,
    Signal,
    StrategyConfig,
    StrategyType,
)
from selene_space_types.trading import OrderSide, OrderType

REPO_ROOT = Path(__file__).resolve().parents[2]
SMA_TEMPLATE = REPO_ROOT / "packages/strategy-engine/selene_space_strategy/templates/sma_cross.yaml"


class _EchoTool(BaseTool):
    """Trivial SAFE tool used to exercise the agent tool loop."""

    @property
    def name(self) -> str:
        return "integration_echo"

    @property
    def description(self) -> str:
        return "Echo integration test message."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }

    @property
    def risk_level(self):
        return RiskLevelEnum.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(output=str(args.get("msg", "")), error=None)


def _risk_context(acct) -> dict[str, Any]:
    return dict(
        last_trade_times={},
        daily_realized_pnl=0.0,
        positions={},
        total_equity=float(acct.current_balance),
    )


def _buy_signal() -> Signal:
    return Signal(
        reason="integration BUY",
        strength=0.9,
        timestamp=int(time.time() * 1000),
        side=OrderSide.BUY,
        symbol="BTC/USDT",
        strategy_id="int-test",
    )


def test_sma_template_runs_through_backtest(demo_data: pd.DataFrame) -> None:
    cfg = load_strategy(SMA_TEMPLATE)
    runtime = StrategyRuntime(compile_strategy(cfg), max_bars=2000)
    engine = BacktestEngine(
        config=BacktestConfig(initial_cash=10_000.0, strategy_id=cfg.id),
    )
    result = engine.run(strategy=runtime, data=demo_data)

    assert result.metrics.total_trades > 0
    assert math.isfinite(result.metrics.sharpe_ratio)
    assert len(result.equity_curve) == len(demo_data)


def test_pipeline_handles_signal_end_to_end(
    paper_account,
    risk_engine,
) -> None:
    mgr = PaperOrderManager()
    pipe = DecisionPipeline(
        risk_engine=risk_engine,
        order_manager=mgr,
        config=PipelineConfig(
            default_quantity=0.01,
            min_order_interval_seconds=0.0,
            default_account_id=paper_account.id,
        ),
        risk_context_provider=lambda: _risk_context(paper_account),
    )
    outcome = pipe.process_signal(_buy_signal())
    assert outcome.action in (PipelineAction.EXECUTED, PipelineAction.BLOCKED)
    if outcome.action == PipelineAction.EXECUTED:
        assert outcome.order_id is not None
    else:
        assert "Risk" in outcome.reason or "risk" in outcome.reason.lower()


def test_daily_loss_rule_flags_big_drawdown() -> None:
    engine = RiskEngine([DailyLossLimitRule(max_daily_loss_pct=0.001)])
    intent = TradeIntent(
        confidence=0.5,
        price=50_000.0,
        quantity=0.01,
        side=OrderSide.BUY,
        symbol="BTC/USDT",
        order_type=OrderType.MARKET,
        reasoning="integration",
        risk_params={},
        timestamp=int(time.time() * 1000),
    )
    ctx = {
        "daily_realized_pnl": -10.0,
        "total_equity": 10_000.0,
    }
    decision = engine.pre_check(intent, ctx)
    # The daily-loss rule is advisory (DANGEROUS): either it blocks outright or
    # it degrades the decision level rather than silently passing.
    assert not decision.allowed or decision.level.value in ("dangerous", "restricted")
    if decision.alerts:
        assert any("daily_loss_limit" in a.rule_id for a in decision.alerts)


def test_agent_emits_tool_call_and_result(mock_llm) -> None:
    catalog = ToolCatalog()
    catalog.register(_EchoTool())

    runtime = AgentRuntime(
        llm=mock_llm,
        tool_catalog=catalog,
        context_manager=ContextManager(),
        prompt_context=AgentContext(available_tools=catalog.get_tool_definitions()),
        max_tool_rounds=5,
    )

    async def _drain() -> list:
        collected = []
        async for ev in runtime.process_message("run echo"):
            collected.append(ev)
        return collected

    seen = {e.type for e in asyncio.run(_drain())}
    assert AgentStreamEventType.TOOL_CALL in seen
    assert AgentStreamEventType.TOOL_RESULT in seen


def test_market_state_from_synthetic_klines(demo_data: pd.DataFrame) -> None:
    def _event(row) -> KlineEvent:
        return KlineEvent(
            closed=True,
            volume=float(row.volume),
            low=float(row.low),
            close=float(row.close),
            high=float(row.high),
            open=float(row.open),
            interval="1h",
            timestamp=int(row.timestamp),
            symbol="BTC/USDT",
            exchange="backtest",
        )

    events = [_event(row) for row in demo_data.itertuples(index=False)]
    analyzer = MarketStateEngine()
    state = analyzer.analyze("BTC/USDT", events)
    assert state.regime.value
    assert state.volatility >= 0.0


def test_strategy_and_backtest_rows_roundtrip() -> None:
    async def _scenario() -> None:
        db = AsyncSQLiteManager(":memory:")
        await db.connect()
        try:
            strategies = StrategyRepository(db)
            cfg = StrategyConfig(
                symbols=["BTC/USDT"],
                entry_rules={},
                exit_rules={},
                risk_params={},
                interval="1h",
                description="test",
                type=StrategyType.SMA_CROSS,
                name="Integration Strategy",
                id="int-strat-1",
                parameters={"a": 1},
            )
            saved_id = await strategies.save(cfg)
            assert saved_id == cfg.id
            fetched = await strategies.get(saved_id)
            assert fetched is not None
            assert fetched.model_dump() == cfg.model_dump()

            backtests = BacktestRepository(db)
            metrics = BacktestMetrics(
                total_trades=3,
                profit_factor=1.2,
                win_rate=0.5,
                max_drawdown=-0.02,
                sharpe_ratio=1.0,
                annual_return=0.1,
                total_return=0.01,
            )
            record = BacktestResult(
                created_at=1_700_000_000_000,
                trades_count=3,
                equity_curve=[10_000.0, 10_050.0, 10_020.0],
                metrics=metrics,
                end_date=datetime(2025, 1, 31, tzinfo=UTC),
                start_date=datetime(2025, 1, 1, tzinfo=UTC),
                strategy_id=saved_id,
                id="int-bt-1",
            )
            saved_bt_id = await backtests.save(record)
            assert saved_bt_id == record.id
            reloaded = await backtests.get(saved_bt_id)
            assert reloaded is not None
            # Drawdown/buy-hold curves are recomputed on load when stored empty;
            # compare everything except those derived curves.
            got = reloaded.model_dump()
            want = record.model_dump()
            for derived in ("drawdown_curve", "buy_hold_curve"):
                got.pop(derived, None)
                want.pop(derived, None)
            assert got == want
            assert len(reloaded.drawdown_curve) == len(record.equity_curve)
            assert len(reloaded.buy_hold_curve) == len(record.equity_curve)
        finally:
            await db.close()

    asyncio.run(_scenario())
