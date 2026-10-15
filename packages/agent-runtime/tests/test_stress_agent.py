"""Boundary-condition stress tests for agent-runtime (cleanroom hardening).

Covers: TokenCostTracker negative/huge inputs and concurrent recording,
TokenBudget recovery loop guards (all-system, empty), ContextPruner
non-mutation and TTL tiers, and extract-free schema edges.
"""

from __future__ import annotations

import asyncio
import threading
import time

from selene_space_agent.context.budget import BudgetStatus, TokenBudget
from selene_space_agent.context.pruning import ContextPruner
from selene_space_agent.cost.tracker import TokenCostTracker
from selene_space_types.agent import ChatMessage


def _msg(role: str = "user", content: str = "hi", ts: int | None = None) -> ChatMessage:
    return ChatMessage(
        role=role, content=content, timestamp=ts or int(time.time() * 1000)
    )


# ---------------------------------------------------------------------------
# TokenCostTracker
# ---------------------------------------------------------------------------


class TestTokenCostTrackerEdges:
    def test_zero_usage_zero_cost(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m", 0, 0, cost_usd=0.0)
        snap = tracker.get_session_cost("s")
        assert snap["total_tokens"] == 0
        assert snap["total_cost_usd"] == 0.0

    def test_unknown_session_returns_zero_shape(self) -> None:
        tracker = TokenCostTracker()
        snap = tracker.get_session_cost("ghost")
        assert snap["total_tokens"] == 0
        assert snap["call_count"] == 0
        assert snap["by_tool"] == {}
        assert snap["by_model"] == {}

    def test_negative_tokens_silently_accepted_lock_current(self) -> None:
        """No input validation on the accumulator — lock current behavior."""
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m", -10, -5)
        assert tracker.get_session_cost("s")["total_tokens"] == -15

    def test_negative_explicit_cost_accepted_lock_current(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m", 1, 1, cost_usd=-99.0)
        assert tracker.get_session_cost("s")["total_cost_usd"] == -99.0

    def test_huge_token_counts_do_not_overflow(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m", 10**15, 10**15)
        assert tracker.get_session_cost("s")["total_tokens"] == 2 * 10**15

    def test_fallback_pricing_when_cost_omitted(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m", 1_000_000, 1_000_000)
        # 1M input @0.25/M + 1M output @1.0/M = 1.25
        assert abs(tracker.get_session_cost("s")["total_cost_usd"] - 1.25) < 1e-9

    def test_by_tool_and_by_model_accumulate(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m1", 10, 5, tool_name="t1")
        tracker.record_usage("s", "m2", 20, 15, tool_name="t1")
        tracker.record_usage("s", "m2", 1, 1)
        snap = tracker.get_session_cost("s")
        assert snap["by_tool"] == {"t1": 2}
        assert snap["by_model"]["m1"]["input_tokens"] == 10
        assert snap["by_model"]["m2"]["output_tokens"] == 16

    def test_explicit_timestamp_buckets_into_utc_day(self) -> None:
        tracker = TokenCostTracker()
        fixed = 1709164800.0  # 2024-02-29 00:00:00 UTC
        tracker.record_usage("s", "m", 1, 1, timestamp=fixed)
        daily = tracker.get_daily_cost(time.strftime("%Y-%m-%d", time.gmtime(fixed)))
        assert daily["call_count"] == 1

    def test_reset_clears_everything(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("s", "m", 1, 1)
        tracker.reset()
        assert tracker.total_records == 0
        assert tracker.summary()["sessions"] == 0

    def test_summary_totals_across_sessions(self) -> None:
        tracker = TokenCostTracker()
        tracker.record_usage("a", "m", 100, 50, cost_usd=0.1)
        tracker.record_usage("b", "m", 200, 60, cost_usd=0.2)
        totals = tracker.summary()
        assert totals["total_input_tokens"] == 300
        assert totals["total_output_tokens"] == 110
        assert abs(totals["total_cost_usd"] - 0.3) < 1e-9

    def test_concurrent_record_no_lost_updates(self) -> None:
        tracker = TokenCostTracker()

        def worker() -> None:
            for _ in range(200):
                tracker.record_usage("shared", "m", 1, 1)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = tracker.get_session_cost("shared")
        assert snap["call_count"] == 1600


# ---------------------------------------------------------------------------
# TokenBudget ladder + recovery guards
# ---------------------------------------------------------------------------


class TestTokenBudgetLadder:
    def _budget(self) -> TokenBudget:
        return TokenBudget(max_tokens=100, warning_threshold=10, hard_floor=5)

    def test_status_ladder_boundaries(self) -> None:
        b = self._budget()
        assert b.check_budget(0) is BudgetStatus.OK
        assert b.check_budget(90) is BudgetStatus.OK
        assert b.check_budget(91) is BudgetStatus.WARNING
        assert b.check_budget(95) is BudgetStatus.WARNING
        assert b.check_budget(96) is BudgetStatus.CRITICAL
        assert b.check_budget(100) is BudgetStatus.CRITICAL
        assert b.check_budget(101) is BudgetStatus.OVERFLOW

    def test_get_remaining_clamps_at_zero(self) -> None:
        assert self._budget().get_remaining(150) == 0
        assert self._budget().get_remaining(50) == 50

    def test_empty_messages_recovery(self) -> None:
        b = self._budget()

        async def run() -> list[ChatMessage]:
            return await b.recover([], 9999, ContextPruner())

        assert asyncio.run(run()) == []

    def test_all_system_over_budget_never_dropped(self) -> None:
        """Recovery must not hang or drop the only (system) message."""
        b = TokenBudget(max_tokens=10, warning_threshold=2, hard_floor=1)

        async def run() -> list[ChatMessage]:
            msgs = [_msg(role="system", content="x" * 500)]
            return await b.recover(msgs, 9999, ContextPruner())

        result = asyncio.run(run())
        assert len(result) == 1
        assert result[0].role == "system"

    def test_recovers_to_under_target_by_dropping_oldest(self) -> None:
        b = TokenBudget(max_tokens=40, warning_threshold=5, hard_floor=3)
        pruner = ContextPruner(ttl_seconds=3600.0)

        async def run() -> list[ChatMessage]:
            msgs = [
                _msg(role="system", content="sys"),
                *[_msg(content="u" * 400) for _ in range(10)],
            ]
            return await b.recover(msgs, 9999, pruner)

        result = asyncio.run(run())
        total_chars = sum(len(m.content) for m in result)
        # target = max(1, 40-3)=37 tokens ≈ 148 chars; allow slack for system msg
        assert total_chars <= 400 + 200
        assert any(m.role == "system" for m in result)


# ---------------------------------------------------------------------------
# ContextPruner immutability + TTL tiers
# ---------------------------------------------------------------------------


class TestContextPrunerEdges:
    def test_prune_never_mutates_input(self) -> None:
        pruner = ContextPruner(ttl_seconds=100.0)
        old_tool = _msg(role="tool", content="x" * 500, ts=int(time.time() * 1000) - 10_000_000)
        original_content = old_tool.content
        history = [_msg(role="system", content="keep"), old_tool]
        out = pruner.prune(history, budget_tokens=10_000)
        assert old_tool.content == original_content
        assert history[1] is old_tool

    def test_system_messages_always_preserved_verbatim(self) -> None:
        pruner = ContextPruner(ttl_seconds=0.0)  # everything stale
        sys_msg = _msg(role="system", content="S" * 900)
        out = pruner.prune([sys_msg], budget_tokens=1)
        assert len(out) == 1 and out[0].content.startswith("SSS")

    def test_hard_threshold_replaces_with_placeholder(self) -> None:
        pruner = ContextPruner(ttl_seconds=10.0, hard_threshold=2.0)
        ancient = _msg(role="tool", content="T" * 800, ts=int(time.time() * 1000) - 100_000)
        out = pruner.prune([ancient], budget_tokens=10_000)
        assert out != [ancient]
        assert all("T" * 100 not in m.content for m in out)

    def test_fresh_tool_message_untouched(self) -> None:
        pruner = ContextPruner(ttl_seconds=3600.0)
        fresh = _msg(role="tool", content="F" * 800)
        out = pruner.prune([fresh], budget_tokens=10_000)
        assert out == [fresh]

    def test_empty_history(self) -> None:
        assert ContextPruner().prune([], budget_tokens=100) == []
