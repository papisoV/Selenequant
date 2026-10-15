"""C2 fix: rollback-safety — C2's fail-closed survives Phase 1 rollback.

Pre-mortem #5: if Phase 1 is rolled back (C4 code reverted), C2's
fail-closed behavior must stay active as long as SELENE_AUTH_FAILCLOSED=1.
The flag is an env var, not a code change, so reverting code does NOT
unset it. Ops can hot-toggle without redeploy.

This test simulates the rollback scenario by re-importing the agent
module fresh (simulating code revert) and verifying the flag still gates
behavior.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    yield


@pytest.mark.asyncio
async def test_c2_failclosed_survives_phase1_rollback(monkeypatch):
    """Simulate Phase 1 rollback: flag stays ON → C2 still fail-closed.

    The rollback scenario: C4 code is reverted, but the env var
    SELENE_AUTH_FAILCLOSED=1 is still set in the deployment env. C2's
    safety property (fail-closed on repo/lookup errors) is preserved
    because the flag is read at request time from the env, not from a
    code constant.
    """
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    # Simulate "C4 rolled back" — irrelevant to C2, but proves independence.
    # C2's fail-closed only depends on SELENE_AUTH_FAILCLOSED + repo state.
    monkeypatch.setattr(deps, "_chat_session_repo", None)

    # Reload to simulate fresh process after rollback.
    importlib.reload(agent_mod)

    result = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert result is False, "C2 must stay fail-closed after Phase 1 rollback"


@pytest.mark.asyncio
async def test_c2_failclosed_can_be_hot_toggled_off(monkeypatch):
    """Ops can hot-toggle SELENE_AUTH_FAILCLOSED=0 for emergency fail-open.

    This is the rollback escape hatch: if C2's fail-closed breaks
    production (e.g. locks out localhost), ops sets =0 without redeploy.
    """
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    from app.api.v1 import agent as agent_mod
    from app.core import dependencies as deps

    monkeypatch.setattr(deps, "_chat_session_repo", None)

    # Confirm fail-closed first.
    r1 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r1 is False

    # Hot-toggle off → fail-open.
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    r2 = await agent_mod._verify_session_owner("sess-1", "user-remote")
    assert r2 is True


@pytest.mark.asyncio
async def test_c2_flag_decoupled_from_c4_state(monkeypatch):
    """C2 flag works regardless of C4-related module state.

    The flag is in app.core.auth_flags, decoupled from strategy_runner
    (C4's home). Reverting C4 does not affect C2's flag read.
    """
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    from app.core.auth_flags import is_auth_failclosed

    # Flag read is a pure env read — no dependency on C4 modules.
    assert is_auth_failclosed() is True
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "0")
    assert is_auth_failclosed() is False
