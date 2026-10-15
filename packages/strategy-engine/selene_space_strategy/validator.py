"""Pre-compilation checks for strategy configs.

A config passes through three independent screens before the compiler will
touch it:

1. *parameter ranges* — window lengths and similar numeric knobs must be
   usable (positive, integral where an int is required);
2. *logic consistency* — every opened direction must have a matching exit,
   and entry/exit rule sets must not be mirror images of each other;
3. *data availability* — every indicator referenced by a rule (including the
   comparator side) must exist in a known indicator universe.

:func:`validate` runs all three and folds findings into one
:class:`ValidationResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selene_space_strategy.models import EngineStrategyConfig


class Recovery(str, Enum):
    """Closed set of next actions an agent may take on a validation issue."""

    REPAIR_AND_REVALIDATE = "repair_and_revalidate"
    FETCH_CONTRACT_AND_REBUILD = "fetch_contract_and_rebuild"
    STOP_AND_REPORT = "stop_and_report"


@dataclass
class ValidationIssue:
    """One structured validation finding.

    ``code`` and ``recovery`` are closed enums — agents must treat them as
    the only machine-readable signal for what to do next.
    """

    code: str
    field_path: str
    message: str
    recovery: Recovery


@dataclass
class ValidationResult:
    """Accumulated outcome of a validation pass.

    ``valid`` starts True and flips permanently once :meth:`add_error`
    records the first problem; ``errors`` keeps the human-readable list.
    """

    valid: bool = True
    errors: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Log ``message`` and mark the overall result invalid."""
        self.valid = False
        self.errors.append(message)

    def add_issue(
        self, code: str, field_path: str, message: str, recovery: Recovery
    ) -> None:
        """Record a structured issue (also flips ``valid`` like add_error)."""
        self.valid = False
        self.errors.append(message)
        self.issues.append(
            ValidationIssue(
                code=code, field_path=field_path, message=message, recovery=recovery
            )
        )


# Window-like parameters that must be positive integers wherever they appear.
_WINDOW_PARAMS = {"period", "fast_period", "slow_period", "signal_period"}

# Fallback indicator universe when no registry listing is supplied.
_BUILTIN_INDICATORS = {"sma", "ema", "rsi", "macd"}

# Multi-output families: these aliases resolve through their parent indicator.
_FAMILY_ALIASES = {
    "macd": ("macd_signal", "macd_histogram"),
    "bbands": ("bbands_upper", "bbands_middle", "bbands_lower"),
}


def _check_window_param(param_name: str, value: object) -> str | None:
    """Return an error string when ``value`` is unusable as a window length."""
    if not isinstance(value, (int, float)) or value <= 0:
        return f"must be positive, got {value!r}"
    if isinstance(value, float) and not value.is_integer():
        return f"must be an integer, got {value}"
    return None


def _screen_parameter_ranges(
    config: EngineStrategyConfig, out: ValidationResult
) -> None:
    """Reject non-positive or fractional windows in parameters and rules."""
    for key, value in config.parameters.items():
        if key in _WINDOW_PARAMS:
            problem = _check_window_param(key, value)
            if problem:
                out.add_issue(
                    "invalid_parameter",
                    f"parameters.{key}",
                    f"Parameter '{key}' {problem}",
                    Recovery.REPAIR_AND_REVALIDATE,
                )

    for section_name, section in (
        ("entry_rules", config.parsed_entry_rules),
        ("exit_rules", config.parsed_exit_rules),
    ):
        for direction in ("long", "short", "close_long", "close_short"):
            for position, rule in enumerate(getattr(section, direction, ())):
                for pname, pval in rule.params.items():
                    if pname in _WINDOW_PARAMS:
                        problem = _check_window_param(pname, pval)
                        if problem:
                            out.add_issue(
                                "invalid_parameter",
                                f"{section_name}.{direction}[{position}].{pname}",
                                f"parameter '{pname}' {problem}",
                                Recovery.REPAIR_AND_REVALIDATE,
                            )
                comparator_params = (
                    rule.comparator.get("params")
                    if isinstance(rule.comparator, dict)
                    else None
                )
                if isinstance(comparator_params, dict):
                    for pname, pval in comparator_params.items():
                        if pname in _WINDOW_PARAMS:
                            problem = _check_window_param(pname, pval)
                            if problem:
                                out.add_issue(
                                    "invalid_parameter",
                                    f"{section_name}.{direction}[{position}].comparator.{pname}",
                                    f"parameter '{pname}' {problem}",
                                    Recovery.REPAIR_AND_REVALIDATE,
                                )


def _rule_signature(rule: object) -> tuple:
    """Identity tuple used to detect entry/exit sets that cancel each other.

    The comparator participates so that e.g. 'RSI crosses_above 45' vs
    'RSI crosses_above 65' count as different conditions.
    """
    return (rule.indicator, rule.operator, str(rule.params), str(rule.comparator))  # type: ignore[attr-defined]


def _screen_logic_consistency(
    config: EngineStrategyConfig, out: ValidationResult
) -> None:
    """Every entry direction needs exits, and neither side may be a no-op."""
    entry = config.parsed_entry_rules
    exit_ = config.parsed_exit_rules

    if entry.long and not exit_.close_long:
        out.add_issue(
            "missing_exit",
            "exit_rules.close_long",
            "Strategy has long entry rules but no close_long exit rules",
            Recovery.REPAIR_AND_REVALIDATE,
        )
    if entry.short and not exit_.close_short:
        out.add_issue(
            "missing_exit",
            "exit_rules.close_short",
            "Strategy has short entry rules but no close_short exit rules",
            Recovery.REPAIR_AND_REVALIDATE,
        )

    # Identical signature sets on both sides would open and close at once.
    paired_sides = (
        (entry.long, exit_.close_long, "Long"),
        (entry.short, exit_.close_short, "Short"),
    )
    for entry_list, exit_list, label in paired_sides:
        if entry_list and exit_list:
            if {_rule_signature(r) for r in entry_list} == {
                _rule_signature(r) for r in exit_list
            }:
                out.add_issue(
                    "self_cancelling_rules",
                    f"entry_rules.{label.lower()}",
                    f"{label} entry and {'close_' + label.lower()} exit conditions are identical — "
                    "positions would close immediately",
                    Recovery.STOP_AND_REPORT,
                )


def _resolve_family(name: str, available: set[str]) -> str | None:
    """Map a multi-output alias to its family root, else pass the name through."""
    for root, aliases in _FAMILY_ALIASES.items():
        if name in aliases:
            return root
    return name


def _screen_data_availability(
    config: EngineStrategyConfig,
    out: ValidationResult,
    available_indicators: set[str] | None,
) -> None:
    """Every referenced indicator (rules + comparators) must be known."""
    universe = (
        available_indicators
        if available_indicators is not None
        else set(_BUILTIN_INDICATORS)
    )

    references: list[tuple[str, str]] = []
    for section_name, section in (
        ("entry_rules", config.parsed_entry_rules),
        ("exit_rules", config.parsed_exit_rules),
    ):
        for direction in ("long", "short", "close_long", "close_short"):
            for position, rule in enumerate(getattr(section, direction, ())):
                where = f"{section_name}.{direction}[{position}]"
                references.append((where, rule.indicator))
                if isinstance(rule.comparator, dict) and "indicator" in rule.comparator:
                    references.append(
                        (f"{where}.comparator", rule.comparator["indicator"])
                    )

    for where, name in references:
        root = _resolve_family(name, universe)
        if root == name and name not in universe and root not in universe:
            listing = sorted(universe)
            out.add_issue(
                "unknown_indicator",
                where,
                f"unknown indicator '{name}' (available: {listing})",
                Recovery.FETCH_CONTRACT_AND_REBUILD,
            )
        elif root != name and root not in universe:
            out.add_issue(
                "alias_needs_family",
                where,
                f"'{name}' requires '{root}' indicator",
                Recovery.FETCH_CONTRACT_AND_REBUILD,
            )


def validate(
    config: EngineStrategyConfig,
    available_indicators: set[str] | None = None,
) -> ValidationResult:
    """Screen a strategy config; returns the combined verdict.

    Args:
        config: Config under review.
        available_indicators: Indicator names considered resolvable. ``None``
            selects the built-in minimal set {sma, ema, rsi, macd}.
    """
    verdict = ValidationResult()

    if not config.symbols:
        verdict.add_issue(
            "missing_field",
            "symbols",
            "Strategy must specify at least one symbol",
            Recovery.REPAIR_AND_REVALIDATE,
        )
    if not config.interval:
        verdict.add_issue(
            "missing_field",
            "interval",
            "Strategy must specify an interval",
            Recovery.REPAIR_AND_REVALIDATE,
        )

    _screen_parameter_ranges(config, verdict)
    _screen_logic_consistency(config, verdict)
    _screen_data_availability(config, verdict, available_indicators)

    return verdict
