"""Polymarket position tracking + CTF auto-redemption client behavior."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from selene_space_exchange.exchanges.polymarket.models import (
    AutoRedeemSummary,
    PolymarketMarket,
    PolymarketPosition,
    PolymarketPositionStatus,
    RedemptionResult,
)
from selene_space_exchange.exchanges.polymarket.redemption import (
    CTF_ADDRESS,
    DEFAULT_CLOB_URL,
    DEFAULT_DATA_URL,
    DEFAULT_GAMMA_URL,
    PARENT_COLLECTION_ID,
    USDC_E_ADDRESS,
    PolymarketRedemptionClient,
)

WALLET = "0x1234567890abcdef1234567890abcdef12345678"
CONDITION = "0xabc123def456789abc123def456789abc123def456789abc123def456789abc1"
TOKEN_YES = "111222333444"
TOKEN_NO = "555666777888"


class TestPydanticModels:
    def test_position_lifecycle_enum_values(self) -> None:
        values = {s.value for s in PolymarketPositionStatus}
        assert values == {"open", "redeemable", "redeemed", "closed"}

    def test_fresh_position_sits_in_open_with_zero_size(self) -> None:
        pos = PolymarketPosition(condition_id="0xabc")
        checks = {
            "size": pos.size == 0.0,
            "flag": pos.redeemable is False,
            "winner": pos.is_winner is False,
            "status": pos.status is PolymarketPositionStatus.OPEN,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_populated_position_carries_pnl_fields(self) -> None:
        pos = PolymarketPosition(
            condition_id="0xabc",
            asset_id="token_yes",
            title="Will X happen?",
            outcome="Yes",
            size=100.0,
            avg_price=0.55,
            current_price=1.0,
            current_value=100.0,
            cost_basis=55.0,
            cash_pnl=45.0,
            percent_pnl=81.8,
            redeemable=True,
            is_winner=True,
            status=PolymarketPositionStatus.REDEEMABLE,
        )
        assert (pos.cash_pnl, pos.is_winner) == (45.0, True)

    def test_redemption_result_starts_unsuccessful(self) -> None:
        bare = RedemptionResult(condition_id="0xabc")
        checks = {
            "success": bare.success is False,
            "shares": bare.shares_redeemed == 0.0,
            "usdc": bare.usdc_received == 0.0,
            "tx": bare.transaction_hash == "",
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_successful_redemption_result(self) -> None:
        win = RedemptionResult(
            condition_id="0xabc",
            outcome="Yes",
            shares_redeemed=100.0,
            usdc_received=100.0,
            transaction_hash="0xhash123",
            success=True,
        )
        assert (win.success, win.usdc_received) == (True, 100.0)

    def test_summary_defaults_are_all_zero(self) -> None:
        tally = AutoRedeemSummary()
        checks = {
            "checked": tally.markets_checked == 0,
            "found": tally.redeemable_found == 0,
            "usdc": tally.total_usdc_redeemed == 0.0,
            "results": tally.results == [],
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_market_resolution_fields(self) -> None:
        settled = PolymarketMarket(
            condition_id="0xabc",
            question="Will X happen?",
            resolved=True,
            winning_outcome="Yes",
        )
        assert (settled.resolved, settled.winning_outcome) == (True, "Yes")


class TestOnchainConstants:
    @staticmethod
    def _is_hex_address(value: str) -> bool:
        return value.startswith("0x") and len(value) == 42

    def test_ctf_contract(self) -> None:
        assert self._is_hex_address(CTF_ADDRESS)

    def test_usdc_e_contract(self) -> None:
        assert self._is_hex_address(USDC_E_ADDRESS)

    def test_parent_collection_is_bytes32(self) -> None:
        assert PARENT_COLLECTION_ID.startswith("0x") and len(PARENT_COLLECTION_ID) == 66


class TestClientWiring:
    def test_defaults_point_at_production_urls(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        assert (client._wallet, client._gamma_url, client._clob_url, client._data_url) == (
            WALLET,
            DEFAULT_GAMMA_URL,
            DEFAULT_CLOB_URL,
            DEFAULT_DATA_URL,
        )

    def test_custom_urls_have_trailing_slash_stripped(self) -> None:
        client = PolymarketRedemptionClient(
            wallet_address=WALLET,
            gamma_url="https://custom-gamma.example.com/",
            clob_url="https://custom-clob.example.com/",
            data_url="https://custom-data.example.com/",
        )
        assert (client._gamma_url, client._clob_url, client._data_url) == (
            "https://custom-gamma.example.com",
            "https://custom-clob.example.com",
            "https://custom-data.example.com",
        )

    def test_private_key_stays_optional(self) -> None:
        assert PolymarketRedemptionClient(wallet_address=WALLET)._private_key is None
        keyed = PolymarketRedemptionClient(wallet_address=WALLET, private_key="0xprivatekey")
        assert keyed._private_key == "0xprivatekey"


# ---------------------------------------------------------------------------
# Response doubles + payload builders
# ---------------------------------------------------------------------------


def _json_response(data: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(data).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://stub.invalid"),
    )


def _market_payload(*, resolved: bool = False, winner_outcome: str = "") -> dict[str, Any]:
    tokens = [
        {
            "token_id": TOKEN_YES,
            "outcome": "Yes",
            "price": 1.0 if winner_outcome == "Yes" else 0.0,
            "winner": winner_outcome == "Yes",
        },
        {
            "token_id": TOKEN_NO,
            "outcome": "No",
            "price": 1.0 if winner_outcome == "No" else 0.0,
            "winner": winner_outcome == "No",
        },
    ]
    return {
        "condition_id": CONDITION,
        "question_id": "qid_123",
        "question": "Will X happen?",
        "description": "Test market",
        "market_slug": "will-x-happen",
        "end_date_iso": "2026-12-31T23:59:59Z",
        "active": not resolved,
        "closed": resolved,
        "resolved": resolved,
        "tokens": tokens,
        "volume": "50000",
        "volume_num_24hr": "1200",
        "liquidity": "25000",
    }


def _position_payload(
    *,
    condition: str = CONDITION,
    asset: str = TOKEN_YES,
    outcome: str = "Yes",
    size: float = 50.0,
    cur_price: float = 0.75,
    redeemable: bool = False,
    is_winner: bool = False,
) -> dict[str, Any]:
    current_value = size * cur_price
    initial_value = size * 0.6
    return {
        "conditionId": condition,
        "assetId": asset,
        "title": "Will X happen?",
        "outcome": outcome,
        "size": str(size),
        "avgPrice": "0.6",
        "curPrice": str(cur_price),
        "currentValue": str(current_value),
        "initialValue": str(initial_value),
        "cashPnl": str(current_value - initial_value),
        "percentPnl": "66.7" if redeemable else "25.0",
        "redeemable": redeemable,
        "isWinner": is_winner,
    }


def _wired_client(get_result=None, post_result=None) -> PolymarketRedemptionClient:
    """Client with mocked transport and rate limiter ready to go."""
    client = PolymarketRedemptionClient(wallet_address=WALLET)
    client._rate_limiter = AsyncMock()
    client._http = AsyncMock()
    if get_result is not None:
        client._http.get = AsyncMock(return_value=get_result)
    if post_result is not None:
        client._http.post = AsyncMock(return_value=post_result)
    return client


class TestPositionQueries:
    @pytest.mark.asyncio
    async def test_open_position_parses_numeric_strings(self) -> None:
        client = _wired_client(_json_response([_position_payload()]))
        positions = await client.get_positions()

        assert len(positions) == 1
        pos = positions[0]
        checks = {
            "condition": pos.condition_id == CONDITION,
            "asset": pos.asset_id == TOKEN_YES,
            "outcome": pos.outcome == "Yes",
            "size": pos.size == 50.0,
            "avg": pos.avg_price == 0.6,
            "status": pos.status is PolymarketPositionStatus.OPEN,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_redeemable_tag_promotes_status(self) -> None:
        client = _wired_client(_json_response([_position_payload(redeemable=True, is_winner=True)]))
        positions = await client.get_positions()

        assert len(positions) == 1
        assert (positions[0].redeemable, positions[0].is_winner) == (True, True)
        assert positions[0].status is PolymarketPositionStatus.REDEEMABLE

    @pytest.mark.asyncio
    async def test_get_redeemable_filters_the_feed(self) -> None:
        client = _wired_client(_json_response([_position_payload(redeemable=True, is_winner=True)]))
        redeemable = await client.get_redeemable_positions()
        assert len(redeemable) == 1 and redeemable[0].redeemable is True

    @pytest.mark.asyncio
    async def test_empty_wallet_yields_nothing(self) -> None:
        client = _wired_client(_json_response([]))
        assert await client.get_positions() == []

    @pytest.mark.asyncio
    async def test_portfolio_value_passthrough(self) -> None:
        client = _wired_client(_json_response({"value": 1234.56}))
        assert await client.get_portfolio_value() == 1234.56


class TestMarketResolution:
    @pytest.mark.asyncio
    async def test_live_market_has_no_winner_yet(self) -> None:
        client = _wired_client(_json_response(_market_payload(resolved=False)))
        market = await client.get_market(CONDITION)
        assert (market.resolved, market.winning_outcome, market.active) == (False, "", True)

    @pytest.mark.asyncio
    async def test_yes_side_settles_first(self) -> None:
        client = _wired_client(_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        market = await client.get_market(CONDITION)
        assert (market.resolved, market.winning_outcome) == (True, "Yes")

    @pytest.mark.asyncio
    async def test_no_side_can_win_too(self) -> None:
        client = _wired_client(_json_response(_market_payload(resolved=True, winner_outcome="No")))
        market = await client.get_market(CONDITION)
        assert (market.resolved, market.winning_outcome) == (True, "No")

    @pytest.mark.asyncio
    async def test_resolution_probe_true_and_false(self) -> None:
        settled = _wired_client(_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        live = _wired_client(_json_response(_market_payload(resolved=False)))
        assert await settled.is_market_resolved(CONDITION) is True
        assert await live.is_market_resolved(CONDITION) is False

    @pytest.mark.asyncio
    async def test_winning_outcome_none_while_unresolved(self) -> None:
        settled = _wired_client(_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        live = _wired_client(_json_response(_market_payload(resolved=False)))
        assert await settled.get_winning_outcome(CONDITION) == "Yes"
        assert await live.get_winning_outcome(CONDITION) is None


class TestRedeemSingle:
    @pytest.mark.asyncio
    async def test_happy_path_records_tx_hash_and_outcome(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        client._http.post = AsyncMock(
            return_value=_json_response({"transactionHash": "0xredeem_hash_123", "message": "Tokens redeemed successfully."})
        )

        result = await client.redeem_position(condition_id=CONDITION)
        checks = {
            "success": result.success is True,
            "tx": result.transaction_hash == "0xredeem_hash_123",
            "condition": result.condition_id == CONDITION,
            "outcome": result.outcome == "Yes",
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_unresolved_market_refuses_to_redeem(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=_json_response(_market_payload(resolved=False)))

        result = await client.redeem_position(condition_id=CONDITION)
        assert (result.success, "not yet resolved" in result.error) == (False, True)

    @pytest.mark.asyncio
    async def test_api_error_message_surfaces(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        client._http.post = AsyncMock(return_value=_json_response({"error": "Invalid condition ID"}, status=400))

        result = await client.redeem_position(condition_id=CONDITION)
        assert (result.success, "Invalid condition ID" in result.error) == (False, True)

    @pytest.mark.asyncio
    async def test_transport_failure_is_caught_not_raised(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        client._http.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        result = await client.redeem_position(condition_id=CONDITION)
        assert (result.success, "connection refused" in result.error) == (False, True)

    @pytest.mark.asyncio
    async def test_explicit_index_sets_flow_into_body(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        client._http.post = AsyncMock(return_value=_json_response({"transactionHash": "0xhash456"}))

        result = await client.redeem_position(condition_id=CONDITION, index_sets=[1])
        assert result.success is True

        body = client._http.post.call_args.kwargs.get("json", {})
        assert body["indexSets"] == [1]

    def test_redeem_headers_carry_wallet_identity(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        headers = client._build_redeem_headers()
        assert (headers["POLY-ADDRESS"], headers["Content-Type"]) == (WALLET, "application/json")

    @pytest.mark.asyncio
    async def test_default_body_targets_usdc_collateral(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()
        client._http = AsyncMock()
        client._http.get = AsyncMock(return_value=_json_response(_market_payload(resolved=True, winner_outcome="Yes")))
        client._http.post = AsyncMock(return_value=_json_response({"transactionHash": "0xhash"}))

        await client.redeem_position(condition_id=CONDITION)

        body = client._http.post.call_args.kwargs.get("json", {})
        checks = {
            "collateral": body["collateralToken"] == USDC_E_ADDRESS,
            "parent": body["parentCollectionId"] == PARENT_COLLECTION_ID,
            "condition": body["conditionId"] == CONDITION,
            "both-sides": body["indexSets"] == [1, 2],
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestAutoRedeemSweep:
    @pytest.mark.asyncio
    async def test_nothing_to_do_reports_zeroes(self) -> None:
        client = _wired_client(_json_response([]))
        summary = await client.auto_redeem_all()
        checks = {
            "checked": summary.markets_checked == 0,
            "found": summary.redeemable_found == 0,
            "attempted": summary.redemptions_attempted == 0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_single_winner_gets_redeemed_for_full_share_count(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()

        def route(url: str, **kwargs: Any) -> httpx.Response:
            return _json_response(
                [_position_payload(size=50.0, cur_price=1.0, redeemable=True, is_winner=True)]
                if "positions" in url
                else _market_payload(resolved=True, winner_outcome="Yes")
            )

        client._http = AsyncMock()
        client._http.get = AsyncMock(side_effect=route)
        client._http.post = AsyncMock(return_value=_json_response({"transactionHash": "0xauto_hash"}))

        summary = await client.auto_redeem_all()
        checks = {
            "checked": summary.markets_checked == 1,
            "found": summary.redeemable_found == 1,
            "attempted": summary.redemptions_attempted == 1,
            "succeeded": summary.redemptions_succeeded == 1,
            "usdc": summary.total_usdc_redeemed == 50.0,
            "one-result": len(summary.results) == 1 and summary.results[0].success is True,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_dry_run_never_posts(self) -> None:
        client = _wired_client(_json_response([_position_payload(size=50.0, cur_price=1.0, redeemable=True, is_winner=True)]))
        summary = await client.auto_redeem_all(dry_run=True)

        checks = {
            "found": summary.redeemable_found == 1,
            "no-attempt": summary.redemptions_attempted == 0,
            "marker-error": summary.results[0].error == "dry_run",
            "would-be-usdc": summary.results[0].usdc_received == 50.0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"
        client._http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_dust_filter_excludes_tiny_holdings(self) -> None:
        dust = [_position_payload(size=0.05, cur_price=1.0, redeemable=True, is_winner=True)]
        client = _wired_client(_json_response(dust))
        summary = await client.auto_redeem_all(min_shares=1.0)
        assert (summary.redeemable_found, summary.redemptions_attempted) == (0, 0)

    @pytest.mark.asyncio
    async def test_untagged_positions_checked_against_market_state(self) -> None:
        """API may omit the redeemable tag; the sweep asks the market directly."""
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()

        def route(url: str, **kwargs: Any) -> httpx.Response:
            if "positions" in url:
                return _json_response([_position_payload()])
            return _json_response(_market_payload(resolved=True, winner_outcome="Yes"))

        client._http = AsyncMock()
        client._http.get = AsyncMock(side_effect=route)
        client._http.post = AsyncMock(return_value=_json_response({"transactionHash": "0xuntagged"}))

        summary = await client.auto_redeem_all()
        checks = {
            "found": summary.redeemable_found == 1,
            "attempted": summary.redemptions_attempted == 1,
            "succeeded": summary.redemptions_succeeded == 1,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_losing_side_never_enters_the_queue(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()

        losing = [_position_payload(asset=TOKEN_NO, outcome="No", size=50.0, cur_price=0.0)]

        def route(url: str, **kwargs: Any) -> httpx.Response:
            if "positions" in url:
                return _json_response(losing)
            return _json_response(_market_payload(resolved=True, winner_outcome="Yes"))

        client._http = AsyncMock()
        client._http.get = AsyncMock(side_effect=route)

        summary = await client.auto_redeem_all()
        assert (summary.redeemable_found, summary.redemptions_attempted) == (0, 0)

    @pytest.mark.asyncio
    async def test_one_failure_does_not_halt_the_sweep(self) -> None:
        client = PolymarketRedemptionClient(wallet_address=WALLET)
        client._rate_limiter = AsyncMock()

        two_positions = [
            _position_payload(condition="0xcond_a", asset="tok_a", size=100.0, redeemable=True, is_winner=True),
            _position_payload(condition="0xcond_b", asset="tok_b", size=200.0, cur_price=1.0, redeemable=True, is_winner=True),
        ]

        def route(url: str, **kwargs: Any) -> httpx.Response:
            if "positions" in url:
                return _json_response(two_positions)
            if "0xcond_a" in url:
                return _json_response({**_market_payload(resolved=True, winner_outcome="Yes"), "condition_id": "0xcond_a"})
            return _json_response({**_market_payload(resolved=True, winner_outcome="Yes"), "condition_id": "0xcond_b"})

        posts: list[httpx.Response] = [
            _json_response({"error": "Network timeout"}, status=500),
            _json_response({"transactionHash": "0xb_hash"}),
        ]

        async def pop_post(*args: Any, **kwargs: Any) -> httpx.Response:
            return posts.pop(0)

        client._http = AsyncMock()
        client._http.get = AsyncMock(side_effect=route)
        client._http.post = AsyncMock(side_effect=pop_post)

        summary = await client.auto_redeem_all()
        checks = {
            "found": summary.redeemable_found == 2,
            "attempted": summary.redemptions_attempted == 2,
            "partial-success": summary.redemptions_succeeded == 1,
            "two-results": len(summary.results) == 2,
            "order-preserved": (summary.results[0].success, summary.results[1].success) == (False, True),
            "net-usdc": summary.total_usdc_redeemed == 200.0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


@pytest.mark.asyncio
async def test_close_shuts_the_transport() -> None:
    client = PolymarketRedemptionClient(wallet_address=WALLET)
    client._http = AsyncMock()
    await client.close()
    client._http.aclose.assert_called_once()
