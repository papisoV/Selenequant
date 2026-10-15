"""Unit tests for selene_space_macrodata package.

Tests cover:
- Protocol conformance (MacroDataSource @runtime_checkable)
- Fallback helpers (with_retry, is_cache_valid, stale_fallback, empty_series)
- Registry (register_provider / get_provider / list_registered)
- FRED/BIS/BLS provider catalog integrity
- Provider FakeSource pattern (in-memory, no network)

Pattern aligned with packages/market-data/tests/test_service.py (FakeSource pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from selene_space_macrodata import (
    MacroDataSource,
    MacroDataSourceConfig,
    MacroSeries,
    SeriesKind,
    get_provider,
    register_provider,
)
from selene_space_macrodata.fallback import (
    empty_series,
    is_cache_valid,
    stale_fallback,
    with_retry,
)


# ---------------------------------------------------------------------------
# Fake provider for testing (FakeSource pattern from market-data tests)
# ---------------------------------------------------------------------------


class FakeMacroSource:
    """In-memory fake macro data provider for tests."""

    def __init__(self, cfg: MacroDataSourceConfig) -> None:
        self._cfg = cfg
        self._running = False
        self._cache: dict[str, MacroSeries] = {}
        self._fail_next: bool = False

    @property
    def config(self) -> MacroDataSourceConfig:
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

    async def fetch_series(
        self,
        series_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> MacroSeries:
        if self._fail_next:
            self._fail_next = False
            raise RuntimeError("simulated fetch failure")

        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        df = pd.DataFrame(
            {"date": dates, "value": [100.0, 101.0, 102.0, 103.0, 104.0]}
        )
        series = MacroSeries(
            provider=self._cfg.provider,
            series_id=series_id,
            kind=SeriesKind.RATES,
            description=f"fake {series_id}",
            observations=df,
            fetched_at=datetime.now(UTC),
        )
        self._cache[series_id] = series
        return series

    def get_cached(self, series_id: str) -> MacroSeries | None:
        cached = self._cache.get(series_id)
        if cached is None:
            return None
        return cached if is_cache_valid(cached, self._cfg.cache_ttl_seconds) else None

    def list_series(self) -> list[str]:
        return ["FAKE1", "FAKE2"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_protocol_conformance():
    """FakeMacroSource should satisfy MacroDataSource Protocol."""
    fake = FakeMacroSource(MacroDataSourceConfig(provider="fake"))
    assert isinstance(fake, MacroDataSource)


def test_protocol_rejects_non_conforming():
    """A class missing methods should not satisfy the Protocol."""

    class Broken:
        pass

    assert not isinstance(Broken(), MacroDataSource)


def test_config_pydantic():
    """MacroDataSourceConfig should validate via pydantic."""
    cfg = MacroDataSourceConfig(provider="fred", api_key="abc")
    assert cfg.provider == "fred"
    assert cfg.api_key == "abc"
    assert cfg.timeout_seconds == 30.0
    assert cfg.cache_ttl_seconds == 3600


def test_series_kind_enum():
    """SeriesKind should have the expected members."""
    assert SeriesKind.RATES.value == "rates"
    assert SeriesKind.INFLATION.value == "inflation"
    assert SeriesKind.EMPLOYMENT.value == "employment"


def test_macro_series_dataframe_field():
    """MacroSeries should accept DataFrame observations."""
    df = pd.DataFrame({"date": ["2024-01-01"], "value": [100.0]})
    series = MacroSeries(
        provider="fred",
        series_id="DGS10",
        kind=SeriesKind.RATES,
        description="10y",
        observations=df,
        fetched_at=datetime.now(UTC),
    )
    assert len(series.observations) == 1
    assert series.observations.iloc[0]["value"] == 100.0


@pytest.mark.asyncio
async def test_with_retry_succeeds_on_first_attempt():
    """with_retry should return immediately on success."""

    async def _ok() -> MacroSeries:
        return empty_series("fred", "X")

    result = await with_retry(_ok, attempts=3)
    assert result.series_id == "X"


@pytest.mark.asyncio
async def test_with_retry_recovers_after_failure():
    """with_retry should retry on failure and eventually succeed."""
    calls = {"n": 0}

    async def _flaky() -> MacroSeries:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return empty_series("fred", "X")

    result = await with_retry(_flaky, attempts=3, backoff_seconds=0.01)
    assert result.series_id == "X"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_with_retry_raises_after_exhausting():
    """with_retry should raise after all attempts fail."""

    async def _always_fail() -> MacroSeries:
        raise RuntimeError("permanent")

    with pytest.raises(RuntimeError, match="permanent"):
        await with_retry(_always_fail, attempts=2, backoff_seconds=0.01)


def test_is_cache_valid_within_ttl():
    """is_cache_valid should return True for fresh series."""
    series = empty_series("fred", "X")
    assert is_cache_valid(series, ttl_seconds=3600)


def test_is_cache_valid_expired():
    """is_cache_valid should return False for stale series."""
    series = MacroSeries(
        provider="fred",
        series_id="X",
        kind=SeriesKind.OTHER,
        description="stale",
        observations=pd.DataFrame(columns=["date", "value"]),
        fetched_at=datetime.now(UTC) - timedelta(hours=2),
    )
    assert not is_cache_valid(series, ttl_seconds=3600)


def test_stale_fallback_returns_cached():
    """stale_fallback should return the cached series even if stale."""
    cached = empty_series("fred", "X")
    result = stale_fallback(cached, "X")
    assert result.series_id == "X"


def test_stale_fallback_raises_when_no_cache():
    """stale_fallback should raise when no cache is available."""
    with pytest.raises(RuntimeError, match="no cached fallback"):
        stale_fallback(None, "X")


def test_empty_series():
    """empty_series should produce a valid MacroSeries with zero rows."""
    s = empty_series("fred", "X")
    assert s.provider == "fred"
    assert s.series_id == "X"
    assert len(s.observations) == 0


def test_registry_register_and_get():
    """register_provider + get_provider should roundtrip."""
    register_provider("test_fake", lambda cfg: FakeMacroSource(cfg))
    cfg = MacroDataSourceConfig(provider="test_fake")
    p = get_provider(cfg)
    assert isinstance(p, FakeMacroSource)
    assert p.config.provider == "test_fake"


def test_registry_unknown_provider_raises():
    """get_provider should raise KeyError for unknown providers."""
    with pytest.raises(KeyError, match="Unknown macro data provider"):
        get_provider(MacroDataSourceConfig(provider="does_not_exist_xyz"))


@pytest.mark.asyncio
async def test_fake_source_lifecycle():
    """FakeMacroSource start/stop/fetch lifecycle."""
    register_provider("test_fake2", lambda cfg: FakeMacroSource(cfg))
    p = get_provider(MacroDataSourceConfig(provider="test_fake2"))
    assert not p.is_running
    await p.start()
    assert p.is_running
    series = await p.fetch_series("FAKE1")
    assert series.series_id == "FAKE1"
    assert len(series.observations) == 5
    cached = p.get_cached("FAKE1")
    assert cached is not None
    assert cached.series_id == "FAKE1"
    await p.stop()
    assert not p.is_running


# ---------------------------------------------------------------------------
# Provider catalog integrity (P0)
# ---------------------------------------------------------------------------


def test_fred_catalog_has_crypto_relevant_series():
    """FRED catalog should include yield curve, money supply, VIX, DXY."""
    from selene_space_macrodata.providers.fred import FRED_CATALOG

    # Yield curve
    assert "DGS10" in FRED_CATALOG
    assert "DGS2" in FRED_CATALOG
    assert "T10Y2Y" in FRED_CATALOG
    # Money supply
    assert "M2SL" in FRED_CATALOG
    assert "WALCL" in FRED_CATALOG
    # Financial stress
    assert "VIXCLS" in FRED_CATALOG
    assert "STLFSI4" in FRED_CATALOG
    # FX
    assert "DTWEXBGS" in FRED_CATALOG
    # Inflation
    assert "CPIAUCSL" in FRED_CATALOG
    assert "PCEPILFE" in FRED_CATALOG
    # Employment
    assert "UNRATE" in FRED_CATALOG


def test_bis_catalog_has_crypto_relevant_series():
    """BIS catalog should include NEER, policy rates, credit gap."""
    from selene_space_macrodata.providers.bis import BIS_CATALOG

    assert "NEER_US" in BIS_CATALOG
    assert "REER_US" in BIS_CATALOG
    assert "POLICY_RATE_US" in BIS_CATALOG
    assert "POLICY_RATE_EU" in BIS_CATALOG
    assert "CREDIT_GAP_US" in BIS_CATALOG


def test_bls_catalog_has_crypto_relevant_series():
    """BLS catalog should include CPI, PPI, employment."""
    from selene_space_macrodata.providers.bls import BLS_CATALOG

    assert "CUUR0000SA0" in BLS_CATALOG
    assert "WPSFD49207" in BLS_CATALOG
    assert "LNS14000000" in BLS_CATALOG
    assert "JTS000000000000000JOL" in BLS_CATALOG


def test_fred_provider_creates_and_lists():
    """FredProvider should be instantiable and list its catalog."""
    from selene_space_macrodata.providers.fred import FredProvider

    p = FredProvider(MacroDataSourceConfig(provider="fred"))
    assert isinstance(p, MacroDataSource)
    assert len(p.list_series()) > 0
    assert not p.is_running


def test_bis_provider_creates_and_lists():
    """BisProvider should be instantiable and list its catalog."""
    from selene_space_macrodata.providers.bis import BisProvider

    p = BisProvider(MacroDataSourceConfig(provider="bis"))
    assert isinstance(p, MacroDataSource)
    assert len(p.list_series()) > 0


def test_bls_provider_creates_and_lists():
    """BlsProvider should be instantiable and list its catalog."""
    from selene_space_macrodata.providers.bls import BlsProvider

    p = BlsProvider(MacroDataSourceConfig(provider="bls"))
    assert isinstance(p, MacroDataSource)
    assert len(p.list_series()) > 0


def test_providers_auto_registered_on_import():
    """Importing selene_space_macrodata.providers should auto-register fred/bis/bls."""
    from selene_space_macrodata.registry import list_registered

    registered = list_registered()
    assert "fred" in registered
    assert "bis" in registered
    assert "bls" in registered


# ---------------------------------------------------------------------------
# Service test
# ---------------------------------------------------------------------------


def test_macrodata_service_manages_providers():
    """MacroDataService should lazily create and reuse providers."""
    from selene_space_macrodata.service import MacroDataService

    register_provider("test_fake3", lambda cfg: FakeMacroSource(cfg))
    svc = MacroDataService()
    cfg = MacroDataSourceConfig(provider="test_fake3")
    p1 = svc.get_provider(cfg)
    p2 = svc.get_provider(cfg)
    assert p1 is p2  # singleton per provider id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
