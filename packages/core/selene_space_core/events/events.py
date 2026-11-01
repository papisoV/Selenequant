"""Immutable payloads crossing package boundaries on the EventBus.

Every event is a frozen, keyword-only dataclass; subscribers key off the
class itself. The field NAMES form the cross-package wire contract --
renaming one breaks every publishing package -- but declaration order is
deliberately narrative: read top-down as "when, what, from, to".
"""

from __future__ import annotations
from dataclasses import dataclass, field

__all__ = [
    "OverfitDetectedEvent",
    "RegimeChangedEvent",
    "RiskThresholdBreachedEvent",
]


@dataclass(frozen=True, kw_only=True)
class RegimeChangedEvent:
    """A market-regime classifier flipped state for one symbol.

    Published by alpha regime classification; strategy layers subscribe
    to re-weight or pause exposure on the flip.
    """

    timestamp: int
    symbol: str
    new_regime: str
    old_regime: str


# -- risk side ----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RiskThresholdBreachedEvent:
    """A risk-engine guardrail fired; kill-switch logic subscribes.

    ``rule_id`` says which guardrail tripped, ``level`` the severity tag
    (e.g. ``"critical"``); ``details`` carries scalar breach context for
    downstream auditors.
    """

    message: str
    level: str
    details: dict[str, float | str] = field(default_factory=dict)
    rule_id: str


@dataclass(frozen=True, kw_only=True)
class OverfitDetectedEvent:
    """Deflated-Sharpe / PBO analysis condemned a strategy as overfit.

    Emitted by the backtest engine after robustness scoring; deployment
    gates block promotion when ``pbo`` is high relative to ``dsr`` and
    ``haircut_sharpe``.
    """

    strategy_id: str
    haircut_sharpe: float
    pbo: float
    dsr: float
