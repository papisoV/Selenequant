"""Statistical-arbitrage preset family.

Two templates live here:

* :class:`PairsTradingStrategy` — trades the z-scored spread between a
  pair, gated by a Hurst-exponent persistence check.
* :class:`HurstReversionStrategy` — single-asset mean reversion whose
  aggressiveness follows the Hurst exponent.

Import surface: the classes re-export through this package module; older
call sites may also reach them via ``templates.presets`` or
``selene_space_strategy`` top-level re-exports.
"""

from selene_space_strategy.templates.presets.statistical.hurst_reversion import (
    HurstReversionStrategy,
)
from selene_space_strategy.templates.presets.statistical.pairs_trading import (
    PairsTradingStrategy,
)

__all__ = ["HurstReversionStrategy", "PairsTradingStrategy"]
