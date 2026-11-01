"""Overtrading detection: rate limits and direction-flip monitoring."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from selene_space_types import OrderSide, RiskLevel

_HOUR = 3600.0
_DAY = 86400.0


@dataclass
class OvertradingConfig:
    max_orders_per_hour: int = 20
    max_orders_per_day: int = 100
    max_reversals_per_hour: int = 6
    reversal_window_seconds: int = 3600


@dataclass(frozen=True)
class OvertradingAlert:
    rule: str
    severity: RiskLevel
    message: str


@dataclass
class _OrderRecord:
    symbol: str
    side: OrderSide
    timestamp: float


def _count_flips(sides: list[OrderSide]) -> int:
    flips = 0
    for previous, current in zip(sides, sides[1:]):
        if previous is not current:
            flips += 1
    return flips


@dataclass
class OvertradingDetector:
    config: OvertradingConfig = field(default_factory=OvertradingConfig)
    _orders: list[_OrderRecord] = field(default_factory=list, repr=False)

    def record_order(
        self,
        symbol: str,
        side: OrderSide,
        timestamp: float | None = None,
    ) -> None:
        """Log one order for later rate/reversal checks.

        Args:
            symbol: Trading pair the order targets.
            side: Buy or sell; reversal detection compares these over time.
            timestamp: POSIX seconds; defaults to now. Overridable so tests
                and replays can drive the clock deterministically.
        """
        self._orders.append(
            _OrderRecord(
                symbol=symbol,
                side=side,
                timestamp=time.time() if timestamp is None else timestamp,
            )
        )

    def check_hourly_rate(self, now: float | None = None) -> OvertradingAlert | None:
        moment = time.time() if now is None else now
        recent = self._within(moment - _HOUR, moment)
        if len(recent) < self.config.max_orders_per_hour:
            return None
        return OvertradingAlert(
            rule="hourly_rate",
            severity=RiskLevel.RESTRICTED,
            message=(
                f"过去一小时订单数 {len(recent)} 达到上限 "
                f"{self.config.max_orders_per_hour}，暂停下单。"
            ),
        )

    def check_daily_rate(self, now: float | None = None) -> OvertradingAlert | None:
        moment = time.time() if now is None else now
        recent = self._within(moment - _DAY, moment)
        if len(recent) < self.config.max_orders_per_day:
            return None
        return OvertradingAlert(
            rule="daily_rate",
            severity=RiskLevel.DANGEROUS,
            message=(
                f"过去一天订单数 {len(recent)} 达到上限 "
                f"{self.config.max_orders_per_day}，请检查交易计划。"
            ),
        )

    def check_reversal(
        self,
        symbol: str,
        side: OrderSide,
        now: float | None = None,
    ) -> OvertradingAlert | None:
        moment = time.time() if now is None else now
        window_start = moment - self.config.reversal_window_seconds
        history_sides = [
            order.side
            for order in sorted(
                (o for o in self._orders if o.symbol == symbol),
                key=lambda o: o.timestamp,
            )
            if window_start <= order.timestamp <= moment
        ]
        hypothetical = [*history_sides, side]
        flips = _count_flips(hypothetical)
        if flips < self.config.max_reversals_per_hour:
            return None
        return OvertradingAlert(
            rule="frequent_reversal",
            severity=RiskLevel.RESTRICTED,
            message=(
                f"{symbol} 在反转窗口内方向翻转次数将达 {flips}"
                f"（上限 {self.config.max_reversals_per_hour}），疑似反复开平仓。"
            ),
        )

    def evaluate(
        self,
        symbol: str,
        side: OrderSide,
        now: float | None = None,
    ) -> list[OvertradingAlert]:
        """Run all rate-limit rules for a would-be order.

        Evaluates hourly rate, daily rate, and same-symbol direction
        reversal against the recorded order history. Does NOT record the
        order — call :meth:`record_order` separately once the trade goes
        through.

        Args:
            symbol: Trading pair the hypothetical order targets.
            side: Direction of the hypothetical order.
            now: POSIX seconds override for deterministic checks.

        Returns:
            All triggered alerts (empty list means every rule passed).
        """
        alerts: list[OvertradingAlert] = []
        hourly = self.check_hourly_rate(now=now)
        if hourly is not None:
            alerts.append(hourly)
        daily = self.check_daily_rate(now=now)
        if daily is not None:
            alerts.append(daily)
        reversal = self.check_reversal(symbol, side, now=now)
        if reversal is not None:
            alerts.append(reversal)
        return alerts

    def reset(self) -> None:
        self._orders.clear()

    def _within(self, start: float, end: float) -> list[_OrderRecord]:
        return [order for order in self._orders if start <= order.timestamp <= end]
