"""Serenity data fetcher — DeFi Llama (free) + GitHub API.

DeFi Llama provides TVL, protocol revenue, and chain metrics with no API key.
GitHub API provides commit activity, stars, and issues (free tier: 60 req/hr).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway

from .config import SerenityConfig
from .models import GitHubActivity, ProtocolMetrics, SerenitySnapshot

logger = logging.getLogger(__name__)

# Symbol -> DeFi Llama protocol slug mapping
SYMBOL_TO_DEFI_LLAMA: dict[str, str] = {
    "ETH": "ethereum-staking",
    "SOL": "solana-blaze",
    "ARB": "arbitrum",
    "OP": "optimism",
    "AVAX": "avalanche",
    "MATIC": "polygon",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "AAVE": "aave-v3",
    "MKR": "makerdao",
    "CRV": "curve-dex",
    "LDO": "lido",
    "JUP": "jupiter-exchange-solana",
    "RAY": "raydium",
    "SNX": "synthetix",
}

# Symbol -> GitHub repo mapping
SYMBOL_TO_GITHUB: dict[str, str] = {
    "BTC": "bitcoin/bitcoin",
    "ETH": "ethereum/go-ethereum",
    "SOL": "solana-labs/solana",
    "ARB": "OffchainLabs/nitro",
    "OP": "ethereum-optimism/optimism",
    "AVAX": "ava-labs/avalanchego",
    "MATIC": "maticnetwork/bor",
    "LINK": "smartcontractkit/chainlink",
    "UNI": "Uniswap/v4-core",
    "AAVE": "bgd-labs/aave-v3-origin",
    "MKR": "makerdao/evm-chain-director",
    "CRV": "curvefi/curve-contract",
    "LDO": "lidofinance/lido-dapp",
    "JUP": "jup-ag/jupiter-v6",
}

# Category mapping from DeFi Llama categories to simplified groups
_CATEGORY_MAP: dict[str, str] = {
    "Chain": "chain",
    "Bridge": "bridge",
    "DEX": "dex",
    "Lending": "lending",
    "CDP": "lending",
    "Yield": "yield",
    "Staking": "staking",
    "Derivatives": "derivatives",
    "NFT Lending": "lending",
    "Liquid Staking": "staking",
}


class SerenityFetcher:
    """Async fetcher for on-chain/DeFi fundamental data."""

    def __init__(
        self,
        *,
        config: SerenityConfig | None = None,
        proxy_url: str | None = None,
        derivatives_db: Any | None = None,
        kline_db: Any | None = None,
    ) -> None:
        self._config = config or SerenityConfig()
        self._proxy_url = proxy_url
        self._derivatives_db = derivatives_db
        self._kline_db = kline_db
        self._cache: dict[str, Any] = {}
        self._cache_ts: dict[str, int] = {}
        self._lock: Any = None
        self._client: OutboundGateway | None = None
        self._protocols_list: list[dict[str, Any]] | None = None

    async def _get_lock(self) -> Any:
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()
        return self._lock

    async def _get_client(self) -> OutboundGateway:
        if self._client is None or self._client.is_closed:
            self._client = OutboundGateway(
                proxy=self._proxy_url,
                default_timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                default_circuit=True,
            )
        return self._client

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache or key not in self._cache_ts:
            return False
        ttl_ms = self._config.cache_ttl_seconds * 1000
        return (int(time.time() * 1000) - self._cache_ts[key]) < ttl_ms

    # --- DeFi Llama endpoints (all free, no auth) ---

    async def get_protocols(self) -> list[dict[str, Any]]:
        """Fetch all protocols from DeFi Llama."""
        if self._protocols_list is not None and self._is_cached("protocols_list"):
            return self._protocols_list

        client = await self._get_client()
        try:
            resp = await client.get(f"{self._config.defi_llama_base_url}/protocols")
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()
            self._protocols_list = data
            self._cache_ts["protocols_list"] = int(time.time() * 1000)
            return data
        except Exception as e:
            logger.error("DeFi Llama /protocols fetch failed: %s", e)
            return self._protocols_list or []

    async def get_protocol(self, symbol: str, force_refresh: bool = False) -> ProtocolMetrics | None:
        """Fetch protocol metrics for a symbol via the symbol -> slug map."""
        slug = SYMBOL_TO_DEFI_LLAMA.get(symbol.upper())
        if not slug:
            return None
        return await self.get_protocol_by_slug(slug, force_refresh=force_refresh)

    async def get_protocol_by_slug(
        self, slug: str, force_refresh: bool = False
    ) -> ProtocolMetrics | None:
        """Fetch protocol metrics for a DeFi Llama slug.

        Pool candidates come from /protocols and are not in the manual
        SYMBOL_TO_DEFI_LLAMA map, so the pool scanner resolves the slug from
        the entry itself and calls this directly.
        """
        cache_key = f"protocol:{slug}"
        if not force_refresh and self._is_cached(cache_key):
            cached = self._cache[cache_key]
            if cached is None:
                return None
            return cached

        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self._config.defi_llama_base_url}/protocol/{slug}"
            )
            if resp.status_code == 404:
                logger.debug("DeFi Llama: no protocol for slug %s", slug)
                self._cache[cache_key] = None
                self._cache_ts[cache_key] = int(time.time() * 1000)
                return None
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            tvl_raw = data.get("tvl", 0) or 0
            if isinstance(tvl_raw, list):
                # /protocol/{slug} returns a TVL time series; use the latest point.
                tvl = float(tvl_raw[-1].get("totalLiquidityUSD", 0) or 0) if tvl_raw else 0.0
            else:
                tvl = float(tvl_raw or 0)
            mcap = float(data.get("mcap", 0) or 0)
            fdv = float(data.get("fdv", 0) or 0)
            chains = tuple(data.get("chains", [])[:10])
            category_raw = data.get("category", "")
            category = _CATEGORY_MAP.get(category_raw, category_raw.lower())

            tvl_change_1d = float(data.get("change_1d", 0) or 0) / 100.0
            tvl_change_7d = float(data.get("change_7d", 0) or 0) / 100.0

            fees_data = data.get("fees", {})
            revenue_24h = float((fees_data or {}).get("fees_24h", 0) or 0)
            revenue_7d = float((fees_data or {}).get("fees_7d", 0) or 0)

            pm = ProtocolMetrics(
                protocol_id=slug,
                name=data.get("name", slug),
                category=category,
                chains=chains,
                tvl_usd=tvl,
                tvl_change_1d=tvl_change_1d,
                tvl_change_7d=tvl_change_7d,
                mcap_to_tvl=mcap / (tvl + 1e-9),
                fdv_to_tvl=fdv / (tvl + 1e-9),
                revenue_24h=revenue_24h,
                revenue_7d=revenue_7d,
                timestamp=int(time.time() * 1000),
            )
            self._cache[cache_key] = pm
            self._cache_ts[cache_key] = int(time.time() * 1000)
            logger.info("Fetched DeFi Llama protocol: %s (TVL $%.0f)", slug, tvl)
            return pm

        except httpx.ConnectError:
            logger.warning(
                "DeFi Llama unreachable for %s (connection error — proxy may be required in CN)",
                slug,
            )
            return self._cache.get(cache_key)
        except Exception as e:
            logger.error("DeFi Llama protocol fetch failed for %s: %s", slug, e)
            return self._cache.get(cache_key)

    # --- GitHub endpoints (free tier) ---

    async def get_github_activity(self, symbol: str, force_refresh: bool = False) -> GitHubActivity | None:
        """Fetch GitHub development activity for a symbol via the symbol -> repo map."""
        repo = SYMBOL_TO_GITHUB.get(symbol.upper())
        if not repo:
            return None
        return await self.get_github_by_repo(repo, force_refresh=force_refresh)

    async def get_github_by_repo(
        self, repo: str, force_refresh: bool = False
    ) -> GitHubActivity | None:
        """Fetch GitHub activity for an explicit owner/name repo.

        Pool candidates are not in the manual SYMBOL_TO_GITHUB map; the pool
        scanner derives the repo from the /protocols entry's github URL and
        calls this directly.
        """
        cache_key = f"github:{repo}"
        if not force_refresh and self._is_cached(cache_key):
            return self._cache.get(cache_key)

        client = await self._get_client()
        headers: dict[str, str] = {
            "Accept": "application/vnd.github.v3+json",
        }
        if self._config.github_token:
            headers["Authorization"] = f"Bearer {self._config.github_token}"

        try:
            # Get repo info (stars, open issues)
            resp = await client.get(
                f"{self._config.github_api_base_url}/repos/{repo}",
                headers=headers,
            )
            if resp.status_code == 404:
                logger.debug("GitHub: repo %s not found", repo)
                self._cache[cache_key] = None
                self._cache_ts[cache_key] = int(time.time() * 1000)
                return None
            resp.raise_for_status()
            repo_data: dict[str, Any] = resp.json()

            stars = repo_data.get("stargazers_count", 0)
            open_issues = repo_data.get("open_issues_count", 0)

            # Get recent commits (last 4 weeks ~ 30 days)
            since = int(time.time()) - 30 * 86400
            commits_resp = await client.get(
                f"{self._config.github_api_base_url}/repos/{repo}/commits",
                params={"since": f"{since}-01-01T00:00:00Z", "per_page": 100},
                headers=headers,
            )
            commits_4w = 0
            active_devs: set[str] = set()
            if commits_resp.status_code == 200:
                commits_data: list[dict[str, Any]] = commits_resp.json()
                commits_4w = min(len(commits_data), 100)
                for c in commits_data:
                    author = c.get("author", {})
                    if author and isinstance(author, dict):
                        login = author.get("login", "")
                        if login:
                            active_devs.add(login)

            # Get recently closed issues (last 4 weeks)
            closed_resp = await client.get(
                f"{self._config.github_api_base_url}/repos/{repo}/issues",
                params={"state": "closed", "per_page": 30, "sort": "updated"},
                headers=headers,
            )
            closed_issues_4w = 0
            if closed_resp.status_code == 200:
                issues_data: list[dict[str, Any]] = closed_resp.json()
                cutoff = time.time() - 30 * 86400
                for issue in issues_data:
                    closed_at = issue.get("closed_at", "")
                    if closed_at:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(
                                closed_at.replace("Z", "+00:00")
                            )
                            if dt.timestamp() > cutoff:
                                closed_issues_4w += 1
                        except (ValueError, TypeError):
                            pass

            ga = GitHubActivity(
                protocol_id=repo,
                repo_name=repo,
                stars=stars,
                commits_4w=commits_4w,
                active_devs_4w=len(active_devs),
                open_issues=open_issues,
                closed_issues_4w=closed_issues_4w,
                timestamp=int(time.time() * 1000),
            )
            self._cache[cache_key] = ga
            self._cache_ts[cache_key] = int(time.time() * 1000)
            logger.info(
                "Fetched GitHub activity: %s (%d stars, %d devs, %d commits)",
                repo, stars, len(active_devs), commits_4w,
            )
            return ga

        except Exception as e:
            logger.error("GitHub fetch failed for %s: %s", repo, e)
            return self._cache.get(cache_key)

    # --- Composite snapshot ---

    async def get_serenity_snapshot(self, symbol: str, force_refresh: bool = False) -> SerenitySnapshot:
        """Build a complete SerenitySnapshot for a symbol.

        force_refresh bypasses the per-source 300s cache — deep_verify uses it
        so tier-2 cross-checking sees current data, not the tier-1 snapshot.
        """
        symbol = symbol.upper()
        base = symbol.replace("/USDT", "").replace("USDT", "")

        protocol, github = await asyncio.gather(
            self.get_protocol(base, force_refresh=force_refresh),
            self.get_github_activity(base, force_refresh=force_refresh),
        )

        tvl_chain_share = 0.0
        if protocol is not None and protocol.tvl_usd > 0:
            tvl_chain_share = 1.0

        funding_rate, oi_change, ls_ratio, price_change_7d, volume_change_7d = (
            await self._enrich_from_local_dbs(base)
        )

        return SerenitySnapshot(
            symbol=base,
            protocol_metrics=protocol,
            github_activity=github,
            funding_rate=funding_rate,
            oi_change=oi_change,
            ls_ratio=ls_ratio,
            tvl_chain_share=tvl_chain_share,
            price_change_7d=price_change_7d,
            volume_change_7d=volume_change_7d,
            timestamp=int(time.time() * 1000),
        )

    async def _enrich_from_local_dbs(
        self, symbol: str
    ) -> tuple[
        float | None, float | None, float | None, float | None, float | None
    ]:
        """Read derivatives/klines enrichment for a symbol (None when absent).

        These are sync SQLite reads via pandas — running them inline blocks the
        event loop for ~50-200ms per symbol during the 300s refresh sweep, which
        stalls WS tick handling and /api/v1/health. Wrap each in asyncio.to_thread
        and run them concurrently so the loop stays responsive.
        """
        from .db_accessor import (
            get_funding_rate_from_db,
            get_oi_change_from_db,
            get_ls_ratio_from_db,
            get_price_change_7d_from_db,
            get_volume_change_7d_from_db,
        )

        return await asyncio.gather(
            asyncio.to_thread(get_funding_rate_from_db, self._derivatives_db, symbol),
            asyncio.to_thread(get_oi_change_from_db, self._derivatives_db, symbol),
            asyncio.to_thread(get_ls_ratio_from_db, self._derivatives_db, symbol),
            asyncio.to_thread(get_price_change_7d_from_db, self._kline_db, symbol),
            asyncio.to_thread(get_volume_change_7d_from_db, self._kline_db, symbol),
        )

    async def get_pool_snapshot(
        self,
        protocol_entry: dict[str, Any],
        force_refresh: bool = False,
        fetch_github: bool = True,
    ) -> SerenitySnapshot:
        """Build a SerenitySnapshot for a protocol entry from /protocols.

        Pool candidates are not in the manual SYMBOL_TO_DEFI_LLAMA /
        SYMBOL_TO_GITHUB maps, so slug and repo are resolved from the entry
        itself (``id`` + ``github[0]``). GitHub is optional — when skipped or
        absent the evidence grading degrades, it never crashes.
        """
        # Real /protocols entries carry a numeric ``id`` + string ``slug``;
        # the detail endpoint takes the slug. Tests mock entries with only ``id``.
        slug = str(
            protocol_entry.get("slug")
            or protocol_entry.get("id")
            or ""
        ).strip()
        symbol = str(protocol_entry.get("symbol") or "").strip().upper() or slug.upper()
        github_urls = protocol_entry.get("github") or []
        repo = self._repo_from_github_url(github_urls[0]) if github_urls else None

        if fetch_github and repo:
            protocol, github = await asyncio.gather(
                self.get_protocol_by_slug(slug, force_refresh=force_refresh),
                self.get_github_by_repo(repo, force_refresh=force_refresh),
            )
        else:
            protocol = await self.get_protocol_by_slug(slug, force_refresh=force_refresh)
            github = None

        tvl_chain_share = 0.0
        if protocol is not None and protocol.tvl_usd > 0:
            tvl_chain_share = 1.0

        funding_rate, oi_change, ls_ratio, price_change_7d, volume_change_7d = (
            await self._enrich_from_local_dbs(symbol)
        )

        return SerenitySnapshot(
            symbol=symbol,
            protocol_metrics=protocol,
            github_activity=github,
            funding_rate=funding_rate,
            oi_change=oi_change,
            ls_ratio=ls_ratio,
            tvl_chain_share=tvl_chain_share,
            price_change_7d=price_change_7d,
            volume_change_7d=volume_change_7d,
            timestamp=int(time.time() * 1000),
        )

    @staticmethod
    def _repo_from_github_url(url: str) -> str | None:
        """Extract ``owner/name`` from a GitHub repo URL, or None.

        DeFi Llama protocol entries carry ``github`` as a list of repo URLs
        (e.g. ``https://github.com/uniswap/uniswap-v3-core``).
        ``get_github_by_repo`` needs the bare ``owner/name``.
        """
        if not url:
            return None
        parts = url.strip().split("github.com/", 1)
        if len(parts) != 2:
            return None
        chunks = [p for p in parts[1].split("/") if p]
        return "/".join(chunks[:2]) if len(chunks) >= 2 else None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
