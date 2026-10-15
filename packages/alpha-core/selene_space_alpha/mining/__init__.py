"""Factor mining engine - Transformer-based formula discovery."""

from .engine import AlphaEngine, TrainingStopRequested
from .model import FormulaPolicyNet
from .low_rank import LowRankRegularizer, EffectiveRankMonitor
from .backtest import MemeBacktest
from .island_engine import IslandAlphaEngine, EliteRecord, IslandTrainingState

__all__ = [
    "AlphaEngine",
    "TrainingStopRequested",
    "FormulaPolicyNet",
    "LowRankRegularizer",
    "EffectiveRankMonitor",
    "MemeBacktest",
    "IslandAlphaEngine",
    "EliteRecord",
    "IslandTrainingState",
]
