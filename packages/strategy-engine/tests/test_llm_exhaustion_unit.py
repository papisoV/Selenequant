"""Exhaustion + KillSwitch unit tests for LLMSignalStrategy (v4 任务4).

Verifies the three-tier fallback degradation flow (llm_signal_strategy.py:270-337)
and the exhaustion → CRITICAL → KillSwitch STRATEGY scope chain (v4 A2/Mi1).

Tests are physically decoupled from the 24h shadow run (M1): they exercise the
strategy + router + advisory rule + kill switch directly with mock providers,
no live LLM calls, no market data, no event bus.

Mock injection point: LLMProvider layer (not response layer). The real
LLMRouter chain runs so provider fallback semantics are exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from selene_space_llm.base import (
    LLMConfig,
    LLMError,
    LLMMessage,
    LLMProvider,
)
from selene_space_llm.router import LLMRouter
from selene_space_risk.dex_rules import LLMAdvisoryRule
from selene_space_risk.kill_switch import KillSwitch
from selene_space_strategy.llm_signal_strategy import LLMSignalStrategy
from selene_space_types.agent import TradeIntent
from selene_space_types.llm_signal import LLMSignalConfig


# ---------------------------------------------------------------------------
# Mock providers (inject at provider layer, not response layer)
# ---------------------------------------------------------------------------


class _SuccessProvider(LLMProvider):
    """Returns a valid LLMSignal-shaped dict on every call."""

    def __init__(self, name: str = "success", action: str = "hold",
                 size_pct: float = 0.0) -> None:
        super().__init__(config=LLMConfig(model="stub-success"))
        self._name = name
        self._action = action
        self._size_pct = size_pct
        self.call_count = 0

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        return "ok"

    async def chat_stream(self, messages: list[LLMMessage], **kwargs: Any) -> AsyncIterator[str]:
        yield "ok"

    async def generate_structured(
        self, messages: list[LLMMessage], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self.call_count += 1
        return {
            "action": self._action,
            "confidence": 0.5,
            "rationale": "stub",
            "suggested_size_pct": self._size_pct,
            "risk_notes": "",
        }


class MockAllFailProvider(LLMProvider):
    """Always raises LLMError — simulates a dead provider."""

    def __init__(self, name: str = "fail", exc: LLMError | None = None) -> None:
        super().__init__(config=LLMConfig(model="stub-fail"))
        self._name = name
        self._exc = exc or LLMError(f"mock fail from {name}")
        self.call_count = 0

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        raise self._exc

    async def chat_stream(self, messages: list[LLMMessage], **kwargs: Any) -> AsyncIterator[str]:
        raise self._exc
        yield ""  # type: ignore[misc]

    async def generate_structured(
        self, messages: list[LLMMessage], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self.call_count += 1
        raise self._exc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_kill_switch(tmp_path):
    """Reset KillSwitch singleton + use a tmp state file."""
    KillSwitch._reset_singleton()
    ks = KillSwitch(state_path=tmp_path / "kill_switch_test.json")
    yield ks
    # Cleanup: deactivate any scopes activated during the test.
    for scope, scope_id in ks.active_scopes():
        ks.deactivate_scope(scope, scope_id)
    KillSwitch._reset_singleton()


@pytest.fixture
def ctx() -> dict[str, Any]:
    """Minimal strategy context for evaluate()."""
    return {
        "symbol": "BTC/USDT",
        "klines": [
            {"open": 100.0, "high": 105.0, "low": 99.0, "close": 102.0, "volume": 1000.0}
            for _ in range(20)
        ],
        "equity": 1000.0,
        "current_price": 102.0,
    }


def _make_strategy(router, *, threshold: int = 5, max_retries: int = 0,
                   retry_backoff_base_s: float = 0.001,
                   strategy_id: str = "llm_signal_strategy_v1") -> LLMSignalStrategy:
    """Build an LLMSignalStrategy with fast-test config."""
    config = LLMSignalConfig(
        llm_provider="stub",
        max_retries=max_retries,
        retry_backoff_base_s=retry_backoff_base_s,
        fallback_exhausted_threshold=threshold,
        fallback_exhausted_window_s=3600.0,
        signal_ttl_s=300.0,
        strategy_id=strategy_id,
    )
    return LLMSignalStrategy(router=router, config=config)


# ---------------------------------------------------------------------------
# Test 1: provider1 fails → provider2 succeeds → no exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider1_fail_provider2_success(ctx):
    """provider1 raises LLMError → router falls back to provider2 → success.

    fallback_exhausted_count must be 0 (no exhaustion recorded).
    """
    p1 = MockAllFailProvider(name="fail1")
    p2 = _SuccessProvider(name="success")
    router = LLMRouter(providers=[("fail1", p1), ("success", p2)])
    strategy = _make_strategy(router, max_retries=0)

    intent = await strategy.evaluate(ctx)

    # provider2 returned "hold" → evaluate returns None (no trade).
    assert intent is None
    # provider1 was tried and failed; provider2 was tried and succeeded.
    assert p1.call_count == 1
    assert p2.call_count == 1
    # No exhaustion recorded.
    assert strategy.fallback_exhausted_count() == 0


# ---------------------------------------------------------------------------
# Test 2: all providers fail → exhaustion recorded → count increments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_providers_fail_exhaustion(ctx):
    """All providers raise → fallback exhausted → count increments.

    Each evaluate() call records exactly one exhaustion event.
    After N calls, fallback_exhausted_count() == N (within window).
    """
    p1 = MockAllFailProvider(name="fail1")
    p2 = MockAllFailProvider(name="fail2")
    router = LLMRouter(providers=[("fail1", p1), ("fail2", p2)])
    strategy = _make_strategy(router, max_retries=0)

    # Trigger 5 evaluate calls — each exhausts.
    for _ in range(5):
        intent = await strategy.evaluate(ctx)
        assert intent is None  # exhaustion → None

    # All 5 exhaustion events recorded (within window).
    assert strategy.fallback_exhausted_count() == 5
    # Each provider tried once per evaluate (max_retries=0).
    assert p1.call_count == 5
    assert p2.call_count == 5


# ---------------------------------------------------------------------------
# Test 3: exponential backoff delays (1s / 2s / 4s)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exponential_backoff(monkeypatch, ctx):
    """Verify retry backoff schedule: base * 2**attempt.

    With max_retries=3, base=0.001, the delays are:
      attempt 0 → 0.001s, attempt 1 → 0.002s, attempt 2 → 0.004s.
    We monkeypatch asyncio.sleep to record delays without actually sleeping.
    """
    import asyncio as _asyncio

    recorded_delays: list[float] = []

    async def fake_sleep(delay):
        recorded_delays.append(delay)

    monkeypatch.setattr(_asyncio, "sleep", fake_sleep)

    p1 = MockAllFailProvider(name="fail1")
    router = LLMRouter(providers=[("fail1", p1)])
    strategy = _make_strategy(router, max_retries=3, retry_backoff_base_s=1.0)

    intent = await strategy.evaluate(ctx)
    assert intent is None

    # max_retries=3 → 4 total attempts (0,1,2,3) → 3 backoff sleeps
    # between attempts (after attempts 0,1,2; no sleep after attempt 3).
    assert len(recorded_delays) == 3
    # Delays: 1.0 * 2**0 = 1.0, 1.0 * 2**1 = 2.0, 1.0 * 2**2 = 4.0
    assert recorded_delays[0] == pytest.approx(1.0)
    assert recorded_delays[1] == pytest.approx(2.0)
    assert recorded_delays[2] == pytest.approx(4.0)
    # 4 attempts total on the single failing provider.
    assert p1.call_count == 4
    assert strategy.fallback_exhausted_count() == 1


# ---------------------------------------------------------------------------
# Test 4: fallback_exhausted_count sliding window eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_exhausted_count_sliding_window(ctx):
    """Events older than fallback_exhausted_window_s are evicted."""
    p1 = MockAllFailProvider(name="fail1")
    router = LLMRouter(providers=[("fail1", p1)])
    # Short window (10s) so we can simulate eviction with a fake clock.
    config = LLMSignalConfig(
        max_retries=0,
        retry_backoff_base_s=0.001,
        fallback_exhausted_window_s=10.0,
        fallback_exhausted_threshold=5,
        signal_ttl_s=300.0,
        strategy_id="llm_signal_strategy_v1",
    )

    # Fake clock: list of timestamps consumed by each time.time() call.
    fake_now = [1000.0]

    def fake_clock():
        return fake_now[0]

    strategy = LLMSignalStrategy(router=router, config=config, clock=fake_clock)

    # Record 3 events at t=1000.
    for _ in range(3):
        await strategy.evaluate(ctx)
    assert strategy.fallback_exhausted_count() == 3

    # Advance clock past window (1000 + 11 = 1011 > 1000 + 10).
    fake_now[0] = 1011.0
    # Record 1 more event at t=1011 — old 3 events should evict.
    await strategy.evaluate(ctx)
    # Only the new event (at t=1011) is within the window [1001, 1011].
    assert strategy.fallback_exhausted_count() == 1


# ---------------------------------------------------------------------------
# Test 5: get_latest_signal TTL (300s)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_signal_ttl(ctx):
    """Cached signal is valid within TTL, None after TTL expires."""
    # Use action="long" + non-zero size so _build_intent produces a valid intent.
    p1 = _SuccessProvider(name="success", action="long", size_pct=0.1)
    router = LLMRouter(providers=[("success", p1)])
    config = LLMSignalConfig(
        max_retries=0,
        retry_backoff_base_s=0.001,
        signal_ttl_s=300.0,
        strategy_id="llm_signal_strategy_v1",
    )

    fake_now = [1000.0]

    def fake_clock():
        return fake_now[0]

    strategy = LLMSignalStrategy(router=router, config=config, clock=fake_clock)

    # First evaluate caches a signal at t=1000.
    intent = await strategy.evaluate(ctx)
    assert intent is not None  # action="long" → trade intent
    assert strategy.get_latest_signal() is not None  # within TTL

    # Advance to t=1300 (exactly TTL) — still valid (age == ttl, not > ttl).
    fake_now[0] = 1300.0
    assert strategy.get_latest_signal() is not None

    # Advance to t=1301 (age=301 > 300) — expired, returns None.
    fake_now[0] = 1301.0
    assert strategy.get_latest_signal() is None


# ---------------------------------------------------------------------------
# Test 6: exhaustion triggers KillSwitch STRATEGY scope (v4 A2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhaustion_triggers_killswitch_strategy_scope(ctx, fresh_kill_switch):
    """5 exhaustions → LLMAdvisoryRule raises CRITICAL → KillSwitch STRATEGY scope.

    Verifies (v4 A2):
      - KillSwitch activates STRATEGY scope for llm_signal_strategy_v1.
      - A1/A2 intents (different strategy_id) are NOT blocked (scope isolation).
    """
    p1 = MockAllFailProvider(name="fail1")
    p2 = MockAllFailProvider(name="fail2")
    router = LLMRouter(providers=[("fail1", p1), ("fail2", p2)])
    strategy = _make_strategy(router, max_retries=0, threshold=5,
                              strategy_id="llm_signal_strategy_v1")

    # Trigger 5 evaluate calls — each exhausts.
    for _ in range(5):
        await strategy.evaluate(ctx)

    assert strategy.fallback_exhausted_count() == 5

    # LLMAdvisoryRule reads fallback_exhausted_count and raises CRITICAL
    # when count >= threshold (5).
    rule = LLMAdvisoryRule(llm_strategy=strategy)

    # Build an intent that looks like it came from the LLM strategy.
    llm_intent = TradeIntent(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        price=102.0,
        order_type="market",
        reasoning="llm signal",
        confidence=0.5,
        risk_params={"strategy_id": "llm_signal_strategy_v1"},
        timestamp=0,
    )

    context: dict[str, Any] = {}
    alert = rule.check(llm_intent, context)
    assert alert is not None
    assert "CRITICAL" in str(alert.level) or alert.level.value == "critical"

    # The rule propagates strategy_id into context (dex_rules.py:378-379).
    assert context.get("strategy_id") == "llm_signal_strategy_v1"

    # Simulate the engine's auto-trigger (engine.py:208-213).
    fresh_kill_switch.auto_activate(
        reason="Auto-triggered by risk rules: llm_advisory",
        triggered_by_rule="llm_advisory",
        strategy_id=context.get("strategy_id"),
    )

    # KillSwitch STRATEGY scope activated for llm_signal_strategy_v1.
    assert fresh_kill_switch.is_active_for(strategy_id="llm_signal_strategy_v1") is True

    # A1/A2 intents (different strategy_id) are NOT blocked — scope isolation.
    assert fresh_kill_switch.is_active_for(strategy_id="a1_trailing_stop_v1") is False
    assert fresh_kill_switch.is_active_for(strategy_id="a2_limit_pegging_v1") is False

    # SYSTEM scope is NOT active (STRATEGY scope only).
    assert fresh_kill_switch.is_active is False


# ---------------------------------------------------------------------------
# Test 7: A1/A2 intent isolation — different strategy_id not误杀
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a1_a2_intent_isolation(ctx, fresh_kill_switch):
    """When LLM strategy is killed, A1/A2 intents still pass risk check.

    This is the core v4 A2 guarantee: STRATEGY scope blocks ONLY the
    degraded strategy, not the whole system.
    """
    p1 = MockAllFailProvider(name="fail1")
    router = LLMRouter(providers=[("fail1", p1)])
    strategy = _make_strategy(router, max_retries=0, threshold=5,
                              strategy_id="llm_signal_strategy_v1")

    # Exhaust 5 times to trigger.
    for _ in range(5):
        await strategy.evaluate(ctx)

    # Activate KillSwitch STRATEGY scope for the LLM strategy.
    fresh_kill_switch.auto_activate(
        reason="LLM exhaustion",
        triggered_by_rule="llm_advisory",
        strategy_id="llm_signal_strategy_v1",
    )

    # Build an A1 intent (different strategy_id).
    a1_intent = TradeIntent(
        symbol="BTC/USDT",
        side="sell",
        quantity=0.05,
        price=102.0,
        order_type="market",
        reasoning="a1 trailing stop",
        confidence=0.8,
        risk_params={"strategy_id": "a1_trailing_stop_v1"},
        timestamp=0,
    )

    # A1 intent is NOT blocked — STRATEGY scope only blocks llm_signal_strategy_v1.
    assert fresh_kill_switch.is_active_for(
        strategy_id=a1_intent.risk_params["strategy_id"]
    ) is False

    # LLM intent IS blocked.
    llm_intent = TradeIntent(
        symbol="BTC/USDT",
        side="buy",
        quantity=0.1,
        price=102.0,
        order_type="market",
        reasoning="llm signal",
        confidence=0.5,
        risk_params={"strategy_id": "llm_signal_strategy_v1"},
        timestamp=0,
    )
    assert fresh_kill_switch.is_active_for(
        strategy_id=llm_intent.risk_params["strategy_id"]
    ) is True
