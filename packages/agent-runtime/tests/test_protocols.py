"""Behaviour locks for Protocol interfaces, ComponentRegistry CRUD, and the
bundled community implementations."""

from __future__ import annotations

import pytest

from selene_space_agent.implementations import (
    BasicContextManager,
    FixedModelRouter,
    KeywordMemoryBackend,
    RuleBasedFeedback,
    SingleAgentRunner,
)
from selene_space_agent.protocols import (
    AgentOrchestrator,
    ContextEngine,
    FeedbackEngine,
    MemoryBackend,
    MemoryEntry,
    ModelRouter,
)
from selene_space_agent.registry import ComponentRegistry

# (implementation instance, protocol it must satisfy)
_PROTOCOL_PAIRS = [
    (KeywordMemoryBackend(), MemoryBackend),
    (SingleAgentRunner(), AgentOrchestrator),
    (FixedModelRouter(), ModelRouter),
    (BasicContextManager(), ContextEngine),
    (RuleBasedFeedback(), FeedbackEngine),
]


class TestProtocolCheckable:
    @pytest.mark.parametrize(("instance", "protocol"), _PROTOCOL_PAIRS, ids=lambda p: getattr(p, "__name__", "impl"))
    def test_runtime_checkable_satisfied(self, instance, protocol) -> None:
        assert isinstance(instance, protocol)


class TestRegistryRegister:
    def test_register_then_get_same_object(self) -> None:
        reg = ComponentRegistry()
        impl = KeywordMemoryBackend()
        reg.register("memory", impl)
        assert reg.get("memory") is impl

    def test_duplicate_key_rejected(self) -> None:
        reg = ComponentRegistry()
        reg.register("memory", KeywordMemoryBackend())
        with pytest.raises(KeyError, match="already registered"):
            reg.register("memory", KeywordMemoryBackend())


class TestRegistryReplace:
    def test_swap_existing_entry(self) -> None:
        reg = ComponentRegistry()
        old, fresh = KeywordMemoryBackend(), KeywordMemoryBackend()
        reg.register("memory", old)
        reg.replace("memory", fresh)
        assert reg.get("memory") is fresh

    def test_replace_unknown_key_rejected(self) -> None:
        reg = ComponentRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.replace("ghost-slot", KeywordMemoryBackend())


class TestRegistryGetMissing:
    def test_get_unknown_key_rejected(self) -> None:
        reg = ComponentRegistry()
        with pytest.raises(KeyError, match="not registered"):
            reg.get("ghost-slot")


class TestRegistryListing:
    def test_mapping_lists_class_names(self) -> None:
        reg = ComponentRegistry()
        reg.register("memory", KeywordMemoryBackend())
        reg.register("router", FixedModelRouter())

        mapping = reg.list_registered()
        trio = ("memory" in mapping, "router" in mapping, mapping["memory"], mapping["router"])
        assert trio == (True, True, "KeywordMemoryBackend", "FixedModelRouter")

    def test_empty_registry_empty_mapping(self) -> None:
        assert ComponentRegistry().list_registered() == {}


class TestCommunityImplementations:
    @pytest.mark.asyncio
    async def test_memory_store_then_keyword_recall(self) -> None:
        backend = KeywordMemoryBackend()
        await backend.store(MemoryEntry(id="e1", content="DOGE volume spike"))
        await backend.store(MemoryEntry(id="e2", content="ETH staking note"))

        hits = await backend.recall("DOGE")
        pair = (len(hits), hits[0].id)
        assert pair == (1, "e1")

    @pytest.mark.asyncio
    async def test_memory_recall_without_match_is_empty(self) -> None:
        backend = KeywordMemoryBackend()
        assert await backend.recall("nothing-at-all") == []

    @pytest.mark.asyncio
    async def test_runner_echoes_prompt(self) -> None:
        out = await SingleAgentRunner().run("review DOT chart", {})
        assert "review DOT chart" in out

    def test_router_returns_fixed_model(self) -> None:
        router = FixedModelRouter(model="claude-sonnet")
        assert router.route([{"role": "user", "content": "hi"}]) == "claude-sonnet"

    @pytest.mark.asyncio
    async def test_context_ingest_then_assemble(self) -> None:
        ctx = BasicContextManager()
        await ctx.bootstrap({"budget": 500})
        await ctx.ingest({"type": "ticker", "data": "SOL=148"})
        assembled = await ctx.assemble(10)
        assert len(assembled) == 1

    @pytest.mark.asyncio
    async def test_feedback_accepts_positive_sharpe(self) -> None:
        verdict = await RuleBasedFeedback().analyze({"sharpe_ratio": 1.2})
        assert "acceptable" in verdict.get("recommendation", "").lower()

    @pytest.mark.asyncio
    async def test_feedback_flags_negative_sharpe(self) -> None:
        verdict = await RuleBasedFeedback().analyze({"sharpe_ratio": -0.4})
        assert "underperforms" in verdict.get("recommendation", "").lower()


class TestRegistryUtilities:
    def test_membership_probe(self) -> None:
        reg = ComponentRegistry()
        reg.register("memory", KeywordMemoryBackend())
        pair = (reg.is_registered("memory"), reg.is_registered("ghost-slot"))
        assert pair == (True, False)

    def test_len_tracks_registrations(self) -> None:
        reg = ComponentRegistry()
        checks_first = len(reg) == 0
        reg.register("a", KeywordMemoryBackend())
        reg.register("b", FixedModelRouter())
        checks_last = len(reg) == 2
        assert checks_first and checks_last
