"""IndicatorRegistry coverage — private registries, the shipped 29-name
default inventory, and class-to-instance resolution."""

from __future__ import annotations

import pytest

from selene_space_strategy.indicators.base import Indicator
from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.macd import MACD
from selene_space_strategy.indicators.registry import (
    IndicatorNotFoundError,
    IndicatorRegistry,
    indicator_registry,
)
from selene_space_strategy.indicators.rsi import RSI
from selene_space_strategy.indicators.sma import SMA


def _fresh_registry_with_sma() -> IndicatorRegistry:
    reg = IndicatorRegistry()
    reg.register("sma", SMA)
    return reg


class TestPrivateRegistryOps:
    """Behaviour of a standalone IndicatorRegistry instance."""

    def test_roundtrip_registration(self) -> None:
        assert _fresh_registry_with_sma().get("sma") is SMA

    def test_casefolded_lookup(self) -> None:
        reg = _fresh_registry_with_sma()
        for spelling in ("SMA", "Sma"):
            assert reg.get(spelling) is SMA

    def test_missing_name_raises(self) -> None:
        with pytest.raises(IndicatorNotFoundError, match="ichimoku"):
            IndicatorRegistry().get("ichimoku")

    def test_new_registry_is_empty(self) -> None:
        assert IndicatorRegistry().list() == []

    def test_list_output_sorted(self) -> None:
        reg = IndicatorRegistry()
        for name, cls in (("rsi", RSI), ("ema", EMA), ("sma", SMA)):
            reg.register(name, cls)
        listed = reg.list()
        assert listed == sorted(listed)
        assert set(listed) == {"rsi", "ema", "sma"}

    @pytest.mark.parametrize("spelling", ["sma", "SMA"])
    def test_membership_probe(self, spelling: str) -> None:
        assert _fresh_registry_with_sma().has(spelling)

    def test_absent_name_not_a_member(self) -> None:
        assert not _fresh_registry_with_sma().has("rsi")

    def test_last_registration_wins(self) -> None:
        reg = _fresh_registry_with_sma()
        reg.register("sma", EMA)
        assert reg.get("sma") is not SMA


# Shipped default inventory: classic oscillator/trend tools plus selene's
# price-action extensions (fvg, liquidity_sweep, order_block, ...).
SHIPPED_NAMES = [
    "adx", "atr", "bbands", "chop", "close", "cmf", "cpr", "divergence",
    "efficiency_ratio", "ema", "force_index", "funding_rate",
    "funding_rate_z", "fvg", "half_life", "hurst", "kdj",
    "liquidity_sweep", "macd", "market_structure", "mfi", "obv",
    "order_block", "ppo", "rsi", "sma", "stoch", "volume_cluster",
    "zscore"
]

CORE_BINDINGS = [("sma", SMA), ("ema", EMA), ("rsi", RSI), ("macd", MACD)]


class TestShippedSingleton:
    """The module-level ``indicator_registry``."""

    def test_inventory_exact_match(self) -> None:
        names = indicator_registry.list()
        assert names == SHIPPED_NAMES
        assert len(names) >= 20

    @pytest.mark.parametrize(("name", "cls"), CORE_BINDINGS)
    def test_core_bindings(self, name: str, cls: type) -> None:
        assert indicator_registry.get(name) is cls

    def test_binding_produces_functional_instance(self) -> None:
        instance = indicator_registry.get("sma")(period=20)
        assert isinstance(instance, Indicator)
        assert instance.name == "sma"
        assert instance.period == 20
