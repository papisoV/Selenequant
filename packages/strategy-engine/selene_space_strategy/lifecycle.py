"""Strategy lifecycle management.

Guards the journey of one strategy from idea to live deployment:

    DRAFT --validate_draft--> VALIDATED --submit_for_backtest--> BACKTESTING
      ^                                                             |
      +---------------------- iterate / reject ---------------------+

v0.1 behavior covers those three promotions. The later stations (CONFIRMED,
RUNNING, RETIRED) already sit in the transition graph so planning tools see
the complete picture; only their behaviors await future versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from selene_space_strategy.compiler import CompilationError, CompiledStrategy, compile
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.validator import ValidationResult, validate


def _utc_stamp() -> datetime:
    """Single timestamp source for every state wrapper."""
    return datetime.now(UTC)


class StrategyState(str, Enum):
    """Lifecycle station identifiers; values are the persisted spellings."""

    DRAFT = "draft"
    VALIDATED = "validated"
    BACKTESTING = "backtesting"
    # Stations whose behavior lands after v0.1:
    CONFIRMED = "confirmed"
    RUNNING = "running"
    RETIRED = "retired"


class LifecycleError(Exception):
    """An operation ran from the wrong station or crossed a forbidden edge."""


# ---------------------------------------------------------------------------
# Per-station record types
# ---------------------------------------------------------------------------


class StrategyDraft(BaseModel):
    """Starting record: raw config plus provenance, unscreened."""

    config: EngineStrategyConfig = Field(..., description="Strategy definition under review")
    state: StrategyState = Field(StrategyState.DRAFT, description="Station this record currently occupies")
    created_at: datetime = Field(default_factory=_utc_stamp, description="Creation moment (UTC)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Origin tags such as author or generator")


class ValidatedStrategy(BaseModel):
    """Screened config: validation passed, compilation permitted next.

    ``arbitrary_types_allowed`` is on because ``validation_result`` stores a
    plain dataclass rather than a Pydantic model.
    """

    config: EngineStrategyConfig = Field(..., description="Strategy definition under review")
    state: StrategyState = Field(StrategyState.VALIDATED, description="Station this record currently occupies")
    validation_result: ValidationResult = Field(..., description="Validator report that cleared this config")
    validated_at: datetime = Field(default_factory=_utc_stamp, description="Moment validation passed (UTC)")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class BacktestReadyStrategy:
    """Handoff bundle for the backtest engine.

    Intentionally not a Pydantic model: ``compiled`` wraps live Indicator
    instances, and serializing those would be meaningless.
    """

    __slots__ = ("compiled", "config", "state", "submitted_at")

    def __init__(
        self,
        config: EngineStrategyConfig,
        compiled: CompiledStrategy,
        state: StrategyState = StrategyState.BACKTESTING,
        submitted_at: datetime | None = None,
    ) -> None:
        self.compiled = compiled
        self.config = config
        self.state = state
        self.submitted_at = submitted_at if submitted_at is not None else _utc_stamp()


# ---------------------------------------------------------------------------
# Station guards and promotions
# ---------------------------------------------------------------------------


def _assert_station(observed: StrategyState, required: StrategyState, verb: str) -> None:
    """Common precheck: refuse ``verb`` unless ``observed`` is ``required``."""
    if observed != required:
        raise LifecycleError(f"Cannot {verb} in state '{observed.value}' (expected '{required.value}')")


def draft_from_config(
    config: EngineStrategyConfig,
    metadata: dict[str, Any] | None = None,
) -> StrategyDraft:
    """Open a DRAFT around ``config``, optionally tagging provenance."""
    return StrategyDraft(
        state=StrategyState.DRAFT,
        config=config,
        metadata=dict(metadata) if metadata else {},
    )


def validate_draft(
    draft: StrategyDraft,
    available_indicators: set[str] | None = None,
) -> ValidatedStrategy:
    """Screen a DRAFT; promote it to VALIDATED when nothing fails.

    Raises:
        LifecycleError: source station is not DRAFT, or the validator found
            problems — every error string is joined into one message.
    """
    _assert_station(draft.state, StrategyState.DRAFT, "validate strategy")

    report = validate(draft.config, available_indicators=available_indicators)
    if not report.valid:
        raise LifecycleError(f"Strategy validation failed: {'; '.join(report.errors)}")

    return ValidatedStrategy(
        validation_result=report,
        config=draft.config,
        state=StrategyState.VALIDATED,
    )


def submit_for_backtest(validated: ValidatedStrategy) -> BacktestReadyStrategy:
    """Compile a VALIDATED config into its backtest handoff bundle.

    Raises:
        LifecycleError: source station is not VALIDATED, or compilation failed.
    """
    _assert_station(validated.state, StrategyState.VALIDATED, "submit for backtest")

    try:
        artifact = compile(validated.config)
    except CompilationError as exc:
        raise LifecycleError(f"Compilation failed: {exc}") from exc

    return BacktestReadyStrategy(config=validated.config, compiled=artifact)


# ---------------------------------------------------------------------------
# Transition graph
# ---------------------------------------------------------------------------

# Forward edges follow the happy path; every station may fall back to DRAFT
# for iteration, and RETIRED can reopen as a fresh draft.
_ALLOWED_EDGES: dict[StrategyState, frozenset[StrategyState]] = {
    StrategyState.DRAFT: frozenset((StrategyState.VALIDATED,)),
    StrategyState.VALIDATED: frozenset((StrategyState.BACKTESTING, StrategyState.DRAFT)),
    StrategyState.BACKTESTING: frozenset((StrategyState.CONFIRMED, StrategyState.DRAFT)),
    StrategyState.CONFIRMED: frozenset((StrategyState.RUNNING, StrategyState.DRAFT)),
    StrategyState.RUNNING: frozenset((StrategyState.RETIRED, StrategyState.DRAFT)),
    StrategyState.RETIRED: frozenset((StrategyState.DRAFT,)),
}


def can_transition(from_state: StrategyState, to_state: StrategyState) -> bool:
    """Does an edge connect ``from_state`` to ``to_state``?"""
    return to_state in _ALLOWED_EDGES.get(from_state, frozenset())


def transition(from_state: StrategyState, to_state: StrategyState) -> StrategyState:
    """Walk an edge and report the destination station.

    Raises:
        LifecycleError: no such edge exists.
    """
    if not can_transition(from_state, to_state):
        raise LifecycleError(f"Invalid transition: {from_state.value} → {to_state.value}")
    return to_state
