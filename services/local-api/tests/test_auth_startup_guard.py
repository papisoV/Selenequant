"""C2 fix: main.py startup production guard.

Refuses to start (RuntimeError) when SELENE_AUTH_DISABLED=1 AND any of:
- SELENE_ENV == "production"
- SELENE_DEPLOY == "prod"
- non-localhost bind (SELENE_HOST not in 127.0.0.1/localhost/::1)

This is the triple-signal guard — catches the "deploy script forgot to
set SELENE_ENV" case via the interface bind check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


def _run_create_app():
    """Call create_app() fresh (re-import to pick up env at call time)."""
    import importlib
    import app.main as main_mod
    importlib.reload(main_mod)
    return main_mod.create_app()


def test_prod_env_with_auth_disabled_refuses(monkeypatch):
    monkeypatch.setenv("SELENE_ENV", "production")
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "1")
    monkeypatch.delenv("SELENE_DEPLOY", raising=False)
    monkeypatch.setenv("SELENE_HOST", "127.0.0.1")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _run_create_app()


def test_prod_deploy_with_auth_disabled_refuses(monkeypatch):
    monkeypatch.delenv("SELENE_ENV", raising=False)
    monkeypatch.setenv("SELENE_DEPLOY", "prod")
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "1")
    monkeypatch.setenv("SELENE_HOST", "127.0.0.1")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _run_create_app()


def test_non_localhost_bind_with_auth_disabled_refuses(monkeypatch):
    monkeypatch.delenv("SELENE_ENV", raising=False)
    monkeypatch.delenv("SELENE_DEPLOY", raising=False)
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "1")
    monkeypatch.setenv("SELENE_HOST", "0.0.0.0")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        _run_create_app()


def test_localhost_dev_with_auth_disabled_allowed(monkeypatch):
    """localhost + bypass + no prod signal → allowed (dev mode)."""
    monkeypatch.delenv("SELENE_ENV", raising=False)
    monkeypatch.delenv("SELENE_DEPLOY", raising=False)
    monkeypatch.setenv("SELENE_AUTH_DISABLED", "1")
    monkeypatch.setenv("SELENE_HOST", "127.0.0.1")
    # Should not raise.
    app = _run_create_app()
    assert app is not None


def test_no_auth_disabled_no_guard(monkeypatch):
    """No SELENE_AUTH_DISABLED → no guard, allowed in any env."""
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SELENE_ENV", "production")
    monkeypatch.setenv("SELENE_HOST", "0.0.0.0")
    app = _run_create_app()
    assert app is not None
