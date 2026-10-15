"""C3: theme pool scan API endpoints + in-memory per-theme cache.

/pool/scan runs a pool scan and caches per theme; refresh=true forces a rescan.
/pool/{theme} reads the latest cached scan (empty state before any scan).
"""

from __future__ import annotations

import asyncio
import sys
import types as _types
from pathlib import Path

# Register a path-only selene_space_alpha so the API module's lazy imports work
# without triggering the torch-heavy package __init__ (same trick as
# test_api_serenity.py / scripts/serenity_scan.py).
_ALPHA_CORE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "packages" / "alpha-core" / "selene_space_alpha"
)
assert _ALPHA_CORE.is_dir(), (
    f"alpha-core not found at {_ALPHA_CORE} — an off-by-one in the fake-package "
    "path silently loads the editable-install copy from another replica instead"
)
if "selene_space_alpha" not in sys.modules:
    _parent = _types.ModuleType("selene_space_alpha")
    _parent.__path__ = [str(_ALPHA_CORE)]
    _parent.__package__ = "selene_space_alpha"
    sys.modules["selene_space_alpha"] = _parent

from selene_space_alpha.serenity.pool import (  # noqa: E402
    PoolCandidate,
    PoolLayerSummary,
    SerenityPoolScan,
)
from selene_space_alpha.serenity.pool_db import SerenityPoolDB  # noqa: E402

from app.api.v1.serenity import (  # noqa: E402
    _pool_cache,
    _pool_scan_to_dict,
    bootstrap_pool_cache,
    pool_latest,
    pool_scan,
    set_serenity_cache,
    set_serenity_pool_scanner,
)
from app.core.dependencies import set_serenity_pool_db  # noqa: E402


class _FakeRequest:
    class _State:
        request_id = "test-request"

    state = _State()


def _patch_meta(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.serenity.build_response_meta",
        lambda request, pagination=None: {"request_id": "test-request"},
    )


def _sample_scan(theme: str = "lending", theme_name: str = "Lending") -> SerenityPoolScan:
    strong = PoolCandidate(
        symbol="AAA", name="Alpha", category="Lending", chains=("eth",),
        normalized_score=0.72, tier="strong_bullish", scarce_layer_score=0.86,
        evidence_strength=0.7, evidence_label="强", rationale="卡点:高共识稀缺",
        top_penalty="fork_risk:0.55",
        disproof=("收入连续 3 周下滑打破动量",),
    )
    weak = PoolCandidate(
        symbol="BBB", name="Beta", category="DEX", chains=("eth",),
        normalized_score=0.60, tier="bullish", scarce_layer_score=0.40,
        evidence_strength=0.3, evidence_label="中", rationale="无明显卡点特征",
        top_penalty="tvl_concentration:0.70", disproof=(),
    )
    return SerenityPoolScan(
        theme=theme, theme_name=theme_name, scanned_at=1735689600000,
        universe_size=20,
        layer_summary=(
            PoolLayerSummary(layer="Lending", count=15, top_scarce_symbols=("AAA",)),
        ),
        ranked=(strong, weak),
        top_picks=("AAA",),
        weak_evidence_count=1,
    )


class _FakeScanner:
    def __init__(self, scan, error_theme=None):
        self._scan = scan
        self._error_theme = error_theme
        self.calls = 0

    async def scan(self, theme, top_n=None):
        self.calls += 1
        if self._error_theme and theme == self._error_theme:
            raise ValueError(f"unknown theme: {theme}")
        return self._scan


def test_pool_scan_runs_caches_and_refreshes(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    scanner = _FakeScanner(_sample_scan())
    set_serenity_pool_scanner(scanner)

    resp = asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))
    assert scanner.calls == 1
    assert resp.data["theme_name"] == "Lending"
    assert resp.data["universe_size"] == 20
    assert resp.data["top_picks"] == ["AAA"]
    assert resp.data["layer_summary"][0]["layer"] == "Lending"
    assert resp.data["ranked"][0]["symbol"] == "AAA"
    assert resp.data["ranked"][0]["disproof"] == ["收入连续 3 周下滑打破动量"]
    assert resp.data["ranked"][1]["disproof"] == []
    assert "lending" in _pool_cache

    # Cache hit: scanner not called again.
    asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))
    assert scanner.calls == 1

    # refresh=true forces a rescan.
    asyncio.run(pool_scan(request=_FakeRequest(), theme="lending", refresh=True))
    assert scanner.calls == 2


def test_pool_scan_unknown_theme_returns_error(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    set_serenity_pool_scanner(_FakeScanner(_sample_scan(), error_theme="bogus"))

    resp = asyncio.run(pool_scan(request=_FakeRequest(), theme="bogus"))
    assert "unknown theme" in resp.data["error"]
    assert "bogus" not in _pool_cache  # failed scan never cached


def test_pool_scan_scanner_unavailable_returns_error(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    set_serenity_pool_scanner(None)

    resp = asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))
    assert "not available" in resp.data["error"]


def test_pool_latest_empty_state_before_any_scan(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()

    resp = asyncio.run(pool_latest(request=_FakeRequest(), theme="lending"))
    assert resp.data == {"theme": "lending", "message": "no scan yet"}


def test_pool_latest_returns_cached_scan(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    set_serenity_pool_scanner(_FakeScanner(_sample_scan()))
    asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))

    resp = asyncio.run(pool_latest(request=_FakeRequest(), theme="lending"))
    assert resp.data["theme_name"] == "Lending"
    assert resp.data["ranked"][0]["symbol"] == "AAA"

    # A different theme has no cached scan yet.
    resp2 = asyncio.run(pool_latest(request=_FakeRequest(), theme="dex"))
    assert resp2.data["message"] == "no scan yet"


# --- F1: DB fallback + bootstrap (v023) ---


def test_pool_latest_falls_back_to_db_when_cache_empty(tmp_path, monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    db.put_scan(_sample_scan())
    set_serenity_pool_db(db)
    try:
        resp = asyncio.run(pool_latest(request=_FakeRequest(), theme="lending"))
        assert resp.data["theme_name"] == "Lending"
        assert resp.data["ranked"][0]["symbol"] == "AAA"
        # The cache was rehydrated from the DB so the next read is cache-first.
        assert "lending" in _pool_cache
    finally:
        set_serenity_pool_db(None)
        db.close()


def test_bootstrap_pool_cache_rehydrates(tmp_path, monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    db.put_scan(_sample_scan())
    db.put_scan(_sample_scan(theme="dex", theme_name="DEX"))
    set_serenity_pool_db(db)
    try:
        n = bootstrap_pool_cache()
        assert n == 2
        assert "lending" in _pool_cache
        assert "dex" in _pool_cache
    finally:
        set_serenity_pool_db(None)
        db.close()


def test_db_restored_scan_serializes_consistently(tmp_path, monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    fresh = _sample_scan()
    db = SerenityPoolDB(db_path=tmp_path / "pool.db")
    db.open()
    db.put_scan(fresh)
    set_serenity_pool_db(db)
    try:
        restored = db.get_latest("lending")
        assert restored is not None
        # A DB-restored scan must serialize to the same JSON shape as a fresh one.
        assert _pool_scan_to_dict(restored) == _pool_scan_to_dict(fresh)
    finally:
        set_serenity_pool_db(None)
        db.close()


# --- B1: /pool/scan syncs fresh scans into the live cache rank gate ---


class _FakeCacheGate:
    """Minimal stand-in for SerenityScoreCache's pool-gate surface."""

    def __init__(self) -> None:
        self._pool_mode = "soft"
        self._pool_top_n = 5
        self.received: dict[str, SerenityPoolScan] | None = None

    def set_pool_filter(self, scans, mode, top_n) -> None:
        self.received = dict(scans)


def test_pool_scan_syncs_fresh_scan_into_cache_gate(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    gate = _FakeCacheGate()
    set_serenity_cache(gate)
    set_serenity_pool_scanner(_FakeScanner(_sample_scan()))
    try:
        resp = asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))
        assert resp.data["theme_name"] == "Lending"
        # The gate received the fresh scan map with the new theme.
        assert gate.received is not None
        assert "lending" in gate.received
        assert gate.received["lending"].ranked[0].symbol == "AAA"
    finally:
        set_serenity_cache(None)


def test_pool_scan_does_not_sync_when_gate_off(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    gate = _FakeCacheGate()
    gate._pool_mode = "off"
    set_serenity_cache(gate)
    set_serenity_pool_scanner(_FakeScanner(_sample_scan()))
    try:
        asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))
        assert gate.received is None  # off gate: no re-feed
    finally:
        set_serenity_cache(None)


def test_pool_scan_noop_without_cache(monkeypatch):
    _patch_meta(monkeypatch)
    _pool_cache.clear()
    set_serenity_cache(None)
    set_serenity_pool_scanner(_FakeScanner(_sample_scan()))
    resp = asyncio.run(pool_scan(request=_FakeRequest(), theme="lending"))
    assert resp.data["theme_name"] == "Lending"
