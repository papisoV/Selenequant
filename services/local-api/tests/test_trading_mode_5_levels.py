"""Tests for /trading/mode 5 levels and Kill Switch endpoints (Phase 4).

These tests verify the endpoint logic directly by calling the async handler
functions with mocked dependencies. We avoid spinning up a TestClient because
the full lifespan (market data, risk engine, etc.) takes too long for unit
tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _user():
    from app.core.dependencies import AuthenticatedUser
    return AuthenticatedUser(id="local", role="admin")


# ---------------------------------------------------------------------------
# /trading/mode — 5-level support
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_mode_paper_always_allowed_no_live_engine() -> None:
    """PAPER mode should succeed even when no live engine is configured."""
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    captured: dict = {}

    def fake_set_execution_mode(mode: str) -> None:
        captured["mode"] = mode

    with patch("app.api.v1.trading.get_live_engine", return_value=None), \
         patch("app.api.v1.trading.set_execution_mode", side_effect=fake_set_execution_mode):
        from pydantic import BaseModel
        # Build the request object directly
        class _Req(BaseModel):
            mode: ExecutionMode

        req = _Req(mode=ExecutionMode.PAPER)
        resp = await set_mode(req, _user())
        assert resp.mode == "paper"
        assert captured["mode"] == "paper"


@pytest.mark.asyncio
async def test_set_mode_shadow_always_allowed_no_live_engine() -> None:
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    with patch("app.api.v1.trading.get_live_engine", return_value=None), \
         patch("app.api.v1.trading.set_execution_mode") as mock_set:
        class _Req:
            mode = ExecutionMode.SHADOW
        resp = await set_mode(_Req(), _user())  # type: ignore[arg-type]
        assert resp.mode == "shadow"
        mock_set.assert_called_once_with("shadow")


@pytest.mark.asyncio
async def test_set_mode_confirm_rejected_without_live_engine() -> None:
    """CONFIRM requires live engine; without one, returns 400."""
    from fastapi import HTTPException
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    with patch("app.api.v1.trading.get_live_engine", return_value=None), \
         patch("app.api.v1.trading.set_execution_mode") as mock_set:
        class _Req:
            mode = ExecutionMode.CONFIRM
        with pytest.raises(HTTPException) as exc_info:
            await set_mode(_Req(), _user())  # type: ignore[arg-type]
        assert exc_info.value.status_code == 400
        assert "live engine" in exc_info.value.detail.lower()
        mock_set.assert_not_called()


@pytest.mark.asyncio
async def test_set_mode_limited_auto_rejected_without_live_engine() -> None:
    from fastapi import HTTPException
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    with patch("app.api.v1.trading.get_live_engine", return_value=None), \
         patch("app.api.v1.trading.set_execution_mode"):
        class _Req:
            mode = ExecutionMode.LIMITED_AUTO
        with pytest.raises(HTTPException) as exc_info:
            await set_mode(_Req(), _user())  # type: ignore[arg-type]
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_mode_full_auto_rejected_without_live_engine() -> None:
    from fastapi import HTTPException
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    with patch("app.api.v1.trading.get_live_engine", return_value=None), \
         patch("app.api.v1.trading.set_execution_mode"):
        class _Req:
            mode = ExecutionMode.FULL_AUTO
        with pytest.raises(HTTPException) as exc_info:
            await set_mode(_Req(), _user())  # type: ignore[arg-type]
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_mode_confirm_rejected_when_connectivity_fails() -> None:
    """Live engine exists but connectivity check fails → 400."""
    from fastapi import HTTPException
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    fake_live = SimpleNamespace()
    fake_client = AsyncMock()
    fake_client.test_connectivity = AsyncMock(return_value=False)
    fake_live._client = fake_client

    with patch("app.api.v1.trading.get_live_engine", return_value=fake_live), \
         patch("app.api.v1.trading.set_execution_mode"):
        class _Req:
            mode = ExecutionMode.CONFIRM
        with pytest.raises(HTTPException) as exc_info:
            await set_mode(_Req(), _user())  # type: ignore[arg-type]
        assert exc_info.value.status_code == 400
        assert "connectivity" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_set_mode_confirm_succeeds_with_working_live_engine() -> None:
    """Live engine + passing connectivity → mode set successfully."""
    from app.api.v1.trading import set_mode
    from selene_space_types.trading import ExecutionMode

    fake_live = SimpleNamespace()
    fake_client = AsyncMock()
    fake_client.test_connectivity = AsyncMock(return_value=True)
    fake_live._client = fake_client

    captured: dict = {}
    with patch("app.api.v1.trading.get_live_engine", return_value=fake_live), \
         patch("app.api.v1.trading.set_execution_mode", side_effect=lambda m: captured.__setitem__("mode", m)):
        class _Req:
            mode = ExecutionMode.CONFIRM
        resp = await set_mode(_Req(), _user())  # type: ignore[arg-type]
        assert resp.mode == "confirm"
        assert captured["mode"] == "confirm"


# ---------------------------------------------------------------------------
# Kill Switch endpoints
# ---------------------------------------------------------------------------


def _reset_kill_switch() -> None:
    from selene_space_risk.kill_switch import KillSwitch
    KillSwitch._reset_singleton()


@pytest.mark.asyncio
async def test_kill_switch_activate_returns_active_true() -> None:
    from app.api.v1.trading import activate_kill_switch, KillSwitchActivateRequest

    _reset_kill_switch()
    req = KillSwitchActivateRequest(reason="test activation", close_positions=False)
    with patch("app.api.v1.trading.get_live_engine", return_value=None):
        resp = await activate_kill_switch(req, _user())
    assert resp.active is True
    assert "test activation" in resp.reason


@pytest.mark.asyncio
async def test_kill_switch_reset_clears_active_state() -> None:
    from app.api.v1.trading import (
        activate_kill_switch,
        reset_kill_switch,
        KillSwitchActivateRequest,
    )

    _reset_kill_switch()
    req = KillSwitchActivateRequest(reason="activation", close_positions=False)
    with patch("app.api.v1.trading.get_live_engine", return_value=None):
        await activate_kill_switch(req, _user())
        resp = await reset_kill_switch(_user())
    assert resp.active is False


@pytest.mark.asyncio
async def test_kill_switch_get_status_reflects_state() -> None:
    from app.api.v1.trading import (
        activate_kill_switch,
        get_kill_switch_status,
        KillSwitchActivateRequest,
    )

    _reset_kill_switch()
    with patch("app.api.v1.trading.get_live_engine", return_value=None):
        # Initially inactive
        r0 = await get_kill_switch_status(_user())
        assert r0.active is False
        # Activate
        await activate_kill_switch(KillSwitchActivateRequest(reason="x"), _user())
        r1 = await get_kill_switch_status(_user())
        assert r1.active is True
        assert "x" in r1.reason


@pytest.mark.asyncio
async def test_kill_switch_activate_with_close_positions_no_client_logs_warning() -> None:
    """close_positions=True with no live client should still succeed (no crash)."""
    from app.api.v1.trading import activate_kill_switch, KillSwitchActivateRequest

    _reset_kill_switch()
    req = KillSwitchActivateRequest(reason="close test", close_positions=True)
    with patch("app.api.v1.trading.get_live_engine", return_value=None):
        resp = await activate_kill_switch(req, _user())
    assert resp.active is True


@pytest.mark.asyncio
async def test_kill_switch_activate_calls_close_all_positions_when_requested() -> None:
    """close_positions=True should call client.close_all_positions()."""
    from app.api.v1.trading import activate_kill_switch, KillSwitchActivateRequest

    _reset_kill_switch()
    fake_client = SimpleNamespace()
    fake_client.close_all_positions = AsyncMock()
    fake_live = SimpleNamespace(_client=fake_client)

    req = KillSwitchActivateRequest(reason="close all", close_positions=True)
    with patch("app.api.v1.trading.get_live_engine", return_value=fake_live):
        await activate_kill_switch(req, _user())
    fake_client.close_all_positions.assert_awaited_once()


# ---------------------------------------------------------------------------
# Kill switch integration with /orders endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_endpoint_blocks_when_kill_switch_active() -> None:
    """When kill switch is active, place_order returns 403."""
    from fastapi import HTTPException
    from app.api.v1.trading import place_order
    from selene_space_risk.kill_switch import KillSwitch

    _reset_kill_switch()
    KillSwitch().activate(reason="orders block test")

    # Minimal request-like object
    req = SimpleNamespace(
        symbol="BTC-USDT-SWAP",
        side="buy",
        order_type="market",
        quantity=10.0,
        price=None,
        stop_price=None,
        leverage=1,
        margin_mode="cross",
        pos_side="long",
        reduce_only=False,
        account_id="paper-default",
        confirm_risk=False,
    )

    fake_engine = SimpleNamespace()
    try:
        with patch("app.api.v1.trading.get_execution_mode", return_value="paper"):
            await place_order(req, request=SimpleNamespace(), engine=fake_engine, user=_user())
        pytest.fail("Expected HTTPException 403")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "kill switch" in exc.detail.lower()
    finally:
        KillSwitch().deactivate()


@pytest.mark.asyncio
async def test_orders_endpoint_blocks_in_confirm_mode() -> None:
    """In CONFIRM mode, bare /orders is rejected (must use preview+confirm)."""
    from fastapi import HTTPException
    from app.api.v1.trading import place_order

    _reset_kill_switch()  # ensure kill switch not active

    req = SimpleNamespace(
        symbol="BTC-USDT-SWAP",
        side="buy",
        order_type="market",
        quantity=10.0,
        price=None,
        stop_price=None,
        leverage=1,
        margin_mode="cross",
        pos_side="long",
        reduce_only=False,
        account_id="paper-default",
        confirm_risk=False,
    )

    fake_engine = SimpleNamespace()
    try:
        with patch("app.api.v1.trading.get_execution_mode", return_value="confirm"):
            await place_order(req, request=SimpleNamespace(), engine=fake_engine, user=_user())
        pytest.fail("Expected HTTPException 403")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "confirm" in exc.detail.lower()
