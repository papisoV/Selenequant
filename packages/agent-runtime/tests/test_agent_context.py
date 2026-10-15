"""Behaviour locks for the context subsystem: token cost tracking, TTL
pruning, summarising compaction, and budget status/recovery."""

from __future__ import annotations

import time

import pytest

from selene_space_agent.context.budget import BudgetStatus, TokenBudget
from selene_space_agent.context.compaction import ContextCompactor
from selene_space_agent.context.pruning import ContextPruner
from selene_space_agent.cost.tracker import TokenCostTracker
from selene_space_types.agent import ChatMessage


def _msg(role: str, content: str, age_seconds: float = 0) -> ChatMessage:
    """ChatMessage with a backdated timestamp when *age_seconds* is given."""
    ts = int((time.time() - age_seconds) * 1000)
    return ChatMessage(role=role, content=content, timestamp=ts)


class TestTokenCostTracker:
    def test_usage_records_aggregate(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("sess-1", "claude-sonnet", 120, 60, 0.006, tool_name="market_ticker")
        tracker.record_usage("sess-1", "claude-sonnet", 240, 120, 0.012)

        cost = tracker.get_session_cost("sess-1")
        quintet = (
            cost["total_input_tokens"],
            cost["total_output_tokens"],
            cost["total_tokens"],
            cost["total_cost_usd"],
            cost["call_count"],
        )
        assert quintet == (360, 180, 540, pytest.approx(0.018), 2)
        assert "market_ticker" in cost["by_tool"]

    def test_unknown_session_zeroed(self) -> None:
        cost = TokenCostTracker().get_session_cost("never-used")
        pair = (cost["total_tokens"], cost["call_count"])
        assert pair == (0, 0)

    def test_daily_rollup_spans_sessions(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s-a", "model-x", 100, 50, 0.005)
        tracker.record_usage("s-b", "model-x", 200, 100, 0.010)

        today = time.strftime("%Y-%m-%d", time.gmtime())
        day = tracker.get_daily_cost(today)
        pair = (day["call_count"], day["total_cost_usd"])
        assert pair == (2, pytest.approx(0.015))

    def test_reset_empties_history(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s1", "m", 100, 50, 0.005)
        assert tracker.total_records == 1

        tracker.reset()
        assert tracker.total_records == 0


class TestContextPruner:
    def test_fresh_messages_untouched(self) -> None:
        pruner = ContextPruner(ttl_seconds=300)
        thread = [_msg("user", "hello"), _msg("tool", "result payload")]
        kept = pruner.prune(thread, budget_tokens=100_000)
        pair = (len(kept), kept[1].content)
        assert pair == (2, "result payload")

    def test_stale_tool_result_soft_trimmed(self) -> None:
        pruner = ContextPruner(ttl_seconds=60, soft_threshold=0.0)  # soft path always armed
        fat = "x" * 5000
        thread = [
            _msg("user", "hello"),
            _msg("tool", fat, age_seconds=120),  # 2 min old vs 60s TTL
        ]
        kept = pruner.prune(thread, budget_tokens=100_000)
        checks = (len(kept) == 2, "trimmed" in kept[1].content, len(kept[1].content) < len(fat))
        assert all(checks)

    def test_deeply_stale_hard_cleared(self) -> None:
        pruner = ContextPruner(ttl_seconds=60, soft_threshold=0.0, hard_threshold=0.0)
        thread = [_msg("user", "hello"), _msg("tool", "x" * 5000, age_seconds=120)]
        kept = pruner.prune(thread, budget_tokens=100_000)
        assert kept[1].content == "[Old tool result cleared]"

    def test_system_rows_immortal(self) -> None:
        pruner = ContextPruner(ttl_seconds=1, soft_threshold=0.0, hard_threshold=0.0)
        thread = [
            _msg("system", "prompt", age_seconds=600),
            _msg("tool", "data", age_seconds=120),
        ]
        kept = pruner.prune(thread, budget_tokens=100_000)
        pair = (kept[0].role, kept[0].content)
        assert pair == ("system", "prompt")

    def test_input_thread_not_mutated(self) -> None:
        pruner = ContextPruner(ttl_seconds=60, soft_threshold=0.0, hard_threshold=0.0)
        original = _msg("tool", "x" * 5000, age_seconds=120)
        kept = pruner.prune([original], budget_tokens=100_000)
        checks = (len(original.content) == 5000, kept[0].content != original.content)
        assert all(checks)


class TestContextCompactor:
    @pytest.mark.asyncio
    async def test_roomy_budget_no_compaction(self) -> None:
        compactor = ContextCompactor()
        thread = [_msg("user", "hey"), _msg("assistant", "ho")]
        kept = await compactor.compact(thread, target_tokens=100_000)
        assert len(kept) == 2

    @pytest.mark.asyncio
    async def test_without_summarizer_falls_back_to_size_mode(self) -> None:
        compactor = ContextCompactor(summarizer=None)
        bloated = [_msg("user", "x" * 400) for _ in range(20)]
        kept = await compactor.compact(bloated, target_tokens=100)
        assert any("compacted" in m.content for m in kept)

    @pytest.mark.asyncio
    async def test_summarizer_shrinks_thread(self) -> None:
        class _Stenographer:
            async def summarize(self, text: str) -> str:
                return "Condensed notes."

        compactor = ContextCompactor(summarizer=_Stenographer())
        bloated = [_msg("user", "x" * 400) for _ in range(20)]
        kept = await compactor.compact(bloated, target_tokens=100)
        assert len(kept) < len(bloated)

    @pytest.mark.asyncio
    async def test_recent_tail_protected(self) -> None:
        compactor = ContextCompactor()
        thread = [_msg("user", f"note {i}") for i in range(10)]
        kept = await compactor.compact(thread, target_tokens=10)
        # last messages are shielded from compaction
        assert any("note 9" in m.content for m in kept)


class TestTokenBudget:
    _CFG = dict(max_tokens=200_000, warning_threshold=32_000, hard_floor=16_000)

    @pytest.mark.parametrize(
        ("used", "expected"),
        [
            (100_000, BudgetStatus.OK),
            (175_000, BudgetStatus.WARNING),
            (190_000, BudgetStatus.CRITICAL),
            (200_001, BudgetStatus.OVERFLOW),
        ],
        ids=["ok", "warning", "critical", "overflow"],
    )
    def test_status_ladder(self, used: int, expected: BudgetStatus) -> None:
        assert TokenBudget(**self._CFG).check_budget(used) == expected

    def test_remaining_clamped_at_zero(self) -> None:
        budget = TokenBudget(max_tokens=200_000)
        pair = (budget.get_remaining(150_000), budget.get_remaining(250_000))
        assert pair == (50_000, 0)

    @pytest.mark.asyncio
    async def test_recovery_via_pruning(self) -> None:
        budget = TokenBudget(max_tokens=100, warning_threshold=50, hard_floor=20)
        pruner = ContextPruner(ttl_seconds=1)

        thread = [
            _msg("system", "prompt"),
            _msg("tool", "x" * 800, age_seconds=120),  # stale tool result
            _msg("user", "question"),
        ]
        used = sum(max(1, len(m.content) // 4) for m in thread)

        recovered = await budget.recover(thread, used, pruner)
        assert len(recovered) >= 1

    @pytest.mark.asyncio
    async def test_recovery_drops_oldest_when_pruning_insufficient(self) -> None:
        budget = TokenBudget(max_tokens=50, warning_threshold=20, hard_floor=10)
        pruner = ContextPruner(ttl_seconds=300)  # nothing stale to prune

        thread = [
            _msg("user", "a" * 200),
            _msg("user", "b" * 200),
            _msg("user", "c" * 40),
        ]
        used = sum(max(1, len(m.content) // 4) for m in thread)

        recovered = await budget.recover(thread, used, pruner)
        assert len(recovered) < len(thread)
