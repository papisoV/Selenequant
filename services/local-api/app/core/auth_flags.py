"""Auth fail-closed flags + helpers (C2 fix).

Two independent env flags gate auth behavior, both READ AT REQUEST TIME
(not startup) so they can be hot-toggled without redeploy:

- ``SELENE_AUTH_DISABLED=1`` — audited backdoor. When set, localhost
  ``user_id == "local"`` short-circuits ownership checks. Every bypass
  emits a ``logger.warning`` so the bypass is auditable. Triple-signal
  production guard (in main.py startup) refuses to start if this flag is
  set in production / non-localhost bind.

- ``SELENE_AUTH_FAILCLOSED=1`` (default ON) — gates the fail-closed
  behavior in ``_verify_session_owner`` / ``_verify_trading_ownership``
  / ``_resolve_ws_user``. When ON (default), repo/lookup errors return
  False (deny). When explicitly ``=0``, reverts to v1 fail-open for
  emergency rollback only. Independent of C4 blast radius — survives
  Phase 1 rollback.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_auth_disabled() -> bool:
    """Return True if SELENE_AUTH_DISABLED=1 (audited backdoor active)."""
    return _env_truthy("SELENE_AUTH_DISABLED")


def is_auth_failclosed() -> bool:
    """Return True if fail-closed behavior is active (default ON).

    Set ``SELENE_AUTH_FAILCLOSED=0`` to revert to v1 fail-open for
    emergency rollback. Default-on so a missing env var does NOT
    silently reopen the fail-open hole.
    """
    val = os.environ.get("SELENE_AUTH_FAILCLOSED", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def audit_auth_disabled_bypass(user: str, path: str) -> None:
    """Emit a warning every time the SELENE_AUTH_DISABLED bypass is used."""
    logger.warning("AUTH_DISABLED bypass used user=%s path=%s", user, path)
