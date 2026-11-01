"""PA price alert service (S7).

In-memory one-shot price alerts: a rule registers one or more price levels
and fires once when the live price touches any of them, then is removed
(one-shot — no repeat轰炸).

Two kinds of level are accepted and they are evaluated differently:

- **semantic** (``entry`` / ``stop_loss`` / ``take_profit``) — produced by PA
  decision cards, which always know a direction. Touch sense follows it:
  long takes stop_loss on the way down, take_profit on the way up.
- **generic** (``price``) — a bare line the user drew on the chart. It carries
  no direction, so it is only ever evaluated as a *crossing* (see the
  direction-less branch in ``_first_touch``). Kept in a separate tuple so the
  long/short branches cannot silently reinterpret it as a stop-loss-style
  low-touch.

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

# Semantic levels from PA decision cards. Touch sense depends on direction.
_LEVEL_NAMES = ("entry", "stop_loss", "take_profit")
# Levels that carry no trade intent — a line the user drew on the chart.
# Evaluated as a crossing in every rule, independent of direction.
_GENERIC_LEVEL_NAMES = ("price",)


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
            if level_name in _GENERIC_LEVEL_NAMES:
                # A drawn line has no trade direction; saying "(方向 None)"
                # would read as a broken field rather than an absent concept.
                _msg = (
                    f"{rule['symbol']} {rule['interval']} 触及画线"
                    f" {level} @ {price}"
                )
            else:
                _msg = (
                    f"{rule['symbol']} {rule['interval']} 触达 {level_name}"
                    f" @ {price}(方向 {rule['direction']})"
                )
            try:
                # Offload to a thread: _default_notify runs synchronous network
                # I/O (Telegram/webhook via pulse), which would otherwise block
                # the alert loop's event loop for the request duration.
                await asyncio.to_thread(notify_fn, _msg)
            except Exception:
                logger.warning("PA trade alert notify failed", exc_info=True)
        return triggered


def _ensure_baseline(rule: dict[str, Any], price: float) -> bool:
    """Record the first-seen price as the crossing anchor.

    Returns True when it was *just* recorded, in which case the caller must
    not fire this round — a crossing is only definable against a side the
    price started from, so the anchoring round can only observe, not judge.
    """
    if rule.get("baseline_price") is None:
        rule["baseline_price"] = price
        return True
    return False


def _first_touch(rule: dict[str, Any], price: float) -> tuple[float | None, str | None]:
    """Return (level_price, level_name) for the first level touched.

    Two independent kinds of level, checked in this order:

    - **drawn lines** (``price``): direction-independent crossings. Fires when
      price crosses the level from either side, so the rule is anchored to the
      side the price was on when the alert was created.
    - **semantic levels** (entry/stop_loss/take_profit): sense depends on
      direction — long takes stop_loss low-touch (price <= level) and
      take_profit high-touch, short is mirrored. direction None: first round
      records the live price as baseline and never fires; later rounds fire on
      crossing any level — honest two-sided semantics when the direction is
      unknown.
    """
    direction = rule.get("direction")
    levels = rule.get("levels") or {}

    if any(levels.get(n) is not None for n in _GENERIC_LEVEL_NAMES):
        if _ensure_baseline(rule, price):
            return (None, None)
        baseline = rule["baseline_price"]
        for name in _GENERIC_LEVEL_NAMES:
            value = levels.get(name)
            if value is None:
                continue
            if (baseline - value) * (price - value) <= 0:
                return (value, name)

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
        if _ensure_baseline(rule, price):
            return (None, None)
        baseline = rule["baseline_price"]
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
