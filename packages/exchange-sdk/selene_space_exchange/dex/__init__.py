"""Perpetual DEX adapter package (Decibel, Extended, RISEx)."""

from selene_space_exchange.dex.decibel.adapter import DecibelTradingAdapter
from selene_space_exchange.dex.decibel.config import DecibelConfig
from selene_space_exchange.dex.extended.adapter import ExtendedTradingAdapter
from selene_space_exchange.dex.extended.config import ExtendedConfig
from selene_space_exchange.dex.factory import create_dex_adapter
from selene_space_exchange.dex.perpetual_mixin import PerpetualDEXMixin
from selene_space_exchange.dex.risex.adapter import RisexTradingAdapter
from selene_space_exchange.dex.risex.config import RisexConfig
from selene_space_exchange.dex.types import (
    DEXFill,
    DEXMarket,
    DEXPosition,
    DEXPriceUpdate,
    GridMode,
    GridOrderInfo,
    OutOfRangeAction,
)

__all__ = [
    # Factory
    "create_dex_adapter",
    # Mixin & types
    "PerpetualDEXMixin",
    "DEXMarket",
    "DEXPosition",
    "DEXFill",
    "DEXPriceUpdate",
    "GridOrderInfo",
    "GridMode",
    "OutOfRangeAction",
    # Decibel
    "DecibelTradingAdapter",
    "DecibelConfig",
    # Extended
    "ExtendedTradingAdapter",
    "ExtendedConfig",
    # RISEx
    "RisexTradingAdapter",
    "RisexConfig",
]
