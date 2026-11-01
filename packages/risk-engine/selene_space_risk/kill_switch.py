"""Emergency kill switch — scoped singleton that blocks new orders.

Supports three activation scopes (P1-3):
  - SYSTEM: global stop (manual emergency, infra failure)
  - ACCOUNT: per-exchange-account stop (API failure, account breach)
  - STRATEGY: per-strategy stop (strategy rule breach)

State is persisted to disk (schema v2) so it survives restarts.
Supports activation callbacks for automatic order cancellation.

Backward compatibility:
  - ``is_active`` property returns SYSTEM scope state (legacy boolean)
  - ``activate(reason)`` activates SYSTEM scope (legacy behavior)
  - ``deactivate()`` deactivates SYSTEM scope
  - ``on_activate(callback)`` registers a no-arg callback (legacy signature)

New scoped API:
  - ``is_active_for(account_id=None, strategy_id=None)`` — OR semantics
  - ``activate_scope(scope, scope_id, reason)`` — explicit scope activation
  - ``deactivate_scope(scope, scope_id)`` — explicit scope deactivation
  - ``active_scopes()`` — list of currently active (scope, scope_id) tuples
"""

from __future__ import annotations

import enum
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from selene_space_core.infra.atomic_write import atomic_write

_DEFAULT_STATE_PATH = Path.home() / ".selenespace" / "kill_switch.json"
_SCHEMA_VERSION = 2
logger = logging.getLogger(__name__)


class KillSwitchScope(str, enum.Enum):
    """Activation scope for the kill switch.

    SYSTEM: Global stop — blocks all orders regardless of account/strategy.
    ACCOUNT: Per-exchange-account stop — blocks orders for one account_id.
    STRATEGY: Per-strategy stop — blocks orders for one strategy_id.
    """

    SYSTEM = "system"
    ACCOUNT = "account"
    STRATEGY = "strategy"


@enum.unique
class _ScopeKey(str, enum.Enum):
    """Internal storage keys for each scope's state dict."""

    SYSTEM = "system"
    ACCOUNTS = "accounts"
    STRATEGIES = "strategies"


class KillSwitch:
    """Scoped emergency stop — blocks new orders when any relevant scope is active.

    Thread-safe singleton with persistent state.

    Args:
        state_path: Path for persisted state. Defaults to ~/.selenespace/kill_switch.json.
    """

    _instance: KillSwitch | None = None
    _lock = threading.Lock()

    def __new__(cls, state_path: Path | None = None) -> KillSwitch:
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._init(state_path or _DEFAULT_STATE_PATH)
                cls._instance = instance
            return cls._instance

    def _init(self, state_path: Path) -> None:
        self._state_path = state_path
        # Per-scope activation state:
        #   _system_active: bool (singleton scope)
        #   _account_active: {account_id: _ScopeEntry}
        #   _strategy_active: {strategy_id: _ScopeEntry}
        self._system_active: bool = False
        self._system_activated_at: float | None = None
        self._system_reason: str = ""
        self._account_active: dict[str, dict[str, Any]] = {}
        self._strategy_active: dict[str, dict[str, Any]] = {}
        self._data_lock = threading.Lock()
        self._on_activate_cbs: list[Callable[[], Any]] = []
        self._load()

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate(self, reason: str = "Manual activation") -> None:
        """Activate the SYSTEM scope — backward-compatible legacy entry point.

        All new orders across all accounts/strategies are rejected.
        """
        self.activate_scope(KillSwitchScope.SYSTEM, scope_id=None, reason=reason)

    def activate_scope(
        self,
        scope: KillSwitchScope,
        scope_id: str | None,
        reason: str = "Manual activation",
    ) -> None:
        """Activate a specific scope.

        For SYSTEM scope, scope_id must be None.
        For ACCOUNT/STRATEGY scope, scope_id is the account/strategy identifier.
        """
        if scope == KillSwitchScope.SYSTEM:
            if scope_id is not None:
                raise ValueError("SYSTEM scope does not accept a scope_id")
            with self._data_lock:
                already = self._system_active
                self._system_active = True
                self._system_activated_at = time.time()
                self._system_reason = reason
                self._save_locked()
            if not already:
                self._fire_activate_callbacks()
            return

        if scope_id is None:
            raise ValueError(f"{scope.value} scope requires a scope_id")

        bucket = self._account_active if scope == KillSwitchScope.ACCOUNT else self._strategy_active
        with self._data_lock:
            already = scope_id in bucket
            bucket[scope_id] = {
                "activated_at": time.time(),
                "reason": reason,
            }
            self._save_locked()
        if not already:
            self._fire_activate_callbacks()

    def auto_activate(
        self,
        reason: str,
        triggered_by_rule: str = "",
        strategy_id: str | None = None,
    ) -> None:
        """Auto-activate kill switch from a risk rule breach.

        If ``strategy_id`` is provided, activates STRATEGY scope (only that
        strategy is blocked; other strategies continue trading). If None,
        activates SYSTEM scope (global block — legacy behavior).

        Records which rule triggered it and publishes a
        RiskThresholdBreachedEvent with the scope info.
        """
        if strategy_id is not None:
            self.activate_scope(
                KillSwitchScope.STRATEGY,
                scope_id=strategy_id,
                reason=f"Auto: {reason}",
            )
            self._publish_breach_event(triggered_by_rule, reason, KillSwitchScope.STRATEGY)
            return

        # SYSTEM scope (legacy)
        with self._data_lock:
            if self._system_active:
                return  # Already active, don't overwrite reason
            self._system_active = True
            self._system_activated_at = time.time()
            self._system_reason = f"Auto: {reason}"
            self._save_locked()

        self._fire_activate_callbacks()
        self._publish_breach_event(triggered_by_rule, reason, KillSwitchScope.SYSTEM)

    def _publish_breach_event(
        self,
        triggered_by_rule: str,
        reason: str,
        scope: KillSwitchScope,
    ) -> None:
        """Publish RiskThresholdBreachedEvent (best-effort, never crash)."""
        try:
            from selene_space_core.events import get_event_bus, RiskThresholdBreachedEvent
            get_event_bus().publish(RiskThresholdBreachedEvent(
                rule_id=triggered_by_rule,
                level="critical",
                message=reason,
                details={"auto_activated": True, "scope": scope.value},
            ))
        except Exception:
            pass  # Event publishing failure must not crash kill switch

    # ------------------------------------------------------------------
    # Deactivation
    # ------------------------------------------------------------------

    def deactivate(self) -> None:
        """Deactivate the SYSTEM scope — resume normal operations (legacy)."""
        self.deactivate_scope(KillSwitchScope.SYSTEM, scope_id=None)

    def deactivate_scope(
        self,
        scope: KillSwitchScope,
        scope_id: str | None,
    ) -> None:
        """Deactivate a specific scope."""
        if scope == KillSwitchScope.SYSTEM:
            if scope_id is not None:
                raise ValueError("SYSTEM scope does not accept a scope_id")
            with self._data_lock:
                self._system_active = False
                self._system_activated_at = None
                self._system_reason = ""
                self._save_locked()
            return

        if scope_id is None:
            raise ValueError(f"{scope.value} scope requires a scope_id")

        bucket = self._account_active if scope == KillSwitchScope.ACCOUNT else self._strategy_active
        with self._data_lock:
            bucket.pop(scope_id, None)
            self._save_locked()

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Whether the SYSTEM scope is active (legacy boolean).

        For scoped queries, use ``is_active_for(account_id, strategy_id)``.
        """
        with self._data_lock:
            return self._system_active

    def is_active_for(
        self,
        *,
        account_id: str | None = None,
        strategy_id: str | None = None,
    ) -> bool:
        """Whether any relevant scope is active for the given context.

        OR semantics: returns True if SYSTEM is active, OR the specified
        account_id is active in ACCOUNT scope, OR the specified strategy_id
        is active in STRATEGY scope. SYSTEM scope always wins (global block).

        No arguments → returns SYSTEM scope state (equivalent to ``is_active``).
        """
        with self._data_lock:
            if self._system_active:
                return True
            if account_id is not None and account_id in self._account_active:
                return True
            if strategy_id is not None and strategy_id in self._strategy_active:
                return True
            return False

    def active_scopes(self) -> list[tuple[KillSwitchScope, str | None]]:
        """List all currently active (scope, scope_id) tuples.

        SYSTEM scope is represented as (SYSTEM, None).
        Callbacks use this to decide which clients to cancel orders for.
        """
        with self._data_lock:
            result: list[tuple[KillSwitchScope, str | None]] = []
            if self._system_active:
                result.append((KillSwitchScope.SYSTEM, None))
            for acct_id in self._account_active:
                result.append((KillSwitchScope.ACCOUNT, acct_id))
            for strat_id in self._strategy_active:
                result.append((KillSwitchScope.STRATEGY, strat_id))
            return result

    @property
    def activated_at(self) -> float | None:
        """Epoch timestamp when SYSTEM scope was activated, or None."""
        with self._data_lock:
            return self._system_activated_at

    @property
    def reason(self) -> str:
        """Reason for SYSTEM scope activation."""
        with self._data_lock:
            return self._system_reason

    def status(self) -> dict[str, object]:
        """Return kill switch status as a dict (SYSTEM scope, legacy shape)."""
        with self._data_lock:
            return {
                "active": self._system_active,
                "activated_at": self._system_activated_at,
                "reason": self._system_reason,
            }

    def full_status(self) -> dict[str, object]:
        """Return full status across all scopes (v2 shape)."""
        with self._data_lock:
            return {
                "version": _SCHEMA_VERSION,
                "system": {
                    "active": self._system_active,
                    "activated_at": self._system_activated_at,
                    "reason": self._system_reason,
                },
                "accounts": dict(self._account_active),
                "strategies": dict(self._strategy_active),
            }

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_activate(self, callback: Callable[[], Any]) -> None:
        """Register a no-arg callback invoked when any scope activates.

        Use this to wire automatic order cancellation or notification.
        Callbacks are fired outside the data lock to avoid deadlock.
        Callbacks can query ``active_scopes()`` to decide which clients
        to cancel orders for.
        """
        self._on_activate_cbs.append(callback)

    def _fire_activate_callbacks(self) -> None:
        """Fire all registered activation callbacks (outside lock)."""
        for cb in self._on_activate_cbs:
            try:
                cb()
            except Exception:
                logger.exception("Kill switch activation callback failed")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Persist state to disk using atomic write."""
        with self._data_lock:
            self._save_locked()

    def _save_locked(self) -> None:
        """Persist state to disk (caller holds _data_lock)."""
        data = json.dumps(
            {
                "version": _SCHEMA_VERSION,
                "system": {
                    "active": self._system_active,
                    "activated_at": self._system_activated_at,
                    "reason": self._system_reason,
                },
                "accounts": self._account_active,
                "strategies": self._strategy_active,
            },
            indent=2,
        )
        atomic_write(self._state_path, data)

    def _load(self) -> None:
        """Load persisted state from disk.

        Supports v1 (flat {active, activated_at, reason}) and v2 (nested)
        schemas. v1 auto-migrates to v2 as SYSTEM scope.
        """
        if not self._state_path.exists():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            state = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            # Corrupted state — default to inactive (safe)
            return

        if not isinstance(state, dict):
            return

        version = state.get("version", 1)

        if version == 1 or "system" not in state:
            # v1 schema: flat {active, activated_at, reason} → migrate to SYSTEM
            self._system_active = bool(state.get("active", False))
            self._system_activated_at = state.get("activated_at")
            self._system_reason = str(state.get("reason", ""))
            # Persist as v2 on next save
            self._save()
            return

        # v2 schema
        system = state.get("system", {}) or {}
        self._system_active = bool(system.get("active", False))
        self._system_activated_at = system.get("activated_at")
        self._system_reason = str(system.get("reason", ""))

        accounts = state.get("accounts", {}) or {}
        self._account_active = {
            str(k): dict(v) for k, v in accounts.items() if isinstance(v, dict)
        }

        strategies = state.get("strategies", {}) or {}
        self._strategy_active = {
            str(k): dict(v) for k, v in strategies.items() if isinstance(v, dict)
        }

    @classmethod
    def _reset_singleton(cls) -> None:
        """Reset singleton for testing. Not for production use."""
        with cls._lock:
            cls._instance = None
