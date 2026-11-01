"""US-005: paper-mode app boot smoke — 4 risk monitors all non-None, no NameError.

Booting the app (paper mode, no live creds) must reach the risk block and mount
funding_reversal_monitor / flatten_hook / fee_monitor / fills_ledger on
app.state. This is the integration seam the per-component unit tests can't
cover: main.py's exact wiring, in a real lifespan.

NOTE: we boot the module-global `app`, not a fresh `create_app()`. main.py's
_init_engines_and_runtime / _shutdown_lifespan write `app.state.*` to the
module-global `app` (main.py:3564) — production serves exactly this instance,
so it is the correct target for the smoke assertion.
"""

from __future__ import annotations

import asyncio
from typing import Any

import app.main as _app_main


def _snapshot_deps_state() -> dict[str, Any]:
    """Snapshot deps module singletons + paper ownership registry.

    The full lifespan boot writes live-mode state into module-level
    dependencies singletons and does NOT restore them on shutdown. Capturing
    them before the boot lets the test put them back afterward so later
    router-level tests (test_trading_orders_quantity etc.) don't inherit a
    stale live-mode guard / execution mode.
    """
    from app.api.v1 import paper as _paper
    from app.core import dependencies as _deps

    return {
        "deps._live_engine": _deps._live_engine,
        "deps._market_service": _deps._market_service,
        "deps._execution_mode": _deps._execution_mode,
        "deps._mode_guard": _deps._mode_guard,
        "deps._risk_engine": _deps._risk_engine,
        "deps._order_journal": _deps._order_journal,
        "deps._shadow_engine": _deps._shadow_engine,
        "deps._paper_account_manager": _deps._paper_account_manager,
        "deps._paper_order_manager": _deps._paper_order_manager,
        "deps._paper_position_manager": _deps._paper_position_manager,
        "paper._account_owners": dict(_paper._account_owners),
        "paper._owners_loaded": _paper._owners_loaded,
    }


def _restore_deps_state(snapshot: dict[str, Any]) -> None:
    """Restore deps singletons + paper registry to pre-boot values."""
    from app.api.v1 import paper as _paper
    from app.core import dependencies as _deps

    _deps._live_engine = snapshot["deps._live_engine"]
    _deps._market_service = snapshot["deps._market_service"]
    _deps._execution_mode = snapshot["deps._execution_mode"]
    _deps._mode_guard = snapshot["deps._mode_guard"]
    _deps._risk_engine = snapshot["deps._risk_engine"]
    _deps._order_journal = snapshot["deps._order_journal"]
    _deps._shadow_engine = snapshot["deps._shadow_engine"]
    _deps._paper_account_manager = snapshot["deps._paper_account_manager"]
    _deps._paper_order_manager = snapshot["deps._paper_order_manager"]
    _deps._paper_position_manager = snapshot["deps._paper_position_manager"]
    _paper._account_owners = snapshot["paper._account_owners"]
    _paper._owners_loaded = snapshot["paper._owners_loaded"]


def test_paper_boot_populates_risk_monitors() -> None:
    async def _boot() -> dict[str, object]:
        # Read the module-global app at RUNTIME, not import time. A prior test
        # (test_auth_startup_guard) calls importlib.reload(app.main), which
        # rebinds app.main.app to a fresh instance; the risk block writes to
        # that CURRENT global, so we must reference it the same way.
        app = _app_main.app
        async with app.router.lifespan_context(app):
            return {
                "funding_reversal_monitor": getattr(app.state, "funding_reversal_monitor", None),
                "flatten_hook": getattr(app.state, "flatten_hook", None),
                "fee_monitor": getattr(app.state, "fee_monitor", None),
                "fills_ledger": getattr(app.state, "fills_ledger", None),
            }

    snapshot = _snapshot_deps_state()
    try:
        state = asyncio.run(_boot())
    finally:
        _restore_deps_state(snapshot)

    assert state["funding_reversal_monitor"] is not None, "T1 FundingReversalMonitor not mounted"
    assert state["flatten_hook"] is not None, "T2 FlattenHook not mounted"
    assert state["fee_monitor"] is not None, "T3 FeeRateMonitor not mounted"
    assert state["fills_ledger"] is not None, "S0 FillsLedger not mounted"
