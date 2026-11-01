"""Conftest for local-api tests — adds app to sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure ``services/local-api/`` is on sys.path so ``import app`` works.
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture(autouse=True)
def _reset_kill_switch_singleton():
    """Start every test with a virgin KillSwitch singleton.

    The state *path* is redirected away from ``~/.selenespace`` by the
    repo-root conftest; this fixture only drops the in-process singleton so
    one test's activation cannot be seen by the next.

    This used to also delete ``~/.selenespace/kill_switch.json``.  That was
    worse than the leak it fixed: a developer who armed the kill switch on
    purpose had it silently disarmed by running the test suite (2026-10-22).
    Test isolation must never write to, or delete, real user state.
    """

    def _reset():
        try:
            from selene_space_risk.kill_switch import KillSwitch

            KillSwitch._reset_singleton()
        except Exception:
            pass

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _restore_execution_mode_globals():
    """Give every test the same execution-mode globals it started with.

    ``set_mode`` (``app/api/v1/trading.py``) mutates two process-wide objects:
    it calls ``deps.set_execution_mode(...)`` and, because the guard is a
    singleton, assigns ``guard.mode`` *in place*.  A test that flips the mode
    therefore changes what every later test in the process sees — a
    ``test_set_mode_confirm_*`` leaves the app in CONFIRM, and every later
    ``POST /trading/orders`` is rejected with "Confirm mode requires a
    preview_id" (403 at trading.py:483) until something else happens to set it
    back.  Restoring the module globals here keeps that pollution in one test.
    """
    try:
        from app.core import dependencies as deps
    except ImportError:  # app not importable here — nothing to restore
        yield
        return

    mode_before = deps._execution_mode
    guard_before = deps._mode_guard
    guard_mode_before = getattr(guard_before, "mode", None)
    try:
        yield
    finally:
        deps._execution_mode = mode_before
        deps._mode_guard = guard_before
        # The guard is mutated in place, so restoring the reference alone would
        # leave the previous mode on it.
        if guard_before is not None and guard_mode_before is not None:
            try:
                guard_before.mode = guard_mode_before
            except Exception:
                pass
