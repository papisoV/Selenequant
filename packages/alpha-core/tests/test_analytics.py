"""Unit tests for selene_space_alpha.analytics package.

Tests cover:
- Protocol conformance (AnalyticsProvider @runtime_checkable)
- Registry (register_provider / get_provider / list_registered)
- Each provider instantiates + list_actions + describe_action
- StatsModels happy path (real compute, no heavy deps)
- QuantStats healthcheck (graceful skip if not installed)
- FakeProvider lifecycle (in-memory)

Pattern aligned with packages/macrodata/tests/test_macrodata.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.analytics import (
    AnalyticsProvider,
    AnalyticsProviderConfig,
    AnalyticsReport,
    AnalyticsReportKind,
    get_provider,
    list_registered,
    register_provider,
)


# ---------------------------------------------------------------------------
# Fake provider for testing
# ---------------------------------------------------------------------------


class FakeAnalyticsProvider:
    """In-memory fake analytics provider for tests."""

    def __init__(self, cfg: AnalyticsProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> AnalyticsProviderConfig:
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
        returns: pd.DataFrame | pd.Series | None = None,
        prices: pd.DataFrame | pd.Series | None = None,
        weights: list[float] | None = None,
        benchmark: pd.DataFrame | pd.Series | None = None,
        params: dict[str, object] | None = None,
    ) -> AnalyticsReport:
        return AnalyticsReport(
            provider=self._cfg.provider,
            kind=AnalyticsReportKind.OTHER,
            title=f"fake {action}",
            metrics={"action": action, "params": params or {}},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["fake_action"]

    def describe_action(self, action: str) -> str:
        return f"fake description for {action}"


# ---------------------------------------------------------------------------
# Protocol + Config tests
# ---------------------------------------------------------------------------


def test_protocol_conformance() -> None:
    """FakeAnalyticsProvider should satisfy AnalyticsProvider Protocol."""
    fake = FakeAnalyticsProvider(AnalyticsProviderConfig(provider="fake"))
    assert isinstance(fake, AnalyticsProvider)


def test_protocol_rejects_non_conforming() -> None:
    """A class missing methods should not satisfy the Protocol."""

    class Broken:
        pass

    assert not isinstance(Broken(), AnalyticsProvider)


def test_config_pydantic() -> None:
    """AnalyticsProviderConfig should validate via pydantic."""
    cfg = AnalyticsProviderConfig(provider="quantstats", options={"window": 30})
    assert cfg.provider == "quantstats"
    assert cfg.options == {"window": 30}
    assert cfg.cache_ttl_seconds == 3600


def test_report_kind_enum() -> None:
    """AnalyticsReportKind should have the expected members."""
    assert AnalyticsReportKind.PERFORMANCE.value == "performance"
    assert AnalyticsReportKind.PORTFOLIO.value == "portfolio"
    assert AnalyticsReportKind.FORECAST.value == "forecast"


def test_report_dataframe_field() -> None:
    """AnalyticsReport should accept DataFrame data."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    report = AnalyticsReport(
        provider="fake",
        kind=AnalyticsReportKind.PERFORMANCE,
        title="test",
        metrics={"x": 1},
        data=df,
        computed_at=datetime.now(UTC),
    )
    assert len(report.data) == 3
    assert report.metrics == {"x": 1}


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_registry_register_and_get() -> None:
    """register_provider + get_provider should roundtrip."""
    register_provider("test_fake", lambda cfg: FakeAnalyticsProvider(cfg))
    cfg = AnalyticsProviderConfig(provider="test_fake")
    p = get_provider(cfg)
    assert isinstance(p, FakeAnalyticsProvider)
    assert p.config.provider == "test_fake"


def test_registry_unknown_provider_raises() -> None:
    """get_provider should raise KeyError for unknown providers."""
    with pytest.raises(KeyError, match="Unknown analytics provider"):
        get_provider(AnalyticsProviderConfig(provider="does_not_exist_xyz"))


def test_all_eight_providers_registered() -> None:
    """All 8 high-value providers should be auto-registered on import."""
    registered = list_registered()
    expected = {
        "quantstats",
        "pyportfolioopt",
        "skfolio",
        "statsmodels",
        "ffn",
        "tsmoothie",
        "gluonts",
        "finrl",
    }
    assert expected.issubset(set(registered)), (
        f"missing: {expected - set(registered)}"
    )


# ---------------------------------------------------------------------------
# Per-provider instantiation + actions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider_id",
    ["quantstats", "pyportfolioopt", "skfolio", "statsmodels", "ffn", "tsmoothie", "gluonts", "finrl"],
)
def test_provider_creates_and_lists_actions(provider_id: str) -> None:
    """Each provider should be instantiable and expose a non-empty action list."""
    p = get_provider(AnalyticsProviderConfig(provider=provider_id))
    assert isinstance(p, AnalyticsProvider)
    actions = p.list_actions()
    assert len(actions) > 0
    for action in actions:
        desc = p.describe_action(action)
        assert isinstance(desc, str) and len(desc) > 0


@pytest.mark.asyncio
async def test_fake_provider_lifecycle() -> None:
    """FakeAnalyticsProvider start/stop/compute lifecycle."""
    register_provider("test_fake2", lambda cfg: FakeAnalyticsProvider(cfg))
    p = get_provider(AnalyticsProviderConfig(provider="test_fake2"))
    assert not p.is_running
    await p.start()
    assert p.is_running
    report = await p.compute(action="fake_action", params={"k": 1})
    assert report.title == "fake fake_action"
    assert report.metrics["action"] == "fake_action"
    await p.stop()
    assert not p.is_running


# ---------------------------------------------------------------------------
# StatsModels real compute (no heavy deps)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_statsmodels_adf_compute() -> None:
    """StatsModelsProvider should run an ADF test on a synthetic series."""
    register_provider("statsmodels_test", lambda cfg: get_provider(AnalyticsProviderConfig(provider="statsmodels")))
    # Re-fetch the real statsmodels provider (not the lambda wrapper above).
    p = get_provider(AnalyticsProviderConfig(provider="statsmodels"))
    if not await p.healthcheck():
        pytest.skip("statsmodels not installed")
    rng = np.random.default_rng(42)
    # Stationary white noise → ADF p-value should be small.
    s = pd.Series(rng.standard_normal(200), index=pd.date_range("2024-01-01", periods=200, freq="D"))
    await p.start()
    report = await p.compute(action="adf", returns=s)
    assert report.kind == AnalyticsReportKind.STATISTICS
    assert "p_value" in report.metrics
    assert report.metrics["stationary"] is True


@pytest.mark.asyncio
async def test_statsmodels_cointegration_compute() -> None:
    """StatsModelsProvider should run a cointegration test on two correlated series."""
    p = get_provider(AnalyticsProviderConfig(provider="statsmodels"))
    if not await p.healthcheck():
        pytest.skip("statsmodels not installed")
    rng = np.random.default_rng(7)
    n = 300
    x = rng.standard_normal(n).cumsum()
    y = 0.5 * x + rng.standard_normal(n) * 0.1  # y cointegrated with x
    prices = pd.DataFrame(
        {"A": x, "B": y},
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )
    await p.start()
    report = await p.compute(action="cointegration", prices=prices)
    assert "p_value" in report.metrics
    assert isinstance(report.metrics["cointegrated"], bool)


# ---------------------------------------------------------------------------
# QuantStats healthcheck (skip if not installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quantstats_healthcheck_returns_bool() -> None:
    """QuantStatsProvider healthcheck should return a bool (not raise)."""
    p = get_provider(AnalyticsProviderConfig(provider="quantstats"))
    ok = await p.healthcheck()
    assert isinstance(ok, bool)


# ---------------------------------------------------------------------------
# QuantStats real compute (cumulative / distribution / rolling)
# ---------------------------------------------------------------------------


def _quantstats_provider():
    p = get_provider(AnalyticsProviderConfig(provider="quantstats"))
    return p


def _sample_returns(n: int = 400, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.Series(rng.normal(0.0005, 0.01, n), index=idx)


@pytest.mark.asyncio
async def test_quantstats_cumulative_action() -> None:
    """cumulative action should return strategy + benchmark cumprod curves."""
    p = _quantstats_provider()
    if not await p.healthcheck():
        pytest.skip("quantstats not installed")
    ret = _sample_returns()
    bench = _sample_returns(seed=7)
    await p.start()
    report = await p.compute(action="cumulative", returns=ret, benchmark=bench)
    assert {"strategy_cum", "benchmark_cum"} <= set(report.data.columns)
    assert len(report.data) == len(ret)
    assert report.metrics["total_return"] is not None


@pytest.mark.asyncio
async def test_quantstats_cumulative_without_benchmark() -> None:
    """cumulative action should degrade gracefully without a benchmark."""
    p = _quantstats_provider()
    if not await p.healthcheck():
        pytest.skip("quantstats not installed")
    await p.start()
    report = await p.compute(action="cumulative", returns=_sample_returns())
    assert "strategy_cum" in report.data.columns
    assert len(report.data) > 0


@pytest.mark.asyncio
async def test_quantstats_distribution_action() -> None:
    """distribution action should expose histogram bins + counts."""
    p = _quantstats_provider()
    if not await p.healthcheck():
        pytest.skip("quantstats not installed")
    await p.start()
    report = await p.compute(action="distribution", returns=_sample_returns())
    assert {"bin_center", "count"} <= set(report.data.columns)
    assert len(report.data) > 0
    assert report.metrics["periods"] == 400
    assert report.metrics["mean"] is not None


@pytest.mark.asyncio
async def test_quantstats_rolling_action_includes_vol_beta() -> None:
    """rolling action should include volatility + beta in addition to sharpe."""
    p = _quantstats_provider()
    if not await p.healthcheck():
        pytest.skip("quantstats not installed")
    ret = _sample_returns()
    bench = _sample_returns(seed=7)
    await p.start()
    report = await p.compute(
        action="rolling", returns=ret, benchmark=bench, params={"window": 60}
    )
    assert {"rolling_sharpe", "rolling_volatility", "rolling_beta"} <= set(report.data.columns)
    assert report.metrics["last_beta"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
