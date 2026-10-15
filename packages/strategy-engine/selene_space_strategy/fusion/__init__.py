"""Signal-fusion facade.

Groups the ensemble voting layer behind one short module path. The voting
machinery itself lives in :mod:`selene_space_strategy.fusion.ensemble`; this
package init exists purely as a stable import surface for callers who prefer
``from selene_space_strategy.fusion import StrategyEnsemble`` over reaching
into the implementation module directly.

Quick orientation of what each name does:

- :class:`StrategyEnsemble`
    Owns one :class:`~selene_space_strategy.runtime.StrategyRuntime` per
    member strategy, fans every incoming kline out to all of them, and
    merges their verdicts through the configured fusion method.
- :class:`FusionMethod`
    Enumerates the merge strategies (majority vote, unanimous, weighted,
    threshold-based consensus, pass-through).
- :class:`EnsembleConfig`
    Static settings: which fusion method, the consensus fraction, quorum
    size, and how BUY/SELL conflicts are broken.
- :class:`DynamicThresholdConfig`
    Knobs for volatility- and history-aware adaptation of the consensus
    bar (base level, clamp range, decay, confidence boost).
- :class:`EnsembleSignal`
    Per-bar result record: fused signal (or None), contributor ids, side
    counts, and ensemble confidence.
"""

from selene_space_strategy.fusion.ensemble import (
    DynamicThresholdConfig as DynamicThresholdConfig,
)
from selene_space_strategy.fusion.ensemble import (
    EnsembleConfig as EnsembleConfig,
)
from selene_space_strategy.fusion.ensemble import (
    EnsembleSignal as EnsembleSignal,
)
from selene_space_strategy.fusion.ensemble import (
    FusionMethod as FusionMethod,
)
from selene_space_strategy.fusion.ensemble import (
    StrategyEnsemble as StrategyEnsemble,
)

__all__ = [
    "StrategyEnsemble",
    "FusionMethod",
    "EnsembleConfig",
    "DynamicThresholdConfig",
    "EnsembleSignal",
]
