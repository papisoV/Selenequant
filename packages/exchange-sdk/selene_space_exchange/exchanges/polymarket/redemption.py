"""Polymarket position sweep + auto-redemption.

After resolution, winning outcome tokens redeem 1:1 into USDC.e through the
Conditional Token Framework contract. This client reads positions from the
Data API, checks resolution via Gamma, and posts redemption requests; a
sweep mode finds every redeemable position above a share floor and burns
them one by one, tolerating per-position failures.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.exchanges.polymarket.models import (
    AutoRedeemSummary,
    PolymarketMarket,
    PolymarketPosition,
    PolymarketPositionStatus,
    RedemptionResult,
)

_log = logging.getLogger(__name__)

DEFAULT_GAMMA_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_DATA_URL = "https://data-api.polymarket.com"

CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
PARENT_COLLECTION_ID = "0x" + "00" * 32


class PolymarketRedemptionClient:
    def __init__(
        self,
        *,
        wallet_address: str,
        private_key: str | None = None,
        gamma_url: str = DEFAULT_GAMMA_URL,
        clob_url: str = DEFAULT_CLOB_URL,
        data_url: str = DEFAULT_DATA_URL,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._wallet = wallet_address
        self._private_key = private_key
        self._gamma_url = gamma_url.rstrip("/")
        self._clob_url = clob_url.rstrip("/")
        self._data_url = data_url.rstrip("/")
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(
            calls_per_window=50, window_ms=10_000
        )
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def _gate(self) -> None:
        await self._rate_limiter.acquire()

    # -- positions ---------------------------------------------------------------

    async def get_positions(
        self, *, size_threshold: float = 0.0, redeemable_only: bool = False
    ) -> list[PolymarketPosition]:
        await self._gate()
        resp = await self._http.get(
            f"{self._data_url}/positions", params={"user": self._wallet}
        )
        resp.raise_for_status()
        out: list[PolymarketPosition] = []
        for row in resp.json() or []:
            size = float(row.get("size") or 0.0)
            if size < size_threshold:
                continue
            if row.get("redeemable"):
                status = PolymarketPositionStatus.REDEEMABLE
            else:
                status = PolymarketPositionStatus.OPEN
            pos = PolymarketPosition(
                condition_id=row.get("conditionId", ""),
                asset_id=row.get("assetId", ""),
                title=row.get("title", ""),
                outcome=row.get("outcome", ""),
                size=size,
                avg_price=float(row.get("avgPrice") or 0.0),
                current_price=float(row.get("curPrice") or 0.0),
                current_value=float(row.get("currentValue") or 0.0),
                cost_basis=float(row.get("initialValue") or 0.0),
                cash_pnl=float(row.get("cashPnl") or 0.0),
                percent_pnl=float(row.get("percentPnl") or 0.0),
                redeemable=bool(row.get("redeemable", False)),
                is_winner=bool(row.get("isWinner", False)),
                status=status,
            )
            if redeemable_only and not pos.redeemable:
                continue
            out.append(pos)
        return out

    async def get_redeemable_positions(self) -> list[PolymarketPosition]:
        return await self.get_positions(redeemable_only=True)

    async def get_portfolio_value(self) -> float:
        await self._gate()
        resp = await self._http.get(f"{self._data_url}/value", params={"user": self._wallet})
        resp.raise_for_status()
        return float(resp.json().get("value", 0.0))

    # -- resolution ------------------------------------------------------------------

    async def get_market(self, condition_id: str) -> PolymarketMarket:
        await self._gate()
        resp = await self._http.get(f"{self._gamma_url}/markets/{condition_id}")
        resp.raise_for_status()
        raw = resp.json()
        winner = ""
        if raw.get("resolved"):
            for token in raw.get("tokens") or []:
                if token.get("winner"):
                    winner = token.get("outcome", "")
                    break
        return PolymarketMarket(
            condition_id=raw.get("condition_id", condition_id),
            question_id=raw.get("question_id", ""),
            question=raw.get("question", ""),
            description=raw.get("description", ""),
            market_slug=raw.get("market_slug", ""),
            end_date_iso=raw.get("end_date_iso"),
            active=bool(raw.get("active", True)),
            closed=bool(raw.get("closed", False)),
            resolved=bool(raw.get("resolved", False)),
            winning_outcome=winner,
            volume=float(raw.get("volume") or 0.0),
            volume_24h=float(raw.get("volume_num_24hr") or 0.0),
            liquidity=float(raw.get("liquidity") or 0.0),
        )

    async def is_market_resolved(self, condition_id: str) -> bool:
        return (await self.get_market(condition_id)).resolved

    async def get_winning_outcome(self, condition_id: str) -> str | None:
        market = await self.get_market(condition_id)
        return market.winning_outcome or None

    # -- redemption --------------------------------------------------------------------

    def _build_redeem_headers(self) -> dict[str, str]:
        return {
            "POLY-ADDRESS": self._wallet,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _redeem_body(condition_id: str, index_sets: list[int] | None) -> dict[str, Any]:
        return {
            "collateralToken": USDC_E_ADDRESS,
            "parentCollectionId": PARENT_COLLECTION_ID,
            "conditionId": condition_id,
            "indexSets": index_sets or [1, 2],
        }

    async def redeem_position(
        self, *, condition_id: str, index_sets: list[int] | None = None
    ) -> RedemptionResult:
        try:
            market = await self.get_market(condition_id)
            if not market.resolved:
                return RedemptionResult(
                    condition_id=condition_id,
                    error=f"market {condition_id} is not yet resolved",
                )
            await self._gate()
            resp = await self._http.post(
                f"{self._clob_url}/redeem",
                json=self._redeem_body(condition_id, index_sets),
                headers=self._build_redeem_headers(),
            )
            if resp.status_code >= 400:
                try:
                    message = resp.json().get("error") or resp.text
                except Exception:
                    message = resp.text
                return RedemptionResult(condition_id=condition_id, error=message)

            payload = resp.json()
            return RedemptionResult(
                condition_id=condition_id,
                outcome=market.winning_outcome,
                transaction_hash=payload.get("transactionHash", ""),
                success=True,
            )
        except httpx.HTTPError as exc:
            return RedemptionResult(condition_id=condition_id, error=str(exc))

    # -- sweep -------------------------------------------------------------------------

    async def _check_untagged_positions(
        self, positions: list[PolymarketPosition], min_shares: float
    ) -> list[PolymarketPosition]:
        """API didn't tag these — resolve each market ourselves."""
        confirmed: list[PolymarketPosition] = []
        for pos in positions:
            if pos.size < min_shares:
                continue
            try:
                market = await self.get_market(pos.condition_id)
            except Exception as exc:
                _log.debug("resolution check failed for %s: %s", pos.condition_id, exc)
                continue
            if market.resolved and market.winning_outcome == pos.outcome:
                pos.redeemable = True
                pos.is_winner = True
                pos.status = PolymarketPositionStatus.REDEEMABLE
                confirmed.append(pos)
        return confirmed

    async def auto_redeem_all(
        self, *, min_shares: float = 0.1, dry_run: bool = False
    ) -> AutoRedeemSummary:
        summary = AutoRedeemSummary()
        positions = await self.get_positions()

        tagged = [p for p in positions if p.redeemable]
        untagged = [
            p
            for p in positions
            if not p.redeemable and p.size >= min_shares and not p.is_winner
        ]
        summary.markets_checked = len(positions)

        candidates = [p for p in tagged if p.size >= min_shares]
        if untagged:
            candidates += await self._check_untagged_positions(untagged, min_shares)
        summary.redeemable_found = len(candidates)

        for pos in candidates:
            summary.results.append(RedemptionResult(condition_id=pos.condition_id))
            result = summary.results[-1]
            if dry_run:
                result.error = "dry_run"
                result.usdc_received = pos.current_value
                continue

            summary.redemptions_attempted += 1
            outcome = await self.redeem_position(condition_id=pos.condition_id)
            if outcome.success:
                summary.redemptions_succeeded += 1
                outcome.shares_redeemed = pos.size
                outcome.usdc_received = pos.current_value
                summary.total_usdc_redeemed += outcome.usdc_received
            else:
                _log.warning("redeem %s failed: %s", pos.condition_id, outcome.error)
            summary.results[-1] = outcome

        return summary
