"""Preset strategy catalogue grouped by research theme.

Subpackages collect ready-to-run templates per family (statistical
arbitrage, derivatives carry, seasonality, ...). Importing this package
re-exports the statistical-arbitrage pair at the parent level for
backwards compatibility with older call sites.
"""

from __future__ import annotations

from selene_space_strategy.templates.presets.statistical import (
    HurstReversionStrategy,
    PairsTradingStrategy,
)

__all__ = ["HurstReversionStrategy", "PairsTradingStrategy"]
