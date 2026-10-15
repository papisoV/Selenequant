"""PA price alert service (S7).

In-memory one-shot price alerts for PA decision cards: a rule registers
entry/stop_loss/take_profit levels and fires once when the live price
touches any of them, then is removed (one-shot — no repeat轰炸).

Price source is injected (``get_price`` callable) so the polling loop in
main.py can feed it from the market-data WS cache and tests can inject
mocks. Notifications go through ``selene_space_pulse.send_trade_alert``
(fire-and-forget; gracefully skipped when the pulse package is absent).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

GetPriceFn = Callable[[str, str, str], float | None]
NotifyFn = Callable[[str], Any]

_LEVEL_NAMES = ("entry", "stop_loss", "take_profit")


class PriceAlertService:
    """In-memory registry of one-shot price alerts (lost on restart)."""

    def __init__(self) -> None:
        self._alerts: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    def create_alert(
        self,
        *,
        symbol: str,
        interval: str,
        levels: dict[str, float | None],
        direction: str | None = None,
        input_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        alert_id = self._next_id
        self._next_id += 1
        rule: dict[str, Any] = {
            "id": alert_id,
            "symbol": symbol,
            "interval": interval,
            "direction": direction,
            "input_id": input_id,
            "session_id": session_id,
            "levels": dict(levels),
            "baseline_price": None,
            "enabled": True,
            "created_at": time.time(),
        }
        self._alerts[alert_id] = rule
        return dict(rule)

    def list_alerts(self) -> list[dict[str, Any]]:
        return [dict(rule) for rule in self._alerts.values()]

    def delete_alert(self, alert_id: int) -> bool:
        return self._alerts.pop(alert_id, None) is not None

    async def check_alerts(
        self,
        get_price: GetPriceFn,
        *,
        notify_fn: NotifyFn | None = None,
    ) -> list[dict[str, Any]]:
        """Run one check round over all enabled rules.

        Triggered rules are removed (one-shot) and a trade alert is dispatched
        off-thread (the default notifier does blocking network I/O). A None
        price (WS cache miss / 断线) skips the rule honestly — no fire, no
        delete.
        """
        if notify_fn is None:
            notify_fn = _default_notify
        triggered: list[dict[str, Any]] = []
        for rule in list(self._alerts.values()):
            price = get_price(rule["symbol"], rule["interval"], "futures")
            if price is None:
                continue
            level, level_name = _first_touch(rule, price)
            if level is None:
                continue
            hit: dict[str, Any] = {
                "rule": dict(rule),
                "level": level_name,
                "level_price": level,
                "price": price,
                "triggered_at": time.time(),
            }
            triggered.append(hit)
            self._alerts.pop(rule["id"], None)
            try:
                # Offload to a thread: _default_notify runs synchronous network
                # I/O (Telegram/webhook via pulse), which would otherwise block
                # the alert loop's event loop for the request duration.
                await asyncio.to_thread(
                    notify_fn,
                    f"{rule['symbol']} {rule['interval']} 触达 {level_name}"
                    f" @ {price}(方向 {rule['direction']})",
                )
            except Exception:
                logger.warning("PA trade alert notify failed", exc_info=True)
        return triggered


def _first_touch(rule: dict[str, Any], price: float) -> tuple[float | None, str | None]:
    """Return (level_price, level_name) for the first level touched.

    long: entry/stop_loss low-touch (price <= level), take_profit high-touch.
    short: mirrored. direction None: first round records the live price as
    baseline and never fires; later rounds fire on crossing any level
    (baseline and price on opposite sides) — honest two-sided semantics
    when the direction is unknown.
    """
    direction = rule.get("direction")
    levels = rule.get("levels") or {}
    if direction == "long":
        for name in _LEVEL_NAMES:
            value = levels.get(name)
            if value is None:
                continue
            if (price >= value) if name == "take_profit" else (price <= value):
                return (value, name)
    elif direction == "short":
        for name in _LEVEL_NAMES:
            value = levels.get(name)
            if value is None:
                continue
            if (price <= value) if name == "take_profit" else (price >= value):
                return (value, name)
    else:
        baseline = rule.get("baseline_price")
        if baseline is None:
            rule["baseline_price"] = price
            return (None, None)
        for name in _LEVEL_NAMES:
            value = levels.get(name)
            if value is None:
                continue
            if (baseline - value) * (price - value) <= 0:
                return (value, name)
    return (None, None)


def _default_notify(text: str) -> None:
    # Routes through the dispatcher so price alerts honour the settings page's
    # event->channel subscriptions, falling back to pulse env targets.
    try:
        from app.core.notification_dispatch import dispatch

        dispatch("price_alert", text)
    except Exception:
        logger.warning("pulse notify unavailable — trade alert skipped: %s", text)


_price_alert_service: PriceAlertService | None = None


def get_price_alert_service() -> PriceAlertService:
    """Module-level singleton shared by the router and the polling loop."""
    global _price_alert_service
    if _price_alert_service is None:
        _price_alert_service = PriceAlertService()
    return _price_alert_service
