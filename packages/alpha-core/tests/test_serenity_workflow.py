"""Tests for Serenity workflow — tier-2 must fetch fresh data, not reuse the
tier-1 cached snapshot (P1-1).

Within the 300s cache window, deep_verify reused the same ProtocolMetrics /
GitHubActivity as quick_scan, so tier-2 was bit-identical to tier-1: the
divergence warning at workflow.py:141 never fired and the frontend showed a
misleading "depth deviation 0.000". deep_verify now forces a fresh fetch.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.config import SerenityConfig  # noqa: E402
from serenity.fetcher import SerenityFetcher  # noqa: E402
from serenity.models import GitHubActivity, ProtocolMetrics, SerenitySnapshot  # noqa: E402
from serenity.scorer import SerenityScorer  # noqa: E402
from serenity.workflow import SerenityWorkflow  # noqa: E402


def _rich_snapshot() -> SerenitySnapshot:
    pm = ProtocolMetrics(
        protocol_id="aaa",
        name="AAA",
        category="chain",
        chains=("eth", "sol", "arb", "base", "bsc"),
        tvl_usd=1e10,
        tvl_change_1d=0.05,
        tvl_change_7d=0.2,
        mcap_to_tvl=1.0,
        fdv_to_tvl=1.2,
        revenue_24h=1e6,
        revenue_7d=1e7,
        timestamp=1735689600,
    )
    ga = GitHubActivity(
        protocol_id="aaa",
        repo_name="aaa",
        stars=10000,
        commits_4w=500,
        active_devs_4w=100,
        open_issues=10,
        closed_issues_4w=50,
        timestamp=1735689600,
    )
    return SerenitySnapshot(
        symbol="AAA",
        protocol_metrics=pm,
        github_activity=ga,
        funding_rate=0.01,
        oi_change=0.02,
        ls_ratio=1.1,
        tvl_chain_share=0.5,
        price_change_7d=0.1,
        volume_change_7d=0.05,
        timestamp=1735689600,
    )


def _weaker_snapshot() -> SerenitySnapshot:
    """Same field coverage as _rich_snapshot but weaker values — must score lower."""
    pm = ProtocolMetrics(
        protocol_id="aaa",
        name="AAA",
        category="chain",
        chains=("eth", "sol"),
        tvl_usd=1e10,
        tvl_change_1d=0.05,
        tvl_change_7d=0.05,
        mcap_to_tvl=1.0,
        fdv_to_tvl=1.2,
        revenue_24h=1e6,
        revenue_7d=3e6,
        timestamp=1735689600,
    )
    ga = GitHubActivity(
        protocol_id="aaa",
        repo_name="aaa",
        stars=10000,
        commits_4w=500,
        active_devs_4w=100,
        open_issues=10,
        closed_issues_4w=50,
        timestamp=1735689600,
    )
    return SerenitySnapshot(
        symbol="AAA",
        protocol_metrics=pm,
        github_activity=ga,
        funding_rate=0.01,
        oi_change=0.02,
        ls_ratio=1.1,
        tvl_chain_share=0.5,
        price_change_7d=0.1,
        volume_change_7d=0.05,
        timestamp=1735689600,
    )


# --- fetcher: force_refresh bypasses the per-source cache ---


class _FakeResp:
    def __init__(self, data, status_code=200):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


_PROTO_DATA = {
    "tvl": 1e9,
    "mcap": 2e9,
    "fdv": 3e9,
    "chains": ["eth"],
    "category": "Chain",
    "change_1d": 5.0,
    "change_7d": 10.0,
    "name": "ETH",
    "fees": {"fees_24h": 1e5, "fees_7d": 7e5},
}


def test_get_protocol_force_refresh_bypasses_cache():
    fetcher = SerenityFetcher(config=SerenityConfig(cache_ttl_seconds=300))
    client = AsyncMock()
    client.get = AsyncMock(return_value=_FakeResp(_PROTO_DATA))
    with patch.object(fetcher, "_get_client", new=AsyncMock(return_value=client)):
        first = asyncio.run(fetcher.get_protocol("ETH"))  # fetch + cache
        second = asyncio.run(fetcher.get_protocol("ETH"))  # cache hit
        third = asyncio.run(fetcher.get_protocol("ETH", force_refresh=True))  # bypass
    # 1st fetch + force_refresh; the cached 2nd call must NOT hit the HTTP client.
    assert client.get.await_count == 2
    assert first is not None
    assert second is not None
    assert third is not None


def test_snapshot_passes_force_refresh_to_sources():
    fetcher = SerenityFetcher()
    proto = AsyncMock(return_value=None)
    gh = AsyncMock(return_value=None)
    with patch.object(fetcher, "get_protocol", new=proto), patch.object(
        fetcher, "get_github_activity", new=gh
    ):
        asyncio.run(fetcher.get_serenity_snapshot("ETH", force_refresh=True))
    proto.assert_awaited_once_with("ETH", force_refresh=True)
    gh.assert_awaited_once_with("ETH", force_refresh=True)


# --- workflow: deep_verify requests a fresh fetch, tier2 differs from tier1 ---


class _SequenceFetcher:
    """Returns one snapshot per call, recording whether each was a fresh fetch."""

    def __init__(self, snapshots):
        self._queue = list(snapshots)
        self.calls: list[bool] = []

    async def get_serenity_snapshot(self, symbol, force_refresh=False):
        snap = self._queue.pop(0) if self._queue else self._queue[-1] if self._queue else None
        self.calls.append(force_refresh)
        return snap


def test_scan_and_verify_fetches_fresh_for_tier2():
    fetcher = _SequenceFetcher([_rich_snapshot(), _weaker_snapshot()])
    wf = SerenityWorkflow(fetcher=fetcher, scorer=SerenityScorer())
    report = asyncio.run(wf.scan_and_verify("AAA"))

    # quick_scan may use cache (False); deep_verify must force a fresh fetch (True).
    assert fetcher.calls == [False, True]
    assert report.tier2 is not None
    # Fresh data must produce a real difference — no more bit-identical tier-2.
    assert report.tier2.normalized_score != report.tier1.normalized_score
