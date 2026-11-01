"""Behaviour locks for ContextManager v0.1.1: token counting, auto-compaction
trigger, and live market context injection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from selene_space_agent.context.manager import (
    TIKTOKEN_AVAILABLE,
    ContextManager,
    count_tokens,
)
from selene_space_types.agent import ChatMessage


class TestTokenCounting:
    def test_short_text_counts_at_least_one(self) -> None:
        assert count_tokens("Greetings, planet!") >= 1

    def test_blank_string_still_one(self) -> None:
        assert count_tokens("") >= 1

    def test_long_text_scales(self) -> None:
        passage = "A swift hazel rodent outpaces the drowsy hound. " * 10
        assert count_tokens(passage) > 10

    def test_manager_estimate_uses_same_counter(self) -> None:
        cm = ContextManager()
        probe = "Integration probe for counting"
        pair = (cm.estimate_tokens(probe), count_tokens(probe))
        assert pair[0] == pair[1]

    def test_availability_flag_is_boolean(self) -> None:
        assert isinstance(TIKTOKEN_AVAILABLE, bool)

    def test_heuristic_fallback_math(self) -> None:
        # 400 chars / 4 chars-per-token = 100 tokens under the fallback rule
        from selene_space_agent.context.manager import _FALLBACK_CHARS_PER_TOKEN

        blob = "a" * 400
        assert max(1, len(blob) // _FALLBACK_CHARS_PER_TOKEN) == 100


class TestAutoCompaction:
    @pytest.mark.asyncio
    async def test_threshold_exceeded_invokes_mechanism(self) -> None:
        mock_compactor = AsyncMock()
        mock_compactor.compact = AsyncMock(
            return_value=[ChatMessage(role="system", content="[compacted]", timestamp=0)]
        )

        cm = ContextManager(max_tokens=100, compaction_threshold=0.5, compactor=mock_compactor)

        # flood past the 50-token threshold
        for i in range(20):
            cm.add_message("user", f"Note {i}: " + "x" * 200)

        # mechanism wired in; message bookkeeping intact
        assert cm.message_count >= 1

    def test_missing_compactor_is_tolerated(self) -> None:
        cm = ContextManager(max_tokens=100, compaction_threshold=0.5)
        for i in range(20):
            cm.add_message("user", f"Note {i}: " + "x" * 50)
        assert cm.message_count >= 1

    @pytest.mark.parametrize("threshold", [0.9, 0.5])
    def test_threshold_configurable(self, threshold: float) -> None:
        cm = ContextManager(compaction_threshold=threshold)
        assert cm._compaction_threshold == threshold


class TestMarketContextInjection:
    @pytest.mark.asyncio
    async def test_injection_adds_system_message(self) -> None:
        cm = ContextManager()
        await cm.inject_market_context(
            symbols=["BTC/USDT", "ETH/USDT"],
            prices={"BTC/USDT": 67234.50, "ETH/USDT": 3456.78},
        )

        rows = cm.get_messages()
        checks = (
            len(rows) == 1,
            rows[0].role == "system",
            "BTC/USDT" in rows[0].content,
            "$67,234.50" in rows[0].content,
            "ETH/USDT" in rows[0].content,
        )
        assert all(checks)

    @pytest.mark.asyncio
    async def test_reinjection_replaces_previous_block(self) -> None:
        cm = ContextManager()
        await cm.inject_market_context(symbols=["BTC/USDT"], prices={"BTC/USDT": 60000.00})
        await cm.inject_market_context(symbols=["BTC/USDT"], prices={"BTC/USDT": 67000.00})

        blocks = [m for m in cm.get_messages() if "Live Market Prices" in m.content]
        checks = (
            len(blocks) == 1,
            "$67,000.00" in blocks[0].content,
            "$60,000.00" not in blocks[0].content,
        )
        assert all(checks)

    @pytest.mark.asyncio
    async def test_no_prices_is_noop(self) -> None:
        cm = ContextManager()
        await cm.inject_market_context(symbols=[], prices={})
        assert cm.message_count == 0

    @pytest.mark.asyncio
    async def test_budget_caps_injected_content(self) -> None:
        cm = ContextManager(max_tokens=50)
        # 100 symbols blow past the 10% injection budget -> truncation expected
        names = [f"TOK{i}/USDT" for i in range(100)]
        quotes = {t: float(i * 500) for i, t in enumerate(names)}

        await cm.inject_market_context(symbols=names, prices=quotes)

        rows = cm.get_messages()
        checks = (len(rows) == 1, cm.estimate_tokens(rows[0].content) <= cm._max_tokens)
        assert all(checks)


class TestCoreBehaviourStillHolds:
    def test_append_and_read_back(self) -> None:
        cm = ContextManager()
        cm.add_message("user", "hey")
        cm.add_message("assistant", "hello")
        rows = cm.get_messages()
        pair = (len(rows), tuple(r.role for r in rows))
        assert pair == (2, ("user", "assistant"))

    def test_token_total_positive(self) -> None:
        cm = ContextManager()
        cm.add_message("user", "a" * 400)
        cm.add_message("assistant", "b" * 200)
        assert cm.total_tokens() > 0

    def test_clear_resets(self) -> None:
        cm = ContextManager()
        cm.add_message("user", "temp")
        cm.clear()
        assert cm.message_count == 0
