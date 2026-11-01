"""C2 fix: auth fail-closed behavior.

Verifies that _verify_session_owner / _verify_trading_ownership / _resolve_ws_user
fail CLOSED (return False / raise / close 4401) on repo/lookup errors when
SELENE_AUTH_FAILCLOSED=1 (default), and revert to fail-open when =0.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure service root on path (conftest does this, but be explicit for direct runs).
import sys
from pathlib import Path
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start each test with auth flags at defaults."""
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    yield


@pytest.mark.asyncio
async def test_verify_session_owner_failclosed_repo_none_denies(monkeypatch):
    """repo is None → return False (not True) when failclosed=1."""
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)
    # Non-local user so the local short-circuit does not apply.
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is False


@pytest.mark.asyncio
async def test_verify_session_owner_failclosed_session_none_denies(monkeypatch):
    """session lookup returns None → return False when failclosed=1."""
    from app.api.v1 import agent as agent_mod

    fake_repo = MagicMock()
    fake_repo.get_session = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.core.dependencies.get_chat_session_repo", lambda: fake_repo
    )
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is False


@pytest.mark.asyncio
async def test_verify_session_owner_failclosed_exception_denies(monkeypatch):
    """repo.get_session raises → return False when failclosed=1."""
    from app.api.v1 import agent as agent_mod

    fake_repo = MagicMock()
    fake_repo.get_session = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(
        "app.core.dependencies.get_chat_session_repo", lambda: fake_repo
    )
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is False


@pytest.mark.asyncio
async def test_verify_session_owner_failopen_rollback_repo_none(monkeypatch):
    """SELENE_AUTH_FAILCLOSED=0 → revert to v1 fail-open (repo None → True)."""
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is True


@pytest.mark.asyncio
async def test_verify_session_owner_owner_match_allows(monkeypatch):
    """Owner matches user_id → True (positive path still works)."""
    from app.api.v1 import agent as agent_mod

    fake_repo = MagicMock()
    fake_repo.get_session = AsyncMock(return_value={"user_id": "user-remote"})
    monkeypatch.setattr(
        "app.core.dependencies.get_chat_session_repo", lambda: fake_repo
    )
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is True


def test_verify_trading_ownership_failclosed_importerror_raises(monkeypatch):
    """ImportError on _owner_of → raise (not pass) when failclosed=1."""
    import builtins
    from app.api.v1 import trading as trading_mod
    from app.core.dependencies import AuthenticatedUser

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "app.api.v1.paper":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    user = AuthenticatedUser(id="user-remote", role="user")
    with pytest.raises(ImportError):
        trading_mod._verify_trading_ownership("pa-1", user)


def test_verify_trading_ownership_failopen_rollback_importerror_passes(monkeypatch):
    """SELENE_AUTH_FAILCLOSED=0 → ImportError passes (v1 fail-open rollback)."""
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    import builtins
    from app.api.v1 import trading as trading_mod
    from app.core.dependencies import AuthenticatedUser

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "app.api.v1.paper":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    user = AuthenticatedUser(id="user-remote", role="user")
    # Should not raise.
    trading_mod._verify_trading_ownership("pa-1", user)
