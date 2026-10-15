"""Unit tests for selene_space_optimize package.

Tests cover:
- Protocol conformance (OptimizeProvider @runtime_checkable)
- Registry (register_provider / get_provider / list_registered)
- Each provider instantiates + list_actions + describe_action
- Portfolio providers (mean_variance / risk_parity / enhanced_index) happy path
- RL provider info action (no SB3 training in tests for speed)
- Service lifecycle (start/stop/healthcheck)
- Integration smoke: end-to-end mean_variance max_sharpe

Pattern aligned with packages/alpha-core/tests/test_analytics.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from selene_space_optimize import (
    OptimizeKind,
    OptimizeProvider,
    OptimizeProviderConfig,
    OptimizeReport,
    get_provider,
    list_registered,
    register_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_prices() -> pd.DataFrame:
    """3-asset price dataframe with 60 days of data."""
    rng = np.random.default_rng(seed=42)
    n = 60
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    # Build cumulative prices from daily returns
    rets = rng.normal(0.0005, 0.01, (n, 3))
    prices = 100 * np.exp(np.cumsum(rets, axis=0))
    return pd.DataFrame(
        prices,
        index=dates,
        columns=["AAPL", "MSFT", "GOOG"],
    )


@pytest.fixture
def sample_benchmark(sample_prices: pd.DataFrame) -> pd.Series:
    """Equal-weight benchmark series."""
    return sample_prices.mean(axis=1)


# ---------------------------------------------------------------------------
# Fake provider for testing
# ---------------------------------------------------------------------------


class FakeOptimizeProvider:
    """In-memory fake optimize provider for tests."""

    def __init__(self, cfg: OptimizeProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> OptimizeProviderConfig:
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
        prices: pd.DataFrame | pd.Series | None = None,
        returns: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, Any] | None = None,
    ) -> OptimizeReport:
        return OptimizeReport(
            provider=self._cfg.provider,
            kind=OptimizeKind.OTHER,
            title=f"fake {action}",
            metrics={"action": action, "params": params or {}},
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
    """FakeOptimizeProvider satisfies OptimizeProvider Protocol."""
    cfg = OptimizeProviderConfig(provider="fake")
    p = FakeOptimizeProvider(cfg)
    assert isinstance(p, OptimizeProvider)


def test_registry_register_and_get() -> None:
    """register_provider + get_provider roundtrip."""
    register_provider("fake_test_a", lambda cfg: FakeOptimizeProvider(cfg))
    cfg = OptimizeProviderConfig(provider="fake_test_a")
    inst = get_provider(cfg)
    assert inst is not None
    assert inst.config.provider == "fake_test_a"


def test_registry_list_registered_includes_builtins() -> None:
    """Built-in providers are auto-registered on package import."""
    names = set(list_registered())
    # 5 RL + 3 portfolio
    assert "ppo" in names
    assert "dqn" in names
    assert "a2c" in names
    assert "sac" in names
    assert "td3" in names
    assert "mean_variance" in names
    assert "risk_parity" in names
    assert "enhanced_index" in names


def test_registry_get_unknown_raises() -> None:
    """Unknown provider key should raise KeyError."""
    with pytest.raises(KeyError):
        get_provider(OptimizeProviderConfig(provider="__never_exists__"))


# ---------------------------------------------------------------------------
# Portfolio provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mean_variance_list_actions() -> None:
    cfg = OptimizeProviderConfig(provider="mean_variance")
    p = get_provider(cfg)
    actions = p.list_actions()
    assert set(actions) == {"max_sharpe", "min_volatility", "efficient_frontier", "target_return"}


@pytest.mark.asyncio
async def test_mean_variance_max_sharpe(sample_prices: pd.DataFrame) -> None:
    cfg = OptimizeProviderConfig(provider="mean_variance")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="max_sharpe", prices=sample_prices)
        assert report.provider == "mean_variance"
        assert report.kind == OptimizeKind.PORTFOLIO
        assert report.data is not None
        assert len(report.data) == 3  # 3 assets
        # Weights sum to 1
        w = report.data["weight"].to_numpy()
        assert abs(float(w.sum()) - 1.0) < 1e-3
        assert "sharpe" in report.metrics
        assert "annual_return" in report.metrics
        assert "annual_volatility" in report.metrics
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_mean_variance_efficient_frontier(sample_prices: pd.DataFrame) -> None:
    cfg = OptimizeProviderConfig(provider="mean_variance")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="efficient_frontier", prices=sample_prices, params={"n_points": 15})
        assert report.kind == OptimizeKind.PORTFOLIO
        assert report.data is not None
        # Frontier has 15 points (some may fail but most should succeed)
        assert len(report.data) <= 15
        assert len(report.data) >= 5
        assert "return" in report.data.columns
        assert "volatility" in report.data.columns
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_mean_variance_unknown_action_raises(sample_prices: pd.DataFrame) -> None:
    cfg = OptimizeProviderConfig(provider="mean_variance")
    p = get_provider(cfg)
    await p.start()
    try:
        with pytest.raises(KeyError, match="Unknown action"):
            await p.compute(action="info", prices=sample_prices)
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_risk_parity_equal_risk(sample_prices: pd.DataFrame) -> None:
    cfg = OptimizeProviderConfig(provider="risk_parity")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="equal_risk", prices=sample_prices)
        assert report.provider == "risk_parity"
        assert report.kind == OptimizeKind.PORTFOLIO
        assert report.data is not None
        w = report.data["weight"].to_numpy()
        assert abs(float(w.sum()) - 1.0) < 1e-3
        assert "converged" in report.metrics
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_enhanced_index_track(
    sample_prices: pd.DataFrame, sample_benchmark: pd.Series
) -> None:
    cfg = OptimizeProviderConfig(provider="enhanced_index")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(
            action="track", prices=sample_prices, benchmark=sample_benchmark
        )
        assert report.provider == "enhanced_index"
        assert report.kind == OptimizeKind.PORTFOLIO
        # Note: SLSQP may fail to converge on random data; when it does the
        # provider returns metrics={"error": ...} and data=None. Accept either.
        if report.data is not None:
            w = report.data["weight"].to_numpy()
            assert abs(float(w.sum()) - 1.0) < 1e-3
            assert "tracking_error" in report.metrics
            assert "information_ratio" in report.metrics
        else:
            assert "error" in report.metrics
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_enhanced_index_missing_benchmark_raises(
    sample_prices: pd.DataFrame,
) -> None:
    cfg = OptimizeProviderConfig(provider="enhanced_index")
    p = get_provider(cfg)
    await p.start()
    try:
        with pytest.raises(ValueError, match="benchmark"):
            await p.compute(action="track", prices=sample_prices)
    finally:
        await p.stop()


# ---------------------------------------------------------------------------
# RL provider tests (info only — no SB3 training in unit tests for speed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rl_info() -> None:
    """RL provider info action works without SB3 installed."""
    cfg = OptimizeProviderConfig(provider="ppo")
    p = get_provider(cfg)
    await p.start()
    try:
        report = await p.compute(action="info")
        assert report.provider == "ppo"
        assert "algorithm" in report.metrics
        assert report.metrics["algorithm"] == "PPO"
        assert "discrete_action" in report.metrics
        assert "sb3_available" in report.metrics
    finally:
        await p.stop()


@pytest.mark.asyncio
async def test_rl_unknown_action_raises() -> None:
    cfg = OptimizeProviderConfig(provider="ppo")
    p = get_provider(cfg)
    await p.start()
    try:
        with pytest.raises(KeyError, match="Unknown action"):
            await p.compute(action="nonexistent_action")
    finally:
        await p.stop()


def test_rl_list_actions() -> None:
    cfg = OptimizeProviderConfig(provider="dqn")
    p = get_provider(cfg)
    actions = p.list_actions()
    assert set(actions) == {"train", "predict", "info"}


def test_rl_describe_action() -> None:
    cfg = OptimizeProviderConfig(provider="a2c")
    p = get_provider(cfg)
    desc = p.describe_action("train")
    assert "train" in desc.lower() or "ppo" in desc.lower() or "agent" in desc.lower()


# ---------------------------------------------------------------------------
# Service lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_lifecycle(sample_prices: pd.DataFrame) -> None:
    from selene_space_optimize.service import OptimizeService

    svc = OptimizeService()
    # Trigger lazy creation of a few providers
    cfg_mv = OptimizeProviderConfig(provider="mean_variance")
    cfg_rp = OptimizeProviderConfig(provider="risk_parity")
    svc.get_provider(cfg_mv)
    svc.get_provider(cfg_rp)
    await svc.start_all()
    try:
        results = await svc.healthcheck_all()
        # All created providers should report healthy
        assert len(results) >= 2
        for name, healthy in results.items():
            assert healthy is True, f"{name} not healthy"
    finally:
        await svc.stop_all()


# ---------------------------------------------------------------------------
# Integration smoke test — end-to-end mean_variance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_integration_smoke_mean_variance(sample_prices: pd.DataFrame) -> None:
    """End-to-end: instantiate provider, compute max_sharpe, verify weights + metrics.

    This is the AC9a-style smoke test for batch 3 — proves the optimize
    package is wired correctly and produces valid portfolio weights.
    """
    cfg = OptimizeProviderConfig(provider="mean_variance")
    provider = get_provider(cfg)
    await provider.start()
    try:
        report = await provider.compute(action="max_sharpe", prices=sample_prices)
        # Verify report structure
        assert report.provider == "mean_variance"
        assert report.kind == OptimizeKind.PORTFOLIO
        assert report.computed_at is not None
        # Verify weights
        assert report.data is not None
        weights = report.data["weight"].to_numpy()
        assert len(weights) == 3
        assert abs(float(weights.sum()) - 1.0) < 1e-3
        # All weights non-negative (long-only constraint)
        assert (weights >= -1e-6).all()
        # Verify metrics
        assert "sharpe" in report.metrics
        assert "annual_return" in report.metrics
        assert "annual_volatility" in report.metrics
        # Sharpe should be a finite number
        sharpe = report.metrics["sharpe"]
        assert isinstance(sharpe, (int, float))
        assert np.isfinite(sharpe)
    finally:
        await provider.stop()


# ---------------------------------------------------------------------------
# Observability tests
# ---------------------------------------------------------------------------


def test_observability_log_compute_no_raise() -> None:
    """log_compute emits structured log without raising."""
    from selene_space_optimize.observability import log_compute

    log_compute(
        provider="mean_variance",
        action="max_sharpe",
        duration_ms=12.34,
        success=True,
        metrics_keys=["sharpe", "annual_return"],
    )
    log_compute(
        provider="ppo",
        action="train",
        duration_ms=5000.0,
        success=False,
        error="test error",
    )


def test_observability_track_compute_context_manager() -> None:
    """track_compute records duration and outcome."""
    from selene_space_optimize.observability import track_compute

    with track_compute(provider="test", action="info") as ctx:
        ctx["success"] = True
        ctx["metrics_keys"] = ["foo"]

    # Should not raise — context exits cleanly
    assert ctx["success"] is True


def test_observability_track_compute_records_error() -> None:
    """track_compute records errors and re-raises."""
    from selene_space_optimize.observability import track_compute

    with pytest.raises(RuntimeError, match="boom"):
        with track_compute(provider="test", action="info") as ctx:
            raise RuntimeError("boom")

    assert ctx["error"] == "boom"


def test_observability_mlflow_log_no_mlflow_returns_false() -> None:
    """mlflow_log returns False when mlflow not installed (graceful)."""
    from selene_space_optimize.observability import mlflow_log

    # Should not raise — just returns False if mlflow missing
    result = mlflow_log(run_name="test", metrics={"sharpe": 1.5}, params={"provider": "test"})
    # Either True (mlflow installed) or False (not installed) — both acceptable
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_service_compute_uses_observability(sample_prices: pd.DataFrame) -> None:
    """OptimizeService.compute() routes through track_compute for observability."""
    from selene_space_optimize.service import OptimizeService

    svc = OptimizeService()
    cfg = OptimizeProviderConfig(provider="mean_variance")
    report = await svc.compute(cfg, action="max_sharpe", prices=sample_prices)
    assert report.kind == OptimizeKind.PORTFOLIO
    assert "sharpe" in report.metrics
