"""Catalogue of indicator classes addressable by string key.

The catalogue maps lowercase keys to :class:`Indicator` subclasses so
strategies (and the fluent mixin) can request computations by name at
runtime. Lookups are case-insensitive; misses raise
:class:`IndicatorNotFoundError` listing what is available.

``indicator_registry`` is the process-wide instance pre-loaded with every
built-in indicator; fresh :class:`IndicatorRegistry` instances start
empty for tests and custom setups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from selene_space_strategy.indicators.base import Indicator


class IndicatorNotFoundError(LookupError):
    """Requested indicator key is absent from the catalogue."""


class IndicatorRegistry:
    """Name-keyed store of indicator classes.

    Supports insertion (:meth:`register`, last writer wins on duplicate
    keys), lookup (:meth:`get` / :meth:`has`), and enumeration
    (:meth:`list`, alphabetical).
    """

    def __init__(self) -> None:
        self._entries: dict[str, type[Indicator]] = {}

    @staticmethod
    def _normalize(key: str) -> str:
        return key.lower()

    def register(self, name: str, indicator_cls: type[Indicator]) -> None:
        """Insert or replace an indicator class under ``name``.

        Args:
            name: catalogue key; stored lowercased.
            indicator_cls: the class itself, not an instance.
        """
        self._entries[self._normalize(name)] = indicator_cls

    def get(self, name: str) -> type[Indicator]:
        """Fetch the class registered under ``name`` (case-insensitive).

        Raises:
            IndicatorNotFoundError: with an alphabetical hint of current keys.
        """
        canonical = self._normalize(name)
        try:
            return self._entries[canonical]
        except KeyError:
            raise IndicatorNotFoundError(
                f"Indicator '{name}' not found. Available: {sorted(self._entries.keys())}"
            ) from None

    def list(self) -> list[str]:
        """All registered keys in alphabetical order."""
        return sorted(self._entries)

    def has(self, name: str) -> bool:
        """Membership test for a (case-insensitive) key."""
        return self._normalize(name) in self._entries


# Every built-in indicator as (catalogue key, module path, class name).
# Keys are frozen API surface — tests and strategies depend on them.
_BUILTINS: tuple[tuple[str, str, str], ...] = (
    # core moving averages and oscillators
    ("sma", "selene_space_strategy.indicators.sma", "SMA"),
    ("ema", "selene_space_strategy.indicators.ema", "EMA"),
    ("rsi", "selene_space_strategy.indicators.rsi", "RSI"),
    ("macd", "selene_space_strategy.indicators.macd", "MACD"),
    ("bbands", "selene_space_strategy.indicators.bbands", "BollingerBands"),
    ("close", "selene_space_strategy.indicators.close", "Close"),
    ("kdj", "selene_space_strategy.indicators.kdj", "KDJ"),
    ("atr", "selene_space_strategy.indicators.atr", "ATR"),
    ("adx", "selene_space_strategy.indicators.adx", "ADX"),
    ("stoch", "selene_space_strategy.indicators.stoch", "Stochastic"),
    # statistical mean-reversion diagnostics
    ("hurst", "selene_space_strategy.indicators.statistical", "HurstExponent"),
    ("zscore", "selene_space_strategy.indicators.statistical", "ZScoreIndicator"),
    ("half_life", "selene_space_strategy.indicators.statistical", "HalfLifeIndicator"),
    # volume flow family
    ("obv", "selene_space_strategy.indicators.volume", "OnBalanceVolume"),
    ("cmf", "selene_space_strategy.indicators.volume", "ChaikinMoneyFlow"),
    ("mfi", "selene_space_strategy.indicators.volume", "MoneyFlowIndex"),
    ("force_index", "selene_space_strategy.indicators.volume", "ForceIndex"),
    # ICT structure detectors
    ("fvg", "selene_space_strategy.indicators.ict", "FVG"),
    ("order_block", "selene_space_strategy.indicators.ict", "OrderBlockDetector"),
    (
        "liquidity_sweep",
        "selene_space_strategy.indicators.ict",
        "LiquiditySweepDetector",
    ),
    ("market_structure", "selene_space_strategy.indicators.ict", "MarketStructure"),
    # ICT time-of-day and dealing-range context
    ("killzone", "selene_space_strategy.indicators.sessions", "KillzoneIndicator"),
    (
        "premium_discount",
        "selene_space_strategy.indicators.premium_discount",
        "PremiumDiscount",
    ),
    # microstructure and derivatives data
    (
        "volume_cluster",
        "selene_space_strategy.indicators.volume_cluster",
        "VolumeCluster",
    ),
    ("cpr", "selene_space_strategy.indicators.cpr", "CPR"),
    (
        "funding_rate",
        "selene_space_strategy.indicators.funding_rate",
        "FundingRateIndicator",
    ),
    (
        "funding_rate_z",
        "selene_space_strategy.indicators.funding_rate",
        "FundingRateZScore",
    ),
    # P1 batch (A2-A5)
    ("ppo", "selene_space_strategy.indicators.ppo", "PPO"),
    ("chop", "selene_space_strategy.indicators.chop", "ChoppinessIndex"),
    (
        "efficiency_ratio",
        "selene_space_strategy.indicators.efficiency_ratio",
        "EfficiencyRatio",
    ),
    ("divergence", "selene_space_strategy.indicators.divergence", "Divergence"),
)


def _load_class(module_path: str, class_name: str) -> type["Indicator"]:
    """Import-time resolution of one built-in entry."""
    import importlib

    module = importlib.import_module(module_path)
    return cast(type["Indicator"], getattr(module, class_name))


def _create_default_registry() -> IndicatorRegistry:
    """Populate a fresh registry with all built-in indicators."""
    populated = IndicatorRegistry()
    for key, module_path, class_name in _BUILTINS:
        populated.register(key, _load_class(module_path, class_name))
    return populated


indicator_registry: IndicatorRegistry = _create_default_registry()
