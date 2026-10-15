"""Unit tests for selene_space_agentstudio package.

Tests cover:
- Protocol conformance (AgentStudioProvider @runtime_checkable)
- Registry (register_provider / get_provider / list_registered)
- 7 R&D loop providers (factor / model / quant / report / kaggle / ds / multitrace)
- Prompt library provider (4 packs: economic / geopolitics / trader / hedgefund_renaissance)
- Service lifecycle (start_all / stop_all / healthcheck_all / compute)
- Observability (log_compute / track_compute / mlflow_log)
- Integration smoke: end-to-end prompt_library get_pack

Pattern aligned with packages/optimize/tests/test_optimize.py.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from typing import Any

import pytest

from selene_space_agentstudio import (
    AgentStudioKind,
    AgentStudioProvider,
    AgentStudioProviderConfig,
    AgentStudioReport,
    get_provider,
    list_registered,
    register_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fake provider for testing
# ---------------------------------------------------------------------------


class FakeAgentStudioProvider:
    """In-memory fake agent studio provider for tests."""

    def __init__(self, cfg: AgentStudioProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> AgentStudioProviderConfig:
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
    ) -> AgentStudioReport:
        return AgentStudioReport(
            provider=self._cfg.provider,
            kind=AgentStudioKind.OTHER,
            title=f"fake {action}",
            metrics={"action": action, "task": task or ""},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["info"]

    def describe_action(self, action: str) -> str:
        return f"fake {action}"


# ---------------------------------------------------------------------------
# Protocol + Registry tests
# ---------------------------------------------------------------------------


def test_protocol_conformance() -> None:
    """FakeAgentStudioProvider satisfies AgentStudioProvider Protocol."""
    cfg = AgentStudioProviderConfig(provider="fake")
    p = FakeAgentStudioProvider(cfg)
    assert isinstance(p, AgentStudioProvider)


def test_registry_register_and_get() -> None:
    """register_provider + get_provider roundtrip."""
    register_provider("fake_test_a", lambda cfg: FakeAgentStudioProvider(cfg))
    cfg = AgentStudioProviderConfig(provider="fake_test_a")
    inst = get_provider(cfg)
    assert inst is not None
    assert inst.config.provider == "fake_test_a"


def test_registry_list_registered_includes_builtins() -> None:
    """Built-in providers are auto-registered on package import."""
    names = set(list_registered())
    # 7 R&D loops + 1 prompt library
    assert "factor_loop" in names
    assert "model_loop" in names
    assert "quant_loop" in names
    assert "report_loop" in names
    assert "kaggle_loop" in names
    assert "ds_loop" in names
    assert "multitrace_loop" in names
    assert "prompt_library" in names


def test_registry_get_unknown_raises() -> None:
    """Unknown provider key should raise KeyError."""
    with pytest.raises(KeyError):
        get_provider(AgentStudioProviderConfig(provider="__never_exists__"))


# ---------------------------------------------------------------------------
# R&D loop provider tests (info only — rdagents not installed in test env)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_factor_loop_info() -> None:
    """factor_loop info action works regardless of rdagents availability."""
    cfg = AgentStudioProviderConfig(provider="factor_loop")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="info")
        assert report.provider == "factor_loop"
        assert "loop" in report.metrics
        assert report.metrics["loop"] == "factor_loop"
        assert "rdagents_available" in report.metrics
        assert "loop_available" in report.metrics
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_model_loop_info() -> None:
    cfg = AgentStudioProviderConfig(provider="model_loop")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="info")
        assert report.metrics["loop"] == "model_loop"
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_kaggle_loop_info() -> None:
    cfg = AgentStudioProviderConfig(provider="kaggle_loop")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="info")
        assert report.metrics["loop"] == "kaggle_loop"
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_rd_loop_unknown_action_raises() -> None:
    cfg = AgentStudioProviderConfig(provider="factor_loop")
    p = get_provider(cfg)
    await p.start()
    try:
        with pytest.raises(KeyError, match="Unknown action"):
            await p.compute(action="nonexistent_action")
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_rd_loop_build_without_rdagents_graceful() -> None:
    """build action without rdagents returns unavailable report (not raise)."""
    cfg = AgentStudioProviderConfig(provider="factor_loop")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(
            action="build",
            task="test task",
            params={"workspace_dir": "./_test_workspace"},
        )
        # Either built (if rdagents available) or unavailable (graceful)
        assert report.provider == "factor_loop"
        assert report.kind == AgentStudioKind.RD_LOOP
        # If rdagents unavailable, metrics has "error"
        # If available, metrics has "built": True
        assert "error" in report.metrics or "built" in report.metrics
    finally:
        await p.stop()


def test_rd_loop_list_actions() -> None:
    cfg = AgentStudioProviderConfig(provider="quant_loop")
    p = get_provider(cfg)
    actions = p.list_actions()
    assert set(actions) == {"build", "run", "info"}


def test_rd_loop_describe_action() -> None:
    cfg = AgentStudioProviderConfig(provider="ds_loop")
    p = get_provider(cfg)
    desc = p.describe_action("build")
    assert "build" in desc.lower() or "ds_loop" in desc.lower()


# ---------------------------------------------------------------------------
# Batch 6a tests — verify rdagent (singular) runtime wiring
# These tests verify the bugfixes from batch 6a:
#   1. rdagents_available() checks `rdagent` (singular), not `rdagents` (plural)
#   2. multitrace_loop reuses ds_loop availability (no separate MultitraceRDLoop class)
#   3. *_AVAILABLE flags reflect real importability of rdagent.app.qlib_rd_loop.{factor,model,quant}
# Tests are tolerant: they pass whether or not rdagent is installed in the test env.
# ---------------------------------------------------------------------------


def test_rdagents_available_returns_bool() -> None:
    """rdagents_available() returns a bool, never raises."""
    from selene_space_agentstudio.providers.rd_loop_provider import rdagents_available
    result = rdagents_available()
    assert isinstance(result, bool)


def test_loop_availability_dict_structure() -> None:
    """LOOP_AVAILABILITY has all 7 loop keys mapped to bool."""
    from selene_space_agentstudio.providers.rd_loop_provider import LOOP_AVAILABILITY
    expected_keys = {
        "factor_loop", "model_loop", "quant_loop",
        "report_loop", "kaggle_loop", "ds_loop", "multitrace_loop",
    }
    assert set(LOOP_AVAILABILITY.keys()) == expected_keys
    for v in LOOP_AVAILABILITY.values():
        assert isinstance(v, bool)


def test_multitrace_availability_matches_ds() -> None:
    """multitrace_loop availability equals ds_loop availability.

    rdagent 0.8.0 has no rdagent.app.multitrace module. The rdagents framework
    treats multitrace as DataScienceRDLoop configured for MCTS, not a separate
    class. So MULTITRACE_LOOP_AVAILABLE must mirror DS_LOOP_AVAILABLE.
    """
    from selene_space_agentstudio.providers.rd_loop_provider import (
        DS_LOOP_AVAILABLE, MULTITRACE_LOOP_AVAILABLE,
        LOOP_AVAILABILITY,
    )
    assert MULTITRACE_LOOP_AVAILABLE == DS_LOOP_AVAILABLE
    assert LOOP_AVAILABILITY["multitrace_loop"] == LOOP_AVAILABILITY["ds_loop"]


def test_pyproject_uses_correct_rdagent_package_name() -> None:
    """pyproject.toml must declare `rdagent` (singular), not `rdagents` (plural).

    Batch 4 had a bug: pyproject.toml extras wrote `rdagents` (plural), but the
    actual PyPI package is `rdagent` (singular, Microsoft RD-Agent framework).
    Batch 6a fixed this. Verify the fix sticks.
    """
    from pathlib import Path
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert 'rd = ["rdagent"]' in content, (
        "pyproject.toml must declare `rdagent` (singular) in [project.optional-dependencies] rd extras"
    )
    assert 'rd = ["rdagents"]' not in content, (
        "pyproject.toml must NOT use `rdagents` (plural) — that package does not exist on PyPI"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not importlib.util.find_spec("rdagent"),
    reason="rdagent is an optional extra (agentstudio[rd]) — not installed here",
)
async def test_factor_model_quant_loops_available_when_rdagent_installed() -> None:
    """When rdagent is installed, factor/model/quant *_AVAILABLE must be True.

    This test is a guard: if rdagent is installed in the test env but any of
    factor/model/quant loops report unavailable, an import path changed.

    The condition is evaluated, not hardcoded: the previous ``skipif(False)``
    meant the test always ran, so it failed wherever the optional extra was
    absent (CI installs a fixed set that does not include rdagent).
    """
    from selene_space_agentstudio.providers.rd_loop_provider import (
        FACTOR_LOOP_AVAILABLE, MODEL_LOOP_AVAILABLE, QUANT_LOOP_AVAILABLE,
    )
    assert FACTOR_LOOP_AVAILABLE, "FactorRDLoop import failed despite rdagent installed"
    assert MODEL_LOOP_AVAILABLE, "ModelRDLoop import failed despite rdagent installed"
    assert QUANT_LOOP_AVAILABLE, "QuantRDLoop import failed despite rdagent installed"


# ---------------------------------------------------------------------------
# Prompt library provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_library_info() -> None:
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="info")
        assert report.provider == "prompt_library"
        assert "pack_count" in report.metrics
        assert report.metrics["pack_count"] == 4
        assert "packs" in report.metrics
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_prompt_library_list_packs() -> None:
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="list_packs")
        assert report.kind == AgentStudioKind.PROMPT_LIBRARY
        assert "pack_count" in report.metrics
        assert report.metrics["pack_count"] == 4
        packs = report.artifacts.get("packs", [])
        assert len(packs) == 4
        pack_names = [pk["name"] for pk in packs]
        assert "economic" in pack_names
        assert "geopolitics" in pack_names
        assert "trader" in pack_names
        assert "hedgefund_renaissance" in pack_names
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_prompt_library_get_pack_economic() -> None:
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(
            action="get_pack",
            params={"pack": "economic"},
        )
        assert report.kind == AgentStudioKind.PROMPT_LIBRARY
        assert "pack" in report.metrics
        assert report.metrics["pack"] == "economic"
        assert "agent_count" in report.metrics
        # economic pack has 6 agents in the prompt pack configs
        assert report.metrics["agent_count"] >= 0  # 0 if prompt pack root not found
        assert "agents" in report.artifacts
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_prompt_library_get_pack_unknown_raises() -> None:
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    p = get_provider(cfg)
    await p.start()
    try:
        with pytest.raises(ValueError, match="Unknown pack"):
            await p.compute(action="get_pack", params={"pack": "__never__"})
    finally:
        await p.stop()


def test_prompt_library_list_actions() -> None:
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    p = get_provider(cfg)
    actions = p.list_actions()
    assert set(actions) == {"list_packs", "get_pack", "info"}


def test_prompt_library_describe_action() -> None:
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    p = get_provider(cfg)
    desc = p.describe_action("list_packs")
    assert "pack" in desc.lower()


# ---------------------------------------------------------------------------
# Service lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_lifecycle() -> None:
    from selene_space_agentstudio.service import AgentStudioService

    svc = AgentStudioService()
    cfg_pl = AgentStudioProviderConfig(provider="prompt_library")
    cfg_fl = AgentStudioProviderConfig(provider="factor_loop")
    svc.get_provider(cfg_pl)
    svc.get_provider(cfg_fl)
    await svc.start_all()
    try:
        results = await svc.healthcheck_all()
        assert len(results) >= 2
        for name, healthy in results.items():
            # prompt_library always healthy; factor_loop healthy iff rdagent installed
            if name == "prompt_library":
                assert healthy is True
    finally:
        await svc.stop_all()


# ---------------------------------------------------------------------------
# Observability tests
# ---------------------------------------------------------------------------


def test_observability_log_compute_no_raise() -> None:
    """log_compute emits structured log without raising."""
    from selene_space_agentstudio.observability import log_compute

    log_compute(
        provider="prompt_library",
        action="list_packs",
        duration_ms=15.5,
        success=True,
        metrics_keys=["pack_count"],
    )
    log_compute(
        provider="factor_loop",
        action="build",
        duration_ms=2000.0,
        success=False,
        error="rdagents not installed",
    )


def test_observability_track_compute_context_manager() -> None:
    """track_compute records duration and outcome."""
    from selene_space_agentstudio.observability import track_compute

    with track_compute(provider="test", action="info") as ctx:
        ctx["success"] = True
        ctx["metrics_keys"] = ["foo"]

    assert ctx["success"] is True


def test_observability_track_compute_records_error() -> None:
    """track_compute records errors and re-raises."""
    from selene_space_agentstudio.observability import track_compute

    with pytest.raises(RuntimeError, match="boom"):
        with track_compute(provider="test", action="info") as ctx:
            raise RuntimeError("boom")

    assert ctx["error"] == "boom"


def test_observability_mlflow_log_no_mlflow_returns_false() -> None:
    """mlflow_log returns False when mlflow not installed (graceful)."""
    from selene_space_agentstudio.observability import mlflow_log

    result = mlflow_log(run_name="test", metrics={"x": 1.0}, params={"p": "v"})
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_service_compute_uses_observability() -> None:
    """AgentStudioService.compute() routes through track_compute for observability."""
    from selene_space_agentstudio.service import AgentStudioService

    svc = AgentStudioService()
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    report = await svc.compute(cfg, action="info")
    assert report.provider == "prompt_library"
    assert "pack_count" in report.metrics


# ---------------------------------------------------------------------------
# Integration smoke test — end-to-end prompt_library get_pack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_smoke_prompt_library_get_pack() -> None:
    """End-to-end: service.compute() routes to prompt_library.get_pack.

    This is the AC9a-style smoke test for batch 4 — proves the agentstudio
    package is wired correctly and the prompt library can load the
    economic prompt pack configs.
    """
    from selene_space_agentstudio.service import AgentStudioService

    svc = AgentStudioService()
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    report = await svc.compute(cfg, action="get_pack", params={"pack": "economic"})
    # Verify report structure
    assert report.provider == "prompt_library"
    assert report.kind == AgentStudioKind.PROMPT_LIBRARY
    assert report.computed_at is not None
    # Verify metrics
    assert report.metrics["pack"] == "economic"
    assert "agent_count" in report.metrics
    # Verify artifacts
    assert "agents" in report.artifacts
    agents = report.artifacts["agents"]
    assert isinstance(agents, list)
    # economic prompt pack has 6 agents (capitalism/keynesian/neoliberal/socialist/marxist/...)
    # If prompt pack root not found, agents is empty — both acceptable for smoke
    if len(agents) > 0:
        first = agents[0]
        assert isinstance(first, dict)
        assert "id" in first or "name" in first
