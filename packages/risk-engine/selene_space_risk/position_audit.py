"""Live position audit — held positions vs strategy target notionals.

Cleanroom implementation: design intent derived from prior art; independent
expression. Design intent absorbed from an external reference's
`monitor_live_risk.py`: compare *held* positions against strategy-implied
target notionals and surface three mismatches — exposure above target, "should
be closed" (target 0) still held, and banned symbols still held. This is
**post-trade advisory** only: it never hard-blocks orders (contrast
`rules.py::SymbolBlacklistRule`, which is a pre-trade CRITICAL gate) and never
feeds the KillSwitch.

Selene's target source is its own: a `PositionTargetProvider` Protocol whose
default implementation is a static explicit target map (plus an independent
banned set). The over-band ratio and the rule thresholds are Selene-derived,
not upstream value copies.

Torch-free by construction: imports only `selene_space_types.risk` and stdlib.
"""

from __future__ import annotations

import math
import time
from typing import Any, Iterable, Mapping, Protocol

from selene_space_types.risk import RiskAlert, RiskLevel

#: Over-band threshold on notional/target. 1.0 = track target exactly; the
#: 0.35 buffer is Selene's own budget for execution slippage, fees and
#: rebalance lag between target computation and live fill — not an upstream
#: copied constant.
AUDIT_OVERBAND_RATIO: float = 1.35


class PositionTargetProvider(Protocol):
    """Strategy-imposed position targets consumed by the live position audit."""

    def get_targets(self) -> dict[str, float]:
        """Desired notional per symbol; an explicit ``0`` means "close it"."""

    def get_banned(self) -> frozenset[str]:
        """Symbols that must not be held at all (advisory)."""


class StaticPositionTargetProvider:
    """A frozen explicit target map plus an independent banned set.

    ``get_targets`` returns the explicit targets only — banned symbols are
    **not** folded in as zero targets, so the audit can tell "explicitly
    flagged to close" apart from "banned" by a membership check. Both getters
    return fresh copies; the caller's mapping/set is never mutated.
    """

    def __init__(
        self,
        targets: Mapping[str, float],
        banned: Iterable[str] = (),
    ) -> None:
        self._targets: dict[str, float] = dict(targets)
        self._banned: frozenset[str] = frozenset(banned)

    def get_targets(self) -> dict[str, float]:
        return dict(self._targets)

    def get_banned(self) -> frozenset[str]:
        return frozenset(self._banned)


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


def position_notional(position: Any) -> float:
    """Notional of a held position in USDT, mirroring the risk snapshot's
    ``abs(qty * avg_entry_price)`` convention (``qty == 0`` or missing entry →
    0, matching a fully closed / flat holding)."""
    qty = float(getattr(position, "quantity", 0.0) or 0.0)
    entry = float(getattr(position, "avg_entry_price", 0.0) or 0.0)
    return abs(qty * entry) if entry else 0.0


def audit_positions_against_targets(
    positions: Iterable[Any],
    targets: Mapping[str, float],
    *,
    banned: frozenset[str] = frozenset(),
    overband_ratio: float = AUDIT_OVERBAND_RATIO,
) -> list[RiskAlert]:
    """Audit held positions against strategy targets.

    Per held position (mutually exclusive, descending priority — at most one
    alert per symbol per cycle):

    1. ``symbol in banned`` and held        -> DANGEROUS ``banned_position``
    2. explicit ``target == 0`` but still held -> WARNING ``position_not_closed``
    3. ``notional / target > overband_ratio``  -> DANGEROUS ``position_over_target``

    Symbols with no target entry produce no alert; a target/provided value of
    NaN is naturally inert (every comparison is False). Flat holdings
    (notional == 0) are skipped.
    """
    alerts: list[RiskAlert] = []
    for position in positions:
        symbol = str(getattr(position, "symbol", "") or "")
        if not symbol:
            continue
        notional = position_notional(position)
        if notional <= 0.0:
            continue

        if symbol in banned:
            alerts.append(
                _alert(
                    "banned_position",
                    RiskLevel.DANGEROUS,
                    f"banned symbol {symbol} is still held",
                    symbol=symbol,
                    notional=notional,
                )
            )
            continue

        if symbol in targets and targets[symbol] == 0:
            alerts.append(
                _alert(
                    "position_not_closed",
                    RiskLevel.WARNING,
                    f"position {symbol} should be closed (target 0) but {notional:.2f} is held",
                    symbol=symbol,
                    notional=notional,
                    target=0.0,
                )
            )
            continue

        if symbol not in targets:
            continue
        target = float(targets[symbol])
        if math.isfinite(target) and notional / target > overband_ratio:
            ratio = notional / target
            alerts.append(
                _alert(
                    "position_over_target",
                    RiskLevel.DANGEROUS,
                    f"position {symbol} notional {notional:.2f} is {ratio:.2f}x over target {target:.2f}",
                    symbol=symbol,
                    notional=notional,
                    target=target,
                    ratio=ratio,
                )
            )
    return alerts
