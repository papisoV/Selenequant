"""C1: fetcher slug/repo direct-fetch paths for the theme pool scanner.

Pool candidates come from DeFi Llama /protocols and are not in the manual
SYMBOL_TO_DEFI_LLAMA / SYMBOL_TO_GITHUB maps. get_protocol_by_slug /
get_github_by_repo fetch by explicit slug/repo; the original symbol wrappers
still resolve through the maps. get_pool_snapshot wires an entry -> snapshot.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.fetcher import SerenityFetcher  # noqa: E402
from serenity.models import GitHubActivity, ProtocolMetrics  # noqa: E402


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
    "name": "Pool Protocol",
    "fees": {"fees_24h": 1e5, "fees_7d": 7e5},
}


def test_get_protocol_by_slug_fetches_directly():
    fetcher = SerenityFetcher()
    client = AsyncMock()
    client.get = AsyncMock(return_value=_FakeResp(_PROTO_DATA))
    with patch.object(fetcher, "_get_client", new=AsyncMock(return_value=client)):
        pm = asyncio.run(fetcher.get_protocol_by_slug("pool-protocol"))
    assert isinstance(pm, ProtocolMetrics)
    assert pm.protocol_id == "pool-protocol"
    # URL must hit the slug, not go through the symbol map.
    assert client.get.await_args.args[0].endswith("/protocol/pool-protocol")


def test_get_protocol_still_resolves_through_symbol_map(monkeypatch):
    fetcher = SerenityFetcher()
    inner = AsyncMock(return_value=None)
    monkeypatch.setattr(fetcher, "get_protocol_by_slug", inner)
    asyncio.run(fetcher.get_protocol("ETH"))
    inner.assert_awaited_once_with("ethereum-staking", force_refresh=False)


_GITHUB_DATA = {
    "stargazers_count": 10000,
    "open_issues_count": 5,
    "full_name": "owner/repo",
}


def test_get_github_by_repo_fetches_directly():
    fetcher = SerenityFetcher()
    client = AsyncMock()
    # 3 sequential calls: repo info, commits, closed issues.
    client.get = AsyncMock(
        side_effect=[_FakeResp(_GITHUB_DATA), _FakeResp([]), _FakeResp([])]
    )
    with patch.object(fetcher, "_get_client", new=AsyncMock(return_value=client)):
        ga = asyncio.run(fetcher.get_github_by_repo("owner/repo"))
    assert isinstance(ga, GitHubActivity)
    assert ga.stars == 10000
    assert client.get.await_args_list[0].args[0].endswith("/repos/owner/repo")


def test_get_github_activity_still_resolves_through_repo_map(monkeypatch):
    fetcher = SerenityFetcher()
    inner = AsyncMock(return_value=None)
    monkeypatch.setattr(fetcher, "get_github_by_repo", inner)
    asyncio.run(fetcher.get_github_activity("UNI"))
    inner.assert_awaited_once_with("Uniswap/v4-core", force_refresh=False)


def test_repo_from_github_url_parses_owner_name():
    f = SerenityFetcher()
    assert (
        f._repo_from_github_url("https://github.com/uniswap/uniswap-v3-core")
        == "uniswap/uniswap-v3-core"
    )
    assert (
        f._repo_from_github_url("https://github.com/aave/protocol-v2/tree/main")
        == "aave/protocol-v2"
    )
    assert f._repo_from_github_url("not-a-github-url") is None
    assert f._repo_from_github_url("") is None


def test_get_pool_snapshot_resolves_slug_repo_and_enriches():
    fetcher = SerenityFetcher()
    proto = AsyncMock(
        return_value=ProtocolMetrics(
            protocol_id="pool-protocol", name="Pool", category="Lending",
            chains=("eth",), tvl_usd=1e8, tvl_change_1d=0.0, tvl_change_7d=0.0,
            mcap_to_tvl=1.0, fdv_to_tvl=1.0, revenue_24h=0.0, revenue_7d=0.0,
            timestamp=1,
        )
    )
    gh = AsyncMock(return_value=None)
    with patch.object(fetcher, "get_protocol_by_slug", new=proto), patch.object(
        fetcher, "get_github_by_repo", new=gh
    ):
        snap = asyncio.run(
            fetcher.get_pool_snapshot(
                {"id": "pool-protocol", "symbol": "POOL",
                 "github": ["https://github.com/pool/protocol"]}
            )
        )
    proto.assert_awaited_once_with("pool-protocol", force_refresh=False)
    gh.assert_awaited_once_with("pool/protocol", force_refresh=False)
    assert snap.symbol == "POOL"
    assert snap.protocol_metrics is not None
    assert snap.github_activity is None
    assert snap.funding_rate is None  # no derivatives_db -> None, never crash


def test_get_pool_snapshot_skips_github_when_disabled_or_absent():
    fetcher = SerenityFetcher()
    proto = AsyncMock(return_value=None)
    gh = AsyncMock(return_value=None)
    with patch.object(fetcher, "get_protocol_by_slug", new=proto), patch.object(
        fetcher, "get_github_by_repo", new=gh
    ):
        snap = asyncio.run(
            fetcher.get_pool_snapshot(
                {"id": "pool-protocol", "symbol": "POOL"}, fetch_github=False
            )
        )
    proto.assert_awaited_once()
    gh.assert_not_awaited()
    assert snap.github_activity is None
    assert snap.protocol_metrics is None

    # No github URL on the entry -> github never requested.
    with patch.object(fetcher, "get_protocol_by_slug", new=proto), patch.object(
        fetcher, "get_github_by_repo", new=gh
    ):
        snap2 = asyncio.run(fetcher.get_pool_snapshot({"id": "pool2", "symbol": "P2"}))
    gh.assert_not_awaited()
    assert snap2.github_activity is None


def test_get_protocol_by_slug_parses_tvl_time_series():
    """Real /protocol/{slug} returns tvl as a history list; take the latest point."""
    data = dict(_PROTO_DATA)
    data["tvl"] = [
        {"date": 1, "totalLiquidityUSD": 5e8},
        {"date": 2, "totalLiquidityUSD": 8e8},
    ]
    fetcher = SerenityFetcher()
    client = AsyncMock()
    client.get = AsyncMock(return_value=_FakeResp(data))
    with patch.object(fetcher, "_get_client", new=AsyncMock(return_value=client)):
        pm = asyncio.run(fetcher.get_protocol_by_slug("pool-protocol"))
    assert pm.tvl_usd == 8e8


def test_get_pool_snapshot_prefers_slug_field_over_numeric_id():
    """Real entries carry a numeric id + string slug; the slug must drive fetch."""
    fetcher = SerenityFetcher()
    proto = AsyncMock(return_value=None)
    gh = AsyncMock(return_value=None)
    with patch.object(fetcher, "get_protocol_by_slug", new=proto), patch.object(
        fetcher, "get_github_by_repo", new=gh
    ):
        asyncio.run(
            fetcher.get_pool_snapshot({"id": 2088, "slug": "aave-v3", "symbol": "AAVE"})
        )
    proto.assert_awaited_once_with("aave-v3", force_refresh=False)
    gh.assert_not_awaited()


def test_config_defi_llama_base_url_env_override(monkeypatch):
    from serenity.config import SerenityConfig

    monkeypatch.setenv("SELENE_DEFI_LLAMA_BASE_URL", "https://api.llama.fi")
    assert SerenityConfig().defi_llama_base_url == "https://api.llama.fi"
    monkeypatch.delenv("SELENE_DEFI_LLAMA_BASE_URL")
    assert SerenityConfig().defi_llama_base_url == "https://api.llama.xyz"
