"""Tests for Serenity db_accessor — 0.0 (flat market) vs None (no data) (P0-3).

get_price_change_7d_from_db / get_volume_change_7d_from_db previously returned
0.0 for BOTH missing data and a genuine 0% change. A flat market then looked
like "no price data" to the scorer and workflow. They now return None when no
data is available, keeping 0.0 exclusively for a real 0% move.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pandas as pd
import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.db_accessor import (  # noqa: E402
    get_price_change_7d_from_db,
    get_volume_change_7d_from_db,
)
from serenity.models import SerenitySnapshot  # noqa: E402
from serenity.scorer import SerenityScorer  # noqa: E402
from serenity.workflow import SerenityWorkflow  # noqa: E402


class _FakeKlineDB:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def get_klines(self, exchange, market_type, symbol, interval):
        return self._df.copy()


def _klines(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    vols = volumes or [1000.0] * n
    return pd.DataFrame(
        {
            "close": closes,
            "volume": vols,
        }
    )


# --- get_price_change_7d_from_db ---


def test_price_change_none_db_returns_none():
    assert get_price_change_7d_from_db(None, "BTC") is None


def test_price_change_insufficient_data_returns_none():
    db = _FakeKlineDB(_klines([100.0, 101.0, 102.0]))
    assert get_price_change_7d_from_db(db, "BTC") is None


def test_price_change_flat_market_returns_zero():
    db = _FakeKlineDB(_klines([100.0] * 8))
    assert get_price_change_7d_from_db(db, "BTC") == 0.0


def test_price_change_real_move_returns_change():
    closes = [100.0] * 7 + [110.0]
    db = _FakeKlineDB(_klines(closes))
    assert get_price_change_7d_from_db(db, "BTC") == pytest.approx(0.10)


# --- get_volume_change_7d_from_db ---


def test_volume_change_none_db_returns_none():
    assert get_volume_change_7d_from_db(None, "BTC") is None


def test_volume_change_insufficient_data_returns_none():
    db = _FakeKlineDB(_klines([100.0] * 10))
    assert get_volume_change_7d_from_db(db, "BTC") is None


def test_volume_change_real_move_returns_change():
    db = _FakeKlineDB(_klines([100.0] * 14, volumes=[100.0] * 7 + [120.0] * 7))
    assert get_volume_change_7d_from_db(db, "BTC") == pytest.approx(0.20)


# --- workflow preserves the 0.0-vs-None distinction ---


class _StubFetcher:
    def __init__(self, snapshot: SerenitySnapshot) -> None:
        self._snapshot = snapshot

    async def get_serenity_snapshot(self, symbol: str) -> SerenitySnapshot:
        return self._snapshot


def _make_snapshot(price_change_7d, volume_change_7d) -> SerenitySnapshot:
    return SerenitySnapshot(
        symbol="AAA",
        protocol_metrics=None,
        github_activity=None,
        funding_rate=None,
        oi_change=None,
        ls_ratio=None,
        tvl_chain_share=0.0,
        price_change_7d=price_change_7d,
        volume_change_7d=volume_change_7d,
        timestamp=1735689600,
    )


def _run_quick_scan(snapshot: SerenitySnapshot):
    wf = SerenityWorkflow(fetcher=_StubFetcher(snapshot), scorer=SerenityScorer())
    return asyncio.run(wf.quick_scan("AAA"))


def test_workflow_keeps_flat_market_zero():
    report = _run_quick_scan(_make_snapshot(0.0, 0.0))
    assert report.tier1.price_change_7d == 0.0
    assert report.tier1.volume_change_7d == 0.0


def test_workflow_keeps_missing_data_none():
    report = _run_quick_scan(_make_snapshot(None, None))
    assert report.tier1.price_change_7d is None
    assert report.tier1.volume_change_7d is None
