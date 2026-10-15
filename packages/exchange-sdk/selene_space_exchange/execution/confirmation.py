"""Order confirmation flow for live trading.

In confirm mode, every order must be previewed before execution.
The preview shows estimated slippage, fees, margin impact, and risk
alerts. Previews expire after 30 seconds and can only be used once.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PREVIEW_TTL_S = 30


@dataclass
class OrderPreview:
    """A preview of an order before execution."""

    preview_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None
    stop_price: float | None
    leverage: int
    margin_mode: str
    pos_side: str
    account_id: str

    estimated_fill_price: float = 0.0
    estimated_slippage_bps: float = 0.0
    estimated_fee: float = 0.0
    required_margin: float = 0.0
    available_balance: float = 0.0
    risk_alerts: list[dict[str, Any]] = field(default_factory=list)

    created_at: float = 0.0
    expires_at: float = 0.0
    confirmed: bool = False


class OrderConfirmationManager:
    """Manages order preview/confirm lifecycle.

    In confirm mode, orders go through:
      1. POST /trading/orders/preview → returns OrderPreview with estimated costs
      2. User reviews and confirms
      3. POST /trading/orders/confirm → executes the order

    Previews expire after 30 seconds. Each preview can only be confirmed once.
    """

    def __init__(self, preview_ttl_s: float = PREVIEW_TTL_S) -> None:
        self._previews: dict[str, OrderPreview] = {}
        self._lock = asyncio.Lock()
        self._ttl = preview_ttl_s

    async def create_preview(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        leverage: int = 1,
        margin_mode: str = "cross",
        pos_side: str = "net",
        account_id: str = "",
        estimated_fill_price: float = 0.0,
        estimated_slippage_bps: float = 0.0,
        estimated_fee: float = 0.0,
        required_margin: float = 0.0,
        available_balance: float = 0.0,
        risk_alerts: list[dict[str, Any]] | None = None,
    ) -> OrderPreview:
        """Create a new order preview. Returns the preview for client review."""
        now = time.time()
        preview = OrderPreview(
            preview_id=f"pv-{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            leverage=leverage,
            margin_mode=margin_mode,
            pos_side=pos_side,
            account_id=account_id,
            estimated_fill_price=estimated_fill_price,
            estimated_slippage_bps=estimated_slippage_bps,
            estimated_fee=estimated_fee,
            required_margin=required_margin,
            available_balance=available_balance,
            risk_alerts=risk_alerts or [],
            created_at=now,
            expires_at=now + self._ttl,
        )

        async with self._lock:
            self._previews[preview.preview_id] = preview

        logger.info(
            "Preview created: %s %s %s qty=%.2f (expires in %ds)",
            preview.preview_id, side, symbol, quantity, self._ttl,
        )
        return preview

    async def confirm_preview(self, preview_id: str) -> OrderPreview | None:
        """Confirm a preview for execution.

        Returns the OrderPreview if valid, None if expired or already used.
        """
        async with self._lock:
            preview = self._previews.get(preview_id)
            if preview is None:
                logger.warning("Preview not found: %s", preview_id)
                return None

            if preview.confirmed:
                logger.warning("Preview already used: %s", preview_id)
                return None

            if time.time() > preview.expires_at:
                logger.warning("Preview expired: %s", preview_id)
                return None

            preview.confirmed = True

        logger.info("Preview confirmed: %s %s %s", preview_id, preview.side, preview.symbol)
        return preview

    async def expire_old_previews(self) -> int:
        """Remove expired previews. Returns count of expired."""
        now = time.time()
        expired_ids: list[str] = []

        async with self._lock:
            for pid, preview in self._previews.items():
                if now > preview.expires_at and not preview.confirmed:
                    expired_ids.append(pid)
            for pid in expired_ids:
                del self._previews[pid]

        if expired_ids:
            logger.debug("Expired %d old previews", len(expired_ids))
        return len(expired_ids)

    async def get_preview(self, preview_id: str) -> OrderPreview | None:
        """Get a preview by ID (for inspection, not confirmation)."""
        return self._previews.get(preview_id)
