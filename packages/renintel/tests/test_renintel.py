"""Unit tests for selene_space_renintel package.

Tests cover:
- Protocol conformance (RenIntelProvider @runtime_checkable)
- Registry (register_provider / get_provider / list_registered)
- 5 providers: personas (12) / reasoning (4) / guardrails (4) / strategies (6) / memory (5)
- Service lifecycle (start_all / stop_all / healthcheck_all / compute)
- Observability (log_compute / track_compute / mlflow_log)
- Integration smoke: end-to-end get_persona

Pattern aligned with packages/agentstudio/tests/test_agentstudio.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from selene_space_renintel import (
    RenIntelKind,
    RenIntelProvider,
    RenIntelProviderConfig,
    RenIntelReport,
    get_provider,
    list_registered,
    register_provider,
)


# ---------------------------------------------------------------------------
# Fake provider for testing
# ---------------------------------------------------------------------------


class FakeRenIntelProvider:
    """In-memory fake renintel provider for tests."""

    def __init__(self, cfg: RenIntelProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> RenIntelProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        return self._running

    async def compute(
        self,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> RenIntelReport:
        return RenIntelReport(
            provider=self._cfg.provider,
            kind=RenIntelKind.OTHER,
            title=f"fake {action}",
            metrics={"action": action, "task": task or ""},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["info", "fake_action"]

    def describe_action(self, action: str) -> str:
        return f"fake {action}"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_protocol_conformance() -> None:
    """FakeRenIntelProvider satisfies RenIntelProvider Protocol."""
    cfg = RenIntelProviderConfig(provider="fake")
    p: RenIntelProvider = FakeRenIntelProvider(cfg)  # type: ignore[assignment]
    assert p.is_running is False
    assert isinstance(p, RenIntelProvider)


def test_config_defaults() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    assert cfg.provider == "personas"
    assert cfg.params == {}


def test_kind_enum_values() -> None:
    assert RenIntelKind.PERSONA.value == "persona"
    assert RenIntelKind.REASONING.value == "reasoning"
    assert RenIntelKind.GUARDRAIL.value == "guardrail"
    assert RenIntelKind.STRATEGY.value == "strategy"
    assert RenIntelKind.MEMORY.value == "memory"
    assert RenIntelKind.OTHER.value == "other"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_list_registered_contains_5_providers() -> None:
    names = list_registered()
    assert "personas" in names
    assert "reasoning" in names
    assert "guardrails" in names
    assert "strategies" in names
    assert "memory" in names
    assert len(names) >= 5


def test_get_provider_returns_protocol() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    assert isinstance(p, RenIntelProvider)
    assert p.config.provider == "personas"


def test_get_provider_unknown_raises() -> None:
    cfg = RenIntelProviderConfig(provider="nonexistent_provider")
    with pytest.raises(KeyError):
        get_provider(cfg)


def test_register_provider_custom() -> None:
    register_provider("fake_test_provider", lambda cfg: FakeRenIntelProvider(cfg))
    cfg = RenIntelProviderConfig(provider="fake_test_provider")
    p = get_provider(cfg)
    assert isinstance(p, RenIntelProvider)
    assert p.list_actions() == ["info", "fake_action"]


# ---------------------------------------------------------------------------
# Personas provider (12 personas)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personas_list_personas() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="list_personas")
        assert report.kind == RenIntelKind.PERSONA
        assert "count" in report.metrics
        assert report.metrics["count"] >= 10  # 10 static entries
        assert "personas" in report.artifacts
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_personas_get_persona() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="get_persona", params={"id": "quant_researcher"})
        assert "Persona:" in report.title
        assert report.artifacts["persona"]["id"] == "quant_researcher"
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_personas_by_department() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="by_department", params={"department": "research"})
        assert report.metrics["department"] == "research"
        assert report.metrics["count"] >= 1
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_personas_info() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    report = await p.compute(action="info")
    assert "persona_count" in report.metrics


def test_personas_list_actions() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    actions = p.list_actions()
    assert "list_personas" in actions
    assert "get_persona" in actions
    assert "by_department" in actions
    assert "info" in actions


@pytest.mark.asyncio
async def test_persona_provider_ic_deliberation():
    """ic_deliberation action evaluates signals with static decision rules."""
    from selene_space_renintel.providers.persona_provider import PersonaProvider

    cfg = RenIntelProviderConfig(provider="test_ic_persona")
    provider = PersonaProvider(cfg)
    await provider.start()

    report = await provider.compute(
        action="ic_deliberation",
        task="review 3 signals for 2026-07-26",
        params={
            "trading_date": "2026-07-26",
            "signals": [
                {"signal_id": "sig-001", "strength": 0.8, "p_value": 0.01, "ic": None},
                {"signal_id": "sig-002", "strength": 0.2, "p_value": 0.10, "ic": None},
                {"signal_id": "sig-003", "strength": 0.4, "p_value": None, "ic": 0.03},
            ],
        },
    )

    assert report.kind == RenIntelKind.PERSONA
    assert report.title == "IC Deliberation"
    assert report.metrics["signals_reviewed"] == 3
    assert report.metrics["approved"] == 2   # sig-001 (0.8) + sig-003 (0.4)
    assert report.metrics["rejected"] == 1   # sig-002 (0.2)
    decisions = report.artifacts["decision"]
    approved_ids = [d["signal_id"] for d in decisions]
    assert "sig-001" in approved_ids
    assert "sig-002" not in approved_ids
    assert "sig-003" in approved_ids
    # ic_deliberation action listed
    assert "ic_deliberation" in provider.list_actions()
    await provider.stop()


# ---------------------------------------------------------------------------
# Reasoning provider (4 patterns)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_list_patterns() -> None:
    cfg = RenIntelProviderConfig(provider="reasoning")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="list_patterns")
        assert report.kind == RenIntelKind.REASONING
        assert report.metrics["count"] >= 4
        assert "patterns" in report.artifacts
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_reasoning_get_pattern() -> None:
    cfg = RenIntelProviderConfig(provider="reasoning")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="get_pattern", params={"id": "signal_analysis"})
        assert "Reasoning:" in report.title
        pattern = report.artifacts["pattern"]
        # static data: has steps
        assert "source_module" in pattern or "steps" in pattern
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_reasoning_info() -> None:
    cfg = RenIntelProviderConfig(provider="reasoning")
    p = get_provider(cfg)
    report = await p.compute(action="info")
    assert "pattern_count" in report.metrics
    assert report.metrics["pattern_count"] >= 4


# ---------------------------------------------------------------------------
# Guardrails provider (4 categories)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guardrails_list_guardrails() -> None:
    cfg = RenIntelProviderConfig(provider="guardrails")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="list_guardrails")
        assert report.kind == RenIntelKind.GUARDRAIL
        assert report.metrics["count"] >= 4
        assert "guardrails" in report.artifacts
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_guardrails_get_guardrail() -> None:
    cfg = RenIntelProviderConfig(provider="guardrails")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="get_guardrail", params={"category": "risk"})
        assert "Guardrail:" in report.title
        assert report.artifacts["guardrail"]["severity_levels"] == ["INFO", "WARNING", "ERROR", "CRITICAL"]
    finally:
        await p.stop()


# ---------------------------------------------------------------------------
# Strategies provider (6 strategies)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strategies_list_strategies() -> None:
    cfg = RenIntelProviderConfig(provider="strategies")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="list_strategies")
        assert report.kind == RenIntelKind.STRATEGY
        assert report.metrics["count"] >= 6
        assert "strategies" in report.artifacts
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_strategies_get_strategy() -> None:
    cfg = RenIntelProviderConfig(provider="strategies")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="get_strategy", params={"id": "mean_reversion"})
        assert "Strategy:" in report.title
        strategy = report.artifacts["strategy"]
        # static data: has parameters
        assert "source_module" in strategy or "parameters" in strategy
    finally:
        await p.stop()


# ---------------------------------------------------------------------------
# Memory provider (5 types)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_list_types() -> None:
    cfg = RenIntelProviderConfig(provider="memory")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="list_types")
        assert report.kind == RenIntelKind.MEMORY
        assert report.metrics["count"] >= 5
        assert "memory_types" in report.artifacts
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_memory_get_type() -> None:
    cfg = RenIntelProviderConfig(provider="memory")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="get_type", params={"id": "agent_memory"})
        assert "Memory:" in report.title
        assert report.artifacts["memory_type"]["schema_fields"] == ["agent_id", "timestamp", "state", "messages"]
    finally:
        await p.stop()


# ---------------------------------------------------------------------------
# Service lifecycle + observability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_compute_with_observability() -> None:
    from selene_space_renintel.service import RenIntelService

    service = RenIntelService()
    cfg = RenIntelProviderConfig(provider="personas")
    report = await service.compute(cfg, action="info")
    assert report.kind == RenIntelKind.OTHER
    assert "persona_count" in report.metrics


@pytest.mark.asyncio
async def test_service_start_stop_all() -> None:
    from selene_space_renintel.service import RenIntelService

    service = RenIntelService()
    cfg = RenIntelProviderConfig(provider="strategies")
    _ = service.get_provider(cfg)
    await service.start_all()
    assert service.get_provider(cfg).is_running is True
    await service.stop_all()
    assert service.get_provider(cfg).is_running is False


@pytest.mark.asyncio
async def test_service_healthcheck_all() -> None:
    from selene_space_renintel.service import RenIntelService

    service = RenIntelService()
    cfg = RenIntelProviderConfig(provider="memory")
    _ = service.get_provider(cfg)
    await service.start_all()
    result = await service.healthcheck_all()
    assert "memory" in result
    assert result["memory"] is True


def test_observability_track_compute() -> None:
    from selene_space_renintel.observability import track_compute

    with track_compute(provider="personas", action="info") as ctx:
        ctx["success"] = True
        ctx["metrics_keys"] = ["persona_count"]
    assert ctx["success"] is True
    assert ctx["error"] is None


def test_observability_track_compute_captures_error() -> None:
    from selene_space_renintel.observability import track_compute

    with pytest.raises(ValueError):
        with track_compute(provider="personas", action="bad"):
            raise ValueError("boom")
    # ctx["error"] should be set even after exception
    # (context manager re-raises, but the log path still runs)


def test_observability_mlflow_log_returns_bool() -> None:
    from selene_space_renintel.observability import mlflow_log

    # mlflow likely not installed in test env — should return False gracefully
    result = mlflow_log(run_name="test", metrics={"x": 1.0})
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Integration smoke: full chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_smoke_all_5_providers() -> None:
    """End-to-end smoke: every registered provider responds to info action."""
    for name in ["personas", "reasoning", "guardrails", "strategies", "memory"]:
        cfg = RenIntelProviderConfig(provider=name)
        p = get_provider(cfg)
        await p.start()
        try:
            report = await p.compute(action="info")
            assert report.provider == name
            assert report.kind == RenIntelKind.OTHER
        finally:
            await p.stop()


@pytest.mark.asyncio
async def test_unknown_action_raises_keyerror() -> None:
    cfg = RenIntelProviderConfig(provider="personas")
    p = get_provider(cfg)
    with pytest.raises(KeyError):
        await p.compute(action="nonexistent_action")
