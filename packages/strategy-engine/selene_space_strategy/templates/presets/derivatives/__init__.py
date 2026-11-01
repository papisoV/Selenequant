"""Derivatives strategy templates.

Carry/arbitrage strategies:
- FundingArbStrategy: Long spot + short perp when funding rate exceeds threshold
- BasisCarryStrategy: Long spot + short quarterly futures when basis rate exceeds threshold
"""

from selene_space_strategy.templates.presets.derivatives.funding_arb import FundingArbStrategy
from selene_space_strategy.templates.presets.derivatives.basis_carry import BasisCarryStrategy

__all__ = ["FundingArbStrategy", "BasisCarryStrategy"]
