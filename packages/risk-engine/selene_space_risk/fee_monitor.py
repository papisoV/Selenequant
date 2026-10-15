"""FeeRateMonitor — watches effective fee rate for VIP-tier anomalies (#12)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from selene_space_risk.fee_ledger import FeeSnapshot

logger = logging.getLogger(__name__)


class FeeRateMonitor:
    """Monitors the effective taker fee rate paid on recent fills.

    Thin + duck-typed: ``fetch_effective_rate`` is a sync callable returning a
    float rate (or None = skip this poll). On a sustained anomaly — effective
    rate above ``expected_taker_rate * (1 + anomaly_threshold_pct)`` for
    ``confirmations`` consecutive polls — the monitor records a snapshot to the
    ledger, sends a trade alert, and (opt-in) activates the kill switch.

    Idempotent: fires at most once per anomaly episode until the rate recovers
    below threshold (or ``reset()`` is called), so a persistently high rate
    doesn't re-alert every poll.
    """

    def __init__(
        self,
        fetch_effective_rate: Callable[[], Any],
        *,
        expected_taker_rate: float = 0.0005,
        anomaly_threshold_pct: float = 0.5,
        enable_kill_on_anomaly: bool = False,
        confirmations: int = 3,
        kill_switch: object | None = None,
        account_id: str = "live",
        ledger: object | None = None,
        poll_interval_s: float = 3600.0,
        reason_prefix: str = "fee-anomaly",
    ) -> None:
        self._fetch = fetch_effective_rate
        self._expected = expected_taker_rate
        self._threshold_pct = anomaly_threshold_pct
        self._enable_kill = enable_kill_on_anomaly
        self._confirmations = max(1, confirmations)
        self._kill_switch = kill_switch
        self._account_id = account_id
        self._ledger = ledger
        self._poll_interval_s = poll_interval_s
        self._reason_prefix = reason_prefix

        self._last_rate: float | None = None
        self._consecutive = 0
        self._triggered = False
        self._task: asyncio.Task[None] | None = None

    @property
    def last_rate(self) -> float | None:
        return self._last_rate

    @property
    def consecutive_worsening(self) -> int:
        return self._consecutive

    @property
    def triggered(self) -> bool:
        return self._triggered

    @property
    def threshold(self) -> float:
        """Effective-rate threshold that counts as an anomaly."""
        return self._expected * (1.0 + self._threshold_pct)

    def check(self) -> float | None:
        """Evaluate one effective-rate sample.

        Returns the sampled rate (None when the fetch returned None — a no-op
        poll). The fetch is isolated: an exception is logged and treated as a
        no-op, so the run loop survives upstream failures.
        """
        try:
            rate = self._fetch()
        except Exception:
            logger.warning("fee rate fetch failed", exc_info=True)
            return None
        if rate is None:
            return None
        self._last_rate = float(rate)

        if self._last_rate <= self.threshold:
            self._consecutive = 0
            return self._last_rate

        self._consecutive += 1
        if self._consecutive >= self._confirmations and not self._triggered:
            self._triggered = True
            self._fire(self._last_rate)
        return self._last_rate

    def _fire(self, rate: float) -> None:
        reason = (
            f"{self._reason_prefix}: effective taker {rate:.6f} > "
            f"expected {self._expected:.6f} +{self._threshold_pct * 100:.0f}%"
        )
        logger.critical("FEE ANOMALY: %s", reason)

        if self._ledger is not None:
            try:
                self._ledger.record(self._snapshot(rate))
            except Exception:
                logger.warning("fee ledger record failed", exc_info=True)

        try:
            from selene_space_pulse.notify import send_trade_alert
            send_trade_alert(f"FEE ANOMALY: {reason}")
        except Exception:
            pass

        if self._enable_kill and self._kill_switch is not None:
            try:
                self._kill_switch.auto_activate(reason, triggered_by_rule="fee_anomaly")
            except Exception:
                logger.warning("fee-anomaly kill switch activation failed", exc_info=True)

    def _snapshot(self, rate: float) -> FeeSnapshot:
        now_ms = int(time.time() * 1000)
        return FeeSnapshot(
            ts=now_ms,
            account_id=self._account_id,
            exchange="aggregated",
            taker_rate=rate,
            maker_rate=0.0,
            effective_rate=rate,
            implied_tier="unknown",
            source="effective_rate_monitor",
            created_at=now_ms,
        )

    async def run(self) -> None:
        while True:
            try:
                self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("fee monitor cycle failed", exc_info=True)
            await asyncio.sleep(self._poll_interval_s)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def reset(self) -> None:
        """Reset the anomaly episode (test / recovery hooks)."""
        self._triggered = False
        self._consecutive = 0
