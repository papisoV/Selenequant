# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); portfolio stop-loss proximity
# re-expressed as banded advisory RiskAlerts with an optional dynamic-stop
# (ATR) enhanced path, replacing the upstream account/snapshot dict shape.

"""Portfolio stop-loss proximity audit — how close is price to its stop.

Cleanroom implementation: design intent derived from prior art; independent
expression. Upstream (`portfolio_risk_service._build_stop_loss`) scans account
snapshots and flags every position whose loss-from-cost percent enters the
"near stop" band (`stop_loss_alert_pct * stop_loss_near_ratio`), reporting the
worst-first list. That algorithm is ported faithfully as the **cost path**:
``loss_pct = max(0, (avg_cost - last_price) / avg_cost * 100)``.

Selene improvement — the **stop path**: upstream can only measure loss from
cost, but Selene's ``ATRDynamicStopRule`` (``advanced_rules.py``) already
computes a real dynamic stop price. When a stop price is available for a
symbol, this module bands on *distance from current price to that stop*
(``stop_distance_pct = (last_price - stop_price) / stop_price * 100`` — how
far the price is above its stop, negative when through) instead of raw loss
from cost, because a volatility-scaled stop can sit far from cost and make
loss-from-cost a bad proximity proxy.

Severity vocabulary is Selene's own, chosen deliberately within
``selene_space_types.risk.RiskLevel``:

- inside the near band but not through the stop -> ``WARNING``
- at/through the stop                            -> ``DANGEROUS``

CRITICAL is never emitted: this module is **post-trade advisory only** — it
never hard-blocks orders, never feeds ``critical_reasons`` and never triggers
the KillSwitch (contrast ``SymbolBlacklistRule``, a pre-trade CRITICAL gate).

The stop source is Selene's own: a ``StopPriceProvider`` Protocol whose trivial
default is a static explicit stop map. Thresholds are Selene-derived (matching
the upstream config semantics, not copied values). Torch-free by construction:
imports only ``selene_space_types.risk`` and stdlib.
"""

from __future__ import annotations

import math
import time
from typing import Any, Iterable, Mapping, Protocol

from selene_space_types.risk import RiskAlert, RiskLevel

#: Loss-from-cost percent at which a position is considered "at its stop".
#: Mirrors the upstream config key ``stop_loss_alert_pct`` semantics; the
#: default here is Selene's own value, not an upstream copy.
STOP_LOSS_ALERT_PCT: float = 10.0

#: Fraction of ``STOP_LOSS_ALERT_PCT`` that defines the "near stop" warning
#: band (upstream key ``stop_loss_near_ratio``); Selene's own value.
STOP_LOSS_NEAR_RATIO: float = 0.8

#: Resolved near-band threshold, ``STOP_LOSS_ALERT_PCT * STOP_LOSS_NEAR_RATIO``.
NEAR_THRESHOLD_PCT: float = STOP_LOSS_ALERT_PCT * STOP_LOSS_NEAR_RATIO

#: Enhanced (stop-path) proximity band: a position is "near its stop" when the
#: price is within this percent *above* the stop. Reuses the cost-path near
#: threshold (``stop_loss_pct * near_ratio``) as the width so both paths share
#: one near-band vocabulary.
STOP_PROXIMITY_BAND_PCT: float = NEAR_THRESHOLD_PCT


class StopPriceProvider(Protocol):
    """Dynamic stop prices consumed by the stop-proximity audit."""

    def get_stop_prices(self) -> dict[str, float]:
        """Current stop price per symbol; missing symbol -> cost path."""


class StaticStopPriceProvider:
    """A frozen explicit stop-price map.

    ``get_stop_prices`` returns a fresh copy; the caller's mapping is never
    mutated and mutations of the returned copy never leak back in.
    """

    def __init__(self, stops: Mapping[str, float]) -> None:
        self._stops: dict[str, float] = dict(stops)

    def get_stop_prices(self) -> dict[str, float]:
        return dict(self._stops)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _alert(rule_id: str, level: RiskLevel, message: str, **context: Any) -> RiskAlert:
    return RiskAlert(
        rule_id=rule_id,
        level=level,
        message=message,
        context=context,
        timestamp=_now_ms(),
    )


def _position_float(position: Any, primary: str, fallback: str) -> float:
    value = getattr(position, primary, None)
    if value is None:
        value = getattr(position, fallback, None)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


_SEVERITY = {RiskLevel.WARNING: 0, RiskLevel.DANGEROUS: 1}


def audit_stop_proximity(
    positions: Iterable[Any],
    stop_provider: StopPriceProvider | None = None,
    *,
    stop_loss_pct: float = STOP_LOSS_ALERT_PCT,
    near_ratio: float = STOP_LOSS_NEAR_RATIO,
) -> list[RiskAlert]:
    """Aggregate whole-portfolio stop-loss proximity into banded alerts.

    Per position (one alert per symbol, highest severity wins on duplicates):

    - **stop path** (a finite positive stop price is provided for the symbol):
      ``stop_distance_pct = (last_price - stop_price) / stop_price * 100``.
      At/through the stop (``<= 0``) -> DANGEROUS ``stop_loss_triggered``;
      within ``stop_loss_pct * near_ratio`` percent above the stop -> WARNING
      ``stop_loss_near``.
    - **cost path** (faithful upstream port, no stop price available):
      ``loss_pct = max(0, (avg_cost - last_price) / avg_cost * 100)``.
      ``loss_pct >= stop_loss_pct`` -> DANGEROUS ``stop_loss_triggered``;
      ``loss_pct >= stop_loss_pct * near_ratio`` -> WARNING ``stop_loss_near``.

    Alerts are returned worst severity first, then by descending proximity
    metric (upstream's worst-first ordering, generalized across both paths).
    Flat / zero-cost positions are skipped; NaN inputs are naturally inert
    (every comparison against NaN is False).
    """
    near_threshold = stop_loss_pct * near_ratio
    stop_prices = stop_provider.get_stop_prices() if stop_provider is not None else {}

    best: dict[str, RiskAlert] = {}
    for position in positions:
        symbol = str(getattr(position, "symbol", "") or "")
        if not symbol:
            continue
        avg_cost = _position_float(position, "avg_cost", "avg_entry_price")
        last_price = _position_float(position, "last_price", "current_price")
        if avg_cost <= 0.0:
            continue

        stop_price = stop_prices.get(symbol)
        stop_price = float(stop_price) if stop_price is not None else math.nan
        if math.isfinite(stop_price) and stop_price > 0.0:
            stop_distance_pct = (last_price - stop_price) / stop_price * 100.0
            if stop_distance_pct <= 0.0:
                alert = _alert(
                    "stop_loss_proximity",
                    RiskLevel.DANGEROUS,
                    f"position {symbol} is at/through its stop {stop_price:.4f} "
                    f"(last {last_price:.4f}, {stop_distance_pct:+.2f}%)",
                    symbol=symbol,
                    avg_cost=round(avg_cost, 8),
                    last_price=round(last_price, 8),
                    stop_price=round(stop_price, 8),
                    stop_distance_pct=round(stop_distance_pct, 4),
                    is_triggered=True,
                )
            elif stop_distance_pct <= STOP_PROXIMITY_BAND_PCT:
                alert = _alert(
                    "stop_loss_proximity",
                    RiskLevel.WARNING,
                    f"position {symbol} is {stop_distance_pct:.2f}% above its "
                    f"stop {stop_price:.4f} (last {last_price:.4f})",
                    symbol=symbol,
                    avg_cost=round(avg_cost, 8),
                    last_price=round(last_price, 8),
                    stop_price=round(stop_price, 8),
                    stop_distance_pct=round(stop_distance_pct, 4),
                    is_triggered=False,
                )
            else:
                continue
            metric = stop_distance_pct
        else:
            # Cost path — faithful upstream port.
            loss_pct = max(0.0, (avg_cost - last_price) / avg_cost * 100.0)
            if loss_pct < near_threshold:
                continue
            triggered = loss_pct >= stop_loss_pct
            alert = _alert(
                "stop_loss_proximity",
                RiskLevel.DANGEROUS if triggered else RiskLevel.WARNING,
                f"position {symbol} loss {loss_pct:.2f}% "
                f"({'at/through' if triggered else 'near'} stop threshold "
                f"{stop_loss_pct:.1f}%)",
                symbol=symbol,
                avg_cost=round(avg_cost, 8),
                last_price=round(last_price, 8),
                loss_pct=round(loss_pct, 4),
                near_threshold_pct=round(near_threshold, 4),
                is_triggered=triggered,
            )
            metric = loss_pct

        prior = best.get(symbol)
        if prior is None or _SEVERITY[alert.level] >= _SEVERITY[prior.level]:
            best[symbol] = alert
            alert.context["metric"] = round(metric, 4)

    def _sort_key(alert: RiskAlert) -> tuple[int, float]:
        return (-_SEVERITY[alert.level], -float(alert.context.get("metric", 0.0)))

    return sorted(best.values(), key=_sort_key)
