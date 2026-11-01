"""Phase guardrail — market-phase / data-quality gating for LLM signals.

Pure, market-agnostic decision guardrail: given the current market phase and
a data-quality flag, decide whether an LLM signal may be executed as-is,
confidence-downgraded, or suppressed into a no-op.

Design constraints:
  - Pure functions, no I/O, no exceptions on bad input (fail-open returns the
    original values with an explanatory adjustment note).
  - Market-agnostic: the strategy does NOT import ashare here. Which phases
    are "conservative" is a configuration choice via ``CONSERVATIVE_PHASES``.
  - Crypto-main-market safe: the caller (LLMSignalStrategy._build_intent)
    keeps the guardrail OFF by default; crypto (7x24) paths pass no phase and
    are unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Phases where immediate buy/sell execution is not permitted (A-share style
# session semantics). Callers override by configuration, not by editing this.
CONSERVATIVE_PHASES = frozenset(
    {"PRE_OPEN", "AUCTION", "LUNCH_BREAK", "CLOSING_AUCTION", "CLOSED"}
)

# Phases where immediate execution is allowed.
OPEN_PHASES = frozenset({"OPEN"})

# Actions considered immediate buy/sell (as opposed to hold/no-op).
IMMEDIATE_ACTIONS = frozenset({"long", "short"})

# Confidence assigned when a non-intraday action is suppressed.
LOW_CONFIDENCE = 0.1

_ADJUSTMENT_CONFIDENCE_CAPPED = "confidence_capped_on_degraded_data"
_ADJUSTMENT_SUPPRESSED = "non_intraday_action_suppressed"
_ADJUSTMENT_FAIL_OPEN = "guardrail_fail_open_invalid_input"


@dataclass(frozen=True)
class GuardrailOutcome:
    """Immutable result of applying the phase guardrail to a signal.

    Attributes:
        adjusted_action: Possibly-rewritten action ("long"/"short"/"hold"/"wait").
        adjusted_confidence: Possibly-capped confidence in [0, 1].
        adjusted_size_pct: Possibly-zeroed position size fraction.
        adjustments: Machine-readable notes describing each applied adjustment.
    """

    adjusted_action: str
    adjusted_confidence: float
    adjusted_size_pct: float
    adjustments: tuple[str, ...] = field(default_factory=tuple)


def apply_phase_guardrail(
    phase: str,
    *,
    data_quality_degraded: bool = False,
    action: str,
    confidence: float,
    suggested_size_pct: float,
    confidence_cap_on_degraded: float = 0.5,
) -> GuardrailOutcome:
    """Apply phase/data-quality gating to a signal. Pure, fail-open.

    Rules (applied in order, adjustments accumulate):
      1. Data degraded + confidence above cap → cap confidence, keep direction.
      2. Phase in CONSERVATIVE_PHASES + action is immediate (long/short) →
         action becomes "wait", size zeroed, confidence dropped to LOW_CONFIDENCE.
      3. Phase in OPEN_PHASES (or conservative rules not triggered) → unchanged.
      4. Invalid input (non-numeric confidence/size, None phase, unknown phase)
         → fail-open: original values returned with a fail-open note.

    Args:
        phase: Market phase string (e.g. from ashare ``market_phase``), or any
            value — invalid values fail open.
        data_quality_degraded: True when input data quality is degraded.
        action: Signal action ("long"/"short"/"hold").
        confidence: Signal confidence in [0, 1].
        suggested_size_pct: Suggested position size as a fraction of equity.
        confidence_cap_on_degraded: Confidence ceiling when data is degraded.

    Returns:
        GuardrailOutcome with the (possibly adjusted) decision.
    """
    # --- Fail-open input validation -------------------------------------
    try:
        conf = float(confidence)
        size = float(suggested_size_pct)
        cap = float(confidence_cap_on_degraded)
    except (TypeError, ValueError):
        return GuardrailOutcome(
            adjusted_action=str(action),
            adjusted_confidence=0.0,
            adjusted_size_pct=0.0,
            adjustments=(_ADJUSTMENT_FAIL_OPEN,),
        )

    phase_str = phase if isinstance(phase, str) else ""
    action_str = action if isinstance(action, str) else ""

    if not phase_str or not action_str:
        return GuardrailOutcome(
            adjusted_action=action_str,
            adjusted_confidence=conf,
            adjusted_size_pct=size,
            adjustments=(_ADJUSTMENT_FAIL_OPEN,),
        )

    adjusted_action = action_str
    adjusted_confidence = conf
    adjusted_size = size
    adjustments: list[str] = []

    # --- Rule 1: degraded data caps confidence, keeps direction ---------
    if data_quality_degraded and adjusted_confidence > cap:
        adjusted_confidence = cap
        adjustments.append(_ADJUSTMENT_CONFIDENCE_CAPPED)

    # --- Rule 2: non-intraday phases suppress immediate actions ---------
    if phase_str in CONSERVATIVE_PHASES and adjusted_action in IMMEDIATE_ACTIONS:
        adjusted_action = "wait"
        adjusted_size = 0.0
        adjusted_confidence = min(adjusted_confidence, LOW_CONFIDENCE)
        adjustments.append(_ADJUSTMENT_SUPPRESSED)

    # OPEN (and unclassified phases) pass through unchanged.

    return GuardrailOutcome(
        adjusted_action=adjusted_action,
        adjusted_confidence=adjusted_confidence,
        adjusted_size_pct=adjusted_size,
        adjustments=tuple(adjustments),
    )
