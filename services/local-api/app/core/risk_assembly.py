"""risk_assembly — factories that assemble risk monitors in main.py lifespan.

Kept as a separate module so the assembly logic (pricing, symbol derivation,
venue handling) is unit-testable without booting the whole app. Each factory
returns a thin, duck-typed component wired to real services.

Note (v4.1 X1): factory defaults use literal values, never main.py constants —
Python evaluates default args at import time and this module must stay
import-cycle-free from ``app.main``.
"""

from __future__ import annotations

import logging
import math
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Sequence

logger = logging.getLogger(__name__)


#: saved ``risk`` settings → rule attribute, as
#: ``(rule_id, settings key, rule attribute, divisor)``.
#:
#: **The two sides use different units.** ``_RISK_STR_KEYS`` in
#: settings_service stores *whole percents as strings* (``"10"`` = ten
#: percent) while ``MaxPositionRule`` & co. hold *fractions* (``0.10``) — hence
#: the 100 divisor. ``cooldown_seconds`` is seconds on both sides.
_RISK_RULE_TARGETS: tuple[tuple[str, str, str, float], ...] = (
    ("max_position", "max_position_pct", "max_position_pct", 100.0),
    ("max_single_risk", "single_risk_pct", "max_risk_pct", 100.0),
    ("daily_loss_limit", "daily_loss_limit_pct", "max_daily_loss_pct", 100.0),
    ("cooldown", "cooldown_seconds", "cooldown_seconds", 1.0),
)


def apply_risk_settings(rules: Sequence[Any], risk_settings: dict[str, Any]) -> int:
    """Push saved ``risk`` settings onto a freshly built rule roster.

    Returns how many rules were overridden. Missing or blank settings leave
    that rule at its constructor default, so a partial settings file does not
    zero out limits nobody configured.

    Two refusals, both logged: a value that isn't a finite number, and a
    percent outside ``0..100`` (a 500% cap would quietly disable the rule by
    never firing). An attribute the rule doesn't have is refused too — that is
    exactly how this wiring went stale once: ``rules.py`` renamed
    ``_max_pct`` to ``max_position_pct`` in aa6c6697 and the assignments kept
    landing on attributes nothing reads.
    """
    by_id = {getattr(rule, "rule_id", None): rule for rule in rules}
    applied = 0
    for rule_id, key, attr, divisor in _RISK_RULE_TARGETS:
        rule = by_id.get(rule_id)
        if rule is None:
            logger.warning("risk settings: rule %r missing from the roster", rule_id)
            continue
        if not hasattr(rule, attr):
            logger.warning(
                "risk settings: rule %r has no attribute %r — wiring is stale, "
                "check the rule's constructor",
                rule_id,
                attr,
            )
            continue
        raw = risk_settings.get(key)
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "risk settings: %s=%r is not a number — keeping the default", key, raw
            )
            continue
        # nan/inf survive the range checks below (`nan < 0` is False), so the
        # finiteness test has to come first.
        if (
            not math.isfinite(value)
            or value < 0.0
            or (divisor != 1.0 and value > 100.0)
        ):
            logger.warning(
                "risk settings: %s=%r is out of range — keeping %s",
                key,
                raw,
                getattr(rule, attr),
            )
            continue
        setattr(rule, attr, value / divisor)
        applied += 1
    return applied


def _norm_symbol(symbol: str) -> str:
    """Normalize a symbol for cross-format matching.

    Slot symbols are ``BTCUSDT``; funding fetcher items are slash-form
    ``BTC/USDT`` (both Binance and OKX). Stripping separators makes them
    comparable.
    """
    return "".join(ch for ch in str(symbol).upper() if ch.isalnum())


def build_funding_reversal_monitor(
    *,
    strategy_runner: Any,
    funding_fetcher: Any,
    kill_switch: Any,
    flatten: Callable[[], Awaitable[Any]],
    exit_threshold: float = 0.0001,
    poll_interval_s: float = 60.0,
    strategy_id: str = "funding_arb",
) -> Any:
    """Assemble a FundingReversalMonitor watching the worst funding_arb pair.

    The ``fetch_rate`` closure walks ``strategy_runner.funding_arb_slots()``,
    matches each slot symbol against ``funding_fetcher.get_all()`` items
    (symbol-normalized), and returns the snapshot with the worst (lowest)
    funding rate across all pairs. Returns None when there are no arb slots,
    no fetcher, or no matching rates — the monitor's run loop treats None as
    "skip this poll", so a disabled/data-less config degrades to a no-op.

    ``flatten`` is awaited by the monitor on reversal — pass the shared
    FlattenHook's async ``fire`` method (fires every registered closure).
    """
    from app.core.funding_reversal_monitor import FundingReversalMonitor

    async def _fetch_rate() -> Any | None:
        if funding_fetcher is None:
            logger.debug("funding fetcher not wired — reversal monitor no-op")
            return None
        try:
            items = await funding_fetcher.get_all()
        except Exception:
            logger.warning("funding fetch failed in reversal monitor", exc_info=True)
            return None
        if not items:
            return None
        by_norm: dict[str, Any] = {}
        for item in items:
            by_norm[_norm_symbol(getattr(item, "symbol", ""))] = item

        try:
            slots = (
                strategy_runner.funding_arb_slots()
                if strategy_runner is not None
                else []
            )
        except Exception:
            logger.warning(
                "funding_arb_slots failed in reversal monitor", exc_info=True
            )
            return None

        worst: Any = None
        for slot in slots:
            item = by_norm.get(_norm_symbol(slot.get("symbol", "")))
            if item is None:
                continue
            rate = float(getattr(item, "funding_rate", 0.0))
            if worst is None or rate < worst.funding_rate:
                worst = SimpleNamespace(
                    funding_rate=rate,
                    symbol=getattr(item, "symbol", slot.get("symbol", "?")),
                )
        return worst

    return FundingReversalMonitor(
        _fetch_rate,
        exit_threshold=exit_threshold,
        strategy_id=strategy_id,
        kill_switch=kill_switch,
        flatten=flatten,
        poll_interval_s=poll_interval_s,
    )


def build_flatten_closure(
    *,
    strategy_runner: Any,
    dual_leg_executor: Any | None,
    market_svc: Any,
    perp_venue: str | None,
    funding_fetcher: Any | None = None,
) -> Callable[..., Awaitable[None]]:
    """Build an async closure that force-flats every funding_arb pair.

    The closure walks ``strategy_runner.funding_arb_slots()`` and calls
    ``DualLegExecutor.flatten`` per pair. Only pairs with a non-zero net
    position are flattened; pairs with any missing leg price are skipped
    (never a price=0 phantom order). Each pair is isolated — one failure
    doesn't abort the rest.

    No-op when the dual-leg executor or perp venue isn't wired (default
    config / paper / shadow / non-Binance venue): logs debug, returns.
    """

    async def _flatten() -> None:
        if strategy_runner is None or dual_leg_executor is None or perp_venue is None:
            logger.debug(
                "flatten disabled (strategy_runner=%s dual_leg_executor=%s perp_venue=%s)",
                strategy_runner is not None,
                dual_leg_executor is not None,
                perp_venue,
            )
            return

        for slot in strategy_runner.funding_arb_slots():
            try:
                symbol = slot["symbol"]
                net_position = float(slot["net_position"])
                if abs(net_position) <= 1e-12:
                    continue
                perp_symbol = symbol if perp_venue == "binance" else f"{symbol}-SWAP"

                spot_ticker = market_svc.get_ticker(
                    symbol, exchange=perp_venue, market_type="spot"
                )
                spot_price = spot_ticker.last_price if spot_ticker is not None else None
                perp_ticker = market_svc.get_ticker(
                    symbol, exchange=perp_venue, market_type="futures"
                )
                perp_price = perp_ticker.last_price if perp_ticker is not None else None

                if perp_price is None and funding_fetcher is not None:
                    for item in await funding_fetcher.get_all():
                        if getattr(item, "symbol", None) == symbol:
                            mark = getattr(item, "mark_price", 0.0)
                            perp_price = mark if mark else None
                            break

                if spot_price is None or perp_price is None:
                    logger.error(
                        "flatten skip %s: price missing (spot=%s perp=%s)",
                        symbol,
                        spot_price,
                        perp_price,
                    )
                    continue

                qty = abs(net_position)
                await dual_leg_executor.flatten(
                    account_id=slot["account_id"],
                    strategy_id=slot["strategy_id"],
                    spot_symbol=symbol,
                    perp_symbol=perp_symbol,
                    spot_qty=qty,
                    perp_qty=qty,
                    spot_price=float(spot_price),
                    perp_price=float(perp_price),
                )
            except Exception:
                logger.exception("flatten failed for %s", slot.get("symbol"))

    return _flatten


def build_fee_monitor(
    *,
    fills_ledger: Any,
    kill_switch: Any | None = None,
    account_id: str = "live",  # X1: literal default — never main.py constants
    expected_taker_rate: float = 0.0005,
    anomaly_threshold_pct: float = 0.5,
    enable_kill_on_anomaly: bool = False,
    confirmations: int = 3,
    poll_interval_s: float = 3600.0,
    min_fills: int = 5,
) -> Any:
    """Assemble a FeeRateMonitor watching the volume-weighted effective taker fee.

    The ``fetch_effective_rate`` closure aggregates the last 24h of fills from
    ``fills_ledger`` into Σfee / Σnotional, ignoring zero-fee fills (they are
    maker fills — not a taker-rate signal) and returning None when fewer than
    ``min_fills`` fee-bearing fills exist (no-op poll; a single stale fill
    would skew the rate). Returns None on any upstream error, so a data-less
    config degrades to a no-op.

    Default is alert-only: the kill switch is armed only when
    ``enable_kill_on_anomaly=True``. ``account_id`` defaults to the literal
    ``"live"`` so this module stays import-cycle-free from ``app.main`` (X1).
    """
    from selene_space_risk.fee_ledger import FeeLedger
    from selene_space_risk.fee_monitor import FeeRateMonitor

    def _fetch_effective_rate() -> float | None:
        if fills_ledger is None:
            logger.debug("fills ledger not wired — fee monitor no-op")
            return None
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 86_400_000  # last 24h
        try:
            fills = fills_ledger.get_all_fills(account_id, start_ms, end_ms)
        except Exception:
            logger.warning("fee aggregation: fills fetch failed", exc_info=True)
            return None
        fee_fills = [f for f in fills if float(getattr(f, "fee", 0.0) or 0.0) > 0]
        if len(fee_fills) < min_fills:
            return None
        total_fee = sum(float(getattr(f, "fee", 0.0)) for f in fee_fills)
        total_notional = sum(
            float(getattr(f, "quantity", 0.0)) * float(getattr(f, "price", 0.0))
            for f in fee_fills
        )
        if total_notional <= 0:
            return None
        return total_fee / total_notional

    ledger = FeeLedger() if fills_ledger is not None else None
    return FeeRateMonitor(
        _fetch_effective_rate,
        expected_taker_rate=expected_taker_rate,
        anomaly_threshold_pct=anomaly_threshold_pct,
        enable_kill_on_anomaly=enable_kill_on_anomaly,
        confirmations=confirmations,
        kill_switch=kill_switch,
        account_id=account_id,
        ledger=ledger,
        poll_interval_s=poll_interval_s,
    )
