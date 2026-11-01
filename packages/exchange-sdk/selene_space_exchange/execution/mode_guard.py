"""Execution mode guard — enforces mode-specific restrictions.

Five graduated modes:
  PAPER        — No restrictions (existing behavior)
  SHADOW       — Intercept REST, journal + simulate only
  CONFIRM      — Requires valid preview_id + confirmation
  LIMITED_AUTO — Hard limits (daily notional, position, frequency)
  FULL_AUTO    — Only risk engine checks
"""

from __future__ import annotations

import logging
from typing import Any

from selene_space_types.trading import ExecutionMode

logger = logging.getLogger(__name__)


class ModeGuardError(Exception):
    """Raised when an order violates mode restrictions."""

    def __init__(self, mode: ExecutionMode, reason: str) -> None:
        self.mode = mode
        self.reason = reason
        super().__init__(f"[{mode.value}] {reason}")


class ExecutionModeGuard:
    """Enforces mode-specific restrictions on order placement.

    Usage::

        guard = ExecutionModeGuard(mode=ExecutionMode.CONFIRM)
        guard.check(order_params)  # raises ModeGuardError if blocked
    """

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.PAPER,
        *,
        # Limited auto limits
        max_daily_notional: float = 2000.0,
        max_daily_orders: int = 20,
        max_position_notional: float = 500.0,
        max_single_order_notional: float = 200.0,
        max_leverage: int = 3,
        allowed_symbols: set[str] | None = None,
        require_stop_loss: bool = True,
        # Full auto limits
        full_auto_max_leverage: int = 20,
    ) -> None:
        self._mode = mode
        self._max_daily_notional = max_daily_notional
        self._max_daily_orders = max_daily_orders
        self._max_position_notional = max_position_notional
        self._max_single_order_notional = max_single_order_notional
        self._max_leverage = max_leverage
        self._allowed_symbols = allowed_symbols
        self._require_stop_loss = require_stop_loss
        self._full_auto_max_leverage = full_auto_max_leverage

        # Daily tracking
        self._daily_notional: float = 0.0
        self._daily_orders: int = 0
        self._position_notionals: dict[str, float] = {}  # symbol → current notional

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    @mode.setter
    def mode(self, value: ExecutionMode) -> None:
        self._mode = value
        logger.info("Execution mode changed to: %s", value.value)

    def check(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        leverage: int = 1,
        has_stop_loss: bool = False,
        preview_id: str | None = None,
        preview_manager: object | None = None,
    ) -> None:
        """Check if an order is allowed under the current mode.

        Raises ModeGuardError if blocked.
        """
        if self._mode == ExecutionMode.PAPER:
            return  # No restrictions

        if self._mode == ExecutionMode.SHADOW:
            # Shadow mode: orders intercepted, no real restrictions needed
            return

        if self._mode == ExecutionMode.CONFIRM:
            self._check_confirm_mode(preview_id=preview_id, preview_manager=preview_manager)
            return

        if self._mode == ExecutionMode.LIMITED_AUTO:
            self._check_limited_auto(
                symbol=symbol, quantity=quantity, leverage=leverage,
                has_stop_loss=has_stop_loss,
            )
            return

        if self._mode == ExecutionMode.FULL_AUTO:
            self._check_full_auto(symbol=symbol, quantity=quantity, leverage=leverage)
            return

    def record_order(self, *, symbol: str, quantity: float) -> None:
        """Record that an order was placed (for daily tracking)."""
        self._daily_notional += quantity
        self._daily_orders += 1
        self._position_notionals[symbol] = self._position_notionals.get(symbol, 0) + quantity

    def reset_daily(self) -> None:
        """Reset daily counters (called at midnight UTC)."""
        self._daily_notional = 0.0
        self._daily_orders = 0

    def _check_full_auto(self, *, symbol: str, quantity: float, leverage: int) -> None:
        """In full_auto mode, risk engine is the primary guard.

        Additional safety rails beyond risk engine:
        - Conservative leverage cap (default 20x)
        - Risk engine pre-check is mandatory (enforced at API layer)
        """
        if leverage > self._full_auto_max_leverage:
            raise ModeGuardError(
                self._mode,
                f"Leverage {leverage}x exceeds full_auto maximum {self._full_auto_max_leverage}x",
            )

    def _check_confirm_mode(self, *, preview_id: str | None, preview_manager: object | None) -> None:
        """In confirm mode, a valid confirmed preview is required."""
        if preview_id is None:
            raise ModeGuardError(
                self._mode,
                "Confirm mode requires a preview_id. Use POST /trading/orders/preview first.",
            )
        # Preview validation is done at the API layer where the manager is accessible

    def _check_limited_auto(
        self,
        *,
        symbol: str,
        quantity: float,
        leverage: int,
        has_stop_loss: bool,
    ) -> None:
        """In limited_auto mode, hard limits are enforced."""
        # Daily notional limit
        if self._daily_notional + quantity > self._max_daily_notional:
            raise ModeGuardError(
                self._mode,
                f"Daily notional limit: ${self._daily_notional:.0f} + ${quantity:.0f} "
                f"exceeds ${self._max_daily_notional:.0f}",
            )

        # Daily order count limit
        if self._daily_orders >= self._max_daily_orders:
            raise ModeGuardError(
                self._mode,
                f"Daily order limit: {self._daily_orders} >= {self._max_daily_orders}",
            )

        # Single order notional limit
        if quantity > self._max_single_order_notional:
            raise ModeGuardError(
                self._mode,
                f"Single order notional ${quantity:.0f} exceeds ${self._max_single_order_notional:.0f}",
            )

        # Position notional limit
        current_pos = self._position_notionals.get(symbol, 0)
        if current_pos + quantity > self._max_position_notional:
            raise ModeGuardError(
                self._mode,
                f"Position limit for {symbol}: ${current_pos:.0f} + ${quantity:.0f} "
                f"exceeds ${self._max_position_notional:.0f}",
            )

        # Leverage limit
        if leverage > self._max_leverage:
            raise ModeGuardError(
                self._mode,
                f"Leverage {leverage}x exceeds max {self._max_leverage}x",
            )

        # Symbol whitelist
        if self._allowed_symbols is not None and symbol not in self._allowed_symbols:
            raise ModeGuardError(
                self._mode,
                f"Symbol {symbol} not in allowed list",
            )

        # Stop loss requirement
        if self._require_stop_loss and not has_stop_loss:
            raise ModeGuardError(
                self._mode,
                "Stop loss is required in limited_auto mode",
            )

    def status(self) -> dict[str, Any]:
        """Return current guard status."""
        return {
            "mode": self._mode.value,
            "daily_notional": self._daily_notional,
            "daily_orders": self._daily_orders,
            "max_daily_notional": self._max_daily_notional,
            "max_daily_orders": self._max_daily_orders,
            "full_auto_max_leverage": self._full_auto_max_leverage,
        }


# ---------------------------------------------------------------------------
# Strategy trust level ↔ execution mode compatibility
# ---------------------------------------------------------------------------

# Minimum execution mode required for each trust level
_TRUST_MODE_MAP: dict[str, ExecutionMode] = {
    "observe": ExecutionMode.PAPER,
    "shadow": ExecutionMode.SHADOW,
    "confirm": ExecutionMode.CONFIRM,
    "limited": ExecutionMode.LIMITED_AUTO,
    "trusted": ExecutionMode.FULL_AUTO,
}

# Ordered levels for comparison
_MODE_ORDER = [
    ExecutionMode.PAPER,
    ExecutionMode.SHADOW,
    ExecutionMode.CONFIRM,
    ExecutionMode.LIMITED_AUTO,
    ExecutionMode.FULL_AUTO,
]


def check_trust_compatibility(trust_level: str, execution_mode: ExecutionMode) -> None:
    """Check if a strategy's trust level is compatible with the current execution mode.

    Raises ModeGuardError if the strategy requires a higher mode than currently active.
    """
    required_mode = _TRUST_MODE_MAP.get(trust_level)
    if required_mode is None:
        return  # Unknown trust level — allow by default

    required_idx = _MODE_ORDER.index(required_mode)
    current_idx = _MODE_ORDER.index(execution_mode)

    if current_idx < required_idx:
        raise ModeGuardError(
            execution_mode,
            f"Strategy trust level '{trust_level}' requires {required_mode.value} mode, "
            f"but current mode is {execution_mode.value}",
        )
