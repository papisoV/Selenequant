"""Turn declarative strategy configs into executable artifacts.

:func:`compile` walks a validated :class:`EngineStrategyConfig`, instantiates
every referenced indicator once (identical indicator+parameter pairs share a
single instance), and packages the four rule lists (long/short entries,
close_long/close_short exits) as :class:`CompiledCondition` records that
:class:`~selene_space_strategy.runtime.StrategyRuntime` can evaluate bar by
bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from selene_space_strategy.indicators.registry import (
    IndicatorNotFoundError,
    IndicatorRegistry,
    indicator_registry,
)
from selene_space_strategy.models import ConditionRule, EngineStrategyConfig
from selene_space_strategy.validator import validate

if TYPE_CHECKING:
    from selene_space_strategy.indicators.base import Indicator


class CompilationError(Exception):
    """A strategy config could not be turned into an executable form."""


# ---------------------------------------------------------------------------
# Compiled artifacts
# ---------------------------------------------------------------------------


@dataclass
class CompiledCondition:
    """One condition lowered to runtime-ready form.

    ``indicator`` is the left-hand instance; the right-hand side is either a
    second indicator instance (``comparator_indicator``) or a fixed number
    (``comparator_value``). ``column_name`` / ``comparator_column_name`` are
    the DataFrame keys the runtime will populate. ``raw`` preserves the
    original rule mapping so zone-style operators can read comparator extras
    (``type``, ``tolerance``, ...).
    """

    indicator: Indicator
    operator: str
    comparator_indicator: Indicator | None = None
    comparator_value: float | None = None
    column_name: str = ""
    comparator_column_name: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class CompiledStrategy:
    """Everything the runtime needs: config plus instantiated pieces."""

    config: EngineStrategyConfig
    indicators: dict[str, Indicator] = field(default_factory=dict)
    long_entry_conditions: list[CompiledCondition] = field(default_factory=list)
    short_entry_conditions: list[CompiledCondition] = field(default_factory=list)
    close_long_conditions: list[CompiledCondition] = field(default_factory=list)
    close_short_conditions: list[CompiledCondition] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Indicator resolution
# ---------------------------------------------------------------------------

# Multi-output families: alias -> family root. The family's calculate_full()
# emits several columns; each alias names one of them.
_OUTPUT_FAMILIES: dict[str, str] = {
    "macd_signal": "macd",
    "macd_histogram": "macd",
    "bbands_upper": "bbands",
    "bbands_lower": "bbands",
    "bbands_middle": "bbands",
}

_FAMILY_COLUMNS: dict[str, tuple[str, str]] = {
    # root -> (alias prefix, replacement target inside the base column name)
    "macd": ("macd", "macd_signal"),
}


def _column_key(root_name: str, params: dict) -> str:
    """Stable cache key: name plus sorted parameter assignments."""
    assignments = "_".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{root_name}_{assignments}" if params else root_name


def _derived_column(base_key: str, family_root: str, alias: str) -> str:
    """Rewrite the family root inside ``base_key`` with the alias name."""
    return base_key.replace(family_root, alias, 1)


def _constructor_kwargs(resolved_name: str, params: dict) -> dict:
    """Translate YAML-ish params into indicator constructor arguments."""
    kwargs: dict = {}
    for key, target in (("period", "period"), ("fast_period", "fast_period"), ("signal_period", "signal_period")):
        if key in params:
            kwargs[target] = int(params[key])
    if resolved_name == "macd" and "slow_period" in params:
        # MACD's primary lookback is its slow EMA.
        kwargs.setdefault("period", int(params["slow_period"]))
    return kwargs


def _resolve_indicator(
    name: str,
    params: dict,
    registry: IndicatorRegistry,
    cache: dict[str, Indicator],
) -> tuple[Indicator, str]:
    """Fetch (or build) the indicator behind ``name`` and return its column key.

    Aliased outputs (macd_signal, bbands_upper, ...) resolve through their
    family root; building a family member registers every sibling column so
    later rules referencing siblings hit the same shared instance.
    """
    alias = name if name in _OUTPUT_FAMILIES else None
    root_name = _OUTPUT_FAMILIES.get(name, name)

    base_key = _column_key(root_name, params)
    column = _derived_column(base_key, root_name, alias) if alias else base_key

    cached = cache.get(column) or cache.get(base_key)
    if cached is not None:
        return cached, column

    try:
        factory = registry.get(root_name)
    except IndicatorNotFoundError as exc:
        raise CompilationError(str(exc)) from exc

    kwargs = _constructor_kwargs(root_name, params)
    try:
        instance = factory(**kwargs) if kwargs else factory(period=14)
    except (TypeError, ValueError) as exc:
        raise CompilationError(f"Cannot instantiate indicator '{name}' with params {params}: {exc}") from exc

    cache[base_key] = instance

    # Register sibling columns of multi-output families on first build.
    if root_name == "macd":
        for sibling in ("macd_signal", "macd_histogram"):
            cache[_derived_column(base_key, "macd", sibling)] = instance
    elif root_name == "bbands":
        for sibling in ("bbands_upper", "bbands_middle", "bbands_lower"):
            cache[_derived_column(base_key, "bbands", sibling)] = instance

    return instance, column


# ---------------------------------------------------------------------------
# Condition lowering
# ---------------------------------------------------------------------------


def _lower_condition(
    rule: ConditionRule,
    registry: IndicatorRegistry,
    cache: dict[str, Indicator],
) -> CompiledCondition:
    """Lower one declarative rule into a :class:`CompiledCondition`."""
    lhs, column = _resolve_indicator(rule.indicator, rule.params, registry, cache)

    rhs_indicator: Indicator | None = None
    rhs_value: float | None = None
    rhs_column = ""

    comparator = rule.comparator
    if isinstance(comparator, (int, float)):
        rhs_value = float(comparator)
    elif isinstance(comparator, dict):
        ref_name = comparator.get("indicator")
        if not ref_name:
            raise CompilationError(f"Comparator dict must have 'indicator' key: {comparator}")
        rhs_indicator, rhs_column = _resolve_indicator(ref_name, comparator.get("params", {}), registry, cache)
    else:
        raise CompilationError(f"Invalid comparator type: {type(comparator)}")

    return CompiledCondition(
        indicator=lhs,
        operator=rule.operator,
        comparator_indicator=rhs_indicator,
        comparator_value=rhs_value,
        column_name=column,
        comparator_column_name=rhs_column,
        raw=rule.model_dump(),
    )


def compile(
    config: EngineStrategyConfig,
    registry: IndicatorRegistry | None = None,
) -> CompiledStrategy:
    """Compile ``config`` into a :class:`CompiledStrategy`.

    Order of operations: validation screen first (fail before any side
    effect), then rule lowering against a fresh instance cache.

    Raises:
        CompilationError: validation rejected the config, an indicator is
            unknown to the registry, or constructor arguments don't fit.
    """
    pool = registry or indicator_registry

    verdict = validate(config, available_indicators=set(pool.list()))
    if not verdict.valid:
        raise CompilationError(f"Strategy validation failed: {'; '.join(verdict.errors)}")

    cache: dict[str, Indicator] = {}
    artifact = CompiledStrategy(config=config)

    lowering_plan = (
        (config.parsed_entry_rules.long, artifact.long_entry_conditions),
        (config.parsed_entry_rules.short, artifact.short_entry_conditions),
        (config.parsed_exit_rules.close_long, artifact.close_long_conditions),
        (config.parsed_exit_rules.close_short, artifact.close_short_conditions),
    )
    for rules, bucket in lowering_plan:
        for rule in rules:
            bucket.append(_lower_condition(rule, pool, cache))

    artifact.indicators = cache
    return artifact
