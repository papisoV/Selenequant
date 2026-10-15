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
def _isolate_kill_switch_state():
    """Isolate KillSwitch persisted state per-test (test isolation).

    KillSwitch is a singleton with disk-persisted state at
    ``~/.selenespace/kill_switch.json``. Without isolation, a CRITICAL risk
    alert in one test auto-activates the KS and the state leaks into the next
    test, causing false failures in test_production_closed_loop etc.

    This fixture resets the singleton and deletes the persisted state file
    before+after each test.
    """
    def _reset():
        try:
            from selene_space_risk.kill_switch import KillSwitch
            KillSwitch._reset_singleton()
        except Exception:
            pass
        try:
            ks_path = Path.home() / ".selenespace" / "kill_switch.json"
            if ks_path.exists():
                ks_path.unlink()
        except Exception:
            pass

    _reset()
    yield
    _reset()

