"""C2 fix: SELENE_AUTH_FAILCLOSED flag — hot-toggable at request time.

The flag is read at request time (not startup), so it can be hot-toggled
without redeploy. Default ON; =0 reverts to v1 fail-open for emergency
rollback. C2's safety property survives Phase 1 rollback via this flag.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    yield


@pytest.mark.asyncio
async def test_failclosed_default_on_repo_none_denies(monkeypatch):
    """No env var → default ON → repo None → False."""
    monkeypatch.delenv("SELENE_AUTH_FAILCLOSED", raising=False)
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is False


@pytest.mark.asyncio
async def test_failclosed_off_reverts_to_failopen(monkeypatch):
    """SELENE_AUTH_FAILCLOSED=0 → repo None → True (v1 fail-open rollback)."""
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)
    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is True


@pytest.mark.asyncio
async def test_failclosed_hot_toggled_between_requests(monkeypatch):
    """Flag read at request time: same process, two requests, different results."""
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)

    # Request 1: failclosed=1 (default) → deny.
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    r1 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r1 is False

    # Request 2: hot-toggle to =0 → allow (fail-open rollback).
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    r2 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r2 is True

    # Request 3: hot-toggle back to =1 → deny again.
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    r3 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r3 is False


@pytest.mark.asyncio
async def test_failclosed_independent_of_session_lookup(monkeypatch):
    """Flag gates session-None path too: =1 → deny, =0 → allow."""
    from app.api.v1 import agent as agent_mod

    fake_repo = MagicMock()
    fake_repo.get_session = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.core.dependencies.get_chat_session_repo", lambda: fake_repo
    )

    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    r1 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r1 is False

    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    r2 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r2 is True
