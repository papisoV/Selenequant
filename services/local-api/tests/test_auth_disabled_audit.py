"""C2 fix: SELENE_AUTH_DISABLED audited bypass.

When SELENE_AUTH_DISABLED=1, the ``user_id == "local"`` short-circuit is
allowed AND a warning is logged on every bypass. Without the flag, the
local short-circuit does NOT bypass ownership checks (fail-closed).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    yield


@pytest.mark.asyncio
async def test_auth_disabled_bypass_local_user_allows_and_logs(monkeypatch, caplog):
    """SELENE_AUTH_DISABLED=1 + user_id=='local' → True + warning logged."""
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "1")
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    # repo is None — would normally fail-closed, but the bypass short-circuits.
    monkeypatch.setattr(deps, "_chat_session_repo", None)
    with caplog.at_level(logging.WARNING, logger="app.api.v1.agent"):
        result = await agent_mod._verify_session_owner("sess-1", "local")
    assert result is True
    assert any("AUTH_DISABLED bypass" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_auth_disabled_off_local_user_does_not_bypass(monkeypatch):
    """No SELENE_AUTH_DISABLED → 'local' user goes through repo check (fail-closed)."""
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)
    # Without the bypass flag, even 'local' must go through the repo check,
    # which fails closed (repo None → False).
    result = await agent_mod._verify_session_owner("sess-1", "local")
    assert result is False


def test_is_auth_disabled_reads_env(monkeypatch):
    from app.core.auth_flags import is_auth_disabled

    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    assert is_auth_disabled() is False
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "1")
    assert is_auth_disabled() is True
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "true")
    assert is_auth_disabled() is True
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "0")
    assert is_auth_disabled() is False


def test_is_auth_failclosed_default_on(monkeypatch):
    from app.core.auth_flags import is_auth_failclosed

    monkeypatch.delenv("SELENE_AUTH_FAILCLOSED", raising=False)
    assert is_auth_failclosed() is True  # default on
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    assert is_auth_failclosed() is False
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "false")
    assert is_auth_failclosed() is False
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    assert is_auth_failclosed() is True
