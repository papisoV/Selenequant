"""Typed configuration layer for the strategy engine.

``selene_space_types`` owns the portable :class:`StrategyConfig` record; this
module bolts the engine's structured view on top of it. Loose dict sections
(``entry_rules`` / ``exit_rules`` / ``risk_params``) gain strongly-typed
mirrors (:class:`EntryRules`, :class:`ExitRules`, :class:`RiskParams`) that are
populated automatically whenever a config is constructed or re-validated, and
:func:`load_strategy` turns a YAML document into a fully parsed config.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from selene_space_types.strategy import StrategyConfig


# ---------------------------------------------------------------------------
# Typed rule primitives
# ---------------------------------------------------------------------------


class ConditionRule(BaseModel):
    """One leaf condition: ``<indicator>(params) <operator> <comparator>``.

    The comparator side is intentionally polymorphic — either a bare number
    (``{"comparator": 30.0}``) or another indicator reference spelled as a
    dict carrying ``indicator`` plus optional ``params``.
    """

    indicator: str = Field(
        ...,
        description="Registry key of the left-hand indicator ('sma', 'rsi', ...)",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments for the indicator constructor",
    )
    operator: str = Field(
        ...,
        description=(
            "Comparison verb: one of 'crosses_above', 'crosses_below', "
            "'greater_than', 'less_than', 'equal'"
        ),
    )
    comparator: dict[str, Any] | float = Field(
        ...,
        description="Numeric threshold, or an {'indicator': ..., 'params': ...} reference",
    )


class EntryRules(BaseModel):
    """Position-opening conditions, split by direction.

    Every rule inside one direction list must evaluate true on the same bar
    for the entry to arm (AND semantics).
    """

    long: list[ConditionRule] = Field(
        default_factory=list,
        description="AND-combined conditions for opening a long position",
    )
    short: list[ConditionRule] = Field(
        default_factory=list,
        description="AND-combined conditions for opening a short position",
    )


class ExitRules(BaseModel):
    """Position-closing conditions, split by the position being flattened."""

    close_long: list[ConditionRule] = Field(
        default_factory=list,
        description="Conditions that flatten an open long",
    )
    close_short: list[ConditionRule] = Field(
        default_factory=list,
        description="Conditions that flatten an open short",
    )


class RiskParams(BaseModel):
    """Bracket sizing / protective-stop settings for one strategy.

    Percentages are decimal fractions (``0.02`` == 2%). Fixed-percent stops
    and ATR-scaled stops coexist: which one actually drives the bracket is
    chosen through ``stop_loss_type`` / ``use_atr_stops``.
    """

    # --- fixed-percent brackets -------------------------------------------
    stop_loss_pct: float | None = Field(None, ge=0, le=1, description="Stop loss distance as decimal fraction")
    take_profit_pct: float | None = Field(None, ge=0, le=1, description="Take profit distance as decimal fraction")
    # --- portfolio exposure caps ------------------------------------------
    max_position_pct: float = Field(0.1, ge=0, le=1, description="Largest single position as portfolio fraction")
    max_open_positions: int = Field(1, ge=1, description="Concurrent position ceiling")
    # --- per-trade sizing ---------------------------------------------------
    position_size_pct: float = Field(1.0, ge=0.01, le=1.0, description="Capital fraction deployed per trade")
    use_kelly_sizing: bool = Field(False, description="Size positions with the Kelly criterion")
    kelly_fraction: float = Field(0.5, ge=0.1, le=1.0, description="Kelly scaling (0.5 = half Kelly)")
    # --- signal throttling --------------------------------------------------
    cooldown_bars: int = Field(0, ge=0, description="Quiet period between signals, in bars (0 disables)")
    # --- ATR-scaled brackets -------------------------------------------------
    use_atr_stops: bool = Field(False, description="Prefer ATR-scaled brackets over fixed percentages")
    atr_sl_mult: float = Field(2.0, ge=0.5, description="ATR multiple applied to the stop leg")
    atr_tp_mult: float = Field(3.0, ge=0.5, description="ATR multiple applied to the target leg")
    # --- layered ATR trail (research follow-up) -----------------------------
    atr_initial_mult: float = Field(1.5, ge=0.5, description="First-layer stop ATR multiple")
    atr_breakeven_mult: float = Field(2.0, ge=1.0, description="Profit ATR multiple that arms breakeven protection")
    # --- method selectors ----------------------------------------------------
    stop_loss_type: str = Field("fixed_pct", description="'fixed_pct' or 'atr_dynamic'")
    position_sizing_method: str = Field("fixed_fraction", description="'fixed_fraction' or 'kelly_half'")


# ---------------------------------------------------------------------------
# Engine config — StrategyConfig + parsed views
# ---------------------------------------------------------------------------


class EngineStrategyConfig(StrategyConfig):
    """A :class:`StrategyConfig` carrying parsed rule structures alongside.

    Construction (and every later ``model_validate``) fills the ``parsed_*``
    mirrors from the raw dict sections whenever the mirrors are still empty.
    Assignments happen through ``object.__setattr__`` because the parent
    model is mutable but post-init field writes need to bypass validation.
    """

    parsed_entry_rules: EntryRules = Field(
        default_factory=EntryRules,
        description="Typed mirror of entry_rules",
    )
    parsed_exit_rules: ExitRules = Field(
        default_factory=ExitRules,
        description="Typed mirror of exit_rules",
    )
    parsed_risk_params: RiskParams = Field(
        default_factory=RiskParams,
        description="Typed mirror of risk_params",
    )

    def model_post_init(self, __context: Any) -> None:
        """Backfill the parsed_* mirrors from their raw dict sources."""
        from selene_space_strategy.rule_parser import parse_entry_rules, parse_exit_rules

        entry_view = self.parsed_entry_rules
        if not entry_view.long and not entry_view.short and self.entry_rules:
            object.__setattr__(self, "parsed_entry_rules", parse_entry_rules(self.entry_rules))

        exit_view = self.parsed_exit_rules
        if not exit_view.close_long and not exit_view.close_short and self.exit_rules:
            object.__setattr__(self, "parsed_exit_rules", parse_exit_rules(self.exit_rules))

        if self.parsed_risk_params == RiskParams() and self.risk_params:
            try:
                object.__setattr__(self, "parsed_risk_params", RiskParams.model_validate(self.risk_params))
            except Exception:
                pass  # unparseable risk dicts keep the defaults


# ---------------------------------------------------------------------------
# YAML ingestion
# ---------------------------------------------------------------------------


class StrategyLoadError(Exception):
    """Raised when a strategy YAML document cannot be turned into a config."""


_YAML_SUFFIXES = (".yaml", ".yml")


def load_strategy(path: str | Path) -> EngineStrategyConfig:
    """Read, sanity-check, and validate a strategy definition from disk.

    Dict-shaped rule sections are routed into the corresponding ``parsed_*``
    fields before validation so Pydantic coerces them in one pass.

    Raises:
        StrategyLoadError: missing file, wrong suffix, malformed YAML,
            non-mapping document, or schema violation.
    """
    file_path = Path(path)

    if not file_path.exists():
        raise StrategyLoadError(f"Strategy file not found: {file_path}")
    if file_path.suffix.lower() not in _YAML_SUFFIXES:
        raise StrategyLoadError(f"Expected .yaml or .yml file, got: {file_path.suffix}")

    try:
        document = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StrategyLoadError(f"Invalid YAML in {file_path}: {exc}") from exc

    if not isinstance(document, dict):
        raise StrategyLoadError(f"Expected a YAML mapping at top level, got {type(document).__name__}")

    # Hand raw dict sections to the parsed_* mirrors for single-pass coercion.
    for raw_key, parsed_key in (
        ("entry_rules", "parsed_entry_rules"),
        ("exit_rules", "parsed_exit_rules"),
        ("risk_params", "parsed_risk_params"),
    ):
        section = document.get(raw_key)
        if isinstance(section, dict):
            document.setdefault(parsed_key, section)

    try:
        return EngineStrategyConfig.model_validate(document)
    except Exception as exc:
        raise StrategyLoadError(f"Invalid strategy configuration in {file_path}: {exc}") from exc
