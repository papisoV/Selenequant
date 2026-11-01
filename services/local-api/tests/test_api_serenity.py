"""Serenity /scores restart bootstrap (P2-3).

_score_cache only lives in-process, so /scores returned empty after a restart
while /history (SQLite-backed) still had data. bootstrap_score_cache() rehydrates
the cache from the latest persisted row per symbol at startup, keeping the two
endpoints consistent.
"""

from __future__ import annotations

import sys
import types as _types
from dataclasses import asdict
from pathlib import Path

# Register a path-only selene_space_alpha so the API module's lazy
# `from selene_space_alpha.serenity.models import ...` works without triggering
# the torch-heavy package __init__ (same trick as scripts/serenity_scan.py).
# 4 parents: tests -> local-api -> services -> repo root.
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

import pandas as pd  # noqa: E402

from selene_space_alpha.serenity.models import SerenityScore  # noqa: E402

from app.api.v1.serenity import (  # noqa: E402
    _report_from_score_row,
    _score_cache,
    bootstrap_score_cache,
)


def _score(symbol: str, normalized: float) -> SerenityScore:
    return SerenityScore(
        symbol=symbol,
        consensus_scarcity=0.5, liquidity_scarcity=0.5, trust_scarcity=0.5,
        replication_difficulty=0.5, certification_progress=0.5,
        prepayment_signal=0.5, decentralization=0.5, evidence_strength=0.0,
        penalty_fork_risk=0.5, penalty_tvl_concentration=0.5,
        penalty_dev_exodus=0.5, penalty_revenue_decline=0.5,
        penalty_oi_divergence=0.5, penalty_whale_exodus=0.5,
        penalty_governance_risk=0.5, penalty_liquidity_trap=0.5,
        raw_score=0.0, normalized_score=normalized,
        tier="bullish" if normalized > 0.55 else "neutral",
        timestamp=1735689600,
    )


def test_report_from_score_row_synthesizes_report():
    report = _report_from_score_row(asdict(_score("BTC", 0.62)))
    assert report.tier1.normalized_score == 0.62
    assert report.signal_direction == 1  # > 0.6 -> long
    assert report.tier2 is None

    report2 = _report_from_score_row(asdict(_score("ETH", 0.30)))
    assert report2.signal_direction == -1  # < 0.4 -> short


def test_bootstrap_score_cache_loads_latest_from_db(monkeypatch):
    rows = [asdict(_score("BTC", 0.62)), asdict(_score("ETH", 0.30))]

    class _FakeDB:
        def get_all_latest(self):
            return pd.DataFrame(rows)

    monkeypatch.setattr(
        "app.core.dependencies.get_serenity_score_db", lambda: _FakeDB()
    )

    _score_cache.clear()
    n = bootstrap_score_cache()
    assert n == 2
    assert "BTC" in _score_cache and "ETH" in _score_cache
    assert _score_cache["BTC"].tier1.normalized_score == 0.62
    assert _score_cache["ETH"].signal_direction == -1


def test_bootstrap_score_cache_empty_db_is_noop(monkeypatch):
    class _EmptyDB:
        def get_all_latest(self):
            return pd.DataFrame()

    monkeypatch.setattr(
        "app.core.dependencies.get_serenity_score_db", lambda: _EmptyDB()
    )

    _score_cache.clear()
    assert bootstrap_score_cache() == 0
    assert len(_score_cache) == 0
