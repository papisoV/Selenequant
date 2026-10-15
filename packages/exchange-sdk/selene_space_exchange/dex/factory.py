"""DEX adapter factory — create perpetual DEX adapters by name."""

from __future__ import annotations

from typing import Any

from selene_space_exchange.dex.perpetual_mixin import PerpetualDEXMixin


def create_dex_adapter(name: str, config: dict[str, Any] | None = None) -> PerpetualDEXMixin:
    """Create a perpetual DEX adapter by name.

    Args:
        name: Exchange identifier — "decibel", "extended", or "risex".
        config: Configuration dict passed to the adapter's Config model.

    Returns:
        A ``PerpetualDEXMixin`` instance (also inherits ``TradingClient``).

    Raises:
        ValueError: Unknown exchange name.
    """
    config = config or {}

    if name == "decibel":
        from selene_space_exchange.dex.decibel.adapter import DecibelTradingAdapter
        from selene_space_exchange.dex.decibel.config import DecibelConfig

        return DecibelTradingAdapter(DecibelConfig(**config))

    if name == "extended":
        from selene_space_exchange.dex.extended.adapter import ExtendedTradingAdapter
        from selene_space_exchange.dex.extended.config import ExtendedConfig

        return ExtendedTradingAdapter(ExtendedConfig(**config))

    if name == "risex":
        from selene_space_exchange.dex.risex.adapter import RisexTradingAdapter
        from selene_space_exchange.dex.risex.config import RisexConfig

        return RisexTradingAdapter(RisexConfig(**config))

    raise ValueError(
        f"Unknown DEX adapter: {name!r}. "
        f"Supported: 'decibel', 'extended', 'risex'."
    )
